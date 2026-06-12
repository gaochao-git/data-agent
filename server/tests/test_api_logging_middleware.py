import os
import sys
import base64

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from middleware.api_logging import _decode_username_header


def test_decode_username_header_decodes_base64_value():
    encoded = base64.b64encode("张三".encode("utf-8"))
    scope = {"headers": [(b"x-username", encoded), (b"x-user-id", b"u-123")]}

    _decode_username_header(scope)

    assert scope["headers"] == [(b"x-username", "张三".encode("utf-8")), (b"x-user-id", b"u-123")]


def test_decode_username_header_keeps_plain_value():
    scope = {"headers": [(b"x-username", "张三".encode("utf-8"))]}

    _decode_username_header(scope)

    assert scope["headers"] == [(b"x-username", "张三".encode("utf-8"))]
