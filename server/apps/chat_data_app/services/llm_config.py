"""智能问数 LLM 配置：模型注册表加载与思考能力分派。

模型清单来自 ``CHAT_DATA_MODELS_FILE`` 指向的 JSON 注册表（每环境一份），
启动即用、每次调用读取（文件几 KB，免重启换列表）。所有字段 fail-fast：
配错宁可起不来，不静默误配。思考能力三层分离——

- 语义层 ``ThinkingCapability``：模型固有能力（mode/dialect/efforts/default）；
- 决策层 ``resolve_thinking``：模型能力 × 用户意图 → 本次请求怎么想（唯一裁决点）；
- 协议层 ``_get_chat_data_extra_body``：按方言把决策序列化为 extra_body。
"""

import json
import os
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models import BaseChatModel

from utils.crypto import decrypt_password
from utils.llm import get_llm


DEFAULT_CHAT_DATA_OUTPUT_MAX_TOKENS = 20000
DEFAULT_CONTEXT_COMPRESSION_SUMMARY_OUTPUT_MAX_TOKENS = 2000

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

EFFORT_LEVELS = ("low", "medium", "high")


@dataclass(frozen=True)
class ThinkingCapability:
    """模型的思考能力声明（来自 models.json 的 thinking 字段）。"""

    mode: str
    dialect: str | None = None
    efforts: tuple[str, ...] = ()
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


def get_context_compression_summary_output_max_tokens() -> int:
    """读取上下文压缩摘要调用的最大输出 token；独立于主模型输出预留。"""
    return _read_positive_int_env(
        "CHAT_DATA_CONTEXT_COMPRESSION_SUMMARY_OUTPUT_MAX_TOKENS",
        DEFAULT_CONTEXT_COMPRESSION_SUMMARY_OUTPUT_MAX_TOKENS,
    )


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


def _parse_thinking(raw: Any, *, path: str, model_id: str) -> ThinkingCapability:
    """解析单个模型的 thinking 字段，任何不合法直接抛 ValueError。"""
    prefix = f"{path} 中模型 {model_id} 的 thinking 配置无效"
    if not isinstance(raw, dict):
        raise ValueError(f"{prefix}: 应为对象")
    unknown_keys = set(raw) - {"mode", "dialect", "efforts", "default_on", "default_effort"}
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
        raise ValueError(
            f"{prefix}: dialect 必须是 {_THINKING_DIALECTS} 之一，当前值: {raw.get('dialect')!r}"
        )

    efforts_raw = raw.get("efforts")
    if efforts_raw is None:
        efforts: tuple[str, ...] = ()
    else:
        if not isinstance(efforts_raw, list) or not all(isinstance(item, str) for item in efforts_raw):
            raise ValueError(f"{prefix}: efforts 应为字符串数组")
        invalid = [item for item in efforts_raw if item not in EFFORT_LEVELS]
        if invalid:
            raise ValueError(f"{prefix}: efforts 含非法等级 {invalid}（支持: {list(EFFORT_LEVELS)}）")
        efforts = tuple(efforts_raw)
        if dialect == THINKING_DIALECT_CHAT_TEMPLATE:
            raise ValueError(
                f"{prefix}: chat_template 方言为纯布尔协议不支持等级，请移除 efforts 或改用其它方言"
            )

    default_effort_raw = raw.get("default_effort")
    if (default_effort_raw is None) != (not efforts):
        raise ValueError(
            f"{prefix}: default_effort 与 efforts 必须成对出现（当前 efforts={efforts_raw!r}, "
            f"default_effort={default_effort_raw!r}）"
        )
    if default_effort_raw is not None and default_effort_raw not in efforts:
        raise ValueError(
            f"{prefix}: default_effort={default_effort_raw!r} 不在 efforts {list(efforts)} 中"
        )

    default_on_raw = raw.get("default_on")
    if mode == THINKING_MODE_FORCED:
        if default_on_raw is not None:
            raise ValueError(f"{prefix}: mode=forced 时不得配置 default_on")
        return ThinkingCapability(
            mode=mode, dialect=dialect, efforts=efforts, default_effort=default_effort_raw
        )
    # optional：default_on 必填（无隐式默认）
    if not isinstance(default_on_raw, bool):
        raise ValueError(f"{prefix}: mode=optional 时 default_on 必填且为布尔")
    return ThinkingCapability(
        mode=mode,
        dialect=dialect,
        efforts=efforts,
        default_on=default_on_raw,
        default_effort=default_effort_raw,
    )


