"""Read the existing workbook; category behavior lives in configuration."""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from openpyxl import load_workbook

DEFAULT_WORKBOOK = Path(__file__).resolve().parents[1] / "店小秘店铺与模板配置.xlsx"
STORE_HEADERS = {"shop_name", "site", "cookie"}
TEMPLATE_HEADERS = {"shop_name", "category", "template_id"}

def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()

def _find_table(workbook: Any, required: set[str]):
    for sheet in workbook:
        for row_number, cells in enumerate(sheet.iter_rows(max_row=min(sheet.max_row, 20)), 1):
            headers = {_text(c.value).casefold(): c.column for c in cells if _text(c.value)}
            if required.issubset(headers):
                return sheet, row_number, headers
    raise ValueError("配置工作簿缺少表头：" + ", ".join(sorted(required)))

def _records(workbook: Any, required: set[str]) -> list[dict[str, Any]]:
    sheet, header, columns = _find_table(workbook, required)
    return [
        {key: (row[col - 1] if key == "template_id" else _text(row[col - 1])) if col <= len(row) else "" for key, col in columns.items()}
        for row in sheet.iter_rows(min_row=header + 1, values_only=True)
        if any(value is not None for value in row)
    ]

def extract_template_id(value: Any) -> str:
    if isinstance(value, (float, int)):
        raise ValueError("template_id 请用文本单元格保存，避免 Excel 丢失长编号精度")
    value = _text(value)
    if value.startswith(("http://", "https://")):
        value = parse_qs(urlparse(value).query).get("id", [""])[0]
    if not re.fullmatch(r"\d+", value):
        raise ValueError("template_id 必须是文本编号或包含 id 的引用链接")
    return value

def normalize_config(config: dict[str, Any]) -> dict[str, Any]:
    result = {key: _text(config.get(key)) for key in ("shop_name", "category", "site", "cookie", "shop_id", "market_place_id")}
    result["template_id"] = extract_template_id(config.get("template_id"))
    result["site"] = result["site"].upper()
    if not result["site"] or not result["cookie"]:
        raise ValueError("所选店铺缺少 site 或 cookie")
    return result


def load_listing_config(workbook_path: str | Path = DEFAULT_WORKBOOK, *, shop_name: str | None = None, category: str | None = None, for_edit: bool = False) -> dict[str, Any]:
    workbook = load_workbook(Path(workbook_path).expanduser(), data_only=True, read_only=True)
    try:
        stores = [r for r in _records(workbook, STORE_HEADERS) if r.get("shop_name")]
        if shop_name:
            stores = [r for r in stores if r["shop_name"].casefold() == shop_name.strip().casefold()]
        if len(stores) != 1:
            raise ValueError("请选择配置表中唯一的 shop_name")
        store = stores[0]
        mappings = [r for r in _records(workbook, TEMPLATE_HEADERS) if r.get("shop_name", "").casefold() == store["shop_name"].casefold()]
        if category:
            mappings = [r for r in mappings if r.get("category", "").casefold() == category.strip().casefold()]
        if len(mappings) != 1 and (not for_edit or category or not mappings):
            raise ValueError(f"请选择该店铺唯一的 category；候选：{[r.get('category') for r in mappings]}")
        result = dict(store)
        result.update({k: v for k, v in mappings[0].items() if v not in (None, "") and k not in {"cookie", "site"}})
        return normalize_config(result)
    finally:
        workbook.close()

def list_configurations(workbook_path: str | Path = DEFAULT_WORKBOOK) -> list[dict[str, Any]]:
    """Agent discovery: no credentials or arbitrary workbook columns."""
    workbook = load_workbook(workbook_path, data_only=True, read_only=True)
    try:
        stores = {r.get("shop_name", "").casefold(): r for r in _records(workbook, STORE_HEADERS)}
        return [
            {"shop_name": r.get("shop_name", ""), "category": r.get("category", ""), "site": stores.get(r.get("shop_name", "").casefold(), {}).get("site", ""), "template_configured": bool(r.get("template_id"))}
            for r in _records(workbook, TEMPLATE_HEADERS) if r.get("shop_name")
        ]
    finally:
        workbook.close()
