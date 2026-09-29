import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from apps.chat_data_app.agent.middleware.after_model_history import (
    create_after_model_history_middleware,
)


@pytest.mark.asyncio
async def test_after_model_history_persists_state_snapshot():
    snapshots = []
    middleware = create_after_model_history_middleware(
        lambda state: asyncio.sleep(0, result=snapshots.append(state)),
    )

    await middleware.aafter_model({"messages": ["assistant"]}, None)

    assert snapshots == [{"messages": ["assistant"]}]


@pytest.mark.asyncio
async def test_after_model_history_does_not_break_agent_when_persistence_fails(monkeypatch):
    errors = []

    async def fail(_state):
        raise RuntimeError("event store unavailable")

    monkeypatch.setattr(
        "apps.chat_data_app.agent.middleware.after_model_history.logger.warning",
        lambda *args, **kwargs: errors.append(args),
    )
    middleware = create_after_model_history_middleware(fail)

    assert await middleware.aafter_model({"messages": []}, None) is None
    assert errors
