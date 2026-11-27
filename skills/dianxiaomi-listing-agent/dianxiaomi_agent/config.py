"""Endpoints used by the minimal listing workflow."""
import os

DXM_BASE_URL = "https://www.dianxiaomi.com"
DXM_QUOTE_EDIT_URL = DXM_BASE_URL + "/api/amazonProductSp/quoteEdit.json?id={template_id}"
DXM_EDIT_URL = DXM_BASE_URL + "/api/amazonProductSp/editSp.json?id={record_id}"
DXM_REPUBLISH_URL = DXM_BASE_URL + "/api/amazonProduct/syncPublicAmazonProduct.json"
DXM_SKU_CHECK_URL = DXM_BASE_URL + "/api/amazonProduct/getProductBySku.json"
DXM_ADD_URL = DXM_BASE_URL + "/api/amazonProductSp/add.json"


def image_upload_url():
    return os.getenv("DXM_IMAGE_UPLOAD_URL") or "https://zhengxihuan.com/img_up/upload.php"
