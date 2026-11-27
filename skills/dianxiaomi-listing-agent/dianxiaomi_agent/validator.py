"""Validate only supplied listing fields, images, SKU, color, price and stock."""
from __future__ import annotations
import copy
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from .utils import is_url, text
from .payload import COLOR_REQUIRED, attribute_object, require_color_listing


def _number(value: Any, label: str, *, stock: bool = False) -> str | int:
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or (stock and number != number.to_integral_value()) or (not stock and number == 0):
            raise ValueError()
        return int(number) if stock else format(number, "f")
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{label} 需要{'非负整数库存' if stock else '正数价格'}") from None


def normalize_images(images, sku: str, base_dir: Path | None = None) -> list[str]:
    if not isinstance(images, list) or not images or not all(isinstance(v, str) and v.strip() for v in images):
        raise ValueError(f"{sku} 需要有序 images 数组，第一张为主图")
    normalized = []
    for value in images:
        if is_url(value):
            normalized.append(value)
            continue
        path = ((base_dir or Path.cwd()) / Path(value).expanduser()).resolve()
        if any(part.casefold() in {"sku", "产品详情页"} for part in path.parts[:-1]) or not path.is_file():
            raise ValueError(f"图片不存在或位于排除目录：{path.name}")
        normalized.append(str(path))
    return normalized


def normalize_task(task: dict[str, Any], base_dir: Path | None = None) -> dict[str, Any]:
    if not isinstance(task, dict):
        raise ValueError("输入必须是 JSON 对象")
    if any(key in task for key in ("cookie", "template_id", "site", "_store_config")):
        raise ValueError("登录及模板配置填写在 Excel，不放入产品 JSON")
    result = {k: copy.deepcopy(task[k]) for k in ("shop_name", "category", "parent", "variants", "price", "quantity") if k in task}
    parent = result.get("parent")
    if not isinstance(parent, dict) or not text(parent.get("sku")) or not text(parent.get("title")):
        raise ValueError("parent 必须包含 sku 和 title")
    for key in ("title", "highlight", "description", "search_terms"):
        if not isinstance(parent.get(key), str):
            raise ValueError(f"parent.{key} 必须提供当前商品文字；确认无内容时填写空字符串")
    bullets = parent.get("bullet_points")
    if not isinstance(bullets, list) or len(bullets) > 5 or not all(isinstance(value, str) for value in bullets):
        raise ValueError("parent.bullet_points 必须是最多五条文字的数组")
    variants = result.get("variants")
    if not isinstance(variants, list) or not variants:
        raise ValueError(COLOR_REQUIRED)
    seen = {text(parent["sku"])}
    for item in variants:
        if not isinstance(item, dict):
            raise ValueError("商品必须是对象")
        sku = text(item.get("sku"))
        if not sku or sku in seen:
            raise ValueError(f"SKU 为空或重复：{sku}")
        seen.add(sku)
        item["sku"] = sku
        item["price"] = _number(item.get("price", parent.get("price", result.get("price"))), sku)
        item["quantity"] = _number(item.get("quantity", parent.get("quantity", result.get("quantity", 50))), sku, stock=True)
        legacy = item.get("variation") or {}
        color = item.get("color") or (legacy.get("color") or legacy.get("color.0.value") if isinstance(legacy, dict) else None)
        if not isinstance(color, str) or not color.strip():
            raise ValueError(f"{sku} 缺少 color 名称")
        item["color"] = color.strip()
        item["images"] = normalize_images(item.get("images"), sku, base_dir)
        allowed = {"sku", "title", "price", "quantity", "images", "color"}
        for key in set(item) - allowed:
            item.pop(key)
    parent["sku"], parent["title"] = text(parent["sku"]), text(parent["title"])
    for key in set(parent) - {"sku", "title", "highlight", "description", "search_terms", "bullet_points", "price", "quantity", "images", "color"}:
        parent.pop(key)
    return result


def validate_template(template: dict[str, Any], config: dict[str, Any]) -> None:
    for key in ("shopId", "marketPlaceId", "categoryId", "productAttr"):
        if template.get(key) in (None, ""):
            raise ValueError(f"引用模板缺少 {key}")
    for configured, remote in (("shop_id", "shopId"), ("market_place_id", "marketPlaceId")):
        if config.get(configured) and str(config[configured]) != str(template.get(remote)):
            raise ValueError(f"引用模板的 {remote} 与店铺配置不一致")
    for entry in attribute_object(template["productAttr"]).get("fulfillment_availability") or []:
        if entry.get("fulfillment_channel_code") not in (None, "", "DEFAULT"):
            raise ValueError("请选择 FBM 卖家自配送模板")


def validate_payload(payload: dict[str, Any]) -> None:
    for key in ("shopId", "marketPlaceId", "categoryId", "productAttr", "sku", "title"):
        if payload.get(key) in (None, ""):
            raise ValueError(f"提交数据缺少 {key}")
    if not isinstance(json.loads(payload["productAttr"]), dict):
        raise ValueError("productAttr 必须是对象")
    for row in require_color_listing(payload):
        sku = text(row["sku"])
        _number(row.get("standardPrice"), sku)
        _number(row.get("quantity"), sku, stock=True)
        if not is_url(text(row.get("mainImage"))) or any(not is_url(v) for v in text(row.get("extraImagesStr")).split(";") if v):
            raise ValueError(f"{sku} 图片 URL 无效")
