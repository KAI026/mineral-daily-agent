"use strict";

const form = document.querySelector("#briefing-form");
const promptInput = document.querySelector("#prompt");
const asOfInput = document.querySelector("#as-of");
const generateButton = document.querySelector("#generate-button");
const buttonLabel = generateButton.querySelector(".button-label");
const formError = document.querySelector("#form-error");
const runState = document.querySelector("#run-state");
const traceList = document.querySelector("#trace-list");
const resultSection = document.querySelector("#result-section");
const resultMeta = document.querySelector("#result-meta");
const reportPreview = document.querySelector("#report-preview");
const markdownSource = document.querySelector("#markdown-source");
const chartPanel = document.querySelector("#chart-panel");
const chartFrame = document.querySelector("#chart-frame");
const copyButton = document.querySelector("#copy-button");
const downloadButton = document.querySelector("#download-button");

let latestMarkdown = "";

function localIsoDate() {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60_000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 10);
}

asOfInput.value = localIsoDate();
asOfInput.max = localIsoDate();

function setRunState(label, state) {
  runState.textContent = label;
  runState.className = `run-badge ${state}`;
}

function setServersRunning() {
  document.querySelectorAll(".server-card").forEach((card) => {
    const dot = card.querySelector(".status-dot");
    dot.className = "status-dot running";
    dot.title = "调用中";
    card.querySelector("p").textContent = "正在通过 MCP 调用…";
  });
}

function updateServerCards(calls) {
  calls.forEach((call) => {
    const card = document.querySelector(`[data-server="${call.server}"]`);
    if (!card) return;
    const dot = card.querySelector(".status-dot");
    dot.className = `status-dot ${call.status}`;
    dot.title = call.status;
    card.querySelector("p").textContent = call.detail;
  });
}

function renderTrace(trace) {
  traceList.replaceChildren();
  trace.forEach((event) => {
    const item = document.createElement("li");
    const phase = document.createElement("span");
    const message = document.createElement("span");
    phase.className = "trace-phase";
    phase.textContent = event.phase;
    message.className = "trace-message";
    message.textContent = event.message;
    item.append(phase, message);
    traceList.append(item);
  });
}

function prepareReportLinks() {
  reportPreview.querySelectorAll("a").forEach((link) => {
    link.target = "_blank";
    link.rel = "noopener noreferrer";
  });
}

function setLoading(loading) {
  generateButton.disabled = loading;
  buttonLabel.textContent = loading ? "Agent 正在生成…" : "生成矿权日报";
}

function showError(message) {
  formError.textContent = message;
  formError.hidden = false;
  setRunState("运行失败", "error");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  formError.hidden = true;
  resultSection.hidden = true;
  setLoading(true);
  setRunState("Agent 运行中", "running");
  setServersRunning();
  traceList.innerHTML = '<li class="trace-empty">正在规划任务并调用 MCP Server…</li>';

  const payload = {
    prompt: promptInput.value,
    as_of: asOfInput.value,
  };

  try {
    const response = await fetch("/api/briefings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "生成失败，请重试");

    latestMarkdown = data.markdown;
    updateServerCards(data.mcp_calls);
    renderTrace(data.trace);
    reportPreview.innerHTML = data.html;
    prepareReportLinks();
    markdownSource.textContent = data.markdown;
    resultMeta.textContent = `${data.topic} · ${data.degraded ? "存在降级，详见风险提示" : "全部数据源正常"}`;

    if (data.chart_svg) {
      chartFrame.innerHTML = data.chart_svg;
      chartPanel.hidden = false;
    } else {
      chartFrame.replaceChildren();
      chartPanel.hidden = true;
    }

    resultSection.hidden = false;
    setRunState(data.degraded ? "完成 · 存在降级" : "生成完成", data.degraded ? "degraded" : "complete");
    resultSection.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    showError(error instanceof Error ? error.message : "生成失败，请重试");
  } finally {
    setLoading(false);
  }
});

copyButton.addEventListener("click", async () => {
  if (!latestMarkdown) return;
  await navigator.clipboard.writeText(latestMarkdown);
  copyButton.textContent = "已复制";
  window.setTimeout(() => { copyButton.textContent = "复制 Markdown"; }, 1500);
});

downloadButton.addEventListener("click", () => {
  if (!latestMarkdown) return;
  const blob = new Blob([latestMarkdown], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `mineral-briefing-${asOfInput.value || localIsoDate()}.md`;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
});
