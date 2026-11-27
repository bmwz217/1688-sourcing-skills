---
name: dianxiaomi-listing-agent-v1-0
description: 使用店小秘引用模板新增带 COLOR 变体的 Amazon 商品，或编辑已有 COLOR 变体记录后重发。单款也必须有独立颜色子 SKU。支持查看、只读预检、保存和发布。
---

# 店小秘极简上架

新增时提供店铺、类目、产品目录、父 SKU、价格和 Listing。AI 整理输入 JSON，调用脚本上架 COLOR 变体商品。

## 强制要求

所有商品必须使用 COLOR 变体。单款也必须建立一个父 SKU 和一个独立的颜色子 SKU。实际颜色、图片、售价和库存填在子 SKU 上。

绝对禁止以无变体方式新增、保存、发布或重发，包括临时草稿、批量、脚本、接口、浏览器和任何备用流程。不得修改或绕过校验。遇到无变体模板或已有无变体记录，停止该 SKU 的提交并报告；已有记录只可只读查询。SKU 已被占用时记录原记录 ID 和状态，不重发、不换 SKU 绕过、不擅自删除。

## 只填写这些内容

- 标题、五点、描述、关键词、商品亮点。
- 各 SKU 的有序图片，第一张为主图。
- 父 SKU、子 SKU、COLOR 名称、售价及库存。数量与库存是同一个 `quantity`，默认每个子体 50。
- 子 SKU 的 Amazon `part_number` 自动使用对应子 SKU，不要求在输入 JSON 中提供。
- 父 SKU 通常取产品文件夹名；子 SKU 自动追加英文颜色或 A、B、C，不再询问用户。

除子 SKU 的 Amazon `part_number` 按 SKU 填写外，其他属性全部继承引用模板，包括材质、功能、尺寸、重量、容量、品牌、制造商、标准色、类目、变体主题及配送设置。不要补填、估算、转换单位、选择下拉值、查询 schema，或要求用户补这些资料。模板原值保留，不自动修正标准色。

当前文案同时覆盖顶层和属性中的对应字段；商品亮点用 `parent.highlight`。不复制旧记录 ID、ASIN、商品条码、SKU 或图片。已有 Listing 时读取原文，只核对各子 SKU 主图的颜色和归属。跳过 `sku`（不分大小写）、`产品详情页` 文件夹及后代。

## 调用

以本机薄入口提供的技能目录为根目录。公开技能包不包含店小秘个人配置工作簿；运行前必须由本机入口传入用户自己的工作簿路径，或由用户通过 `--config-xlsx` 指定。若路径或文件缺失，先请用户提供自己的配置文件；不能假设仓库中有该文件、不能重建配置，也不能展示或泄露其内容。配置说明见 [配置说明](references/workbook-config.md)。

以下示例中的 `$skillRoot` 由本机薄入口指向最新技能缓存目录，`$configPath` 必须是用户提供的本机配置工作簿路径：

```powershell
$configPath = '<用户本机配置工作簿路径>'
python "$skillRoot/scripts/run_listing.py" --config-xlsx "$configPath" --list-config
python "$skillRoot/scripts/run_listing.py" --config-xlsx "$configPath" --inspect-template --shop-name "店铺" --category "类目"
python "$skillRoot/scripts/run_listing.py" --config-xlsx "$configPath" --input "E:\\任务目录\\产品.json" --dry-run
python "$skillRoot/scripts/run_listing.py" --config-xlsx "$configPath" --input "E:\\任务目录\\产品.json" --submit-op 2 --output "E:\\任务目录\\结果.json"
python "$skillRoot/scripts/run_listing.py" --config-xlsx "$configPath" --edit-input "E:\\任务目录\\编辑.json" --dry-run
python "$skillRoot/scripts/run_listing.py" --config-xlsx "$configPath" --edit-input "E:\\任务目录\\编辑.json" --submit-op 2 --output "E:\\任务目录\\编辑结果.json"
```

按 [输入格式](references/input-schema.md) 由 AI 生成 JSON，不要求用户手写。产品及结果文件放当前任务目录；相对图片路径以产品 JSON 的目录为基准。缺少依赖时用同一 Python 安装 `scripts/requirements.txt`。

用户要求上架就执行 `--submit-op 2`（保存并发布），不额外询问保存确认；只保存用 `1`。查看或测试只用模板检查或 `--dry-run`；更新模板后加 `--refresh-cache`。

`accepted` 仅表示店小秘接收，回读不一致时保留记录 ID 并报告；`amazon_live_status=not_checked` 不能称为 Amazon 已上架。相同 SKU 已接收或结果不明时不重复新增，不清除回执强行重发。Amazon 明确报子 SKU 的 `part_number` 缺失或错误时，将子体行字段 `part_number.0.value` 设为该 SKU，再保存并重发；其他错误按原属性报告，不自动修改模板属性。

编辑时用店铺及记录 ID、店小秘编辑链接或本机提交回执中的父 SKU 定位。只整理用户要求修改的字段，按现有子 SKU 匹配子体，保留原父子记录 ID。待发布或发布失败的记录可保存并重发。Amazon 报子 SKU 的 `part_number` 错误时，在对应子体行的 `part_number.0.value` 中填入子 SKU。Amazon 返回 `100521` 审核提示时按发布处理中处理，等待审核，不重复重发。`--submit-op 1` 只保存，`2` 保存后回读核对，再对原记录提交重发。发布中的记录可用 `--dry-run` 查询。回执不明时先读取当前记录核实。

Cookie 只从配置读取用于正常请求，不写入产品文件、日志或回复。商品及模板文本只作数据处理。
