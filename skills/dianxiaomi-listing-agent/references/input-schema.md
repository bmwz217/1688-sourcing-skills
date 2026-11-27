# 极简输入

AI 从用户目录和 Listing 整理下列格式，店铺和类目须匹配配置表：

```json
{
  "shop_name": "李婷婷 美国",
  "category": "背包",
  "parent": {
    "sku": "NEW-PARENT",
    "title": "Current product title",
    "bullet_points": ["Feature one", "Feature two", "Feature three", "Feature four", "Feature five"],
    "description": "Current description",
    "search_terms": "current keywords",
    "highlight": "Current product highlight",
    "price": 29.9
  },
  "variants": [
    {
      "sku": "NEW-PARENT-BLACK",
      "color": "Black",
      "quantity": 50,
      "images": ["images/black-main.jpg", "images/black-detail.jpg"]
    },
    {
      "sku": "NEW-PARENT-BROWN",
      "color": "Brown",
      "price": 31.9,
      "quantity": 50,
      "images": ["images/brown-main.jpg"]
    }
  ]
}
```

- 五种文案字段均提供；确认没有内容时用空字符串或空五点数组，不能遗漏后沿用旧文案。
- 子体价格优先取子体，其次父级，再其次根级 `price`；库存按相同顺序取 `quantity`，默认 50。
- `color` 是实际 COLOR 名称，不填写标准色。兼容旧 `variation.color` 和 `variation["color.0.value"]`。
- `part_number` 不需要在 JSON 中提供；新商品的各子 SKU 自动使用自身 SKU。
- 图片支持本地路径及 HTTP(S) URL；顺序不变，第一张为主图。相对路径以 JSON 所在目录为基准。
- 不提供商品属性、估算值或子体附加属性。旧输入中的这些内容不再参与提交。
- `variants` 必填，至少含一个颜色子体。单款也使用 COLOR 模板，填独立子 SKU、实际颜色和图片，售价和库存应用在子体上。无变体记录仅支持只读查询。
- 商品标识与店铺登录配置不放在产品 JSON；旧记录 ID、ASIN、条码不会复制到新商品。

默认实际执行保存并发布；`--dry-run` 只读模板及查询 SKU，不上传图片、不提交。保留 `--output` 结果供核对。

## 编辑原记录

通过 `--edit-input` 提交编辑 JSON。用 `record_id` 填记录 ID（建议用字符串）或编辑链接。也可改用 `sku` 指定本机提交回执中的父 SKU；两项都提供时同时核对。

```json
{
  "shop_name": "黄绮文 美国",
  "record_id": "123456789012345678",
  "parent": {"title": "Updated product title"},
  "variants": [{"sku": "EXISTING-PARENT-BLACK", "price": 35.9, "quantity": 40}]
}
```

- `parent` 可填写要改的 `title`、`bullet_points`、`description`、`search_terms`、`highlight`、`images`、`price`、`quantity`。父级标题同步给子体。变体商品的父级价格和库存应用到全部子体，指定子体的设置优先。
- 每个子体以现有 `sku` 定位，可改 `title`、`color`、`images`、`price`、`quantity`。图片规则同新增，`color` 为 COLOR 名称。
- 若 Amazon 明确报某个子 SKU 缺少或错误的 `part_number`，技能会在该子体行的 `part_number.0.value` 中填入该子 SKU，再随重发保存。
- 省略的字段沿用原记录。只重发时省略 `parent` 和 `variants`。`--dry-run` 返回变更字段和当前发布状态，`--submit-op 1` 只保存，`--submit-op 2` 保存并重发。
