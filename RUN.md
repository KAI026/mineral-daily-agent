# 矿权日报 Agent 运行说明

下面按顺序操作即可从启动项目到生成完整简报。

## 1. 准备环境

需要安装并启动 Docker Desktop。左下角应显示绿色的 `Engine running`。

不需要安装 Python，也不需要 API Key。

## 2. 打开 PowerShell

按 Windows 键，搜索并打开“Windows PowerShell”。

进入项目目录：

```powershell
Set-Location -LiteralPath 'E:\KAI\简历\凌云智矿\mineral-daily-agent'
```

确认目录正确：

```powershell
Get-ChildItem README.md, RUN.md, docker-compose.yml, Dockerfile
```

不要把两条 `cd` 命令粘在同一行，也不要复制 PowerShell 前面的 `(base) PS C:\...>` 提示符。

## 3. 检查 Docker

```powershell
docker version
docker compose version
```

如果无法连接 Docker Engine，等待 Docker Desktop 显示 `Engine running` 后重试。

## 4. 启动网页

```powershell
docker compose up --build web
```

第一次启动需要下载镜像和依赖。终端自动出现下面这行日志才表示启动成功；这行是输出内容，不要手工输入：

```text
Uvicorn running on http://0.0.0.0:8000
```

保持 PowerShell 窗口打开。下面是浏览器地址，不是 PowerShell 命令；请复制到 Edge 或 Chrome 的地址栏：

```text
http://127.0.0.1:8000
```

## 5. 生成简报

### Pilbara 锂矿

输入：

```text
给我生成一份关于 Pilbara 锂矿的今日简报
```

截止日保持今天或选择历史日期，然后点击“生成矿权日报”。

### Fenelon 金矿

输入：

```text
给我生成一份关于 Fenelon 金矿的今日简报
```

以下四个项目已登记可靠披露、商品和页码，可直接生成完整简报：

- Pilbara / Pilgangoora 锂矿；
- Fenelon 金矿；
- Oyu Tolgoi 铜金矿；
- 三山岛金矿。
- Kainantu 金铜矿。

Kainantu 示例：

```text
生成 Kainantu 金铜矿的今日简报
```

### 其他矿权

只需输入包含矿山名和矿种的一句话，例如“生成 Kambalda 镍矿的今日简报”，然后点击生成。系统会自动搜索项目新闻和可验证的行情代理。未内置技术报告的项目仍会生成新闻、价格和风险分析，资源量章节会明确显示不可用。

页面完成后会显示：

- 3 个 MCP Server 的状态；
- Plan / Act / Observe / Render 执行轨迹；
- 新闻摘要；
- 资源量表格；
- 价格折线图；
- 风险提示和来源链接；
- Markdown 复制与下载按钮。

## 6. 停止和再次启动

停止服务：在运行日志的 PowerShell 中按 `Ctrl+C`，然后执行：

```powershell
docker compose down
```

下次直接启动：

```powershell
docker compose up web
```

代码修改后重新构建：

```powershell
docker compose up --build web
```

## 7. 直接生成固定示例

不使用网页时，可运行：

```powershell
docker compose run --build --rm agent
```

生成结果：

```text
output\pilbara-daily.md
output\assets\pilbara-daily-price-trend.svg
```

## 8. 常见问题

### 页面打不开

```powershell
docker compose ps
docker compose logs --tail 100 web
```

确认 `web` 状态为 `Up`，端口包含 `8000->8000`。

### 页面还是旧版本

重新构建并强制刷新浏览器：

```powershell
docker compose down
docker compose up --build web
```

浏览器按 `Ctrl+F5`。

### Mining.com 返回 HTTP 403

这是网站拒绝自动抓取，不是 Docker 故障。系统会继续使用其他新闻源，并把 403 写入风险提示。

### 新闻为空

系统先查近 30 天，没有结果时自动扩大到 180 天并在风险提示中说明。如果仍为空，表示没有找到日期可验证且与项目名称匹配的新闻；系统不会用不相关旧闻填充。

### 其他矿没有资源量

说明该项目尚未登记可信披露。系统仍会生成新闻和价格，但不会猜测资源数据。若要支持新项目，需要在项目登记表中加入官方报告 URL、对应页码和商品类型。

### 查看完整日志

```powershell
docker compose logs --tail 200 web
```

## 9. 健康检查

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

正常结果应包含：

```text
status  : ok
service : mineral-daily-web
```

## 10. 本地开发与测试（可选）

需要 Python 3.12 和 uv：

```powershell
uv sync --frozen --extra dev
uv run ruff check .
uv run mypy src
uv run pytest --cov --cov-report=term-missing
```

完成以上步骤后，项目即可用于演示或上传 GitHub。
