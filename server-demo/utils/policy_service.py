import logging
from typing import Any, Dict, Tuple

import requests


def fetch_policy_payload(
    policy_id: str,
    base_url: str,
    *,
    timeout: int,
    verify_tls: bool,
    logger: logging.Logger = None,
) -> Tuple[Dict[str, Any], int]:
    """Fetch policy text from the upstream service in a framework-agnostic way."""
    log = logger or logging.getLogger(__name__)
    normalized_id = (policy_id or "").strip()
    if not normalized_id:
        return {"error": "policy_id is required"}, 400

    if not base_url:
        return {"error": "POLICY_SERVICE_BASE_URL is not configured"}, 500

    upstream_base = base_url.rstrip("/")
    target_url = f"{upstream_base}/api/policies/{normalized_id}"
    try:
        upstream_resp = requests.get(
            target_url,
            params={"include": "text"},
            timeout=timeout,
            verify=verify_tls,
        )
        upstream_resp.raise_for_status()
    except requests.RequestException as exc:
        status_code = getattr(exc.response, "status_code", None)
        snippet = ""
        if getattr(exc, "response", None) is not None:
            try:
                snippet = exc.response.text[:500]
            except Exception:
                snippet = "<unreadable response payload>"

        log.error(
            "Policy service request failed url=%s status=%s verify_tls=%s error=%s response_snippet=%s",
            target_url,
            status_code,
            verify_tls,
            exc,
            snippet,
        )
        return {"error": "Failed to fetch policy text", "details": str(exc)}, 502

    try:
        payload = upstream_resp.json()
    except ValueError:
        return {"error": "Invalid JSON from policy service"}, 502

    if not isinstance(payload, dict):
        return {"error": "Unexpected response structure"}, 502

    return payload, 200