def _parse_model_entry(entry: Any, *, path: str) -> dict[str, Any]:
    """解析注册表中的单个模型条目。"""
    if not isinstance(entry, dict):
        raise ValueError(f"{path} 中存在非对象条目: {type(entry).__name__}")
    model_id = str(entry.get("id") or "").strip()
    if not model_id:
        raise ValueError(f"{path} 中存在缺少 id 的模型条目")

    def _require(key: str, check, describe: str):
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
    thinking = _parse_thinking(entry.get("thinking"), path=path, model_id=model_id)
    return {
        "id": model_id,
        "temperature": float(temperature),
        "context_window": int(context_window),
        "vlm": vlm,
        "thinking": thinking,
    }


def load_chat_data_model_configs() -> list[dict[str, Any]]:
    """读取并校验模型注册表；每次调用读文件，错误信息带路径与模型 id。"""
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


def get_chat_data_model_configs() -> list[dict[str, Any]]:
    """读取智能问数模型配置列表（首项为默认模型）。"""
    return load_chat_data_model_configs()


def get_chat_data_model_names() -> list[str]:
    """读取智能问数模型列表。"""
    return [config["id"] for config in get_chat_data_model_configs()]


def get_default_chat_data_model() -> str:
    """读取默认模型（注册表首项）。"""
    return get_chat_data_model_names()[0]


def get_chat_data_model_config(model: str | None = None) -> dict[str, Any]:
    """读取指定模型配置；未命中时抛错而非静默回退（fail-fast）。"""
    selected = str(model or "").strip()
    configs = get_chat_data_model_configs()
    if not selected:
        return configs[0]
    for config in configs:
        if config["id"] == selected:
            return config
    raise ValueError(
        f"请求的模型不在 CHAT_DATA_MODELS_FILE 注册表中: {selected!r}（可用: {get_chat_data_model_names()}）"
    )


def get_chat_data_temperature(model: str | None = None) -> float:
    """读取模型温度。"""
    return float(get_chat_data_model_config(model).get("temperature"))


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
    """档位裁决：用户显式选择优先，但必须在注册表声明的 efforts 内。"""
    if user_effort and user_effort in capability.efforts:
        return user_effort
    return capability.default_effort


def _get_chat_data_extra_body(
    capability: ThinkingCapability,
    enable_thinking: bool | None = None,
    thinking_effort: str | None = None,
) -> dict[str, Any]:
    """按方言把思考决策序列化为 extra_body；omit 时不含任何思考键。"""
    decision, level = resolve_thinking(capability, enable_thinking, thinking_effort)
    extra_body: dict[str, Any] = {}
    if decision == "omit":
        return extra_body
    if capability.dialect == THINKING_DIALECT_CHAT_TEMPLATE:
        flag = decision == "on"
        extra_body["enable_thinking"] = flag
        extra_body["chat_template_kwargs"] = {"enable_thinking": flag}
    elif decision == "on" and level is None:
        # forced 未配置等级：不发任何键，端点按自身默认强度思考
        return {}
    elif capability.dialect == THINKING_DIALECT_REASONING_EFFORT:
        extra_body["reasoning_effort"] = level if decision == "on" else "none"
    elif capability.dialect == THINKING_DIALECT_OPENROUTER:
        extra_body["reasoning"] = (
            {"effort": level} if decision == "on" else {"enabled": False}
        )
    return extra_body


def get_chat_data_model_options() -> dict[str, Any]:
    """读取智能问数前端可选模型列表（含思考能力声明）。"""
    raw_models = get_chat_data_model_configs()
    default_model = raw_models[0]["id"]
    models = []
    for config in raw_models:
        cap: ThinkingCapability = config["thinking"]
        models.append({
            "id": config["id"],
            "label": config["id"],
            "value": config["id"],
            "is_default": config["id"] == default_model,
            "thinking": {
                "mode": cap.mode,
                "efforts": list(cap.efforts),
                "default_on": cap.default_on,
                "default_effort": cap.default_effort,
            },
        })
    return {"models": models, "default_model": default_model}


def _get_chat_data_api_key() -> str | None:
    """读取智能问数 API Key，CHAT_DATA_API_KEY 存储为 SM4 密文。"""
    encrypted_api_key = str(os.getenv("CHAT_DATA_API_KEY") or "").strip()
    if not encrypted_api_key:
        return None
    return decrypt_password(encrypted_api_key)


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
    return get_llm(
        model=selected_model,
        streaming=streaming,
        stream_usage=stream_usage,
        temperature=selected_config["temperature"] if temperature is None else temperature,
        max_tokens=max_tokens or get_chat_data_output_max_tokens(),
        timeout=timeout,
        api_key=_get_chat_data_api_key(),
        base_url=os.getenv("CHAT_DATA_BASE_URL"),
        extra_body=_get_chat_data_extra_body(
            selected_config["thinking"],
            enable_thinking=enable_thinking,
            thinking_effort=thinking_effort,
        ),
    )
