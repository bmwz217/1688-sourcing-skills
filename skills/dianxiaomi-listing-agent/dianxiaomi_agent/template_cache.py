"""Full quoteEdit templates, isolated from legacy skeleton caches."""
from __future__ import annotations
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

CACHE_VERSION = 3

def template_cache_dir() -> Path:
    configured = os.getenv("DXM_TEMPLATE_CACHE_DIR")
    return Path(configured) if configured else Path(os.getenv("LOCALAPPDATA", str(Path.home() / ".cache"))) / "DianxiaomiListingAgent" / "full-template-cache"

def cache_identity(config: dict[str, Any]) -> dict[str, str]:
    return {key: str(config.get(key) or "") for key in ("shop_name", "site", "category", "template_id")}

def cache_path(identity: dict[str, str]) -> Path:
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return template_cache_dir() / f"v{CACHE_VERSION}-{digest}.json"

def load_template_cache(identity: dict[str, str], max_age_seconds: int = 86400) -> dict[str, Any] | None:
    try:
        record = json.loads(cache_path(identity).read_text(encoding="utf-8"))
        if record.get("version") != CACHE_VERSION or record.get("identity") != identity:
            return None
        if time.time() - float(record["created_at"]) > max_age_seconds:
            return None
        template = record.get("product_template")
        return template if isinstance(template, dict) and template.get("productAttr") else None
    except (OSError, ValueError, TypeError, KeyError):
        return None

def save_template_cache(identity: dict[str, str], product_template: dict[str, Any]) -> Path:
    path = cache_path(identity)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"version": CACHE_VERSION, "created_at": time.time(), "identity": identity, "product_template": product_template}
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as handle:
            temp_path = Path(handle.name)
            json.dump(record, handle, ensure_ascii=False)
        temp_path.replace(path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
    return path
