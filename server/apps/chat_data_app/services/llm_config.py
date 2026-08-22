"""智能问数 LLM 配置。"""

import os
from typing import Any

from langchain_core.language_models import BaseChatModel

from utils.crypto import decrypt_password
from utils.llm import get_llm


DEFAULT_CHAT_DATA_OUTPUT_MAX_TOKENS = 20000
DEFAULT_CONTEXT_COMPRESSION_SUMMARY_OUTPUT_MAX_TOKENS = 2000
DEFAULT_CHAT_DATA_MODEL = "gpt-4o-mini"
DEFAULT_MODEL_TEMPERATURE = 0.2
DEFAULT_CHAT_DATA_CONTEXT_WINDOW = 128000

# 第 4 段支持的能力值：vlm-on 接受图片输入，vlm-off 显式关闭。
_KNOWN_CAPABILITIES = {"vlm-on": True, "vlm-off": False}


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
    """读取上下文压缩摘要调用的最大输出 token 数。"""
    return _read_positive_int_env(
        "CHAT_DATA_CONTEXT_COMPRESSION_SUMMARY_OUTPUT_MAX_TOKENS",
        DEFAULT_CONTEXT_COMPRESSION_SUMMARY_OUTPUT_MAX_TOKENS,
    )


def _parse_temperature(raw: str, *, model: str) -> float:
    """解析模型温度，格式错误直接抛出。"""
    text = str(raw or "").strip()
    if not text:
        raise ValueError(f"CHAT_DATA_MODEL 中模型 {model} 的温度不能为空")
    try:
        value = float(text)
    except ValueError as exc:
        raise ValueError(f"CHAT_DATA_MODEL 中模型 {model} 的温度必须为 0 到 2 的数字，当前值: {raw!r}") from exc
    if value < 0 or value > 2:
        raise ValueError(f"CHAT_DATA_MODEL 中模型 {model} 的温度必须为 0 到 2 的数字，当前值: {value}")
    return value


def _parse_context_window(raw: str, *, model: str) -> int:
    """解析模型上下文窗口，格式错误直接抛出。"""
    text = str(raw or "").strip()
    if not text:
        raise ValueError(f"CHAT_DATA_MODEL 中模型 {model} 的上下文窗口不能为空")
    try:
        value = int(text)
    except ValueError as exc:
        raise ValueError(f"CHAT_DATA_MODEL 中模型 {model} 的上下文窗口必须为正整数，当前值: {raw!r}") from exc
    if value < 1:
        raise ValueError(f"CHAT_DATA_MODEL 中模型 {model} 的上下文窗口必须为正整数，当前值: {value}")
    return value


def _parse_capabilities(raw: str, *, model: str) -> bool:
    """解析模型能力位（第 4 段），未知值直接抛出。

    当前仅支持 vlm-on / vlm-off。新能力参数只允许追加到段尾；
    将来增加第 5 段时，段数校验同步放宽为 5 段。
    """
    text = str(raw or "").strip()
    if text in _KNOWN_CAPABILITIES:
        return _KNOWN_CAPABILITIES[text]
    raise ValueError(
        f"CHAT_DATA_MODEL 中模型 {model} 的能力位不支持: {raw!r}（支持: vlm-on, vlm-off）"
    )


def _parse_model_config_item(item: str) -> dict[str, Any]:
    """解析单个模型配置项：model;temperature;context_window;capabilities。"""
    parts = [part.strip().strip("\"'") for part in str(item or "").split(";")]
    if len(parts) != 4 or not parts[0]:
        raise ValueError(
            "CHAT_DATA_MODEL 格式错误，应为 model;temperature;context_window;capabilities，"
            "capabilities 为 vlm-on/vlm-off，多个模型用逗号分隔"
        )
    model = parts[0]
    return {
        "id": model,
        "temperature": _parse_temperature(parts[1], model=model),
        "context_window": _parse_context_window(parts[2], model=model),
        "vlm": _parse_capabilities(parts[3], model=model),
    }


def get_chat_data_model_configs() -> list[dict[str, Any]]:
    """读取智能问数模型配置列表。"""
    raw = str(os.getenv("CHAT_DATA_MODEL") or "").strip()
    if not raw:
        return [{
            "id": DEFAULT_CHAT_DATA_MODEL,
            "temperature": DEFAULT_MODEL_TEMPERATURE,
            "context_window": DEFAULT_CHAT_DATA_CONTEXT_WINDOW,
            "vlm": False,
        }]
    configs = []
    seen = set()
    for item in raw.split(","):
        config = _parse_model_config_item(item)
        if config["id"] in seen:
            continue
        seen.add(config["id"])
        configs.append(config)
    return configs


def get_chat_data_model_names() -> list[str]:
    """读取智能问数模型列表。"""
    return [config["id"] for config in get_chat_data_model_configs()]


def get_default_chat_data_model() -> str:
    """读取智能问数默认模型。"""
    return get_chat_data_model_names()[0]


def get_chat_data_model_config(model: str | None = None) -> dict[str, Any]:
    """读取指定模型配置，未命中时返回默认上下文和温度。"""
    selected = str(model or "").strip()
    configs = get_chat_data_model_configs()
    if not selected:
        return configs[0]
    for config in configs:
        if config["id"] == selected:
            return config
    return {
        "id": selected,
        "temperature": DEFAULT_MODEL_TEMPERATURE,
        "context_window": DEFAULT_CHAT_DATA_CONTEXT_WINDOW,
        "vlm": False,
    }


def get_chat_data_temperature(model: str | None = None) -> float:
    """读取模型温度。"""
    return float(get_chat_data_model_config(model).get("temperature", DEFAULT_MODEL_TEMPERATURE))


def _get_chat_data_extra_body(enable_thinking: bool | None = None) -> dict[str, Any]:
    thinking_enabled = bool(enable_thinking)
    return {
        "enable_thinking": thinking_enabled,
        "chat_template_kwargs": {
            "enable_thinking": thinking_enabled,
        },
        # DeepSeek 不认 enable_thinking，需通过 reasoning_effort 控制思考强度；
        # qwen/glm 会忽略该键，无副作用。三键合并由各自模型认领自己支持的键。
        "reasoning_effort": "high" if thinking_enabled else "none",
    }


def _get_chat_data_api_key() -> str | None:
    """读取智能问数 API Key，CHAT_DATA_API_KEY 存储为 SM4 密文。"""
    encrypted_api_key = str(os.getenv("CHAT_DATA_API_KEY") or "").strip()
    if not encrypted_api_key:
        return None
    return decrypt_password(encrypted_api_key)


def get_chat_data_model_options() -> dict[str, Any]:
    """读取智能问数前端可选模型列表。"""
    raw_models = get_chat_data_model_names()
    default_model = raw_models[0]
    models = []
    for model in raw_models:
        models.append({
            "id": model,
            "label": model,
            "value": model,
            "is_default": model == default_model,
        })
    return {"models": models, "default_model": default_model}


def get_chat_data_llm(
    model: str | None = None,
    streaming: bool = False,
    stream_usage: bool | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: int | None = None,
    enable_thinking: bool | None = None,
) -> BaseChatModel:
    """读取 CHAT_DATA_* 环境变量创建智能问数专用 LLM。"""
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
        extra_body=_get_chat_data_extra_body(enable_thinking=enable_thinking),
    )
