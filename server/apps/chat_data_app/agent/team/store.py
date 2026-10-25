"""Small durable JSON store for one conversation's Agent Team.

The MVP is deliberately single-process: every Team belongs to one conversation,
the file is written atomically, and the service serializes mutations per file.
Cross-process coordination and a database-backed mailbox are future work.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

from utils.storage_paths import CHAT_DATA_HOME

from .models import TeamState


class TeamStore:
    """Atomic JSON persistence keyed by root conversation id."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self._base_dir = Path(base_dir or os.path.join(CHAT_DATA_HOME, "agent_teams"))
        self._locks: dict[str, asyncio.Lock] = {}

    def path_for(self, conversation_id: str) -> Path:
        safe_id = "".join(char if char.isalnum() or char in "-_" else "_" for char in conversation_id)
        return self._base_dir / f"{safe_id}.json"

    def lock_for(self, conversation_id: str) -> asyncio.Lock:
        return self._locks.setdefault(conversation_id, asyncio.Lock())

    def load(self, conversation_id: str) -> TeamState:
        path = self.path_for(conversation_id)
        if not path.exists():
            return TeamState()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return TeamState()
        return TeamState.from_dict(raw)

    def save(self, conversation_id: str, state: TeamState) -> None:
        path = self.path_for(conversation_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)
        fd, temp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
