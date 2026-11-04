"""运行配置缓存：过期时同 key 并发只回源一次。"""

from __future__ import annotations

import asyncio

from apps.chat_data_app.services import setting_service


def test_runtime_setting_single_flight_on_cache_miss(monkeypatch):
    calls = []

    async def fake_load(key):
        calls.append(key)
        await asyncio.sleep(0.02)
        return "10"

    monkeypatch.setattr(setting_service, "_load_raw_setting", fake_load)
    setting_service._cache.clear()
    setting_service._load_locks.clear()

    async def read_many():
        return await asyncio.gather(
            *[setting_service.get_runtime_setting("CHAT_DATA_MAX_UPLOAD_SIZE_MB") for _ in range(5)]
        )

    values = asyncio.run(read_many())
    assert values == [10, 10, 10, 10, 10]
    assert calls == ["CHAT_DATA_MAX_UPLOAD_SIZE_MB"]
