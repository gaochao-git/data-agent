"""智能问数 LLM 配置：模型注册表加载与思考能力分派。

模型清单存于 ``chat_data_model_registry`` 表（管理页维护，变更即时刷新进程内
缓存，启动时预热一次）；表为空时自动从 ``CHAT_DATA_MODELS_FILE`` 指向的 JSON
注册表导入一次（存量迁移种子）。服务路径（事件循环内）**只认 DB**，冷缓存
直接报错不回落文件；无事件循环的脚本/测试场景才读该文件。所有字段 fail-fast：
配错宁可拒绝保存，不静默误配。思考能力三层分离——

- 语义层 ``ThinkingCapability``：模型固有能力（mode/dialect/efforts/default）；
- 决策层 ``resolve_thinking``：模型能力 × 用户意图 → 本次请求怎么想（唯一裁决点）；
- 协议层 ``_get_chat_data_extra_body``：按方言把决策序列化为 extra_body。
"""

import asyncio
import json
import math
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models import BaseChatModel
from sqlalchemy.exc import IntegrityError

from utils.crypto import decrypt_password
from utils.llm import get_llm
from utils.logger import get_logger

from ..daos.model_registry_dao import ModelRegistryDao

logger = get_logger(__name__)

DEFAULT_CHAT_DATA_OUTPUT_MAX_TOKENS = 20000

# ---- 思考能力词表 ----
THINKING_MODE_NONE = "none"  # 模型不支持思考
THINKING_MODE_OPTIONAL = "optional"  # 可开关
THINKING_MODE_FORCED = "forced"  # 强制思考不可关（只能调强度）
_THINKING_MODES = (THINKING_MODE_NONE, THINKING_MODE_OPTIONAL, THINKING_MODE_FORCED)

THINKING_DIALECT_CHAT_TEMPLATE = "chat_template"  # vLLM/SGLang/MLX 开源系
THINKING_DIALECT_REASONING_EFFORT = "reasoning_effort"  # DeepSeek 新 API / OpenAI o 系
THINKING_DIALECT_OPENROUTER = "openrouter"  # 统一 reasoning 对象
_THINKING_DIALECTS = (
    THINKING_DIALECT_CHAT_TEMPLATE,
    THINKING_DIALECT_REASONING_EFFORT,
    THINKING_DIALECT_OPENROUTER,
)

# 档位候选建议（仅供前端下拉候选，不作为校验依据——档位名由各模型自由声明）；
# off/none 之外的对齐 DeepSeek 新 API 档位族（minimal/low/medium/high/xhigh/ultra/max）
EFFORT_SUGGESTIONS = ("off", "minimal", "low", "medium", "high", "xhigh", "ultra", "max")

# 关哨兵档位名：声明为这些名字的档位选中时按「关」形态序列化（不作为 on wire 值透出，
# 防止 reasoning_effort: "off" 这类上游不认识的字面量导致 422）
OFF_EFFORT_SENTINELS = frozenset({"off", "none"})

# 档位名约束：1~32 位字母/数字/_.:-；数量上限 10；字符串参数值 ≤64
_EFFORT_LEVEL_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,32}$")
_MAX_EFFORT_COUNT = 10
_EFFORT_VALUE_MAX_LEN = 64

# 档位参数值类型：string / bool / number（int/float，非 NaN）
EffortValue = str | bool | int | float


@dataclass(frozen=True)
class EffortLevel:
    """思考档位 → API 参数值映射；value 缺省（None）= 发档位名本身。"""

    level: str
    value: EffortValue | None = None


@dataclass(frozen=True)
class ThinkingCapability:
    """模型的思考能力声明（来自注册表 params_json.thinking 字段）。"""

    mode: str
    dialect: str | None = None
    efforts: tuple[EffortLevel, ...] = ()
    default_on: bool = False
    default_effort: str | None = None


def _read_positive_int_env(name: str, default: int) -> int:
    """读取正整数配置，非法值回退默认值。"""
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def get_chat_data_output_max_tokens() -> int:
    """读取智能问数主模型调用的最大输出 token 数。"""
    return _read_positive_int_env(
        "CHAT_DATA_OUTPUT_MAX_TOKENS",
        DEFAULT_CHAT_DATA_OUTPUT_MAX_TOKENS,
    )


