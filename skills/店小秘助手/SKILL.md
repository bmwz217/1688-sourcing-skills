---
name: 店小秘助手
description: 通过现有云端店小秘接口处理 Amazon 店铺运营。用于查多店铺订单量、订单明细、在线 SKU 和最近上架，或按请求同步订单、导出带图订单表、下载面单、移入待打单、发货和逐单回查。也可通过已授权的店小秘商家会话补填欧盟 PID、申请运单号和虚拟发货。
---

# 店小秘助手

使用现有店小秘服务实时查询 Amazon 店铺。Agent 接口前缀为 `https://zhengxihuan.com/dianxiaomi/agent`，普通查询无需 API 密钥。可按当前 [OpenAPI 定义](https://zhengxihuan.com/dianxiaomi/agent/openapi.json) 导入工具。

## 查询订单和商品

| 用户要查 | Agent API |
| --- | --- |
| 店铺列表 | `GET /v1/stores`，用表格里的自定义店铺名匹配返回的 `name`，取对应 `key` |
| 多店铺订单量 | `GET /v1/orders/count?period=month`，时段支持 `today`、`yesterday`、`week`、`month`。指定日期时改用 `start_date` 和 `end_date`（均包含当天） |
| 在线 SKU 总量、列表或明细 | `GET /v1/stores/{key}/online-skus`，只要数量时传 `limit=1` 并取 `total`。查一个子 SKU 传 `sku` |
| 最近上架 | `GET /v1/stores/{key}/latest-listing`，返回当前在线商品中的最新记录及查询覆盖情况 |

- 未指定店铺的统计覆盖所有启用店铺。指定多店时重复传 `store_key`。订单数按北京时间下单日期、在各店内按 Amazon 订单号去重。
- 订单统计 `complete` 时使用 `total`。遇到 `partial`，列出已查出的单量和失败店铺。遇到 `failed`，报告接口返回的失败原因。查不到的店铺单量不能当 0。
- SKU 列表按 `next_cursor` 翻页，沿用同样的店铺和筛选条件。查询最近上架时，`partial` 表示只确认了所返回的最新可见记录，有记录时可用 `latest.listed_at` 和 `observed_days_since`，说明有缺失或抽样。只在结果完整时使用 `days_since` 作确切结论。
- API 明确返回 `retryable: true` 时可重试查询。遇到 `COOKIE_EXPIRED`，提示对应店铺更新 Cookie。回复用用户的店铺名和中文状态，直接给结果。用户问最近上架时给上架日期即可。

订单状态明细、商品图、同步、表格、面单及订单操作见 [操作参考](参考资料/操作参考.md)。仅在用户要求执行订单操作时调用对应写入接口。

## 欧盟 PID、申请运单号和虚拟发货

1. 通过有目标店铺权限的店小秘商家会话核对订单、包裹、商品和指定物流渠道。在订单详情的「报关信息」编辑对应商品。M-PID 可用该商品的订单 SKU。用户确认同一 SKU 也是该商品的非标制造商 PID 时，NS-PID 可填同值。选择仅保存当前订单，保留其他报关字段，并回查 PID 已保存。
2. 在「运单号申请 → 申请失败」按指定物流渠道重新获取该包裹的运单号。「正在申请」时回查同一包裹，避免重复提交。确认申请成功并取得运单号后，按授权从「运单号申请」页提交「虚拟发货」。以逐单平台提交状态 `commitPlatformStatus=success` 确认结果。
