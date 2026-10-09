# 数据契约与来源说明

## 通用结果约定

所有 MCP 工具返回结构化 JSON，包含：

- `status`: `ok`、`partial` 或 `error`
- `warnings`: 可机器读取的降级原因
- `source` 或 `sources`: URL、provider、抓取时间

业务错误使用结构化 `error` 状态，避免把可预期的上游失败变成 MCP 传输失败。

## 新闻

主键采用去除 fragment 和常见跟踪参数后的 canonical URL。只保留具有可解析发布时间、且发布时间不早于 `now - days` 的条目。相同 canonical URL 只保留一条。

Pilbara/Pilgangoora 查询会额外读取 PLS 在 ASX 的官方公司公告表。ASX 公告使用澳大利亚悉尼时区解析，再统一转换为 UTC；公告标题、日期和原始链接进入结果。需要用户确认使用条款的公告正文不会被程序自动确认或误当正文。

## 资源表

记录主键为 `(category, page, ore_quantity, grade)`。字段包括分类、矿石量与单位、品位与单位、金属量与单位、commodity、页码、confidence、evidence。来源只披露金属量总数时，矿石量和品位保持 `null`，不会反推。

解析器不会把缺失或不确定的金属量推导出来；列映射不可靠时返回 `null` 并写入 warning。报告标准从正文识别，JORC 不能被当成 NI 43-101。

内置项目允许使用已对照原始链接核验的结构化快照，并在结果中明确标出核验日期和未实时下载原文。Oyu Tolgoi 保留 S-K 1300 的 Measured & Indicated / Inferred 分类；三山岛保留公开披露中的资源量总量 / 储量总量分类，不把两者混写。

多金属矿允许同一资源分类按不同矿种分别记录。Kainantu 的黄金与铜记录均来自 Kora + Judd 汇总表，报告中明确排除的矿床不会并入总量。价格图只使用登记的主商品代理，并在报告中说明不能代表其他伴生金属。

## 行情

基础商品使用 Nasdaq historical API 可访问的公开基金代理：锂 `LIT`、黄金 `GLD`、铜 `CPER`、白银 `SLV`、铝/基本金属 `DBB`。未预置商品会查询 Nasdaq autocomplete，只接受名称覆盖全部商品关键词、资产类型为 ETF、且名称不含 Ultra/2x/3x/Inverse/Short 的候选；随后再抓取历史收盘数据。没有合格候选时返回结构化 error，不回退到单一矿业公司股票。结果返回 symbol、currency、unit、实际 provider、`is_proxy` 和动态发现告警。当请求日期为非交易日时，返回此前最近收盘并告警。

默认映射中的公开行情都是代理，不代表官方 LME 结算价。生产部署应替换为持牌行情源，同时保留现有返回契约和来源字段。

趋势图使用工具返回的日期和收盘点确定性生成 SVG。图题、纵轴和说明会保留 instrument、unit 与代理口径，避免把基金代理走势误称为矿产品现货价格。
