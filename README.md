# 矿权日报 Agent

这是面试题“矿权日报 Agent”的完整实现。用户输入一句需求，例如：

```text
给我生成一份关于 Pilbara 锂矿的今日简报
```

系统会通过 3 个 MCP Server 获取新闻、技术报告和价格数据，再由客户端 Agent 完成 Plan → Act → Observe → Render 编排，输出包含以下内容的 Markdown 简报：

- 新闻摘要与原文链接；
- 矿产资源量、报告标准、页码和证据；
- 价格走势、统计数据和折线图；
- 风险提示与来源索引。

推荐使用 Docker Desktop + 浏览器运行，不需要 Python、模型 API Key 或手工配置容器。完整操作见 [RUN.md](RUN.md)。

## 快速开始

确认 Docker Desktop 显示 `Engine running`，在 PowerShell 中执行：

```powershell
Set-Location -LiteralPath 'E:\KAI\简历\凌云智矿\mineral-daily-agent'
docker compose up --build web
```

浏览器打开：

```text
http://127.0.0.1:8000
```

只需输入一句需求并点击“生成矿权日报”。截止日可选，默认今天；数据窗口和商品类型由系统自动处理。

## 支持的矿权

### 内置项目

以下项目只需输入提示词：

| 项目 | 可识别名称 | 价格代理 | 技术报告 |
|---|---|---|---|
| Pilgangoora 锂矿 | Pilbara、Pilgangoora、皮尔巴拉、Pilbara Minerals | LIT | JORC 2012，第 2 页 |
| Fenelon 金矿 | Fenelon、Wallbridge、芬尼隆 | GLD | NI 43-101，第 229 页 |
| Oyu Tolgoi 铜金矿 | Oyu Tolgoi、奥尤陶勒盖 | CPER | S-K 1300，第 17 页 |
| 三山岛金矿 | 三山岛、Sanshandao | GLD | 上交所公开披露，第 7 页 |
| Kainantu 金铜矿 | Kainantu、凯南图、K92 Mining | GLD | NI 43-101，第 7 页 |

Fenelon 示例：

```text
给我生成一份关于 Fenelon 金矿的今日简报
```

### 其他矿权

直接输入“生成某某矿的今日简报”即可。陌生项目会走动态流程：

1. 从提示词识别矿山和矿种；
2. 爬取 Google News RSS、Mining.com RSS 等公开新闻源并执行项目名与日期校验；
3. 对未预置的矿种调用 Nasdaq 公开搜索接口，寻找名称明确且非杠杆、非反向的 ETF；
4. 抓取该工具的历史收盘数据并生成折线图；
5. 根据新闻事件词、价格变化、资源分类和数据缺口生成风险分析。

找不到合格行情工具或可信报告时，对应章节会弃答并解释原因，不会随意选择股票或编造资源量。

目前支持的价格代理：

| 商品 | 代理代码 |
|---|---|
| 锂 | LIT |
| 黄金 | GLD |
| 铜 | CPER |
| 白银 | SLV |
| 铝/基本金属 | DBB |

这些都是公开基金价格代理，不代表矿产品现货或官方 LME 结算价，报告会明确说明。

此外可识别镍、铀、钴、稀土、铁矿石、铂、钯、锌、铅、锡、锰、石墨、磷矿和钾盐。系统会实时搜索合格 ETF；例如镍可发现 `NIKL`。某些矿种没有直接且非杠杆的公开工具，此时价格章节保持为空。

## 是否使用 AI API

默认不调用 OpenAI、Claude 或其他模型 API，也不需要 API Key。这里的 Agent 是自写的确定性编排器：理解项目与矿种、并行调用 MCP、检查来源和口径、在失败时降级，最后生成 Markdown。原题第 2 题允许“LangGraph / 自写 ReAct / 你的方案”，没有要求必须调用大模型。事实只来自 MCP 返回的新闻、报告和行情；模型 API 即使接入，也不能代替可信来源。

## 系统架构

