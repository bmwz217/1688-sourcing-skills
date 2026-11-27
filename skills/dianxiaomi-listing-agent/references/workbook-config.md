# 极简配置

`店小秘店铺与模板配置.xlsx` 是每位用户自己的个人配置文件，含店铺 Cookie 和模板映射，不随公开仓库分发，也不得放入公开技能缓存。运行时必须从本机薄入口提供的路径读取，或通过 `--config-xlsx` 指向用户提供的本机文件。若没有可用路径或文件，先请用户提供自己的配置工作簿再继续；不能假设仓库中存在、不能重建、不能上传到仓库，也不能展示或泄露其中的内容。

保留现有表格，实际使用的列：

| 表 | 列 |
|---|---|
| 店铺 | shop_name、site、cookie |
| 模板映射 | shop_name、category、template_id |
| 可选核对 | shop_id、market_place_id |

`template_id` 用文本保存，也接受含 id 参数的引用链接。店铺和类目组合唯一；新增模板只加一行映射。只选择目标店铺的 COLOR 变体 FBM 模板。

其他旧列原样保留但不参与操作，包括 attribute_defaults、variation_fields、schema_id、merchant_shipping_group、submit_op。所有属性及配送设置从引用商品继承；操作由命令行决定，默认 2 发布，1 只保存。

完整模板缓存和防重回执仍使用 %LOCALAPPDATA%/DianxiaomiListingAgent。模板修改后用 --refresh-cache；不要删除回执重复新增同一 SKU。
