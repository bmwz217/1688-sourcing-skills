"""Minimal template-inheritance listing workflow."""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any
from .config import image_upload_url
from .dxm_client import DianxiaomiClient, SubmissionRejected
from .edit import edit_from_file
from .image_uploader import ImageUploader
from .payload import LISTING_ATTRIBUTES, attribute_object, build_payload, row_dimensions, template_rows, template_summary
from .receipts import fingerprint, read_receipt, receipt_path, submission_lock, write_receipt
from .template_cache import cache_identity, load_template_cache, save_template_cache
from .utils import compact_json, is_url, parse_json_value, text
from .validator import normalize_task, validate_payload, validate_template
from .workbook_config import DEFAULT_WORKBOOK, list_configurations, load_listing_config, normalize_config


def _config(task, config_xlsx, shop_name, category):
    workbook = Path(config_xlsx or DEFAULT_WORKBOOK)
    if config_xlsx or workbook.exists():
        return load_listing_config(workbook, shop_name=shop_name or task.get("shop_name"), category=category or task.get("category"))
    return normalize_config({
        "shop_name": shop_name or task.get("shop_name") or "environment",
        "category": category or task.get("category") or "environment",
        "cookie": os.getenv("DXM_COOKIE", ""), "site": os.getenv("DXM_SITE", ""),
        "template_id": os.getenv("DXM_TEMPLATE_ID", ""),
    })


def _template(client, config, refresh):
    client.set_template_context(config["template_id"])
    identity = cache_identity(config)
    template = None if refresh else load_template_cache(identity)
    hit = template is not None
    if template is None:
        template = client.get_product_template(config["template_id"])
    validate_template(template, config)
    if not hit:
        try:
            save_template_cache(identity, template)
        except OSError:
            pass
    return template, hit


def inspect_template(*, config_xlsx=None, shop_name=None, category=None, refresh_cache=False):
    config = _config({}, config_xlsx, shop_name, category)
    template, hit = _template(DianxiaomiClient(config["cookie"]), config, refresh_cache)
    return {"state": True, "shop_name": config["shop_name"], "category": config["category"], "template_cache_hit": hit, **template_summary(template, config)}


def _read_back(client, record_id, payload):
    if not record_id or not str(record_id).isdigit():
        return {"status": "unverified", "reason": "未返回可回读的记录 ID"}
    try:
        saved = client.get_product_template(str(record_id))
        fields = ("sku", "title", "description", "bulletPoint", "searchTerms", "titleDifferentiation")
        mismatch = [key for key in fields if text(saved.get(key)) != text(payload.get(key))]
        actual_attrs, expected_attrs = attribute_object(saved["productAttr"]), attribute_object(payload["productAttr"])
        for root in LISTING_ATTRIBUTES:
            values = lambda attrs: [text(row.get("value")) for row in attrs.get(root) or [] if text(row.get("value"))]
            if values(actual_attrs) != values(expected_attrs):
                mismatch.append(root)
        expected = json.loads(payload["variationString"]) if payload["isVariation"] else [payload]
        actual = template_rows(saved) if payload["isVariation"] else [saved]
        by_sku = {row.get("sku"): row for row in actual}
        if set(by_sku) != {row["sku"] for row in expected}:
            mismatch.append("child_skus")
        for row in expected:
            other = by_sku.get(row["sku"], {})
            for key in ("standardPrice", "quantity"):
                if Decimal(str(other.get(key))) != Decimal(str(row[key])):
                    mismatch.append(f"{row['sku']}.{key}")
            actual_specifics = parse_json_value(other.get("specifics") or "{}", "specifics")
            actual_part_number = actual_specifics.get("part_number.0.value") if isinstance(actual_specifics, dict) else None
            expected_part_number = row.get("part_number.0.value")
            if text(actual_part_number) != text(expected_part_number):
                mismatch.append(f"{row['sku']}.partNumber")
            for key in ("mainImage", "extraImagesStr", *[k for k in row if k.startswith("color.")]):
                if text(other.get(key)) != text(row.get(key)):
                    # Read responses may keep color names in specifics.
                    if key.startswith("color.") and text((other.get("specifics") or {}).get(key)) == text(row[key]):
                        continue
                    mismatch.append(f"{row['sku']}.{key}")
        return {"status": "mismatch", "fields": mismatch} if mismatch else {"status": "matched"}
    except Exception:
        return {"status": "unverified", "reason": "已接收，回读需要核实；不要重复新增"}


def publish_product(task: dict[str, Any], *, dry_run=False, config_xlsx=None, shop_name=None, category=None, refresh_cache=False, submit_op=2):
    task = normalize_task(task)
    config = _config(task, config_xlsx, shop_name, category)
    if submit_op not in (1, 2):
        raise ValueError("submit_op 只能是 1（保存）或 2（保存并发布）")
    config["submit_op"] = submit_op
    receipt = receipt_path(config, task["parent"]["sku"])
    if dry_run:
        return _run(task, config, receipt, None, True, refresh_cache)
    with submission_lock(receipt):
        digest, previous = fingerprint(task, config), read_receipt(receipt)
        if previous and previous.get("status") == "accepted":
            if previous.get("fingerprint") == digest:
                return {**previous["result"], "reused_receipt": True}
            raise ValueError(f"该 SKU 已有接收回执，不重复新增：{receipt}")
        if previous and previous.get("status") in {"submitting", "unknown"}:
            raise ValueError(f"上次结果未确认，请先到店小秘核实；禁止重发：{receipt}")
        return _run(task, config, receipt, digest, False, refresh_cache)


