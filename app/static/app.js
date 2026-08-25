"use strict";

const MAX_POINTS = 300;
const fmtSpeed = (bps) => {
  if (bps == null) return "-";
  if (bps >= 1048576) return (bps / 1048576).toFixed(2) + " MB/s";
  if (bps >= 1024) return (bps / 1024).toFixed(1) + " KB/s";
  return bps.toFixed(0) + " B/s";
};
const fmtMs = (ms) => (ms == null ? "超时" : ms.toFixed(1) + " ms");
const fmtTime = (ts) => new Date(ts).toLocaleTimeString("zh-CN", { hour12: false });
const fmtUptime = (s) => {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}小时${m}分` : `${m}分${s % 60}秒`;
};
const $ = (id) => document.getElementById(id);

const labels = [], down = [], up = [], lat = [], jit = [], loss = [];

function color(ms) {
  if (ms == null) return "timeout";
  if (ms < 100) return "fast";
  if (ms < 300) return "slow";
  return "timeout";
}

Chart.defaults.color = "#94a3b8";
Chart.defaults.borderColor = "rgba(148,163,184,0.15)";
Chart.defaults.font.family = "'Segoe UI','Microsoft YaHei',sans-serif";

const trafficChart = new Chart($("trafficChart"), {
  type: "line",
  data: {
    labels,
    datasets: [
      { label: "下载", data: down, borderColor: "#38bdf8", backgroundColor: "rgba(56,189,248,0.12)",
        fill: true, borderWidth: 1.5, pointRadius: 0, tension: 0.3 },
      { label: "上传", data: up, borderColor: "#a78bfa", backgroundColor: "rgba(167,139,250,0.10)",
        fill: true, borderWidth: 1.5, pointRadius: 0, tension: 0.3 },
    ],
  },
  options: {
    animation: false, responsive: true, maintainAspectRatio: false,
    scales: {
      x: { ticks: { maxTicksLimit: 8 } },
      y: { ticks: { callback: (v) => fmtSpeed(v) }, beginAtZero: true },
    },
    plugins: { legend: { labels: { boxWidth: 10, boxHeight: 10 } } },
  },
});

const latencyChart = new Chart($("latencyChart"), {
  type: "line",
  data: {
    labels,
    datasets: [
      { label: "延迟(ms)", data: lat, borderColor: "#22c55e", backgroundColor: "rgba(34,197,94,0.10)",
        fill: true, borderWidth: 1.5, pointRadius: 0, tension: 0.3 },
      { label: "抖动(ms)", data: jit, borderColor: "#f59e0b", backgroundColor: "transparent",
        borderWidth: 1, pointRadius: 0, tension: 0.3, borderDash: [4, 4] },
    ],
  },
  options: {
    animation: false, responsive: true, maintainAspectRatio: false,
    scales: { x: { ticks: { maxTicksLimit: 8 } }, y: { beginAtZero: true } },
    plugins: { legend: { labels: { boxWidth: 10, boxHeight: 10 } } },
  },
});

const lossChart = new Chart($("lossChart"), {
  type: "bar",
  data: {
    labels,
    datasets: [{
      label: "丢包率(%)", data: loss,
      backgroundColor: (ctx) => (ctx.raw >= 10 ? "#ef4444" : ctx.raw > 0 ? "#f59e0b" : "#334155"),
      borderWidth: 0, borderRadius: 2, barPercentage: 0.9,
    }],
  },
  options: {
    animation: false, responsive: true, maintainAspectRatio: false,
    scales: {
      x: { ticks: { maxTicksLimit: 8 } },
      y: { beginAtZero: true, suggestedMax: 100, ticks: { callback: (v) => v + "%" } },
    },
    plugins: { legend: { display: false } },
  },
});

function pushPoint(ts, d, u, l, j, ls) {
  labels.push(fmtTime(ts));
  down.push(d); up.push(u); lat.push(l); jit.push(j); loss.push(ls);
  if (labels.length > MAX_POINTS) {
    labels.shift(); down.shift(); up.shift(); lat.shift(); jit.shift(); loss.shift();
  }
  trafficChart.update(); latencyChart.update(); lossChart.update();
}

function renderCards(s) {
  $("downSpeed").textContent = fmtSpeed(s.down_bps);
  $("upSpeed").textContent = fmtSpeed(s.up_bps);
  $("downTotal").textContent = "累计 " + s.totals_mb.down.toFixed(1) + " MB";
  $("upTotal").textContent = "累计 " + s.totals_mb.up.toFixed(1) + " MB";
  $("latency").textContent = s.latency != null ? s.latency.toFixed(1) + " ms" : "超时";
  $("latency").className = s.latency == null ? "value text-red" : s.latency > 200 ? "value text-amber" : "value";
  $("jitter").textContent = "抖动 " + s.jitter.toFixed(1) + " ms";
  $("lossPct").textContent = s.loss_pct.toFixed(1) + " %";
  $("lossPct").className = s.loss_pct >= 10 ? "value text-red" : s.loss_pct > 0 ? "value text-amber" : "value";

  let score = 100;
  if (s.loss_pct > 0) score -= Math.min(50, s.loss_pct * 5);
  if (s.latency != null && s.latency > 50) score -= Math.min(50, (s.latency - 50) / 4);
  score = Math.max(0, Math.round(score));
  $("quality").textContent = score + "/100";
  $("quality").className = score >= 85 ? "value text-green" : score >= 60 ? "value text-amber" : "value text-red";
  $("qualityHint").textContent = s.status === "DOWN" ? "网络中断" : score >= 85 ? "状态良好" : "质量下降";

  const wifi = s.wifi;
  if (wifi && wifi.connected) {
    let label = "WiFi";
    if (wifi.signal != null) label += " " + wifi.signal + "%";
    const cls = wifi.signal == null ? "text-blue"
      : wifi.signal >= 60 ? "text-green"
      : wifi.signal >= 40 ? "text-amber" : "text-red";
    $("wifiCard").textContent = label;
    $("wifiCard").className = "value " + cls;
    $("nicCard").textContent = (wifi.ssid ? wifi.ssid + " · " : "") + s.nic;
  } else {
    $("wifiCard").textContent = s.link_up ? "有线/以太网" : "网卡断开";
    $("wifiCard").className = "value " + (s.link_up ? "text-blue" : "text-red");
    $("nicCard").textContent = s.nic;
  }

  $("gateway").textContent = s.gateway || "未检测到";
  $("gatewayMs").textContent = s.gateway_ms != null ? fmtMs(s.gateway_ms) : "无响应";

  $("dns").textContent = s.dns_ok === true ? (s.dns_ms ?? "-") + " ms" : s.dns_ok === false ? "异常" : "检测中";
  $("dns").className = s.dns_ok === false ? "value text-red" : "value";

  const pill = $("statusPill");
  if (s.status === "OK") { pill.className = "pill ok"; pill.textContent = "连接正常"; }
  else if (s.status === "DEGRADED") { pill.className = "pill warn"; pill.textContent = "网络降级"; }
  else { pill.className = "pill bad"; pill.textContent = "网络中断"; }
  $("uptime").textContent = fmtUptime(s.uptime_s);

  const probes = $("probes");
  probes.innerHTML = "";
  for (const p of s.probes) {
    const chip = document.createElement("div");
    chip.className = "chip";
    const n = document.createElement("span"); n.className = "n"; n.textContent = p.name;
    const m = document.createElement("span");
    m.className = "m " + color(p.ms);
    m.textContent = p.ms != null ? p.ms.toFixed(0) + "ms" : "超时";
    chip.append(n, m);
    probes.appendChild(chip);
  }
}

function renderAnalysis(report) {
  $("summaryText").textContent = report.summary || "正在采集数据…";
  const sug = $("suggestions");
  sug.innerHTML = "";
  (report.suggestions || []).forEach((t) => {
    const li = document.createElement("li");
    li.textContent = t;
    sug.appendChild(li);
  });

  const ul = $("issues");
  const events = report.events || [];
  ul.innerHTML = "";
  if (!events.length) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "暂无异样";
    ul.appendChild(li);
    return;
  }
  const sevName = { high: "严重", medium: "警告", low: "提示" };
  for (const e of events) {
    const li = document.createElement("li");
    li.className = e.severity + (e.active ? "" : " inactive");
    const head = document.createElement("div");
    head.className = "issue-head";
    const badge = document.createElement("span");
    badge.className = "badge " + e.severity;
    badge.textContent = e.active ? sevName[e.severity] || e.severity : "已恢复";
    const t = document.createElement("span"); t.className = "t"; t.textContent = e.title;
    const time = document.createElement("span"); time.className = "time";
    time.textContent = new Date(e.last_seen * 1000).toLocaleTimeString("zh-CN", { hour12: false });
    head.append(badge, t, time);
    const detail = document.createElement("div");
    detail.className = "issue-detail";
    detail.textContent = e.detail + (e.count > 1 ? `（触发 ${e.count} 次采样）` : "");
    li.append(head, detail);
    ul.appendChild(li);
  }
}

function onSnapshot(s) {
  if (!s || !s.ts) return;
  renderCards(s);
  pushPoint(s.ts, s.down_bps, s.up_bps, s.latency, s.jitter, s.loss_pct);
}

let ws = null;
function connectWS() {
  ws = new WebSocket("ws://" + location.host + "/ws");
  ws.onmessage = (ev) => onSnapshot(JSON.parse(ev.data));
  ws.onclose = () => setTimeout(connectWS, 2000);
  ws.onerror = () => ws.close();
}

async function loadHistory() {
  try {
    const r = await fetch("/api/history?seconds=600");
    const h = await r.json();
    for (let i = 0; i < h.ts.length; i++) {
      pushPoint(h.ts[i], h.down_bps[i], h.up_bps[i], h.latency[i], h.jitter[i], h.loss_pct[i]);
    }
  } catch (e) { /* 重试 */ }
}

async function loadAnalysis() {
  try {
    const r = await fetch("/api/analysis");
    renderAnalysis(await r.json());
  } catch (e) { /* 重试 */ }
}

connectWS();
loadHistory();
loadAnalysis();
setInterval(loadAnalysis, 5000);
setInterval(loadHistory, 30000);
