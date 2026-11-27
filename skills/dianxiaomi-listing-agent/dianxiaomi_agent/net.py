from __future__ import annotations

import json
import mimetypes
import os
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, ProxyHandler, build_opener
from typing import Any


def request_bytes(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: int = 60,
) -> bytes:
    request = Request(url, data=body, headers=headers or {}, method=method)
    try:
        opener = build_opener() if os.getenv("DXM_USE_SYSTEM_PROXY") == "1" else build_opener(ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            return response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"网络请求失败: {exc.reason}") from exc


def request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    raw = request_bytes(url, method=method, headers=headers, body=body, timeout=timeout)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("接口返回不是有效 JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("接口返回不是 JSON 对象")
    return data


def form_body(values: dict[str, Any]) -> tuple[bytes, str]:
    return urlencode({key: str(value) for key, value in values.items()}).encode(), "application/x-www-form-urlencoded"


def multipart_body(
    fields: dict[str, Any],
    files: list[tuple[str, str, bytes, str]],
    *,
    files_first: bool = False,
) -> tuple[bytes, str]:
    boundary = "----CodexDxm" + uuid.uuid4().hex
    chunks: list[bytes] = []

    def append_fields() -> None:
        for name, value in fields.items():
            chunks.extend([
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                str(value).encode(),
                b"\r\n",
            ])

    def append_files() -> None:
        for field, filename, content, mime in files:
            safe_name = filename.replace('"', "")
            chunks.extend([
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{field}"; filename="{safe_name}"\r\n'.encode(),
                f"Content-Type: {mime or mimetypes.guess_type(filename)[0] or 'application/octet-stream'}\r\n\r\n".encode(),
                content,
                b"\r\n",
            ])

    if files_first:
        append_files()
        append_fields()
    else:
        append_fields()
        append_files()
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"

