import json
import logging
import os
from typing import AsyncIterator
from uuid import uuid4

import httpx
from a2a.client import A2ACardResolver
from a2a.client.client_factory import ClientConfig, ClientFactory
from a2a.types import Message, Part, Role, TextPart, TransportProtocol

from .tool_register import register_tool, ToolStreamChunk


def _env_flag(key: str, default: bool = True) -> bool:
    value = os.getenv(key)
    if value is None:
        return default
    trimmed = value.strip()
    if not trimmed:
        return default
    return trimmed.lower() in {"1", "true", "yes", "y", "on"}


BASE_URL = os.getenv("POLICY_SERVICE_BASE_URL")
A2A_BASE_URL = os.getenv("POLICY_SERVICE_A2A_BASE_URL")
POLICY_SERVICE_VERIFY_TLS = _env_flag("POLICY_SERVICE_VERIFY_TLS", True)
POLICY_SERVICE_PROVIDER = os.getenv("POLICY_SERVICE_PROVIDER", "legacy").strip().lower()
logger = logging.getLogger(__name__)
print(
    f"[policy] POLICY_SERVICE_PROVIDER={POLICY_SERVICE_PROVIDER} "
    f"A2A_BASE_URL={A2A_BASE_URL} "
    f"BASE_URL={BASE_URL} "
    f"VERIFY_TLS={POLICY_SERVICE_VERIFY_TLS}"
)

gpts_id = "regulationassistant"


