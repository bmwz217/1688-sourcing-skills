from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def parse_json_value(value: Any, label: str) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label} 不是有效 JSON: {exc}") from exc
    raise ValueError(f"{label} 类型不正确")


def as_list(value: Any, label: str) -> list[Any]:
    parsed = parse_json_value(value, label)
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        return [parsed]
    raise ValueError(f"{label} 解析后不是对象或数组")


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def local_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()

