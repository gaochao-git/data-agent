import os
import sys

import pytest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from server.legal_knowledge import history as history_module
from server.legal_knowledge import service as service_module


class DummyResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_request_legal_answer_invalid_payload(monkeypatch):
    monkeypatch.setattr(history_module, "_qa_history", [])

    def fake_post(*_, **__):
        return DummyResponse(["not", "a", "dict"])

    monkeypatch.setattr(service_module.requests, "post", fake_post)