def _run(task, config, receipt, digest, dry_run, refresh):
    stage, started = "template", time.perf_counter()
    try:
        client = DianxiaomiClient(config["cookie"])
        template, hit = _template(client, config, refresh)
        stage = "preflight"
        items = task.get("variants") or [task["parent"]]
        local = list(dict.fromkeys(value for item in items for value in item["images"] if not is_url(value)))
        payload = build_payload(template, task, {value: f"https://preflight.invalid/{i}" for i, value in enumerate(local)})
        validate_payload(payload)
        rows = json.loads(payload["variationString"]) if payload["isVariation"] else [payload]
        summary = {
            "shop_name": config["shop_name"], "site": config["site"], "template_id": config["template_id"],
            "parent_sku": payload["sku"], "child_count": len(rows) if payload["isVariation"] else 0,
            "submit_op": config["submit_op"], "attribute_policy": "inherit_template", "template_cache_hit": hit,
            "variants": [{k: row[k] for k in ("sku", "standardPrice", "quantity", *[v for v in row if v.startswith("color.")])} for row in rows],
        }
        stage = "sku_check"
        for sku in [task["parent"]["sku"], *[item["sku"] for item in task.get("variants") or []]]:
            client.check_sku(sku, payload["shopId"], payload["marketPlaceId"])
        if dry_run:
            return {"state": True, "dry_run": True, "submitted": False, "images_uploaded": 0, **summary}
        stage = "image_upload"
        uploaded = dict(zip(local, ImageUploader(image_upload_url()).resolve(local)))
        payload = build_payload(template, task, uploaded)
        validate_payload(payload)
        stage = "submit"
        base = {"fingerprint": digest, "parent_sku": payload["sku"], "submit_op": config["submit_op"], "time": time.time()}
        write_receipt(receipt, {**base, "status": "submitting"})
        try:
            response = client.save_draft(compact_json(payload), op=config["submit_op"])
        except SubmissionRejected as exc:
            write_receipt(receipt, {**base, "status": "rejected", "error": str(exc)})
            return {"state": False, "submission_status": "rejected", "stage": stage, "error": str(exc), "receipt_path": str(receipt), **summary}
        except Exception:
            write_receipt(receipt, {**base, "status": "unknown"})
            return {"state": False, "submission_status": "unknown", "stage": stage, "error": "结果不明，请先到店小秘核实 SKU，禁止直接重发", "receipt_path": str(receipt), **summary}
        record_id = response.get("data")
        if isinstance(record_id, dict):
            record_id = record_id.get("id") or record_id.get("idStr")
        result = {
            "state": True, "submitted": True, "submission_status": "accepted", "record_id": str(record_id) if record_id else None,
            "amazon_live_status": "not_checked", "images_uploaded": len(uploaded), "receipt_path": str(receipt),
            "read_back": {"status": "pending"}, "warnings": [], "elapsed_seconds": round(time.perf_counter() - started, 3), **summary,
        }
        def persist():
            try:
                write_receipt(receipt, {**base, "status": "accepted", "result": result})
            except OSError:
                result["warnings"].append("回执写入失败，请保留记录 ID，禁止重复新增")
        persist()
        result["read_back"] = _read_back(client, record_id, payload)
        result["needs_attention"] = result["read_back"]["status"] != "matched" or bool(result["warnings"])
        persist()
        return result
    except Exception as exc:
        return {"state": False, "stage": stage, "error": str(exc)}


def publish_from_file(path, **kwargs):
    path = Path(path).expanduser().resolve()
    task = normalize_task(json.loads(path.read_text(encoding="utf-8-sig")), base_dir=path.parent)
    return publish_product(task, **kwargs)


def main():
    parser = argparse.ArgumentParser(description="店小秘极简新增与原记录编辑重发")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--input", help="产品 JSON 路径")
    action.add_argument("--edit-input", help="已有商品编辑 JSON 路径")
    action.add_argument("--list-config", action="store_true")
    action.add_argument("--inspect-template", action="store_true")
    parser.add_argument("--config-xlsx")
    parser.add_argument("--shop-name")
    parser.add_argument("--category")
    parser.add_argument("--dry-run", action="store_true", help="只读预检，不上传不提交")
    parser.add_argument("--submit-op", type=int, choices=(1, 2), default=2, help="1 保存；2 保存并发布（默认）")
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--output", help="结果 JSON 路径")
    args = parser.parse_args()
    try:
        options = dict(config_xlsx=args.config_xlsx, shop_name=args.shop_name, category=args.category, refresh_cache=args.refresh_cache)
        if args.list_config:
            result = {"state": True, "configurations": list_configurations(args.config_xlsx or DEFAULT_WORKBOOK)}
        elif args.inspect_template:
            result = inspect_template(**options)
        elif args.edit_input:
            result = edit_from_file(args.edit_input, dry_run=args.dry_run, submit_op=args.submit_op,
                                    config_xlsx=args.config_xlsx, shop_name=args.shop_name, category=args.category)
        else:
            result = publish_from_file(args.input, dry_run=args.dry_run, submit_op=args.submit_op, **options)
    except Exception as exc:
        result = {"state": False, "stage": "input_or_config", "error": str(exc)}
    if args.output:
        try:
            target = Path(args.output).expanduser().resolve()
            input_path = args.input or args.edit_input
            if target.is_relative_to(Path(__file__).resolve().parents[1]) or (input_path and target == Path(input_path).expanduser().resolve()) or target == Path(args.config_xlsx or DEFAULT_WORKBOOK).resolve():
                raise ValueError("结果不能覆盖输入、配置表或技能文件")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except (OSError, ValueError) as exc:
            result["output_error"] = str(exc)
            result["needs_attention"] = True
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("state"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
