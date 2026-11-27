# 英文 Listing 校验

先核对当前 Amazon 美国站产品标题、五点、详情、后台搜索词和具体商品类型规则，记录日期和链接。政策或类目规则不可核实时，记录未核验项，按可确认的较保守规则保留草稿。下列是通用写稿检查基线，商品类型若有更严格限制，以实际字段规则为准。

**最低长度为适用上限的 80%（强制）**。对本次新写或改写的英文字段按实际规则的字符数或 UTF-8 字节数计量，下限向上取整。只有五点合计上限时按五条正文合计判定。有单条及合计上限时同时检查。类目上限无法核实时，在研究记录注明缺口并标草稿。素材或相关词不足时补查 Amazon 竞品和产品资料；仍低于门槛时逐款记录实测值、门槛和缺口，以未达标草稿交付。禁止用重复词、无关词或无依据的商品宣称凑长度。

- **标题**。自 2026-07-27 起，Amazon 美国站非媒体商品标题最多 75 字符（含空格）。核对禁用字符、重复实词和目标类目的品牌、变体格式。包袋和家居品的通用范围为 60–75 字符。条件冲突时记录原因，以更严格的平台规则为准。 [Amazon 标题要求](https://sellercentral.amazon.com/gp/help/external/YTR6SYGFA5E3EQC?mons_sel_locale=en_US) [Amazon 非媒体标题和亮点公告](https://sellercentral.amazon.com/seller-forums/discussions/t/2f40628c-eb97-4b24-b983-cb1e331e06dc)
- **商品亮点**。适用时通用范围为 100–125 字符。圣诞和家居装饰品按用户要求或目标类目提供该字段。保留旧稿的仅标题更新可报告旧亮点过短。英文文案按类目现行限制核对。 [Amazon 商品亮点公告](https://sellercentral.amazon.com/seller-forums/discussions/t/2f40628c-eb97-4b24-b983-cb1e331e06dc)
- **五点与描述**。五点恰好五条，各点独立，须人工核对商品类型的字段及长度限制。描述按可复制的纯文本交付并检查外链、促销等。 [Amazon 五点要求](https://sellercentral.amazon.com/help/hub/reference/external/GX5L8BF8GLMML6CX?language=en_us) [Amazon 商品详情页规则](https://sellercentral.amazon.com/help/hub/reference/external/G200390640?locale=en-us)
- **后台搜索词**。全小写、单空格分隔、删除重复及前台已覆盖词。Amazon 美国站通用范围为 200–249 UTF-8 字节，以 250 字节阈值计算 80%，并保持严格少于 250。包袋 235–249 字节是相关词充足时的写作目标。 [Amazon 搜索词要求](https://sellercentral.amazon.com/help/hub/reference/external/GNYWHX2TP7C8GXHK?itemid=201468410&locale=en-us)

## 运行校验

把最终英文纯文本写入临时 UTF-8 JSON。保留五点正文中的标题短语及分隔符，去除字段名、中文和序号。下面仅演示 JSON 字段结构，样例短文不用于通过长度校验。

```json
{
  "parent_sku": "BAG-001",
  "title": "Convertible Backpack Purse with Zip Front Pocket and Carry Handle",
  "highlight": "A wide zip opening reveals the main compartment, and a removable strap lets you switch from shoulder to backpack carry",
  "bullets": [
    "Material: Verified material details",
    "Organization: Verified pocket details",
    "Carry options: Verified strap details",
    "Closure: Verified closure details",
    "Everyday use: Suitable everyday occasions"
  ],
  "description": "A short description with verified common specifications and color choices",
  "search_terms": "rucksack daypack"
}
```

PowerShell 中把下列路径替换为实际值。现有包袋命令保持默认。家居品使用 `--profile home-decor`，无需填写不适用的 `highlight`。仅改现有标题加 `--title-only`，仍传入未变的全部英文文案，检查新标题引入的重词。新写亮点用完整检查。

```powershell
python '<skill-dir>/scripts/check_listing.py' '<temp-dir>/listing.json'
python '<skill-dir>/scripts/check_listing.py' '<temp-dir>/listing.json' --profile home-decor
```

当已核实类目限值时，在 JSON 的可选 `length_limits` 中传入 `title`、`highlight`、`description`、`bullet`（每条）、`bullets_total`（合计）或 `search_terms` 对应的对象，如 `{"maximum": 1000, "unit": "characters"}`。单位可为 `characters` 或 `utf8_bytes`，排除型上限可用 `"exclusive": true`。仅填写有可追溯依据的上限。

返回码 `0` 表示结构和已提供的硬性检查通过，另查 `warnings`。返回码 `1` 为硬性错误，`2` 为 JSON 读取失败。每个字段分别报告字符数和 UTF-8 字节数。重复和前台重合比较确切英文词元，合理变形、品牌、实际商品事实、变体适用性及类目规则由人工核对。脚本校验每父 SKU 仅一个 `title`，Excel 的父 SKU 唯一性和逐款 TXT 一致性另查。
