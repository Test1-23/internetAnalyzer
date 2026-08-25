"use strict";

const MAX_POINTS = 300;
const fmtSpeed = (bps) => {
  if (bps == null) return "-";
  if (bps >= 1048576) return (bps / 1048576).toFixed(2) + " MB/s";
  if (bps >= 1024) return (bps / 1024).toFixed(1) + " KB/s";
  return bps.toFixed(0) + " B/s";
};
const fmtTime = (ts) => new Date(ts).toLocaleTimeString("zh-CN", { hour12: false });
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

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
      { label: "下载", data: down, borderColor: "#38bdf8", backgroundColor: "rgba(56,189,248,0.12)", fill: true, borderWidth: 1.5, pointRadius: 0, tension: 0.3 },
      { label: "上传", data: up, borderColor: "#a78bfa", backgroundColor: "rgba(167,139,250,0.10)", fill: true, borderWidth: 1.5, pointRadius: 0, tension: 0.3 },
    ],
  },
  options: {
    animation: false, responsive: true, maintainAspectRatio: false,
    scales: { x: { ticks: { maxTicksLimit: 8 } }, y: { ticks: { callback: v => fmtSpeed(v) }, beginAtZero: true } },
    plugins: { legend: { labels: { boxWidth: 10, boxHeight: 10 } } },
  },
});

const latencyChart = new Chart($("latencyChart"), {
  type: "line",
  data: {
    labels,
    datasets: [
      { label: "延迟(ms)", data: lat, borderColor: "#22c55e", backgroundColor: "rgba(34,197,94,0.10)", fill: true, borderWidth: 1.5, pointRadius: 0, tension: 0.3 },
      { label: "抖动(ms)", data: jit, borderColor: "#f59e0b", backgroundColor: "transparent", borderWidth: 1, pointRadius: 0, tension: 0.3, borderDash: [4, 4] },
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
      backgroundColor: ctx => (ctx.raw >= 10 ? "#ef4444" : ctx.raw > 0 ? "#f59e0b" : "#334155"),
      borderWidth: 0, borderRadius: 2, barPercentage: 0.9,
    }],
  },
  options: {
    animation: false, responsive: true, maintainAspectRatio: false,
    scales: { x: { ticks: { maxTicksLimit: 8 } }, y: { beginAtZero: true, suggestedMax: 100, ticks: { callback: v => v + "%" } } },
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

let lastNetInfo = null;

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
  const gwLine = [];
  if (s.gateway_ms != null) gwLine.push("网关 " + s.gateway_ms + "ms");
  if (s.dns_ok === true) gwLine.push("DNS " + s.dns_ms + "ms");
  else if (s.dns_ok === false) gwLine.push("DNS 异常");
  $("dnsSub").textContent = gwLine.join(" · ") || "-";
  $("dnsSub").className = s.dns_ok === false ? "sub text-red" : "sub";

  $("cpuCard").textContent = s.cpu != null ? s.cpu.toFixed(0) + "%" : "-";
  $("cpuCard").className = s.cpu > 85 ? "value text-red" : "value";
  $("memCard").textContent = s.mem != null ? "内存 " + s.mem.toFixed(0) + "%" : "-";

  const pill = $("statusPill");
  if (s.status === "OK") { pill.className = "pill ok"; pill.textContent = "连接正常"; }
  else if (s.status === "DEGRADED") { pill.className = "pill warn"; pill.textContent = "网络降级"; }
  else { pill.className = "pill bad"; pill.textContent = "网络中断"; }
  $("uptime").textContent = fmtUptimeShort(s.uptime_s);

  if (s.public_ip) $("pubIpChip").textContent = `公网IP: ${s.public_ip}${s.isp ? " · " + s.isp : ""}`;
  else $("pubIpChip").textContent = "公网IP: 获取中…";
  $("proxyChip").classList.toggle("hidden", !s.proxy);
  $("errChip").classList.toggle("hidden", !(s.nic_err_rate > 5));

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

const fmtUptimeShort = (s) => {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}小时${m}分` : `${m}分${s % 60}秒`;
};

function renderAnalysis(report) {
  $("summaryText").textContent = report.summary || "正在采集数据…";
  const sug = $("suggestions");
  sug.innerHTML = "";
  (report.suggestions || []).forEach(t => {
    const li = document.createElement("li"); li.textContent = t; sug.appendChild(li);
  });

  const ul = $("issues");
  ul.innerHTML = "";
  const events = report.events || [];
  if (!events.length) {
    ul.innerHTML = '<li class="empty">暂无异样</li>';
    return;
  }
  const sevName = { high: "严重", medium: "警告", low: "提示" };
  for (const e of events) {
    const li = document.createElement("li");
    li.className = e.severity + (e.active ? "" : " inactive");
    li.innerHTML = `<div class="issue-head">
        <span class="badge ${e.severity}">${e.active ? (sevName[e.severity] || e.severity) : "已恢复"}</span>
        <span class="t">${esc(e.title)}</span>
        <span class="time">${fmtTime(e.last_seen * 1000)}</span></div>
      <div class="issue-detail">${esc(e.detail)}${e.count > 1 ? `（触发 ${e.count} 次采样）` : ""}</div>`;
    ul.appendChild(li);
  }
}

/* ---------- 网络详情 ---------- */
async function loadNetInfo() {
  try {
    const r = await fetch("/api/netinfo");
    lastNetInfo = await r.json();
    renderIfTable(lastNetInfo);
    renderEnvInfo(lastNetInfo);
  } catch (e) { /* retry */ }
}

function renderIfTable(info) {
  const tb = $("ifTable").querySelector("tbody");
  tb.innerHTML = "";
  for (const itf of info.interfaces || []) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${esc(itf.name)}${itf.virtual ? ' <span class="vtag">代理/VPN?</span>' : ""}</td>
      <td>${(itf.ips || []).map(i => esc(i.addr) + (i.netmask ? "/" + esc(i.netmask) : "")).join("<br>") || "-"}</td>
      <td>${esc(itf.gateway || "-")}</td>
      <td>${esc((itf.dns || []).join(", ") || "-")}</td>
      <td>${esc(itf.mac || "-")}</td>
      <td>${itf.speed_mbps ? itf.speed_mbps + " Mbps" : "-"}</td>
      <td>${itf.mtu || "-"}</td>
      <td>${itf.total_mb} MB</td>
      <td class="${itf.up ? "text-green" : "text-red"}">${itf.up ? "已连接" : "断开"}</td>`;
    tb.appendChild(tr);
  }
}

