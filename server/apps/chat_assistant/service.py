import os
import time
import requests


class ChatAssistantService:
    def __init__(self):
        base = os.getenv("DIFY_BASE_URL", "https://localhost/v1")
        chat_base = os.getenv("DIFY_BASE_URL") or base
        configured = os.getenv("DIFY_BASE_URL")
        if configured:
            norm = configured.rstrip("/")
            if norm.endswith("/chat-messages"):
                self.api_url = norm
            else:
                self.api_url = f"{norm}/chat-messages"
        else:
            self.api_url = f"{chat_base.rstrip('/')}/chat-messages"
        self.api_key = os.getenv("DIFY_CHAT_ASSIST_API_KEY") or os.getenv("DIFY_API_KEY", "")
        self.default_user = os.getenv("DIFY_CHAT_DEFAULT_USER", "chat-assistant")
        self.timeout = int(os.getenv("CHAT_TIMEOUT_SEC", "600") or "600")
        self.max_retries = 3

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def ask(self, question: str, inputs: dict | None, conversation_id: str | None, user: str | None) -> dict:
        if not question or not isinstance(question, str):
            raise ValueError("question 不能为空")
        payload: dict[str, object] = {
            "query": question,
            "inputs": inputs or {},
            "response_mode": "blocking",
            "user": (user or "").strip() or self.default_user,
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id
        last_error = None
        for i in range(self.max_retries):
            try:
                resp = requests.post(self.api_url, headers=self._headers(), json=payload, timeout=self.timeout, verify=False)
                resp.raise_for_status()
                data = resp.json()
                answer = ""
                if isinstance(data, dict):
                    answer = str(data.get("answer") or data.get("output") or "")
                return {
                    "answer": answer,
                    "conversation_id": str(data.get("conversation_id") or conversation_id or ""),
                    "id": data.get("id"),
                    "raw": data,
                }
            except Exception as exc:
                last_error = exc
                time.sleep(2**i)
        raise last_error

    def stream(self, question: str, inputs: dict | None, conversation_id: str | None, user: str | None):
        if not question or not isinstance(question, str):
            yield 'data: {"error": "question 不能为空"}\n\n'
            return
        payload: dict[str, object] = {
            "query": question,
            "inputs": inputs or {},
            "response_mode": "streaming",
            "user": (user or "").strip() or self.default_user,
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id
        try:
            with requests.post(self.api_url, headers=self._headers(), json=payload, stream=True, timeout=self.timeout, verify=False) as resp:
                resp.raise_for_status()
                for raw in resp.iter_lines(decode_unicode=True):
                    if not raw:
                        continue
                    line = raw if isinstance(raw, str) else raw.decode("utf-8", errors="ignore")
                    if line.startswith("data:"):
                        yield line + "\n\n"
                    else:
                        # 统一包裹为 data 事件
                        yield f"data: {line}\n\n"
        except Exception as exc:
            yield f'data: {{"error": "聊天流式调用失败: {str(exc)}"}}\n\n'
