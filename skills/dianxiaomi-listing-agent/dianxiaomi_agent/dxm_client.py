"""Read a reference, check SKU availability and submit a fresh record."""
from __future__ import annotations
import io
import json
import zipfile
from .config import DXM_ADD_URL, DXM_BASE_URL, DXM_EDIT_URL, DXM_QUOTE_EDIT_URL, DXM_REPUBLISH_URL, DXM_SKU_CHECK_URL
from .net import form_body, multipart_body, request_json
from .payload import require_color_listing


class SubmissionRejected(RuntimeError):
    pass


class DianxiaomiClient:
    def __init__(self, cookie: str, timeout: int = 60):
        if not cookie.strip():
            raise ValueError("缺少店小秘 Cookie")
        self.timeout = timeout
        self.headers = {
            "Cookie": cookie, "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.dianxiaomi.com", "Referer": "https://www.dianxiaomi.com/",
            "User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest",
        }

    def set_template_context(self, template_id):
        self.headers["Referer"] = "https://www.dianxiaomi.com/web/amazon/quoteEdit?id=" + str(template_id)

    def get_product_template(self, template_id):
        response = request_json(DXM_QUOTE_EDIT_URL.format(template_id=template_id), headers=self.headers, timeout=self.timeout)
        product = (response.get("data") or {}).get("product")
        if not isinstance(product, dict):
            raise ValueError("引用模板读取失败，请检查登录及模板 ID")
        self.set_template_context(template_id)
        return product

    def get_existing_product(self, record_id):
        self.headers["Referer"] = f"{DXM_BASE_URL}/web/amazon/edit?id={record_id}"
        response = request_json(DXM_EDIT_URL.format(record_id=record_id), headers=self.headers, timeout=self.timeout)
        product = (response.get("data") or {}).get("product")
        if not isinstance(product, dict):
            raise ValueError("已有记录读取失败，请检查店铺登录和记录 ID")
        return product

    def republish(self, record_id, shop_id, is_new):
        require_color_listing(self.get_existing_product(record_id))
        body, content_type = form_body({"ids": record_id, "shopId": shop_id, "operation": "add", "isNew": is_new})
        headers = {**self.headers, "Referer": f"{DXM_BASE_URL}/web/amazon/product", "Content-Type": content_type}
        response = request_json(DXM_REPUBLISH_URL, method="POST", headers=headers, body=body, timeout=self.timeout)
        data = response.get("data")
        ret = data.get("ret") if isinstance(data, dict) else None
        if response.get("code") not in (0, 200) or ret not in (None, 1, "1"):
            raise SubmissionRejected(f"店小秘拒绝重发：{response.get('msg') or response.get('code')}")
        return response

    def check_sku(self, sku, shop_id, marketplace_id):
        body, content_type = form_body({"id": "", "sku": sku, "shopId": shop_id, "marketPlaceId": marketplace_id})
        response = request_json(DXM_SKU_CHECK_URL, method="POST", headers={**self.headers, "Content-Type": content_type}, body=body, timeout=self.timeout)
        if response.get("code") not in (0, 200) or (response.get("data") or {}).get("ret") != 1:
            raise ValueError(f"SKU 不可用或查询失败：{sku}")

    def save_draft(self, amazon_save_text, op=2):
        require_color_listing(json.loads(amazon_save_text))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("amazonSave.txt", amazon_save_text)
        body, content_type = multipart_body({"op": str(op)}, [("file", "blob", buffer.getvalue(), "application/zip")], files_first=False)
        response = request_json(DXM_ADD_URL, method="POST", headers={**self.headers, "Content-Type": content_type}, body=body, timeout=self.timeout)
        if response.get("code") != 0:
            raise SubmissionRejected(f"店小秘拒绝提交（{response.get('code')}）：{response.get('msg') or '未知业务错误'}")
        return response
