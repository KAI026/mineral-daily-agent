# 系统架构与设计决策

## 目标

系统接收矿权简报请求，通过三个独立 MCP Server 获取新闻、技术报告资源量和市场价格，最后生成带来源约束的 Markdown。任何上游失败都只降级对应章节，不允许用猜测补齐字段。

## 数据流

```text
用户请求
  │
  ▼
Web UI / CLI
  │
  ▼
MineralDailyOrchestrator
  ├─ plan：识别项目、商品、报告 URL 和截止日
  ├─ act：并发调用三个 MCP Server
  │    ├─ mining-news-mcp：RSS + PLS ASX 公告
  │    ├─ mineral-pdf-mcp：NI 43-101 / JORC 表格解析
  │    └─ lme-price-mcp：明确 instrument 和 proxy 的价格序列
  ├─ observe：校验 status、warnings、来源和记录数量
  └─ render：Markdown + 独立 SVG 价格图
```

## 组件边界

- `servers/` 只暴露 MCP 工具和 stdio 入口，不承载业务规则。
- `services/` 负责上游访问、解析、校验、来源元数据和结构化错误。
- `agent/mcp_client.py` 管理 MCP 生命周期、超时和结构化响应解包。
- `agent/orchestrator.py` 负责 Plan-Act-Observe、并行调用、降级和报告组合。
- `chart.py` 把已经验证的价格点转成无外部依赖的 SVG，不访问网络。
- `web.py` 提供本地输入界面和 JSON API，只展示 Agent 结果，不绕过编排器直接访问数据源。
- `web_assets/` 负责提示词输入、三个 MCP 状态、执行轨迹、Markdown 预览和下载。

## 正确性决策

1. 新闻必须有可解析发布日期并位于查询窗口，未知日期和旧闻不进入简报。
2. ASX 悉尼时间统一转 UTC；需要条款确认的公告正文不会自动代用户确认。
3. PDF 解析保留物理页码、证据文本和置信度；JORC 不会重标为 NI 43-101。
4. Indicated 和 Inferred 是资源量分类，不写成 Proven 或 Probable 储量。
5. 免费行情返回 instrument、provider、unit 和 `is_proxy`；LIT/GLD/CPER/SLV/DBB 等公开基金代理不冒充矿产品现货或 LME 结算价。
6. `--as-of` 同时约束新闻和价格窗口，便于复现某一截止日的结果。

## 降级策略

| 故障 | 行为 |
|---|---|
| 单个新闻源不可用 | 使用其他来源并在风险提示中说明 |
| 没有窗口内新闻 | 保留空新闻章节，不使用旧闻填充 |
| PDF 下载或解析失败 | 资源字段保持空缺并保留原始错误 |
| 报告标准不匹配 | 返回数据但标记 `partial`，禁止错误改名 |
| 行情缺失 | 使用请求日以前最近收盘并明确日期 |
| 图表无法生成 | 保留价格文字和指标表，不影响主报告 |

## 安全边界

所有外部 URL 必须使用 HTTPS，拒绝内嵌凭据、私网地址和跳转到私网的目标。RSS、文章和 PDF 都有限制大小，PDF 限制为 600 页。MCP 工具调用、DNS 和 HTTP 请求分别设置超时。
