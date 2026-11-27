"""Save changes to an existing record, verify them and request republication."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

from .config import image_upload_url
from .dxm_client import DianxiaomiClient, SubmissionRejected
from .image_uploader import ImageUploader
from .payload import TEMPLATE_FIELDS, _listing_attributes, attribute_object, color_field, require_color_listing, row_dimensions, set_part_number, template_rows
from .receipts import read_receipt, receipt_path, submission_lock, write_receipt
from .utils import compact_json, is_url, parse_json_value, text
from .validator import _number, normalize_images
from .workbook_config import DEFAULT_WORKBOOK, extract_template_id, load_listing_config


COPY_FIELDS = {
    "title": "title", "bullet_points": "bulletPoint", "description": "description",
    "search_terms": "searchTerms", "highlight": "titleDifferentiation",
}
SELLING_FIELDS = {"price": "standardPrice", "quantity": "quantity"}
CHILD_FIELDS = (
    "sku", "partNumber", "title", "titleDifferentiation", "skuTitle", "standardProductType",
    "standardProductId", "conditionType", "conditionValue", "standardPrice", "saleSalePrice",
    "saleStartDate", "saleEndDate", "quantity", "mainImage", "extraImagesStr",
)
PARENT_FIELDS = (
    *TEMPLATE_FIELDS, *COPY_FIELDS.values(),
    "sku", "isVariation", "sourceUrl", "standardProductType", "standardProductId", "standardPrice",
    "saleSalePrice", "saleStartDateStr", "saleEndDateStr", "quantity", "conditionType", "conditionValue",
    "mainImage", "extraImagesStr", "draftImgUrl", "skuTitle",
)
IN_PROGRESS = {"publishing", "waitConfirm"}
PUBLISHABLE = {"waitPublish", "publishFail"}
AMAZON_REVIEW_CODE = "100521"


def _record_id(record):
    value = text(record.get("idStr") or record.get("id"))
    if not value.isascii() or not value.isdigit() or value == "0":
        raise ValueError("已有商品缺少有效记录 ID")
    return value


def _status(product):
    fields = ("sku", "dxmState", "dxmOfflineState", "publishState", "asin", "errMsg", "productJsonFeedStatus", "platformUpdateTime")
    children = sorted(template_rows(product), key=lambda row: text(row.get("sku")))
    return {
        "parent": {key: product.get(key) for key in fields},
        "variants": [{key: row.get(key) for key in fields} for row in children],
    }


def _states(status):
    return {row.get("dxmOfflineState") for row in [status.get("parent", {}), *status.get("variants", [])]}


def _in_progress(status):
    rows = [status.get("parent", {}), *status.get("variants", [])]
    return bool(_states(status) & IN_PROGRESS) or any(AMAZON_REVIEW_CODE in text(row.get("errMsg")) for row in rows)


def _projection(product):
    parent = {key: product.get(key) for key in PARENT_FIELDS}
    parent["id"] = _record_id(product)
    parent["currency"] = product.get("standardPriceCurrency") or product.get("currency")
    parent["productAttr"] = attribute_object(product["productAttr"])
    children = {}
    for row in template_rows(product):
        child = {key: row.get(key) for key in CHILD_FIELDS}
        child["id"] = _record_id(row)
        specifics = parse_json_value(row.get("specifics") or "{}", "specifics")
        if not isinstance(specifics, dict):
            specifics = {}
        title = text(child.get("title"))
        sku = text(row.get("sku"))
        title_suffix = title.rfind(" (")
        if title_suffix >= 0 and sku and sku in title[title_suffix + 2:]:
            child["title"] = title[:title_suffix]
        child.update(row_dimensions(row))
        part_number = text(specifics.get("part_number.0.value") or row.get("part_number.0.value"))
        if part_number:
            child["part_number.0.value"] = part_number
        if isinstance(specifics, dict):
            child["partNumber"] = text(row.get("partNumber") or specifics.get("partNumber"))
        child["specificsVariant"] = parse_json_value(row.get("specificsVariant") or "{}", "specificsVariant")
        sku = text(row.get("sku"))
        if not sku or sku in children:
            raise ValueError("已有子体 SKU 为空或重复")
        children[sku] = child
    if len({row["id"] for row in children.values()}) != len(children):
        raise ValueError("已有子体记录 ID 重复")
    if bool(children) != (text(parent["isVariation"]) == "1"):
        raise ValueError("已有商品的变体标记与子体不一致")
    return parent, children


def _changed(before, after):
    fields = []
    for key in before.keys() | after.keys():
        old, new = before.get(key), after.get(key)
        if key in {"standardPrice", "saleSalePrice", "quantity"}:
            old, new = Decimal(str(old or 0)), Decimal(str(new or 0))
        elif old is None or new is None:
            old, new = text(old), text(new)
        if old != new:
            fields.append(key)
    return sorted(fields)


def _differences(before, after):
    parent, children = before
    target, variants = after
    fields = _changed(parent, target)
    if children.keys() != variants.keys():
        return [*fields, "child_skus"]
    return fields + [f"{sku}.{field}" for sku, child in children.items() for field in _changed(child, variants[sku])]


def _patch_copy(values):
    for key, value in values.items():
        if key == "bullet_points":
            if not isinstance(value, list) or len(value) > 5 or not all(isinstance(line, str) for line in value):
                raise ValueError("bullet_points 需要最多五条文字")
        elif not isinstance(value, str) or (key == "title" and not value.strip()):
            raise ValueError(f"{key} 需要有效文字")


def _patch_selling(row, changes, folder, uploaded):
    for key, field in SELLING_FIELDS.items():
        if key in changes:
            row[field] = _number(changes[key], key, stock=key == "quantity")
    if "images" in changes:
        images = normalize_images(changes["images"], text(row["sku"]), folder)
        images = [uploaded.get(image, image) for image in images]
        row["mainImage"], row["extraImagesStr"] = images[0], ";".join(images[1:])


def _plan(product, task, folder, uploaded):
    parent, children = _projection(product)
    patch = task.get("parent", {})
    if not isinstance(patch, dict) or set(patch) - (COPY_FIELDS.keys() | SELLING_FIELDS.keys() | {"images"}):
        raise ValueError("parent 只接受文案、图片、价格和库存字段")
    copy_values = {key: patch[key] for key in COPY_FIELDS if key in patch}
    _patch_copy(copy_values)
    if copy_values:
        _listing_attributes(parent["productAttr"], copy_values)
    for key, value in copy_values.items():
        parent[COPY_FIELDS[key]] = "±".join(value) if key == "bullet_points" else value
    parent_selling = patch if not children else {key: patch[key] for key in ("images",) if key in patch}
    _patch_selling(parent, parent_selling, folder, uploaded)
    for child in children.values():
        _patch_selling(child, {key: patch[key] for key in SELLING_FIELDS if key in patch}, folder, uploaded)
        if "title" in copy_values:
            child["title"] = copy_values["title"]
    variants = task.get("variants", [])
    if not isinstance(variants, list):
        raise ValueError("variants 需要按现有子 SKU 填写的数组")
    seen = set()
    field = None
    for changes in variants:
        if not isinstance(changes, dict) or set(changes) - (SELLING_FIELDS.keys() | {"sku", "title", "color", "images"}):
            raise ValueError("子体只接受定位用 SKU、标题、COLOR、图片、价格和库存")
        sku = text(changes.get("sku"))
        if sku in seen or sku not in children:
            raise ValueError(f"子 SKU 重复或不属于该记录：{sku}")
        seen.add(sku)
        child = children[sku]
        _patch_selling(child, changes, folder, uploaded)
        if "title" in changes:
            _patch_copy({"title": changes["title"]})
            child["title"] = changes["title"]
        if "color" in changes:
            if not isinstance(changes["color"], str) or not changes["color"].strip():
                raise ValueError(f"{sku} 的 COLOR 名称不能为空")
            field = field or color_field(product)
            child[field] = changes["color"].strip()
    if field:
        colors = [text(child.get(field)).casefold() for child in children.values()]
        if "" in colors or len(set(colors)) != len(colors):
            raise ValueError("编辑后 COLOR 名称为空或重复")
    return parent, children


def _part_number_error(row):
    error = text(row.get("errMsg")).casefold()
    return row.get("dxmOfflineState") == "publishFail" and ("part_number" in error or "零件编号" in error)


def _fill_failed_part_numbers(product, target):
    parent, children = target
    for row in template_rows(product):
        sku = text(row.get("sku"))
        if sku in children and _part_number_error(row):
            set_part_number(children[sku], sku)
    return parent, children


def _wire(projection):
    parent, children = projection
    return {
        **parent, "productAttr": compact_json(parent["productAttr"]),
        "variationString": compact_json([
            {**child, "specificsVariant": compact_json(child["specificsVariant"])} for child in children.values()
        ]),
    }


def _digest(projection):
    parent, children = projection
    def canonical(row):
        result = {key: "" if value is None else value for key, value in row.items()}
        for key in ("standardPrice", "saleSalePrice", "quantity"):
            result[key] = str(Decimal(str(result[key] or 0)).normalize())
        return result
    data = [canonical(parent), {sku: canonical(row) for sku, row in children.items()}]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _verified_product(client, record_id, scope, sku):
    product = client.get_existing_product(record_id)
    actual_scope = (text(product.get("shopId")), text(product.get("marketPlaceId")))
    if _record_id(product) != record_id or scope != actual_scope or (sku and sku != product.get("sku")):
        raise ValueError("记录 ID、父 SKU 或店铺站点与编辑目标不一致")
    return product


def _submit(client, current, load_current, before, target, receipt, result, submit_op):
    stage = "save"
    journal = None
    try:
        if _differences(before, target):
            pending = {"status": "submitting", "stage": stage, "expected": _digest(target)}
            write_receipt(receipt, pending)
            journal = pending
            response = client.save_draft(compact_json(_wire(target)), op=1)
            saved_id = response.get("data")
            if isinstance(saved_id, dict):
                saved_id = saved_id.get("idStr") or saved_id.get("id")
            if text(saved_id).isdigit() and text(saved_id) != result["record_id"]:
                raise ValueError(f"店小秘返回了不同的保存记录 ID：{saved_id}")
            write_receipt(receipt, {**journal, "status": "accepted"})
            journal = None
            result["saved"] = True
            current = load_current()
            mismatch = _differences(target, _projection(current))
            if mismatch:
                return {**result, "state": False, "stage": "read_back", "mismatch": mismatch}
            result["read_back"] = "matched"
        if submit_op == 1:
            return {**result, "state": True, "platform_status": _status(current)}
        stage = "publish"
        status = _status(current)
        if _in_progress(status) or not (_states(status) & PUBLISHABLE):
            return {**result, "state": False, "stage": stage, "platform_status": status, "error": "保存后记录状态已变化"}
        pending = {"status": "submitting", "stage": stage, "before": status}
        write_receipt(receipt, pending)
        journal = pending
        client.republish(result["record_id"], current["shopId"], current.get("isNew") or 2)
        accepted = {**journal, "status": "accepted"}
        write_receipt(receipt, accepted)
        journal = None
        result["submission_status"] = "accepted"
        try:
            status = _status(load_current())
            result["platform_status"] = status
            write_receipt(receipt, {**accepted, "after": status})
        except Exception as exc:
            result["status_check_error"] = str(exc)
        return {**result, "state": True}
    except SubmissionRejected as exc:
        if journal:
            write_receipt(receipt, {**journal, "status": "rejected"})
        return {**result, "state": False, "stage": stage, "submission_status": "rejected", "error": str(exc)}
    except Exception as exc:
        if journal:
            write_receipt(receipt, {**journal, "status": "unknown"})
        return {**result, "state": False, "stage": stage, "submission_status": "unknown" if journal else "not_submitted", "error": str(exc)}


def edit_from_file(path, *, config_xlsx=None, shop_name=None, category=None, dry_run=False, submit_op=2):
    path = Path(path).expanduser().resolve()
    task = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(task, dict) or set(task) - {"shop_name", "category", "record_id", "sku", "parent", "variants"}:
        raise ValueError("编辑 JSON 只接受店铺、记录 ID 或父 SKU、parent 和 variants")
    if submit_op not in (1, 2):
        raise ValueError("编辑操作只能是 1 保存或 2 保存并重发")
    config = load_listing_config(config_xlsx or DEFAULT_WORKBOOK, shop_name=shop_name or task.get("shop_name"), category=category or task.get("category"), for_edit=True)
    sku, record_id = text(task.get("sku")), text(task.get("record_id"))
    if not record_id and sku:
        previous = read_receipt(receipt_path(config, sku)) or {}
        record_id = text((previous.get("result") or {}).get("record_id")) if previous.get("status") == "accepted" else ""
    if not record_id:
        raise ValueError("本机未找到该父 SKU 的记录，请提供店小秘记录 ID 或编辑链接")
    record_id = extract_template_id(record_id)
    client = DianxiaomiClient(config["cookie"])
    shop_id, market_id = config.get("shop_id"), config.get("market_place_id")
    if not shop_id or not market_id:
        reference = client.get_product_template(config["template_id"])
        shop_id = shop_id or text(reference.get("shopId"))
        market_id = market_id or text(reference.get("marketPlaceId"))
    if not shop_id or not market_id:
        raise ValueError("无法确定所选店铺的 ID 和站点")

    def load_current():
        return _verified_product(client, record_id, (shop_id, market_id), sku)

    current = load_current()
    sku = text(current.get("sku"))
    if not sku:
        raise ValueError("已有记录缺少父 SKU")
    result = {"record_id": record_id, "parent_sku": sku, "shop_name": config["shop_name"], "saved": False, "submission_status": "not_submitted"}
    if dry_run:
        target = _fill_failed_part_numbers(current, _plan(current, task, path.parent, {}))
        fields = _differences(_projection(current), target)
        status = _status(current)
        try:
            require_color_listing(current)
        except ValueError as exc:
            return {**result, "state": True, "dry_run": True, "changed_fields": [],
                    "platform_status": status, "can_edit": False, "edit_blocked_reason": str(exc)}
        return {**result, "state": True, "dry_run": True, "changed_fields": fields, "platform_status": status,
                "can_edit": bool(_states(status) & PUBLISHABLE) and not _in_progress(status)}

    lock = receipt_path(config, sku)
    receipt = lock.with_name(f"{lock.stem}-edit-{record_id}.json")
    with submission_lock(lock):
        current = load_current()
        try:
            require_color_listing(current)
        except ValueError as exc:
            return {**result, "state": False, "stage": "preflight",
                    "platform_status": _status(current), "error": str(exc)}
        before = _projection(current)
        status = _status(current)
        states = _states(status)
        result.update(platform_status=status, receipt_path=str(receipt))
        previous = read_receipt(receipt) or {}
        if states & IN_PROGRESS and previous.get("stage") == "publish" and previous.get("status") != "rejected":
            write_receipt(receipt, {**previous, "after": status})
        if _in_progress(status) or not (states & PUBLISHABLE):
            return {**result, "state": False, "stage": "status", "error": "当前重发入口适用于待发布或发布失败的商品，且父子体均需结束正在进行的发布"}
        if previous.get("stage") == "save" and previous.get("status") in {"submitting", "unknown"} and previous.get("expected") != _digest(before):
            return {**result, "state": False, "stage": "save", "error": "上次保存结果未确认，请核对原记录及编辑回执"}
        if previous.get("stage") == "publish" and previous.get("status") != "rejected":
            settled = "publishFail" in states and (status != previous.get("before") or bool(_states(previous.get("after", {})) & IN_PROGRESS))
            if not settled:
                return {**result, "state": False, "stage": "publish", "error": "上次重发尚未确认结束，请核对当前发布状态及编辑回执"}

        target = _fill_failed_part_numbers(current, _plan(current, task, path.parent, {}))
        result["changed_fields"] = _differences(before, target)
        image_changes = [task.get("parent", {}), *task.get("variants", [])]
        images = list(dict.fromkeys(
            image for change in image_changes if "images" in change
            for image in normalize_images(change["images"], change.get("sku", sku), path.parent)
            if not is_url(image)
        ))
        if images:
            uploaded = dict(zip(images, ImageUploader(image_upload_url()).resolve(images)))
            target = _fill_failed_part_numbers(current, _plan(current, task, path.parent, uploaded))
            latest = load_current()
            if _status(latest) != status or _differences(before, _projection(latest)):
                return {**result, "state": False, "stage": "preflight", "error": "上传图片期间原记录发生变化，请重新预检"}
            current = latest
        return _submit(client, current, load_current, before, target, receipt, result, submit_op)
