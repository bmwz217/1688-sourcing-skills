"""Copy template defaults and replace only listing copy and selling fields."""
from __future__ import annotations
import copy
import re
from typing import Any
from .utils import as_list, compact_json, parse_json_value, text

TEMPLATE_FIELDS = (
    "shopId", "marketPlaceId", "categoryId", "categoryBrowsePath", "audience",
    "brandSettledType", "fullCid", "variationTheme", "brand", "manufacturer",
    "gpsrManufacturerReference", "dsaResponsiblePartyAddress", "gpsrSafetyAttestation",
    "switchFulfillmentTo", "merchantShippingGroup",
)
LISTING_ATTRIBUTES = {
    "item_name": "title", "bullet_point": "bullet_points",
    "product_description": "description", "generic_keyword": "search_terms",
    "title_differentiation": "highlight",
}
IDENTIFIER_ATTRIBUTES = {"externally_assigned_product_identifier", "merchant_suggested_asin"}


def attribute_object(value: Any) -> dict[str, Any]:
    parsed = parse_json_value(value, "productAttr")
    if isinstance(parsed, list) and len(parsed) == 1:
        parsed = parsed[0]
    if not isinstance(parsed, dict):
        raise ValueError("productAttr 必须是属性对象")
    return copy.deepcopy(parsed)


def template_rows(template: dict[str, Any]) -> list[dict[str, Any]]:
    raw = template.get("variationString")
    rows = as_list(raw if raw not in (None, "", "null") else template.get("variations") or [], "variationString")
    rows = [parse_json_value(row, "子体") if isinstance(row, str) else row for row in rows]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("模板子体必须是对象")
    return rows


def row_dimensions(row: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for source in (row.get("specificsString"), row.get("specifics"), row):
        if isinstance(source, str) and source:
            source = parse_json_value(source, "specifics")
        if isinstance(source, dict):
            result.update({k: v for k, v in source.items()
                           if k != "part_number.0.value" and re.fullmatch(r"\w+\.\d+\.\w+", k)})
    return result


def color_field(template: dict[str, Any]) -> str:
    fields = {key for row in template_rows(template) for key in row_dimensions(row)}
    theme = re.sub(r"[^A-Z]", "", text(template.get("variationTheme")).upper())
    if theme not in {"COLOR", "COLORNAME"} or any(not key.startswith("color.") for key in fields) or len(fields) > 1:
        raise ValueError("极简版只处理 COLOR 变体，请选择颜色变体模板")
    return next(iter(fields), "color.0.value")


COLOR_REQUIRED = "禁止无变体提交：必须使用 COLOR 变体，且至少有一个独立的颜色子 SKU"


def require_color_listing(product: dict[str, Any]) -> list[dict[str, Any]]:
    if product.get("isVariation") not in (1, "1"):
        raise ValueError(COLOR_REQUIRED)
    field = color_field(product)
    rows = template_rows(product)
    parent_sku = text(product.get("sku"))
    skus = [text(row.get("sku")) for row in rows]
    colors = [text(row_dimensions(row).get(field)).casefold() for row in rows]
    if (not rows or not parent_sku or "" in skus or parent_sku in skus
            or len(set(skus)) != len(rows) or "" in colors or len(set(colors)) != len(rows)):
        raise ValueError(COLOR_REQUIRED)
    return rows


def template_summary(template: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    return {
        "template_id": config["template_id"], "template_sku": template.get("sku"),
        "variation_theme": template.get("variationTheme"),
        "color_field": color_field(template) if template.get("variationTheme") else None,
        "template_child_count": len(template_rows(template)), "attribute_policy": "inherit_template",
    }


def _listing_attributes(attrs: dict[str, Any], parent: dict[str, Any]) -> None:
    sample = (attrs.get("item_name") or [{}])[0]
    metadata = {k: v for k, v in sample.items() if k in {"language_tag", "marketplace_id", "default_language_tag"}}
    for root, key in LISTING_ATTRIBUTES.items():
        if key not in parent:
            continue
        values = parent[key] if key == "bullet_points" else [parent[key]]
        old = attrs.get(root) or []
        attrs[root] = [
            {**copy.deepcopy(old[min(i, len(old) - 1)] if old else metadata), "value": value}
            for i, value in enumerate(values)
        ]


def set_part_number(row: dict[str, Any], sku: str) -> None:
    """Store Amazon's child part_number in Dianxiaomi's flattened row fields."""
    row["part_number.0.value"] = sku
    variant_attrs = row.get("specificsVariant")
    if isinstance(variant_attrs, dict):
        variant_attrs.pop("part_number", None)


def _new_row(item: dict[str, Any], uploaded: dict[str, str], defaults: dict[str, Any], title: str) -> dict[str, Any]:
    images = [uploaded.get(value, value) for value in item["images"]]
    variant_attrs = parse_json_value(defaults.get("specificsVariant") or "{}", "specificsVariant")
    if not isinstance(variant_attrs, dict):
        variant_attrs = {}
    return {
        "sku": item["sku"], "part_number.0.value": item["sku"], "title": item.get("title") or title, "skuTitle": 0,
        "standardProductType": defaults.get("standardProductType") or "mpn", "standardProductId": "",
        "conditionType": defaults.get("conditionType") or "new_new", "conditionValue": defaults.get("conditionValue"),
        "standardPrice": item["price"], "quantity": item["quantity"],
        "saleSalePrice": None, "saleStartDate": "", "saleEndDate": "",
        "mainImage": images[0], "extraImagesStr": ";".join(images[1:]),
        "specificsVariant": compact_json(variant_attrs),
    }


def build_payload(template: dict[str, Any], task: dict[str, Any], uploaded: dict[str, str], config: dict[str, Any] | None = None) -> dict[str, Any]:
    parent, variants = task["parent"], task.get("variants") or []
    if not variants or template.get("isVariation") not in (1, "1"):
        raise ValueError(COLOR_REQUIRED)
    field = color_field(template)
    attrs = attribute_object(template["productAttr"])
    for key in IDENTIFIER_ATTRIBUTES:
        attrs.pop(key, None)
    _listing_attributes(attrs, parent)
    result = {key: copy.deepcopy(template.get(key)) for key in TEMPLATE_FIELDS}
    result.update({
        "currency": template.get("standardPriceCurrency") or template.get("currency"),
        "sku": parent["sku"], "title": parent["title"],
        "description": parent["description"], "bulletPoint": "±".join(parent["bullet_points"]),
        "searchTerms": parent["search_terms"], "titleDifferentiation": parent["highlight"],
        "productAttr": compact_json(attrs), "isVariation": int(bool(variants)),
        "standardProductType": None, "standardProductId": None, "standardPrice": None,
        "quantity": None, "mainImage": "", "extraImagesStr": "", "draftImgUrl": "", "sourceUrl": "", "skuTitle": 0,
    })
    defaults = template_rows(template)
    rows = []
    for item in variants:
        color = item["color"]
        sample = next((row for row in defaults if text(row_dimensions(row).get(field)).casefold() == color.casefold()), defaults[0] if defaults else {})
        row = _new_row(item, uploaded, sample, parent["title"])
        row[field] = color
        rows.append(row)
    result["variationString"] = compact_json(rows)
    require_color_listing(result)
    return result
