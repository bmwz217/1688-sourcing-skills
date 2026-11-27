# 极简配置

默认读取技能根目录的 `店小秘店铺与模板配置.xlsx`；也可用 `--config-xlsx` 指定其他表格。

保留现有表格，实际使用的列：

| 表 | 列 |
|---|---|
| 店铺 | shop_name、site、cookie |
| 模板映射 | shop_name、category、template_id |
| 可选核对 | shop_id、market_place_id |

`template_id` 用文本保存，也接受含 id 参数的引用链接。店铺和类目组合唯一；新增模板只加一行映射。只选择目标店铺的 COLOR 变体 FBM 模板。

其他旧列原样保留但不参与操作，包括 attribute_defaults、variation_fields、schema_id、merchant_shipping_group、submit_op。所有属性及配送设置从引用商品继承；操作由命令行决定，默认 2 发布，1 只保存。

完整模板缓存和防重回执仍使用 %LOCALAPPDATA%/DianxiaomiListingAgent。模板修改后用 --refresh-cache；不要删除回执重复新增同一 SKU。