@register_tool(gpts_id, desc="我需要检索相关法律...", ing_desc="正在检索 {query} ...", end_desc="法律检索")
async def search_laws(
    query:(str, '用户问题', True)
) -> AsyncIterator[ToolStreamChunk]:
    """根据问题查询相关法律，支持流式返回。"""

    async def _legacy_stream() -> AsyncIterator[ToolStreamChunk]:
        if not BASE_URL:
            raise RuntimeError("POLICY_SERVICE_BASE_URL is not configured")

        target_url = f"{BASE_URL.rstrip('/')}/api/legal_search/ai_chat"

        logger.info(
            "Policy service stream start query=%s url=%s verify_tls=%s",
            query,
            target_url,
            POLICY_SERVICE_VERIFY_TLS,
        )

        async with httpx.AsyncClient(
            timeout=120.0,
            trust_env=False,
            verify=POLICY_SERVICE_VERIFY_TLS,
        ) as client:
            try:
                async with client.stream(
                    "POST",
                    target_url,
                    json={"query": query, "stream": "True"},
                ) as response:
                    response.raise_for_status()
                    async for raw_line in response.aiter_lines():
                        if raw_line is None:
                            continue

                        print(f"[legacy-raw] {raw_line}")
                        line = raw_line.strip()
                        if not line:
                            continue

                        if line.startswith("data:"):
                            line = line[5:].strip()

                        if not line or line.lower() in {"[done]", "[completed]"}:
                            continue

                        delta_text: str | None = line
                        payload: dict | None = None
                        desc: str | None = None

                        try:
                            json_payload = json.loads(line)
                        except json.JSONDecodeError:
                            json_payload = None
                        else:
                            if isinstance(json_payload, dict):
                                payload = json_payload
                                token = (
                                    json_payload.get("delta")
                                    or json_payload.get("text")
                                    or json_payload.get("content")
                                )

                                # desc_candidate = json_payload.get("desc") or json_payload.get("stage")
                                desc_candidate = json_payload.get("stage")
                                if isinstance(desc_candidate, str) and desc_candidate.strip():
                                    desc = desc_candidate.strip()

                        chunk = ToolStreamChunk(delta=delta_text, payload=payload, desc=desc)
                        print(
                            f"[legacy-chunk] event={chunk.event} "
                            f"delta={repr(chunk.delta)} payload={payload} desc={desc}"
                        )
                        # print(
                        #     "Policy service stream chunk",
                        #     "delta=",
                        #     chunk.delta,
                        #     "desc=",
                        #     chunk.desc,
                        #     "payload_keys=",
                        #     list(chunk.payload.keys())
                        #     if isinstance(chunk.payload, dict)
                        #     else type(chunk.payload).__name__,
                        # )
                        yield chunk
            except httpx.HTTPError:
                logger.exception(
                    "Policy service stream failed query=%s url=%s verify_tls=%s",
                    query,
                    target_url,
                    POLICY_SERVICE_VERIFY_TLS,
                )
                raise

    async def _alt_stream() -> AsyncIterator[ToolStreamChunk]:
        if not A2A_BASE_URL:
            raise RuntimeError("POLICY_SERVICE_A2A_BASE_URL is not configured")

        base_url = A2A_BASE_URL.rstrip("/")
        logger.info(
            "Policy service A2A stream start query=%s url=%s verify_tls=%s",
            query,
            base_url,
            POLICY_SERVICE_VERIFY_TLS,
        )

        async with httpx.AsyncClient(
            timeout=120.0,
            trust_env=False,
            verify=POLICY_SERVICE_VERIFY_TLS,
        ) as client:
            try:
                resolver = A2ACardResolver(httpx_client=client, base_url=base_url)
                card = await resolver.get_agent_card()
                if card is None:
                    raise RuntimeError(f"A2A agent card not available at {base_url}")

                config = ClientConfig(
                    supported_transports=[
                        TransportProtocol.http_json,
                        TransportProtocol.jsonrpc,
                    ],
                    httpx_client=client,
                    use_client_preference=True,
                )
                a2a_client = ClientFactory(config).create(card)
                message = Message(
                    role=Role.user,
                    parts=[Part(root=TextPart(text=query))],
                    message_id=uuid4().hex,
                )

                async for event in a2a_client.send_message(message):
                    payload = event[1] if isinstance(event, tuple) and len(event) > 1 else event
                    if getattr(payload, "kind", None) != "artifact-update":
                        continue

                    texts: list[str] = []
                    artifact = getattr(payload, "artifact", None)
                    parts = getattr(artifact, "parts", None) if artifact is not None else None
                    if parts:
                        for part in parts:
                            root = getattr(part, "root", None)
                            if isinstance(root, TextPart) and getattr(root, "text", None):
                                texts.append(root.text)
                            elif isinstance(part, TextPart) and getattr(part, "text", None):
                                texts.append(part.text)

                    for text in texts:
                        for raw_line in text.splitlines():
                            print(f"[a2a-raw] {raw_line}")
                            line = raw_line.strip()
                            if not line:
                                continue
                            if line.startswith("data:"):
                                line = line[5:].strip()
                            if not line or line.lower() in {"[done]", "[completed]"}:
                                continue

                            parsed_payload: dict | None = None
                            delta_text: str | None = None
                            desc: str | None = None

                            try:
                                json_payload = json.loads(line)
                            except json.JSONDecodeError:
                                json_payload = None
                            else:
                                if isinstance(json_payload, dict):
                                    parsed_payload = json_payload
                                    delta_value = (
                                        json_payload.get("delta")
                                        or json_payload.get("content")
                                        or json_payload.get("text")
                                    )
                                    if delta_value is not None:
                                        if isinstance(delta_value, str):
                                            delta_text = delta_value
                                        else:
                                            try:
                                                delta_text = json.dumps(delta_value, ensure_ascii=False)
                                            except TypeError:
                                                delta_text = str(delta_value)
                                    if delta_text is None:
                                        delta_text = line
                                    desc_candidate = json_payload.get("stage")
                                    if isinstance(desc_candidate, str) and desc_candidate.strip():
                                        desc = desc_candidate.strip()

                            if delta_text is None and parsed_payload is None:
                                delta_text = line

                            if delta_text is None and desc is None:
                                continue

                            chunk = ToolStreamChunk(
                                delta=delta_text,
                                payload=parsed_payload,
                                desc=desc,
                            )
                            print(
                                f"[a2a-chunk] event={chunk.event} "
                                f"delta={repr(chunk.delta)} payload={parsed_payload} desc={desc}"
                            )
                            yield chunk
            except httpx.HTTPError:
                logger.exception(
                    "Policy service A2A stream failed query=%s url=%s verify_tls=%s",
                    query,
                    base_url,
                    POLICY_SERVICE_VERIFY_TLS,
                )
                raise

    if POLICY_SERVICE_PROVIDER in {"alt", "new"}:
        return _alt_stream()
    return _legacy_stream()
