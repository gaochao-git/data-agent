"""NO_PROXY 合并：保留部署已有项，补齐默认项。"""

from __future__ import annotations

import os

from utils.common_util import merge_no_proxy


def test_merge_no_proxy_keeps_existing_and_appends_defaults(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "internal.example.com,127.0.0.1")
    monkeypatch.setenv("no_proxy", "internal.example.com,127.0.0.1")

    merged = merge_no_proxy()

    parts = merged.split(",")
    assert parts[0] == "internal.example.com"
    assert "dashscope.aliyuncs.com" in parts
    assert len(parts) == len(set(parts))
    assert os.environ["NO_PROXY"] == merged
    assert os.environ["no_proxy"] == merged
