"""模型注册表管理服务：页面化模型清单的校验、保存与连接测试。

DB ``chat_data_model_registry`` 表为唯一真值；保存前复用 llm_config 的
fail-fast 校验（含 thinking 协议），校验不过不落库。连接信息（base_url/
api_key）只以模型行为准，无环境变量回退；api_key 表单只接受 SM4 密文
（base64(iv):base64(cipher)，可用 scripts/crypto.py encrypt 生成），
明文直接拒绝，落库原样存密文。任何写操作成功后即时刷新进程内注册表缓存。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import unquote

import httpx
from sqlalchemy.exc import IntegrityError

from utils.crypto import decrypt_password
from utils.llm import get_llm
from utils.logger import get_logger

from ..daos.model_registry_dao import ModelRegistryDao
from .llm_config import (
    OFF_EFFORT_SENTINELS,
    THINKING_MODE_NONE,
    _get_chat_data_extra_body,
    _parse_thinking,
    _thinking_cap_to_json,
    refresh_registry_cache,
    reset_registry_cache,
)

logger = get_logger(__name__)

# params_json 允许出现的参数键（新增参数：先加校验再进此集合，扩参不改表）
_ALLOWED_PARAM_KEYS = frozenset(
    {"temperature", "context_window", "max_output_tokens", "vlm", "thinking"}
)

_THINKING_MODES = ("none", "optional", "forced")
# SM4 密文形态：base64(iv):base64(cipher)，两段 base64 以冒号连接
_API_KEY_CIPHER_PATTERN = re.compile(r"^[A-Za-z0-9+/]{8,}={0,2}:[A-Za-z0-9+/]{8,}={0,2}$")


class ModelRegistryError(ValueError):
    """模型注册表校验/操作错误（管理端映射 400）。"""


class ModelRegistryConflict(ModelRegistryError):
    """业务唯一性冲突（供应商+请求名重复），管理端映射 40067。"""


class ModelRegistryNotFoundError(LookupError):
    """模型不存在（未删除范围）。"""


def _validate_params(params: Any, *, model_id: str) -> dict[str, Any]:
    """校验模型参数对象并归一化（写库前的唯一校验口径，与加载侧同一套规则）。"""
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ModelRegistryError("params 应为 JSON 对象")
    unknown = sorted(set(params) - _ALLOWED_PARAM_KEYS)
    if unknown:
        raise ModelRegistryError(f"params 含未知参数键: {', '.join(unknown)}（允许: {sorted(_ALLOWED_PARAM_KEYS)}）")

    temperature = params.get("temperature", 0.2)
    if not isinstance(temperature, (int, float)) or isinstance(temperature, bool) or not 0 <= float(temperature) <= 2:
        raise ModelRegistryError(f"temperature 无效: {temperature!r}（应为 0~2 数字）")

    context_window = params.get("context_window", 48000)
    if not isinstance(context_window, int) or isinstance(context_window, bool) or context_window <= 0:
        raise ModelRegistryError(f"context_window 无效: {context_window!r}（应为正整数）")

    max_output = params.get("max_output_tokens")
    if max_output is None:
        max_output = int(context_window) // 4  # 必填项缺省时取上下文窗口的 1/4
    if not isinstance(max_output, int) or isinstance(max_output, bool) or max_output <= 0:
        raise ModelRegistryError(f"max_output_tokens 无效: {max_output!r}（应为正整数）")
    if max_output >= context_window:
        raise ModelRegistryError(f"max_output_tokens={max_output} 必须小于 context_window={context_window}")

    normalized: dict[str, Any] = {
        "temperature": float(temperature),
        "context_window": int(context_window),
        "max_output_tokens": max_output,
        "vlm": bool(params.get("vlm", True)),
    }
    try:
        normalized["thinking"] = _thinking_to_dict(params.get("thinking"), model_id=model_id)
    except ValueError as err:
        raise ModelRegistryError(str(err)) from err
    return normalized


def _thinking_to_dict(raw: Any, *, model_id: str) -> dict[str, Any]:
    """thinking 声明归一化为可 JSON 序列化的字典（经 _parse_thinking 统一校验）。

    thinking 键缺省/空对象视为不支持思考（mode=none），与文件加载侧口径一致；
    序列化形状走 _thinking_cap_to_json 单源（回读必须能再过校验）。
    """
    if not isinstance(raw, dict) or not raw:
        raw = {"mode": THINKING_MODE_NONE}
    cap = _parse_thinking(raw, path="模型配置", model_id=model_id)
    return _thinking_cap_to_json(cap)


def _to_api_key_cipher(api_key: str) -> str:
    """表单 api_key 只接受 SM4 密文（base64(iv):base64(cipher)），原样落库；明文/空值拒绝（必填）。"""
    if not api_key:
        raise ModelRegistryError("api_key 必填：请粘贴加密后的密文（形如 xxx:yyy）")
    if not _API_KEY_CIPHER_PATTERN.match(api_key):
        raise ModelRegistryError("api_key 必须为加密后的密文（形如 xxx:yyy）")
    return api_key


def _validate_model_key(model_key: str) -> str:
    """注册键校验：非空、无空白（唯一性由 DB 唯一键兜底）。"""
    if not model_key:
        raise ModelRegistryError("model_key（注册键）不能为空")
    if any(ch.isspace() for ch in model_key):
        raise ModelRegistryError("model_key（注册键）不能包含空白字符")
    return model_key


def _normalize_incoming_model_key(raw: str) -> str:
    """还原被网关/代理重复编码的注册键（如 ``%23232`` -> ``#232``）。

    注册键会出现在管理端 URL 路径里，``#``、``/`` 等字符必须百分号编码；部分
    网关会先解码再对整条路径重新编码，后端收到的就是 ``%23`` 字面量。键本身
    由服务端生成、不含 ``%``，因此这里只做"有 % 就解开、最多两轮"的幂等还原。
    """
    key = str(raw or "").strip()
    for _ in range(2):
        if "%" not in key:
            break
        decoded = unquote(key)
        if decoded == key:
            break
        key = decoded
    return key


_URL_UNSAFE_KEY_PATTERN = re.compile(r"[^A-Za-z0-9._:-]+")


def _to_url_safe_model_key(model_id: str) -> str:
    """把请求名转为 URL 安全的注册键：``/``、``#``、``%`` 等替换为 ``-``。

    注册键会出现在管理端 URL 路径，直接复用请求名会让 ``#``（片段符）、``/``
    （被网关拆成多段）等字符在代理链路上被误处理。
    """
    cleaned = _URL_UNSAFE_KEY_PATTERN.sub("-", model_id).strip("-")
    return cleaned or "model"


async def _generate_model_key(dao: ModelRegistryDao, model_id: str) -> str:
    """注册键服务端自动生成：默认=请求名 URL 安全化；被任何现存行（含软删墓碑）占用则追加 -n。"""
    base = _to_url_safe_model_key(model_id)
    candidate = base
    suffix = 2
    while await dao.get_by_model_key(model_key=candidate, include_deleted=True) is not None:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


async def create_model(payload: dict[str, Any], *, user_id: str, username: str) -> dict[str, Any]:
    """新建模型：注册键自动生成（用户不填）；成功后刷新注册表缓存。

    业务唯一性=供应商+请求名（活跃行内）：同组合重复新建直接报错；
    软删行请求名已改写墓碑名，不占用组合，重加同名会生成全新行。
    """
    fields = _validate_payload_fields(payload)
    dao = ModelRegistryDao()
    if await dao.has_active_provider_model(provider=fields["provider"], model_id=fields["model_id"]):
        raise ModelRegistryConflict(
            f"该供应商下已存在同名请求名的模型: {fields['model_id']}（如需修改请直接编辑该模型）"
        )
    model_key = await _generate_model_key(dao, fields["model_id"])
    try:
        row_id = await dao.upsert(model_key=model_key, fields=fields, operator_id=user_id, operator_name=username)
    except IntegrityError:
        # 并发窗口：同请求名两请求同时通过查重/查键，唯一键（model_key 或业务组合）兜底撞车
        raise ModelRegistryError(
            f"模型保存冲突（注册键或供应商+请求名已被并发请求占用）: {fields['model_id']}，请重试"
        ) from None
    await _refresh_cache_best_effort()
    rows = await dao.list_rows()
    saved = next((row for row in rows if row["id"] == row_id), None)
    return _mask_row(saved) if saved else {"id": row_id, "model_key": model_key, "model_id": fields["model_id"]}


async def update_model(model_key: str, payload: dict[str, Any], *, user_id: str, username: str) -> dict[str, Any]:
    """更新模型（按注册键定位身份）；改供应商/请求名时校验组合不撞活跃行；成功后刷新缓存。"""
    model_key = _normalize_incoming_model_key(model_key)
    _validate_model_key(model_key)
    fields = _validate_payload_fields(payload)
    dao = ModelRegistryDao()
    if await dao.get_by_model_key(model_key=model_key, include_deleted=False) is None:
        raise ModelRegistryNotFoundError(f"模型不存在: {model_key}")
    if await dao.has_active_provider_model(
        provider=fields["provider"], model_id=fields["model_id"], exclude_key=model_key
    ):
        raise ModelRegistryConflict(
            f"该供应商下已存在同名请求名的模型: {fields['model_id']}（同组合只能有一个模型）"
        )
    row_id = await dao.upsert(model_key=model_key, fields=fields, operator_id=user_id, operator_name=username)
    await _refresh_cache_best_effort()
    rows = await dao.list_rows()
    saved = next((row for row in rows if row["id"] == row_id), None)
    return _mask_row(saved) if saved else {"id": row_id, "model_key": model_key, "model_id": fields["model_id"]}


def _validate_payload_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """校验模型基础字段，返回注册表行字段（模型参数打包为 params_json）。"""
    model_id = str(payload.get("model_id") or "").strip()
    if not model_id:
        raise ModelRegistryError("model_id 不能为空")
    if any(ch.isspace() for ch in model_id):
        raise ModelRegistryError("model_id 不能包含空白字符")

    base_url = str(payload.get("base_url") or "").strip()
    if base_url and not base_url.startswith(("http://", "https://")):
        raise ModelRegistryError("base_url 必须以 http:// 或 https:// 开头")

    api_key = str(payload.get("api_key") or "").strip()
    # enabled 未传（None）时保持原值：DAO 对 enabled_flag=None 跳过 setattr（防 PUT 重置停用模型）
    enabled = payload.get("enabled")
    return {
        "model_id": model_id,
        "provider": str(payload.get("provider") or "").strip(),
        "display_name": str(payload.get("display_name") or "").strip() or model_id,
        "base_url": base_url,
        "api_key_cipher": _to_api_key_cipher(api_key),
        "params_json": json.dumps(_validate_params(payload.get("params"), model_id=model_id), ensure_ascii=False),
        "enabled_flag": None if enabled is None else (1 if enabled else 0),
        "remark_text": str(payload.get("remark") or "").strip(),
    }


def _mask_row(row: dict[str, Any]) -> dict[str, Any]:
    """管理端视图：密文回填（编辑框直接带回，配合必填校验）；params_json 解包为 params 对象。"""
    masked = {key: value for key, value in row.items() if key != "params_json"}
    masked["has_api_key"] = bool(row.get("api_key_cipher"))
    try:
        masked["params"] = json.loads(str(row.get("params_json") or "") or "{}")
    except json.JSONDecodeError:
        masked["params"] = {}
    masked["enabled"] = bool(row.get("enabled_flag"))
    masked["is_default"] = bool(row.get("is_default"))
    return masked


async def list_models() -> list[dict[str, Any]]:
    """管理端模型列表（脱敏，含未启用行）。"""
    return [_mask_row(row) for row in await ModelRegistryDao().list_rows()]


async def set_model_enabled(model_key: str, *, enabled: bool, user_id: str, username: str) -> None:
    """启停模型；停用默认模型时仅提示不阻断（加载层有首行兜底）。"""
    model_key = _normalize_incoming_model_key(model_key)
    if not await ModelRegistryDao().set_enabled(
        model_key=model_key, enabled=enabled, operator_id=user_id, operator_name=username
    ):
        raise ModelRegistryNotFoundError(f"模型不存在: {model_key}")
    await _refresh_cache_best_effort()


async def set_model_default(model_key: str, *, user_id: str, username: str) -> None:
    """设唯一默认模型（同时启用该行）；成功后刷新注册表缓存。"""
    model_key = _normalize_incoming_model_key(model_key)
    if not await ModelRegistryDao().set_default(
        model_key=model_key, operator_id=user_id, operator_name=username
    ):
        raise ModelRegistryNotFoundError(f"模型不存在: {model_key}")
    await _refresh_cache_best_effort()


async def delete_model(model_key: str, *, user_id: str, username: str) -> None:
    """软删模型行；成功后刷新注册表缓存。"""
    model_key = _normalize_incoming_model_key(model_key)
    if not await ModelRegistryDao().mark_deleted(
        model_key=model_key, operator_id=user_id, operator_name=username
    ):
        raise ModelRegistryNotFoundError(f"模型不存在: {model_key}")
    await _refresh_cache_best_effort()


async def _refresh_cache_best_effort() -> None:
    """写操作后刷新进程内注册表缓存；失败时清缓存（服务路径冷缓存会明确报错，不回落文件）。"""
    try:
        await refresh_registry_cache()
    except Exception as err:  # noqa: BLE001 刷新失败不回滚已落库的写操作
        logger.warning("模型注册表缓存刷新失败（已清空冷态，服务路径会明确报错不回落文件）: %s", err)
        reset_registry_cache()


async def discover_models(payload: dict[str, Any]) -> list[str]:
    """探测上游 OpenAI 兼容接口的可用模型清单（GET {base_url}/models）。

    连接信息只以表单为准：base_url 必填，api_key 必须为密文（解密后携带）。
    返回去重排序后的模型 id 列表；上游异常转化为 ModelRegistryError（中文提示）。
    """
    base_url = str(payload.get("base_url") or "").strip()
    if not base_url.startswith(("http://", "https://")):
        raise ModelRegistryError("base_url 必须以 http:// 或 https:// 开头")
    plain_key = decrypt_password(_to_api_key_cipher(str(payload.get("api_key") or "").strip()))

    url = f"{base_url.rstrip('/')}/models"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {plain_key}"})
    except Exception as err:  # noqa: BLE001 探测把网络异常转化为结构化错误
        raise ModelRegistryError(f"探测请求失败: {err}") from err
    if response.status_code != 200:
        raise ModelRegistryError(f"上游返回 HTTP {response.status_code}: {response.text[:200]}")
    try:
        body = response.json()
    except ValueError as err:
        raise ModelRegistryError("上游响应不是合法 JSON") from err
    entries = body.get("data") if isinstance(body, dict) else None
    if not isinstance(entries, list):
        raise ModelRegistryError("上游响应缺少 data 模型列表（非 OpenAI 兼容接口？）")
    return sorted({str(item["id"]).strip() for item in entries if isinstance(item, dict) and item.get("id")})


async def test_model_connection(payload: dict[str, Any]) -> dict[str, Any]:
    """用表单当前值发一次最小请求验证连通性（不要求模型已保存）。

    连接信息只以表单为准（api_key/base_url 必填，校验不通过直接报错）。
    """
    fields = _validate_payload_fields(payload)
    model_id = str(payload.get("model_id") or "").strip()
    params = json.loads(fields["params_json"])
    base_url = fields["base_url"]
    if not base_url:
        raise ModelRegistryError("Base URL 未配置：请先在表单填写（连接信息只以模型行为准）")
    api_key = decrypt_password(fields["api_key_cipher"])

    try:
        raw_thinking = params.get("thinking")
        capability = _parse_thinking(
            raw_thinking if isinstance(raw_thinking, dict) and raw_thinking else {"mode": THINKING_MODE_NONE},
            path="连接测试",
            model_id=model_id,
        )
    except ValueError as err:
        raise ModelRegistryError(str(err)) from err

    started = time.monotonic()
    # 声明了档位时用最高档（末位）发 on 形态探测，提前发现 wire 值不可用（一次 RTT）；
    # 关哨兵档位（off/none）不参与探测（它们序列化为关形态，探测不到 on wire 值），
    # 全是哨兵档时保持 off 形态（只验证连通与鉴权）
    probe_effort = next(
        (effort.level for effort in reversed(capability.efforts) if effort.level not in OFF_EFFORT_SENTINELS),
        None,
    )
    if probe_effort is not None:
        extra_body = _get_chat_data_extra_body(
            capability, enable_thinking=True, thinking_effort=probe_effort
        )
    else:
        extra_body = _get_chat_data_extra_body(capability, enable_thinking=False, thinking_effort=None)
    try:
        llm = get_llm(
            model=model_id,
            streaming=False,
            temperature=0,
            max_tokens=8,
            timeout=20,
            api_key=api_key,
            base_url=base_url,
            extra_body=extra_body,
        )
        response = await llm.ainvoke("ping，只回复 ok")
        latency_ms = int((time.monotonic() - started) * 1000)
        sample = str(getattr(response, "content", "") or "")
        return {"success": True, "latency_ms": latency_ms, "sample": sample[:100]}
    except Exception as err:  # noqa: BLE001 连接测试把异常转化为结构化结果
        logger.warning("模型 %s 连接测试失败: %s", model_id, err)
        return {
            "success": False,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": str(err)[:500],
        }