function renderDnsStats() {
  fetch("/api/dns-stats").then(r => r.json()).then(stats => {
    const box = $("dnsList");
    box.innerHTML = "";
    const entries = Object.entries(stats);
    if (!entries.length) { box.innerHTML = '<p class="note">等待采样…</p>'; return; }
    for (const [server, v] of entries) {
      const pct = v.ok_pct ?? 100;
      const avg = v.avg_ms != null ? v.avg_ms + " ms" : "超时";
      const cls = pct >= 95 ? "fast" : pct >= 50 ? "slow" : "timeout";
      const barW = Math.max(4, Math.min(100, pct));
      const latBarW = v.avg_ms != null ? Math.min(100, v.avg_ms / 5) : 100;
      box.insertAdjacentHTML("beforeend", `
        <div class="dns-item">
          <div class="row-between"><b>${esc(server)}</b><span class="m ${cls}">${avg} · 成功率 ${pct}%</span></div>
          <div class="bar"><i style="width:${barW}%"></i></div>
          <div class="bar amber"><i style="width:${latBarW}%"></i></div>
        </div>`);
    }
  }).catch(() => {});
}

function renderEnvInfo(info) {
  if (!info) return;
  const proxy = info.proxy || {};
  const rows = [
    ["主机名", info.hostname],
    ["公网出口", info.public_ip ? `${info.public_ip.ip}（${info.public_ip.isp || "?"}）` : "获取中…"],
    ["归属地", info.public_ip?.location || "-"],
    ["AS 信息", info.public_ip?.as || "-"],
    ["系统代理", proxy.enabled ? `开启 ${proxy.server || ""}` : "关闭"],
    ["PAC 脚本", proxy.pac || "-"],
    ["虚拟网卡", (info.virtual_adapters || []).join(", ") || "无"],
    ["hosts 文件", info.hosts ? `${info.hosts.active_entries} 条生效记录 / ${info.hosts.size}B` : "-"],
  ];
  $("envInfo").innerHTML = rows.map(([k, v]) =>
    `<div class="kv"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("");
}

/* ---------- 路由路径 ---------- */
const SEG_NAME = { gateway: "家庭网关", private: "内网", carrier_nat: "运营商NAT", public: "公网骨干" };

async function loadTrace() {
  try {
    const r = await fetch("/api/traceroute");
    const d = await r.json();
    renderTrace(d.latest);
    renderTraceHistory(d.history || []);
  } catch (e) { /* retry */ }
}

function renderTrace(t) {
  const tb = $("traceTable").querySelector("tbody");
  tb.innerHTML = "";
  if (!t) {
    $("traceMeta").textContent = "· 尚未探测，点击右上角按钮立即开始";
    $("traceVerdict").textContent = "";
    return;
  }
  $("traceMeta").textContent =
    `· ${new Date(t.ts * 1000).toLocaleString("zh-CN", { hour12: false })} → ${t.target}` +
    (t.changed ? " · ⚠ 与上次相比路径已变更" : "");
  for (const h of t.hops) {
    const times = h.times.map(x => x != null ? (x < 1 ? "<1" : x.toFixed(0)) : "*").join("  ");
    const tr = document.createElement("tr");
    const lossCls = h.loss >= 2 ? "text-red" : h.loss > 0 ? "text-amber" : "";
    tr.innerHTML = `<td>${h.hop}</td><td>${h.hop === 1 ? '<span class="vtag">网关?</span> ' : ""}${esc(h.ip)}</td>
      <td class="mono">${times}</td><td class="${lossCls}">${h.loss}/3</td>
      <td>${t.first_loss_hop === h.hop ? `<b class="text-red">首丢跳</b>` : ""}</td>`;
    tb.appendChild(tr);
  }
  const v = $("traceVerdict");
  if (t.first_loss_hop) {
    v.innerHTML = `⚠ 首丢跳定位：<b>第 ${t.first_loss_hop} 跳（${esc(t.segment)}）</b>。该跳之后节点同样丢包，责任段为「${esc(t.segment)}」。`;
    v.className = "verdict bad";
  } else {
    v.textContent = "✓ 全程未发现持续丢包的节点，路径质量良好。";
    v.className = "verdict good";
  }
}

function renderTraceHistory(list) {
  const ul = $("traceHistory");
  ul.innerHTML = "";
  if (!list.length) { ul.innerHTML = '<li class="empty">暂无历史</li>'; return; }
  for (const h of list.slice(0, 40)) {
    ul.insertAdjacentHTML("beforeend", `<li>
      ${fmtTime(h.ts * 1000)} → ${esc(h.target)}
      签名 ${esc(h.sig)} · ${h.changed ? '<b class="text-amber">已变更</b>' : "无变化"}
      ${h.first_loss_hop ? `· <span class="text-red">首丢跳 #${h.first_loss_hop}（${esc(h.segment)}）</span>` : '· <span class="text-green">路径正常</span>'}
    </li>`);
  }
}

$("traceBtn").addEventListener("click", async () => {
  await fetch("/api/traceroute/run", { method: "POST" });
  $("traceBtn").disabled = true;
  setTimeout(() => { $("traceBtn").disabled = false; loadTrace(); }, 30000);
});

/* ---------- 本机进程 ---------- */
async function loadProcs() {
  try {
    const r = await fetch("/api/connections");
    const d = await r.json();
    const tb = $("procTable").querySelector("tbody");
    tb.innerHTML = "";
    $("procUpdated").textContent = "更新于 " + fmtTime(Date.now());
    for (const p of d.processes || []) {
      if (p.estab === 0 && p.listen === 0) continue;
      const stormCls = p.estab >= 150 ? "text-red" : p.estab >= 50 ? "text-amber" : "";
      tb.insertAdjacentHTML("beforeend", `<tr>
        <td>${esc(p.proc)}</td><td>${p.pid}</td>
        <td class="${stormCls}"><b>${p.estab}</b></td><td>${p.listen}</td>
        <td class="${p.new_per_min >= 60 ? "text-red" : ""}">${p.new_per_min || 0}</td>
        <td class="mono small">${esc(p.top_remotes.join(", ")) || "-"}</td></tr>`);
    }
    const sl = $("stormList");
    sl.innerHTML = "";
    if (!(d.storms || []).length) { sl.innerHTML = '<li class="empty">暂无</li>'; }
    else for (const s of d.storms.slice().reverse()) {
      sl.insertAdjacentHTML("beforeend", `<li>
        <span class="text-red">⚡</span> ${fmtTime(s.ts * 1000)} 进程 <b>${esc(s.proc)}</b>
        活跃连接 ${s.estab} · 新建/分 ${s.new_per_min}</li>`);
    }
  } catch (e) { /* retry */ }
}

/* ---------- 标签页 ---------- */
const tabTimers = {
  netdetail: [[loadNetInfo, 30000], [renderDnsStats, 5000]],
  path: [[loadTrace, 10000]],
  procs: [[loadProcs, 3000]],
};
let runningTimers = [];

function clearTabTimers() {
  runningTimers.forEach(clearInterval);
  runningTimers = [];
}

function activateTab(name) {
  document.querySelectorAll(".tab").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab-page").forEach(p => p.classList.toggle("active", p.id === "tab-" + name));
  clearTabTimers();
  (tabTimers[name] || []).forEach(([fn, ms]) => {
    fn();
    runningTimers.push(setInterval(fn, ms));
  });
}

document.querySelectorAll(".tab").forEach(btn =>
  btn.addEventListener("click", () => activateTab(btn.dataset.tab)));

/* ---------- WebSocket ---------- */
let ws = null;
function connectWS() {
  ws = new WebSocket("ws://" + location.host + "/ws");
  ws.onmessage = ev => {
    const s = JSON.parse(ev.data);
    if (!s.ts) return;
    renderCards(s);
    pushPoint(s.ts, s.down_bps, s.up_bps, s.latency, s.jitter, s.loss_pct);
  };
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
  } catch (e) { /* retry */ }
}

function loadAnalysis() {
  fetch("/api/analysis").then(r => r.json()).then(renderAnalysis).catch(() => {});
}

connectWS();
loadHistory();
loadAnalysis();
setInterval(loadAnalysis, 5000);
setInterval(loadHistory, 30000);
setInterval(loadNetInfo, 60000);
activateTab("overview");
