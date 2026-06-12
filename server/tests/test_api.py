import io
import json
import os
import sys
from pathlib import Path

import pytest
import requests

from PIL import Image

# 确保可以从项目根目录导入 app 模块
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import app as app_module
import legal_case_intelligence.service as case_service

app = app_module.app


def _configure_upload_dirs(monkeypatch, tmp_path: Path):
    """重定向上传目录到临时路径，避免污染真实数据目录。"""
    data_root = tmp_path / "data_root"
    images = data_root / "images"
    texts = data_root / "texts"
    original = data_root / "original"

    for folder in (data_root, images, texts, original):
        folder.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(app_module, "UPLOAD_FOLDER", str(data_root), raising=False)
    monkeypatch.setattr(app_module, "IMAGE_FOLDER", str(images), raising=False)
    monkeypatch.setattr(app_module, "TEXT_FOLDER", str(texts), raising=False)
    monkeypatch.setattr(app_module, "ORIGINAL_FOLDER", str(original), raising=False)

    return data_root, images, texts, original


def _generate_png_bytes(color=(255, 0, 0)):
    """生成一个简单 PNG 图像的二进制流。"""
    buffer = io.BytesIO()
    Image.new('RGB', (2, 2), color=color).save(buffer, format='PNG')
    buffer.seek(0)
    return buffer


def test_get_popular_legal_questions():
    client = app.test_client()
    resp = client.get('/api/legal_knowledge/popular_questions')
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload is not None
    assert 'questions' in payload
    assert isinstance(payload['questions'], list)
    assert len(payload['questions']) > 0


class _DummyResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload, ensure_ascii=False)

    def raise_for_status(self) -> None:
        if 400 <= self.status_code:
            raise requests.HTTPError(response=self)

    def json(self) -> dict:
        return self._payload