def get_context_compression_summary_output_max_tokens() -> int | None:
    """读取上下文压缩摘要调用的最大输出 token 配置。

    显式配置了 CHAT_DATA_CONTEXT_COMPRESSION_SUMMARY_OUTPUT_MAX_TOKENS 时返回该值；
    未配置或非法时返回 None，由调用侧按模型上下文窗口动态计算（独立于主模型输出预留）。
    """
    raw = str(os.getenv("CHAT_DATA_CONTEXT_COMPRESSION_SUMMARY_OUTPUT_MAX_TOKENS") or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


# ---------------------------------------------------------------------------
# 模型注册表（models.json）加载与校验
# ---------------------------------------------------------------------------


def get_chat_data_models_file_path() -> str:
    """读取注册表路径；旧 CHAT_DATA_MODEL 变量存在时直接报迁移错误。"""
    legacy = str(os.getenv("CHAT_DATA_MODEL") or "").strip()
    if legacy:
        raise ValueError(
            "检测到旧配置变量 CHAT_DATA_MODEL，模型清单已迁移到 "
            "CHAT_DATA_MODELS_FILE 指向的 models.json 注册表，请删除旧变量并放置注册表文件"
        )
    raw = str(os.getenv("CHAT_DATA_MODELS_FILE") or "").strip()
    if not raw:
        raise ValueError(
            "CHAT_DATA_MODELS_FILE 未配置：需指向模型注册表 JSON 文件"
            "（如 conf/models.openrouter.json，conf/ 下按供应商分文件），格式见 .env.example"
        )
    return raw


def _parse_effort_value(value: Any, prefix: str) -> EffortValue:
    """校验档位 API 参数值：string(≤64) / bool / number（非 NaN），禁止 dict/list。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if len(value) > _EFFORT_VALUE_MAX_LEN:
            raise ValueError(f"{prefix}: 字符串参数值超长（上限 {_EFFORT_VALUE_MAX_LEN}）: {value!r}")
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            raise ValueError(f"{prefix}: 参数值不允许 NaN")
        return value
    raise ValueError(f"{prefix}: 参数值应为字符串(≤64)/布尔/数字，当前 {value!r}")


def _parse_effort_item(item: Any, prefix: str) -> EffortLevel:
    """解析单个档位：旧格式字符串（档位名=参数值）或新格式 {level, value?} 对象。"""
    if isinstance(item, str):
        level, value = item.strip(), None
    elif isinstance(item, dict):
        unknown = set(item) - {"level", "value"}
        if unknown:
            raise ValueError(f"{prefix}: 档位对象含未知字段 {sorted(unknown)}（仅允许 level/value）")
        level_raw = item.get("level")
        if not isinstance(level_raw, str):
            raise ValueError(f"{prefix}: 档位 level 应为字符串，当前 {level_raw!r}")
        level = level_raw.strip()
        value = item.get("value")
        if value is not None:
            value = _parse_effort_value(value, prefix)
    else:
        raise ValueError(f"{prefix}: 档位应为字符串或 {{level, value}} 对象，当前 {item!r}")
    if not level:
        raise ValueError(f"{prefix}: 档位名不能为空")
    if not _EFFORT_LEVEL_PATTERN.match(level):
        raise ValueError(f"{prefix}: 档位名 {level!r} 非法（限 1~32 位字母/数字/_.:-）")
    return EffortLevel(level=level, value=value)


def _parse_thinking_efforts(raw: dict, dialect: str, prefix: str) -> tuple[EffortLevel, ...]:
    """校验并归一化 thinking.efforts；缺省/空数组返回空元组。

    兼容两种形状：旧 ``["low", "high"]``（读入自动升级，不回写）与
    新 ``[{"level": "low", "value": "minimal"}]``（value 缺省=档位同名）。
    """
    efforts_raw = raw.get("efforts")
    if efforts_raw is None:
        return ()
    if not isinstance(efforts_raw, list):
        raise ValueError(f"{prefix}: efforts 应为数组（档位名或 {{level, value}} 对象）")
    if not efforts_raw:
        return ()  # 空数组等同缺省（无档位），不触发方言冲突校验
    if dialect == THINKING_DIALECT_CHAT_TEMPLATE:
        raise ValueError(f"{prefix}: chat_template 方言为纯布尔协议不支持等级，请移除 efforts 或改用其它方言")
    levels: list[EffortLevel] = []
    seen: set[str] = set()
    for item in efforts_raw:
        effort = _parse_effort_item(item, prefix)
        if effort.level in seen:
            raise ValueError(f"{prefix}: 档位名重复: {effort.level}")
        seen.add(effort.level)
        levels.append(effort)
    if len(levels) > _MAX_EFFORT_COUNT:
        raise ValueError(f"{prefix}: 档位数量 {len(levels)} 超过上限 {_MAX_EFFORT_COUNT}")
    return tuple(levels)


def _parse_thinking_default_effort(raw: dict, efforts: tuple[EffortLevel, ...], prefix: str) -> Any:
    """校验 default_effort 与 efforts 成对出现且取值合法（取值为档位名）。"""
    default_effort_raw = raw.get("default_effort")
    if (default_effort_raw is None) != (not efforts):
        raise ValueError(
            f"{prefix}: default_effort 与 efforts 必须成对出现（当前 efforts={raw.get('efforts')!r}, "
            f"default_effort={default_effort_raw!r}）"
        )
    level_names = [effort.level for effort in efforts]
    if default_effort_raw is not None and default_effort_raw not in level_names:
        raise ValueError(f"{prefix}: default_effort={default_effort_raw!r} 不在档位 {level_names} 中")
    return default_effort_raw


def _parse_thinking(raw: Any, *, path: str, model_id: str) -> ThinkingCapability:
    """解析单个模型的 thinking 字段，任何不合法直接抛 ValueError。"""
    prefix = f"{path} 中模型 {model_id} 的 thinking 配置无效"
    if not isinstance(raw, dict):
        raise ValueError(f"{prefix}: 应为对象")
    # off_value/default_on 均为已废弃字段：存量配置里可能出现，容忍并静默丢弃——
    # 关形态走方言默认兜底；默认开启与否由默认档位推导（default_effort 为普通档=默认开）
    unknown_keys = set(raw) - {"mode", "dialect", "efforts", "default_on", "default_effort", "off_value"}
    if unknown_keys:
        raise ValueError(f"{prefix}: 未知字段 {sorted(unknown_keys)}")

    mode = str(raw.get("mode") or "").strip()
    if mode not in _THINKING_MODES:
        raise ValueError(f"{prefix}: mode 必须是 {_THINKING_MODES} 之一，当前值: {raw.get('mode')!r}")
    if mode == THINKING_MODE_NONE:
        extra = set(raw) - {"mode"}
        if extra:
            raise ValueError(f"{prefix}: mode=none 时不得携带其它字段，发现 {sorted(extra)}")
        return ThinkingCapability(mode=THINKING_MODE_NONE)

    dialect = str(raw.get("dialect") or "").strip()
    if dialect not in _THINKING_DIALECTS:
        raise ValueError(f"{prefix}: dialect 必须是 {_THINKING_DIALECTS} 之一，当前值: {raw.get('dialect')!r}")
    efforts = _parse_thinking_efforts(raw, dialect, prefix)
    default_effort_raw = _parse_thinking_default_effort(raw, efforts, prefix)
    # 默认开启由默认档位推导（仅 optional 有「默认开」概念）：勾了普通档=默认开；勾了关哨兵（off/none）或未勾=默认关
    default_on = (
        mode == THINKING_MODE_OPTIONAL and bool(default_effort_raw) and default_effort_raw not in OFF_EFFORT_SENTINELS
    )
    return ThinkingCapability(
        mode=mode,
        dialect=dialect,
        efforts=efforts,
        default_on=default_on,
        default_effort=default_effort_raw,
    )


def _thinking_cap_to_json(cap: ThinkingCapability) -> dict[str, Any]:
    """ThinkingCapability -> 可落 params_json 的紧凑 dict（回读必须能再过 _parse_thinking）。

    形状按校验规则裁剪：none 只带 mode；default_on 不落键（读侧由 default_effort 推导）；
    efforts/default_effort 成对出现且仅在有值时序列化；档位落新形状
    ``[{"level": ..., "value": ...}]``（value 缺省不落键，读侧=档位同名）。
    """
    if cap.mode == THINKING_MODE_NONE:
        return {"mode": THINKING_MODE_NONE}
    out: dict[str, Any] = {"mode": cap.mode, "dialect": cap.dialect or ""}
    if cap.efforts:
        out["efforts"] = [
            {"level": effort.level, **({"value": effort.value} if effort.value is not None else {})}
            for effort in cap.efforts
        ]
        if cap.default_effort:
            out["default_effort"] = cap.default_effort
    return out


def _parse_model_entry(entry: Any, *, path: str) -> dict[str, Any]:
    """解析注册表中的单个模型条目。"""
    if not isinstance(entry, dict):
        raise ValueError(f"{path} 中存在非对象条目: {type(entry).__name__}")
    model_id = str(entry.get("id") or "").strip()
    if not model_id:
        raise ValueError(f"{path} 中存在缺少 id 的模型条目")

    def _require(key: str, check: Callable[[Any], bool], describe: str) -> Any:
        value = entry.get(key)
        if not check(value):
            raise ValueError(f"{path} 中模型 {model_id} 的 {key} 无效: {value!r}（{describe}）")
        return value

    temperature = _require("temperature", lambda v: isinstance(v, (int, float)) and 0 <= v <= 2, "应为 0~2 数字")
    context_window = _require(
        "context_window",
        lambda v: isinstance(v, int) and not isinstance(v, bool) and v > 0,
        "应为正整数",
    )
    vlm = _require("vlm", lambda v: isinstance(v, bool), "应为布尔")
    max_output_raw = entry.get("max_output_tokens")
    max_output_tokens: int | None = None
    if max_output_raw is not None:
        if not isinstance(max_output_raw, int) or isinstance(max_output_raw, bool) or max_output_raw <= 0:
            raise ValueError(f"{path} 中模型 {model_id} 的 max_output_tokens 无效: {max_output_raw!r}（应为正整数）")
        if max_output_raw >= int(context_window):
            raise ValueError(
                f"{path} 中模型 {model_id} 的 max_output_tokens={max_output_raw} "
                f"必须小于 context_window={context_window}"
            )
        max_output_tokens = max_output_raw
    thinking = _parse_thinking(entry.get("thinking"), path=path, model_id=model_id)
    return {
        "id": model_id,
        "request_model": model_id,
        "temperature": float(temperature),
        "context_window": int(context_window),
        "max_output_tokens": max_output_tokens,
        "vlm": vlm,
        "thinking": thinking,
        # 连接信息可选（DB 不可用回落时也走同一 fail-fast 校验）
        "base_url": str(entry.get("base_url") or "").strip(),
        "api_key_cipher": str(entry.get("api_key_cipher") or "").strip(),
    }


def load_chat_data_model_configs() -> list[dict[str, Any]]:
    """读取并校验 JSON 文件注册表（存量迁移源与 DB 不可用时的回落）；每次调用读文件。"""
    path = get_chat_data_models_file_path()
    try:
        with open(path, encoding="utf-8") as fp:
            raw = json.load(fp)
    except FileNotFoundError as exc:
        raise ValueError(f"CHAT_DATA_MODELS_FILE 指向的文件不存在: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"模型注册表不是合法 JSON: {path}（{exc}）") from exc
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"模型注册表应为非空数组: {path}")

    configs = [_parse_model_entry(entry, path=path) for entry in raw]
    seen: set[str] = set()
    for config in configs:
        if config["id"] in seen:
            raise ValueError(f"模型注册表中 id 重复: {config['id']}（{path}）")
        seen.add(config["id"])
    return configs


# ---------------------------------------------------------------------------
# DB 注册表读取（chat_data_model_registry 表）与进程内缓存
# ---------------------------------------------------------------------------

# 缓存主存 DB 读取结果，来源锚定：_registry_cache_origin 记录构建来源
# （_REGISTRY_ORIGIN_DB=DB；否则为构建时的 CHAT_DATA_MODELS_FILE 路径），
# 来源与当前 env 不符视为冷缓存——测试在同一用例内换注册表文件即自动失效，
# 不再有「夹具预热污染显式注册表用例」的跨源污染。DB 刷新只发生在异步上下文：
# 启动预热、管理端变更后、事件循环内过期重校验——避免跨 event loop 复用连接池。
_REGISTRY_ORIGIN_DB = "\x00db"
_registry_cache: list[dict[str, Any]] | None = None
_registry_cache_loaded_at: float = 0.0
_registry_cache_origin: str = ""
_registry_refresh_task: asyncio.Task | None = None
_DEFAULT_REGISTRY_CACHE_TTL_SECONDS = 30


def _registry_cache_ttl() -> float:
    """注册表缓存 TTL（秒）；多实例部署下其它实例的变更至多延迟 TTL 生效。"""
    return float(
        _read_positive_int_env("CHAT_DATA_MODEL_REGISTRY_CACHE_TTL_SECONDS", _DEFAULT_REGISTRY_CACHE_TTL_SECONDS)
    )


def reset_registry_cache() -> None:
    """清空进程内注册表缓存（管理端失效与测试隔离用）。"""
    global _registry_cache, _registry_cache_loaded_at, _registry_cache_origin
    _registry_cache = None
    _registry_cache_loaded_at = 0.0
    _registry_cache_origin = ""


def _parse_registry_params(params_json: str, *, model_id: str) -> dict[str, Any]:
    """解析行内 params_json；非法 JSON 抛错由上层回落文件兜底（不静默吞掉）。"""
    raw = str(params_json or "").strip() or "{}"
    try:
        params = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"模型 {model_id} 的 params_json 不是合法 JSON: {exc}") from exc
    if not isinstance(params, dict):
        raise ValueError(f"模型 {model_id} 的 params_json 应为 JSON 对象")
    return params


def _registry_row_to_config(row: dict[str, Any]) -> dict[str, Any]:
    """DB 行 dict -> 消费方配置 dict；模型参数取自 params_json，统一 fail-fast 校验。"""
    model_id = str(row["model_id"])
    params = _parse_registry_params(str(row.get("params_json") or ""), model_id=model_id)
    raw_thinking = params.get("thinking")
    thinking = _parse_thinking(
        raw_thinking if isinstance(raw_thinking, dict) and raw_thinking else {"mode": THINKING_MODE_NONE},
        path="chat_data_model_registry",
        model_id=model_id,
    )
    max_output = params.get("max_output_tokens")
    # id=注册键（引用标识，会话/前端选择用它）；request_model=上游请求名（可重复，
    # 同一请求名可接多个供应商）。存量行 model_key 为空时回落 model_id（迁移前兼容）。
    model_key = str(row.get("model_key") or "").strip() or model_id
    return {
        "id": model_key,
        "request_model": model_id,
        "provider": str(row.get("provider") or "").strip(),
        "display_name": str(row["display_name"] or ""),
        "base_url": str(row["base_url"] or "").strip(),
        "api_key_cipher": str(row["api_key_cipher"] or "").strip(),
        "temperature": float(params.get("temperature", 0.2)),
        "context_window": int(params.get("context_window", 48000)),
        "max_output_tokens": int(max_output) if max_output is not None else None,
        "vlm": bool(params.get("vlm", True)),
        "thinking": thinking,
        "is_default": bool(row["is_default"]),
    }


def _registry_config_to_row_fields(config: dict[str, Any], *, is_default: bool) -> dict[str, Any]:
    """JSON 文件条目解析结果 -> 注册表行字段（首次导入用）。"""
    cap: ThinkingCapability = config["thinking"]
    params = {
        "temperature": float(config["temperature"]),
        "context_window": int(config["context_window"]),
        # 必填缺省 = 上下文窗口的 1/4（与页面保存口径一致）
        "max_output_tokens": config["max_output_tokens"]
        if config["max_output_tokens"] is not None
        else int(config["context_window"]) // 4,
        "vlm": bool(config["vlm"]),
        "thinking": _thinking_cap_to_json(cap),
    }
    return {
        "display_name": str(config.get("display_name") or config["id"]),
        # 文件种子：注册键=请求名（id 即 model_id）；连接信息随文件透传
        # （旧格式文件无此二字段落库为空，需在管理页补全）
        "base_url": str(config.get("base_url") or ""),
        "api_key_cipher": str(config.get("api_key_cipher") or ""),
        "params_json": json.dumps(params, ensure_ascii=False),
        "is_default": 1 if is_default else 0,
        "enabled_flag": 1,
        "remark_text": "",
    }


async def _import_registry_from_file(dao: ModelRegistryDao) -> list[dict[str, Any]]:
    """注册表从无到有时从 JSON 文件导入一次（存量迁移种子）；文件缺失/非法时跳过并告警。"""
    try:
        file_configs = load_chat_data_model_configs()
    except ValueError as err:
        logger.warning("模型注册表为空且 CHAT_DATA_MODELS_FILE 不可用，跳过自动导入: %s", err)
        return []
    for index, config in enumerate(file_configs):
        fields = _registry_config_to_row_fields(config, is_default=(index == 0))
        try:
            await dao.upsert(model_key=str(config["id"]), fields=fields, operator_id="system", operator_name="系统迁移")
        except IntegrityError:
            # 多实例/多 worker 同时冷启动并发首导：唯一键撞车说明该行已被并发方写入，跳过即可
            logger.info("模型 %s 首导撞唯一键（并发导入已写入），跳过", config["id"])
    logger.info("模型注册表完成 JSON 文件首次导入: %s 个模型", len(file_configs))
    return await dao.list_rows(enabled_only=True)


async def load_registry_from_db() -> list[dict[str, Any]]:
    """从 DB 读取启用模型配置；表从未有过行时从 JSON 文件导入一次（种子）。

    「从未有过行」含软删墓碑与停用行——管理员删光/停光模型后不得回源文件复活。
    """
    dao = ModelRegistryDao()
    rows = await dao.list_rows(enabled_only=True)
    if not rows and not await dao.has_any_row():
        rows = await _import_registry_from_file(dao)
    return [_registry_row_to_config(row) for row in rows]


async def refresh_registry_cache() -> None:
    """刷新进程内注册表缓存（启动预热与管理端变更后调用）。

    刷新失败时保留现有缓存（stale-while-revalidate 正语义：DB 抖动不放大为
    服务不可用）；缓存为空（如启动预热失败）则维持冷态，服务路径会明确报错。
    """
    global _registry_cache, _registry_cache_loaded_at, _registry_cache_origin
    try:
        configs = await load_registry_from_db()
    except Exception as err:
        logger.warning("模型注册表 DB 读取失败，保留现有缓存继续服务: %s", err)
        return
    _registry_cache = configs
    _registry_cache_loaded_at = time.monotonic()
    _registry_cache_origin = _REGISTRY_ORIGIN_DB


def _cache_origin_matches(origin: str) -> bool:
    """缓存来源锚定校验：DB 来源恒匹配；文件来源需与当前 env 路径一致（env 变更即失效）。"""
    return _registry_cache_origin in (_REGISTRY_ORIGIN_DB, origin)


def _schedule_registry_refresh(loop: asyncio.AbstractEventLoop) -> None:
    """在事件循环内调度一次后台注册表刷新（已有在途任务则不重复调度）。"""
    global _registry_refresh_task
    if _registry_refresh_task is None or _registry_refresh_task.done():
        _registry_refresh_task = loop.create_task(refresh_registry_cache())


def get_chat_data_model_configs() -> list[dict[str, Any]]:
    """读取启用模型配置（进程内缓存优先，来源锚定校验）。

    缓存 TTL 过期且处于事件循环内时触发后台刷新（stale-while-revalidate，先用
    过期缓存不阻塞当次调用）。DB 为唯一真值：服务路径（事件循环内）冷缓存时
    **禁止回落 JSON 文件**——启动预热已失败说明 DB 不可用，宁可报错也不提供
    过期的文件数据；无事件循环（脚本/测试夹具）才允许读 CHAT_DATA_MODELS_FILE。
    """
    global _registry_cache, _registry_cache_loaded_at, _registry_cache_origin
    origin = os.getenv("CHAT_DATA_MODELS_FILE") or ""
    if (
        _registry_cache is not None
        and _cache_origin_matches(origin)
        and time.monotonic() - _registry_cache_loaded_at < _registry_cache_ttl()
    ):
        return _registry_cache
    if _registry_cache is not None and _cache_origin_matches(origin):
        # 过期的缓存：优先返回旧值，事件循环内再补一次后台刷新
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            _schedule_registry_refresh(loop)
        return _registry_cache
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        # 冷缓存 + 服务路径：触发一次重试刷新并明确报错（不静默回落文件）
        _schedule_registry_refresh(loop)
        raise RuntimeError(
            "模型注册表尚未从数据库加载成功（启动预热失败或数据库不可用），已拒绝回落配置文件；"
            "请检查数据库连接后重启服务"
        )
    # 无事件循环（脚本/测试夹具）：读文件并落位缓存（来源锚定该路径，env 变更即失效）
    configs = load_chat_data_model_configs()
    _registry_cache = configs
    _registry_cache_loaded_at = time.monotonic()
    _registry_cache_origin = origin
    return configs


def get_chat_data_model_names() -> list[str]:
    """读取智能问数模型列表。"""
    return [config["id"] for config in get_chat_data_model_configs()]


def _default_model_id(configs: list[dict[str, Any]]) -> str:
    """默认模型 = 显式 is_default 标志位，无则取注册表首行兜底（文件路径无标志位）。"""
    if not configs:
        raise ValueError("模型注册表为空：请在模型管理面板新增并启用至少一个模型")
    for config in configs:
        if config.get("is_default"):
            return str(config["id"])
    return str(configs[0]["id"])


def get_default_chat_data_model() -> str:
    """读取默认模型。"""
    return _default_model_id(get_chat_data_model_configs())


def get_chat_data_model_config(model: str | None = None) -> dict[str, Any]:
    """读取指定模型配置；未命中时抛错而非静默回退（fail-fast）。

    优先按注册键（id）精确匹配；未命中再按请求名（request_model）匹配一次——
    历史会话/定时任务存的是改造前的请求名，注册键迁移后与请求名同值，
    新增同请求名供应商行时按列表序（默认行优先）取首个命中。
    """
    selected = str(model or "").strip()
    configs = get_chat_data_model_configs()
    if not selected:
        return next(config for config in configs if config["id"] == _default_model_id(configs))
    for config in configs:
        if config["id"] == selected:
            return config
    for config in configs:
        if str(config.get("request_model") or "") == selected:
            return config
    raise ValueError(f"请求的模型不在模型注册表中: {selected!r}（可用: {get_chat_data_model_names()}）")


def get_chat_data_temperature(model: str | None = None) -> float:
    """读取模型温度。"""
    raw_temperature = get_chat_data_model_config(model).get("temperature")
    return float(raw_temperature) if raw_temperature is not None else 0.7


def get_chat_data_model_max_output_tokens(model: str | None = None) -> int:
    """解析单次模型调用的最大输出：注册表 max_output_tokens > 环境变量全局默认 > 代码兜底。"""
    per_model = get_chat_data_model_config(model).get("max_output_tokens")
    return int(per_model) if per_model else get_chat_data_output_max_tokens()


# ---------------------------------------------------------------------------
# 决策层与协议层
# ---------------------------------------------------------------------------


def resolve_thinking(
    capability: ThinkingCapability,
    user_enabled: bool | None,
    user_effort: str | None = None,
) -> tuple[str, str | None]:
    """唯一裁决点：模型能力 × 用户意图 → ('omit', None) | ('on', level|None) | ('off', None)。

    forced 模式忽略用户关闭请求（永不产生"关"形态）；optional 未传时按
    default_on 落点；none 一律不发。用户档位仅在模型声明了 efforts 且
    包含该档位时生效，否则回退 default_effort。
    """
    if capability.mode == THINKING_MODE_NONE:
        return ("omit", None)
    if capability.mode == THINKING_MODE_FORCED:
        return ("on", _pick_effort(capability, user_effort))
    # optional
    wants = capability.default_on if user_enabled is None else bool(user_enabled)
    return ("on", _pick_effort(capability, user_effort)) if wants else ("off", None)


def _pick_effort(capability: ThinkingCapability, user_effort: str | None) -> str | None:
    """档位裁决：用户显式选择优先，但必须在注册表声明的档位名内；返回档位名。"""
    if user_effort and any(effort.level == user_effort for effort in capability.efforts):
        return user_effort
    return capability.default_effort


def _effort_wire_value(capability: ThinkingCapability, level: str | None) -> EffortValue | None:
    """档位名 → API 参数值；未声明 value 时发档位名本身（未声明的档位名原样透出）。"""
    if level is None:
        return None
    for effort in capability.efforts:
        if effort.level == level:
            return effort.level if effort.value is None else effort.value
    return level


def _reasoning_effort_body(capability: ThinkingCapability, decision: str, level: str | None) -> dict[str, Any]:
    """reasoning_effort 方言：on 发档位 wire 值；off 固定发 "none"（DeepSeek 新 API/OpenAI gpt-5 系标准关值）。"""
    if decision == "on":
        return {"reasoning_effort": _effort_wire_value(capability, level)}
    return {"reasoning_effort": "none"}


def _openrouter_body(capability: ThinkingCapability, decision: str, level: str | None) -> dict[str, Any]:
    """openrouter 方言：on 发 {"effort": value}；off 固定发 {"enabled": false}（官方协议关形态）。"""
    if decision == "on":
        return {"reasoning": {"effort": _effort_wire_value(capability, level)}}
    return {"reasoning": {"enabled": False}}


def _get_chat_data_extra_body(
    capability: ThinkingCapability,
    enable_thinking: bool | None = None,
    thinking_effort: str | None = None,
) -> dict[str, Any]:
    """按方言把思考决策序列化为 extra_body；omit 时不含任何思考键。

    选中档位名为关哨兵（off/none，见 OFF_EFFORT_SENTINELS）时降级为「关」形态，
    避免把 "off" 这类上游不认识的字面量当 on wire 值发出（实测 422）。
    """
    decision, level = resolve_thinking(capability, enable_thinking, thinking_effort)
    if decision == "on" and level is not None and level in OFF_EFFORT_SENTINELS:
        decision, level = "off", None
    if decision == "omit":
        return {}
    if capability.dialect == THINKING_DIALECT_CHAT_TEMPLATE:
        flag = decision == "on"
        return {"enable_thinking": flag, "chat_template_kwargs": {"enable_thinking": flag}}
    if decision == "on" and level is None:
        # 未声明档位：不发任何思考键。注册表是唯一事实源——
        # 需要思考能力的模型必须在注册表声明 efforts/default_effort，
        # 代码不做配置缺失的兼容兜底。
        return {}
    if capability.dialect == THINKING_DIALECT_REASONING_EFFORT:
        return _reasoning_effort_body(capability, decision, level)
    if capability.dialect == THINKING_DIALECT_OPENROUTER:
        return _openrouter_body(capability, decision, level)
    return {}


def get_chat_data_model_options() -> dict[str, Any]:
    """读取智能问数前端可选模型列表（含思考能力声明）。

    注册表为空（新装未配置模型）时返回空列表由前端引导，不抛错（避免 /config 500）。
    """
    raw_models = get_chat_data_model_configs()
    if not raw_models:
        return {"models": [], "default_model": ""}
    default_model = _default_model_id(raw_models)
    models = []
    for config in raw_models:
        cap: ThinkingCapability = config["thinking"]
        models.append(
            {
                "id": config["id"],
                "label": str(config.get("display_name") or "") or str(config["id"]),
                "provider": str(config.get("provider") or ""),
                # 视觉能力随模型下发：前端选模型弹窗据此标注「视觉」，避免用户误选
                "vlm": bool(config.get("vlm", True)),
                "value": config["id"],
                "is_default": config["id"] == default_model,
                "thinking": {
                    "mode": cap.mode,
                    # 只下发档位名，不下发 wire 值（前端不与协议耦合）
                    "efforts": [effort.level for effort in cap.efforts],
                    "default_on": cap.default_on,
                    "default_effort": cap.default_effort,
                },
            }
        )
    return {"models": models, "default_model": default_model}


def _resolve_chat_data_api_key(config: dict[str, Any]) -> str | None:
    """解密模型行密文；行内未配置返回 None（连接信息只以模型行为准，无环境变量回退）。"""
    cipher = str(config.get("api_key_cipher") or "").strip()
    return decrypt_password(cipher) if cipher else None


def _resolve_chat_data_base_url(config: dict[str, Any]) -> str | None:
    """模型行 base_url；行内未配置返回 None（连接信息只以模型行为准，无环境变量回退）。"""
    return str(config.get("base_url") or "").strip() or None


def get_chat_data_llm(
    model: str | None = None,
    streaming: bool = False,
    stream_usage: bool | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: int | None = None,
    enable_thinking: bool | None = None,
    thinking_effort: str | None = None,
) -> BaseChatModel:
    """读取模型注册表创建智能问数专用 LLM。"""
    selected_model = model or get_default_chat_data_model()
    selected_config = get_chat_data_model_config(selected_model)
    api_key = _resolve_chat_data_api_key(selected_config)
    base_url = _resolve_chat_data_base_url(selected_config)
    # fail-fast：连接信息缺失给出可行动的中文报错，而非透传 OpenAI 的 "Missing credentials"
    if not api_key or not base_url:
        missing = "API Key 密文" if not api_key else "Base URL"
        raise ValueError(f"模型 {selected_model} 未配置{missing}：请在「模型管理」面板编辑该模型补全连接信息")
    return get_llm(
        model=str(selected_config.get("request_model") or selected_model),
        streaming=streaming,
        stream_usage=stream_usage,
        temperature=selected_config["temperature"] if temperature is None else temperature,
        max_tokens=max_tokens or get_chat_data_model_max_output_tokens(selected_model),
        timeout=timeout,
        api_key=api_key,
        base_url=base_url,
        extra_body=_get_chat_data_extra_body(
            selected_config["thinking"],
            enable_thinking=enable_thinking,
            thinking_effort=thinking_effort,
        ),
    )