```text
Web UI / CLI
      │
      ▼
MineralDailyOrchestrator
      ├─ mining-news-mcp ── 新闻搜索与正文提取
      ├─ mineral-pdf-mcp ── 技术报告资源表解析
      └─ lme-price-mcp ──── 价格与趋势数据
      │
      ▼
Markdown 简报 + SVG 折线图 + 来源
```

Agent 的执行流程：

1. Plan：识别项目、商品、新闻搜索词和技术报告；
2. Act：并行调用 3 个 MCP Server；
3. Observe：校验结果、告警和来源；
4. Render：生成 Markdown、风险提示和折线图。

核心代码：

- `src/mineral_daily/agent/`：MCP client、Agent 编排和 CLI；
- `src/mineral_daily/servers/`：3 个 MCP Server；
- `src/mineral_daily/services/`：新闻、PDF 和价格业务逻辑；
- `src/mineral_daily/web.py`：网页 API；
- `src/mineral_daily/web_assets/`：网页界面；
- `tests/`：自动化测试。

## 运行方式

### 网页模式

```powershell
docker compose up --build web
```

打开 `http://127.0.0.1:8000`。

### 固定 Pilbara 示例

```powershell
docker compose run --build --rm agent
```

输出文件：

```text
output/pilbara-daily.md
output/assets/pilbara-daily-price-trend.svg
```

### 本地 Python 模式

需要 Python 3.12 和 uv：

```powershell
uv sync --frozen --extra dev
uv run mineral-daily-web
```

命令行生成自定义矿权：

```powershell
uv run mineral-daily-agent `
  --config mcp-config.local.json `
  --prompt '给我生成一份关于 Example 金矿的今日简报' `
  --commodity gold `
  --resource-pdf 'https://example.com/report.pdf' `
  --resource-pages '120,121' `
  --output output/example.md `
  --trace
```

## Claude Desktop / Cursor 配置

先构建镜像：

```powershell
docker compose build
```

然后把 `mcp-config.json` 中的 `mcpServers` 配置复制到 Claude Desktop 或 Cursor。配置会通过 Docker 分别启动 3 个 stdio MCP Server。

## 测试

```powershell
uv run ruff check .
uv run mypy src
uv run pytest --cov --cov-report=term-missing
```

当前验证结果：62 项测试通过，覆盖率 76.17%。

## 正确性与降级策略

- 新闻必须有明确日期并位于指定窗口内；
- 默认 30 天无匹配新闻时自动扩大到 180 天，并明确标注这一降级；
- 新闻必须命中项目身份词，防止把同一公司的泛新闻误当成矿山新闻；
- 动态行情发现只接受名称匹配的 ETF，并排除杠杆、反向和做空工具；
- 风险结论只基于已检索新闻、实际价格点、资源分类和数据缺口；
- JORC 和 NI 43-101 不会混用；
- 资源量与储量分类按来源保留，Indicated/Inferred 不会被写成 Proven/Probable；
- PDF 数据保留页码、证据和置信度；
- 任一数据源失败时只降级对应章节，不编造缺失数据；
- Mining.com 返回 403 时会继续使用其他新闻源，并在风险提示中保留告警；
- 外部 URL 仅允许 HTTPS，并限制下载大小、页数和请求时间。

## 面试要求对照

| 要求 | 实现 |
|---|---|
| 输入提示词并输出 Markdown 简报 | 网页输入框、CLI、预览和下载 |
| 新闻、资源量、价格、风险和引用 | Agent 输出的完整 Markdown |
| 至少 3 个 MCP Server | 新闻、PDF、价格 3 个独立 Server |
| 1 个客户端 Agent 编排 | `MineralDailyOrchestrator` |
| 可视化价格走势 | SVG 折线图 |
| Claude Desktop / Cursor 配置 | `mcp-config.json` |
| Docker Compose 一键运行 | `docker compose up --build web` |

详细设计见 [ARCHITECTURE.md](ARCHITECTURE.md)，数据口径见 [DATA_NOTES.md](DATA_NOTES.md)。
