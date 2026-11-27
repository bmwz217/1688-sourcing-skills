"""Local submission receipts prevent accidental replay after an uncertain POST."""
from __future__ import annotations
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from .template_cache import template_cache_dir
from .utils import compact_json

def receipt_path(config: dict[str, Any], sku: str) -> Path:
    identity = [config.get("shop_name"), config.get("site"), sku]
    key = hashlib.sha256(compact_json(identity).encode()).hexdigest()
    return template_cache_dir().parent / "submission-receipts" / f"{key}.json"

def fingerprint(task: dict[str, Any], config: dict[str, Any]) -> str:
    public_config = {k: config.get(k) for k in ("shop_name", "site", "template_id", "submit_op")}
    public_config["mode"] = "inherit_template"
    images = []
    for item in task.get("variants") or [task["parent"]]:
        for value in item["images"]:
            if not value.startswith(("http://", "https://")):
                images.append([value, hashlib.sha256(Path(value).read_bytes()).hexdigest()])
    return hashlib.sha256(json.dumps([task, public_config, images], ensure_ascii=False, sort_keys=True).encode()).hexdigest()

def read_receipt(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("status") not in {"accepted", "rejected", "unknown", "submitting"}:
            raise ValueError("回执结构不正确")
        return record
    except (OSError, ValueError) as exc:
        raise ValueError(f"无法读取提交回执，请先检查：{path}") from exc

def write_receipt(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(record, handle, ensure_ascii=False, indent=2)
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()

@contextmanager
def submission_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(".lock")
    try:
        handle = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError(f"该 SKU 正在处理或上次进程异常退出；先核实回执和任务状态：{lock}") from exc
    try:
        os.write(handle, str(os.getpid()).encode())
        yield
    finally:
        os.close(handle)
        lock.unlink(missing_ok=True)
