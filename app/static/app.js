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
  const chipsHtml = (s.probes || []).map(p => {
    const cls = color(p.ms);
    const val = p.ms != null ? p.ms.toFixed(0) + "ms" : "超时";
    return `<div class="chip"><span class="n">${esc(p.name)}</span><span class="m ${cls}">${val}</span></div>`;
  }).join("");
  if (chipsHtml !== probes.dataset.last) {
    probes.dataset.last = chipsHtml;
    probes.innerHTML = chipsHtml;
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
  const gwIp = lastNetInfo && (lastNetInfo.interfaces || []).find(i => i.gateway)?.gateway;
  for (const h of t.hops) {
    const times = h.times.map(x => x != null ? (x < 1 ? "<1" : x.toFixed(0)) : "*").join("  ");
    const tr = document.createElement("tr");
    const lossCls = h.loss >= 2 ? "text-red" : h.loss > 0 ? "text-amber" : "";
    const isGw = gwIp && h.ip === gwIp;
    tr.innerHTML = `<td>${h.hop}</td><td>${isGw ? '<span class="vtag">网关</span> ' : ""}${esc(h.ip)}</td>
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
        <td class="mono small">${esc((p.top_remotes || []).map(r => r.ip + (r.count > 1 ? "×" + r.count : "")).join(", ")) || "-"}</td></tr>`);
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
  map: [[loadMapData, 5000], [loadConnData, 3000]],
  wifi: [[loadWifiEnv, 15000]],
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

/* ================= 网络地图 ================= */
const geoCache = {};
let lastSnap = null;
let lastGeoReq = 0;
const mapGraph = { nodes: [], edges: [] };

function fitCanvas(cv) {
  if (!cv.dataset.h) cv.dataset.h = cv.getAttribute("height") || "400";
  const cssW = cv.clientWidth || 800;
  const cssH = parseInt(cv.dataset.h, 10);
  const dpr = window.devicePixelRatio || 1;
  const pw = Math.round(cssW * dpr), ph = Math.round(cssH * dpr);
  if (cv.width !== pw || cv.height !== ph) {
    cv.width = pw;
    cv.height = ph;
  }
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w: cssW, h: cssH };
}

const edgeColor = ms => ms == null ? "#ef4444" : ms < 100 ? "#22c55e" : ms < 300 ? "#f59e0b" : "#ef4444";

function mkNode(id, x, y, title, sub, ms) {
  return { id, x, y, title, sub, ms };
}

function buildGraph(trace, dnsStats) {
  const cv = $("mapCanvas");
  const W = cv.clientWidth || 900;
  const H = parseInt(cv.getAttribute("height"), 10) || 400;
  const midY = H * 0.46;
  const nodes = [], edges = [];
  let x = 80;

  const me = mkNode("me", x, midY, "本机", lastSnap ? lastSnap.nic : "");
  nodes.push(me); x += 150;

  let fan = me;
  if (lastSnap && lastSnap.proxy) {
    const px = mkNode("proxy", x, midY, "代理隧道", "Clash TUN");
    nodes.push(px);
    edges.push(mkEdge(me, px, lastSnap.latency, false));
    fan = px; x += 150;
  }

  const gwMs = lastSnap ? lastSnap.gateway_ms : null;
  const gw = mkNode("gw", x, midY, lastSnap ? (lastSnap.gateway || "网关") : "网关",
    gwMs != null ? gwMs.toFixed(0) + "ms" : "无响应", gwMs);
  nodes.push(gw);
  edges.push(mkEdge(fan, gw, gwMs, false));
  x += 130;

  const hops = (trace && trace.hops ? trace.hops.slice(1) : []).slice(0, 6);
  const spanW = Math.max(120, W - x - 220);
  hops.forEach((h, i) => {
    const hx = x + (spanW * (i + 1)) / (hops.length + 1);
    const hy = midY + (i % 2 === 0 ? -26 : 26);
    const hn = mkNode("hop" + i, hx, hy, h.ip, h.avg != null ? h.avg.toFixed(0) + "ms" : "超时",
      h.loss >= 2 ? null : h.avg);
    hn.small = true;
    nodes.push(hn);
    edges.push(mkEdge(nodes[nodes.length - 2], hn, h.avg, false));
  });

  const target = mkNode("target", W - 90, midY,
    trace ? trace.target : "223.5.5.5", "探测目标",
    lastSnap ? lastSnap.latency : null);
  target.accent = true;
  nodes.push(target);
  const chainTail = hops.length ? nodes[nodes.length - 2] : gw;
  edges.push(mkEdge(chainTail, target, lastSnap ? lastSnap.latency : null, false));

  const dnsServers = Object.keys(dnsStats || {}).slice(0, 4);
  const dnsY = 52;
  dnsServers.forEach((srv, i) => {
    const dx = 260 + ((W - 380) * (i + 0.5)) / Math.max(1, dnsServers.length);
    const st = dnsStats[srv];
    const dn = mkNode("dns" + i, dx, dnsY, srv,
      st.avg_ms != null ? st.avg_ms.toFixed(0) + "ms · " + (st.ok_pct ?? "?") + "%" : "异常",
      st.ok_pct >= 50 ? st.avg_ms : null);
    dn.small = true;
    nodes.push(dn);
    edges.push(mkEdge(gw, dn, st.avg_ms, true));
  });

  const probes = (lastSnap ? lastSnap.probes : []).filter(p => p.name !== "阿里DNS"
    && p.name !== "114DNS" && p.name !== "谷歌DNS").slice(0, 6);
  const pbY = H - 48;
  probes.forEach((p, i) => {
    const px2 = 240 + ((W - 360) * (i + 0.5)) / Math.max(1, probes.length);
    const pn = mkNode("pb" + i, px2, pbY, p.name,
      p.ms != null ? p.ms.toFixed(0) + "ms" : "超时", p.ms);
    pn.small = true;
    nodes.push(pn);
    edges.push(mkEdge(target, pn, p.ms, true));
  });

  mapGraph.nodes = nodes;
  mapGraph.edges = edges;
}

function mkEdge(a, b, ms, dashed) {
  const key = a.id + "->" + b.id;
  const old = mapGraph.edges.find(e => e.key === key);
  const e = {
    key,
    a, b, ms, dashed,
    parts: old ? old.parts : [],
    nextSpawn: old ? old.nextSpawn : 0,
    dur: () => 700 + Math.min(ms == null ? 400 : ms, 500) * 3,
  };
  return e;
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function drawNode(ctx, n) {
  const w = n.small ? 96 : 118, h = 42;
  const border = n.ms === undefined ? "#38bdf8"
    : n.ms == null ? "#ef4444" : edgeColor(n.ms);
  ctx.save();
  ctx.fillStyle = "#16233b";
  ctx.strokeStyle = n.accent ? "#38bdf8" : border;
  ctx.lineWidth = n.accent ? 2 : 1.4;
  roundRect(ctx, n.x - w / 2, n.y - h / 2, w, h, 9);
  ctx.fill(); ctx.stroke();
  ctx.fillStyle = "#e2e8f0";
  ctx.font = "bold 11px 'Segoe UI','Microsoft YaHei'";
  ctx.textAlign = "center";
  const t = n.title.length > 15 ? n.title.slice(0, 14) + "…" : n.title;
  ctx.fillText(t, n.x, n.y - 3);
  ctx.fillStyle = "#94a3b8";
  ctx.font = "10px 'Segoe UI','Microsoft YaHei'";
  ctx.fillText(n.sub || "", n.x, n.y + 12);
  ctx.restore();
}

function drawEdge(ctx, e, now) {
  const col = edgeColor(e.ms);
  ctx.save();
  ctx.strokeStyle = col;
  ctx.globalAlpha = 0.55;
  ctx.lineWidth = 1.4;
  if (e.dashed) ctx.setLineDash([5, 5]);
  ctx.beginPath();
  ctx.moveTo(e.a.x, e.a.y);
  ctx.lineTo(e.b.x, e.b.y);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.globalAlpha = 1;

  if (now >= e.nextSpawn) {
    e.parts.push({ t0: now });
    e.nextSpawn = now + e.dur() * 0.55;
  }
  const dur = e.dur();
  e.parts = e.parts.filter(p => now - p.t0 < dur);
  for (const p of e.parts) {
    const k = (now - p.t0) / dur;
    const x1 = e.a.x + (e.b.x - e.a.x) * k, y1 = e.a.y + (e.b.y - e.a.y) * k;
    const x2 = e.b.x + (e.a.x - e.b.x) * ((k + 0.5) % 1),
          y2 = e.b.y + (e.a.y - e.b.y) * ((k + 0.5) % 1);
    for (const [px, py, c] of [[x1, y1, "#7dd3fc"], [x2, y2, "#a5f3fc"]]) {
      ctx.fillStyle = c;
      ctx.shadowColor = c;
      ctx.shadowBlur = 6;
      ctx.beginPath();
      ctx.arc(px, py, 2.4, 0, Math.PI * 2);
      ctx.fill();
      ctx.shadowBlur = 0;
    }
  }
  if (e.ms != null) {
    ctx.fillStyle = "#94a3b8";
    ctx.font = "10px Consolas";
    ctx.textAlign = "center";
    ctx.fillText(Math.round(e.ms) + "ms", (e.a.x + e.b.x) / 2, (e.a.y + e.b.y) / 2 - 6);
  }
  ctx.restore();
}

function drawMap(now) {
  const cv = $("mapCanvas");
  const { ctx } = fitCanvas(cv);
  ctx.clearRect(0, 0, cv.width, cv.height);
  for (const e of mapGraph.edges) drawEdge(ctx, e, now);
  for (const n of mapGraph.nodes) drawNode(ctx, n);
}

(function loop(ts) {
  requestAnimationFrame(loop);
  const page = document.getElementById("tab-map");
  if (!page || !page.classList.contains("active")) return;
  drawMap(ts || 0);
})(0);

async function loadMapData() {
  try {
    const [tr, ds] = await Promise.all([
      fetch("/api/traceroute").then(r => r.json()),
      fetch("/api/dns-stats").then(r => r.json()),
    ]);
    buildGraph(tr.latest, ds);
    $("mapMeta").textContent = tr.latest
      ? "路径更新于 " + fmtTime(tr.latest.ts * 1000) : "等待首次路径探测…";
  } catch (e) { /* retry */ }
}

/* ---------- 连接星图 ---------- */
const isPublicIpJs = ip => /^\d{1,3}(\.\d{1,3}){3}$/.test(ip)
  && !/^(10\.|192\.168\.|127\.|169\.254\.|100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.|198\.1[89]\.|26\.|0\.)/.test(ip);

let lastConnSig = "";

async function loadConnData() {
  try {
    const r = await fetch("/api/connections");
    const d = await r.json();
    const procs = (d.processes || []).filter(p => p.estab > 0)
      .sort((a, b) => b.estab - a.estab).slice(0, 8);
    const sig = JSON.stringify(procs.map(p => [
      p.proc, p.estab, p.new_per_min,
      (p.top_remotes || []).map(rm => [rm.ip, rm.count]),
    ]));
    if (sig !== lastConnSig) {
      lastConnSig = sig;
      renderConnCanvas(procs);
    }
    requestGeoBatch(procs);
    $("connMeta").textContent = "更新于 " + fmtTime(Date.now());
  } catch (e) { /* retry */ }
}

function requestGeoBatch(procs) {
  const ips = new Set();
  for (const p of procs.slice(0, 8))
    for (const r of p.top_remotes || [])
      if (isPublicIpJs(r.ip)) ips.add(r.ip);
  const missing = [...ips].filter(ip => !(ip in geoCache));
  if (!missing.length) return;
  const now = Date.now();
  if (now - lastGeoReq < 10000) return;
  lastGeoReq = now;
  fetch("/api/geo", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(missing.slice(0, 40)),
  }).then(r => r.json()).then(res => {
    Object.assign(geoCache, res || {});
    renderConnMeta();
  }).catch(() => {});
}

function renderConnMeta() {
  const box = $("geoLegend");
  const countries = {};
  for (const [, g] of Object.entries(geoCache))
    if (g && g.country) countries[g.country] = (countries[g.country] || 0) + 1;
  box.innerHTML = Object.entries(countries).sort((a, b) => b[1] - a[1])
    .slice(0, 8).map(([c, n]) => `<span class="chip-line">${esc(c)} × ${n}</span>`).join("");
}

function renderConnCanvas(procs) {
  const cv = $("connCanvas");
  const { ctx, w, h } = fitCanvas(cv);
  ctx.clearRect(0, 0, cv.width, cv.height);
  const cx = w / 2, cy = h / 2;
  const list = procs;
  if (!list.length) {
    ctx.fillStyle = "#94a3b8";
    ctx.font = "13px 'Microsoft YaHei'";
    ctx.textAlign = "center";
    ctx.fillText("暂无对外连接", cx, cy);
    return;
  }
  const R1 = Math.min(w, h) * 0.27, R2 = Math.min(w, h) * 0.44;

  ctx.save();
  ctx.fillStyle = "#16233b";
  ctx.strokeStyle = "#38bdf8";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(cx, cy, 34, 0, Math.PI * 2);
  ctx.fill(); ctx.stroke();
  ctx.fillStyle = "#e2e8f0";
  ctx.font = "bold 13px 'Microsoft YaHei'";
  ctx.textAlign = "center";
  ctx.fillText("本机", cx, cy + 4);
  ctx.restore();

  list.forEach((p, i) => {
    const ang = (Math.PI * 2 * i) / list.length - Math.PI / 2;
    const px = cx + R1 * Math.cos(ang), py = cy + R1 * Math.sin(ang);
    const pr = Math.min(26, 7 + Math.sqrt(p.estab) * 2.6);
    const hot = (p.new_per_min || 0) >= 60;

    (p.top_remotes || []).slice(0, 4).forEach((rm, j) => {
      const spread = 0.5;
      const rang = ang + (j - 1.5) * (spread / 2);
      const rx = cx + R2 * Math.cos(rang), ry = cy + R2 * Math.sin(rang);
      const g = geoCache[rm.ip];
      ctx.save();
      ctx.strokeStyle = hot ? "rgba(239,68,68,0.5)" : "rgba(56,189,248,0.35)";
      ctx.lineWidth = Math.min(4, 0.6 + rm.count * 0.35);
      ctx.beginPath();
      ctx.moveTo(px, py);
      ctx.lineTo(rx, ry);
      ctx.stroke();
      ctx.restore();

      ctx.save();
      ctx.fillStyle = "#1e293b";
      ctx.strokeStyle = "#334155";
      ctx.beginPath();
      ctx.arc(rx, ry, 5 + Math.min(6, rm.count), 0, Math.PI * 2);
      ctx.fill(); ctx.stroke();
      ctx.fillStyle = "#cbd5e1";
      ctx.font = "9px Consolas";
      ctx.textAlign = "center";
      ctx.fillText(rm.ip, rx, ry + 17);
      if (g && g.country)
        ctx.fillText(String(g.country).slice(0, 10), rx, ry + 28);
      ctx.restore();
    });

    ctx.save();
    ctx.fillStyle = hot ? "rgba(239,68,68,0.18)" : "rgba(56,189,248,0.14)";
    ctx.strokeStyle = hot ? "#ef4444" : "#38bdf8";
    ctx.lineWidth = hot ? 2 : 1.3;
    ctx.beginPath();
    ctx.arc(px, py, pr, 0, Math.PI * 2);
    ctx.fill(); ctx.stroke();
    ctx.fillStyle = "#e2e8f0";
    ctx.font = "bold 11px 'Microsoft YaHei'";
    ctx.textAlign = "center";
    const nm = p.proc.replace(/\.exe$/i, "");
    ctx.fillText(nm.length > 12 ? nm.slice(0, 11) + "…" : nm, px, py + 3);
    ctx.fillStyle = "#94a3b8";
    ctx.font = "10px Consolas";
    ctx.fillText(String(p.estab), px, py + 15);
    ctx.restore();
  });
}

/* ---------- WiFi 环境 ---------- */
const sigLabels = [], sigData = [];
let wifiData = null;

const sigChart = new Chart($("sigChart"), {
  type: "line",
  data: {
    labels: sigLabels,
    datasets: [{
      label: "信号强度(%)", data: sigData,
      borderColor: "#38bdf8", backgroundColor: "rgba(56,189,248,0.10)",
      fill: true, borderWidth: 1.5, pointRadius: 0, tension: 0.3,
    }],
  },
  options: {
    animation: false, responsive: true, maintainAspectRatio: false,
    scales: { x: { ticks: { maxTicksLimit: 8 } }, y: { min: 0, max: 100, ticks: { callback: v => v + "%" } } },
    plugins: { legend: { display: false } },
  },
});

let chanChart = null;

async function loadWifiEnv() {
  try {
    const r = await fetch("/api/wifi-env");
    wifiData = await r.json();
    renderWifiEnv(wifiData);
  } catch (e) { /* retry */ }
}

function renderWifiEnv(d) {
  if (!d) return;
  const sig = d.our_signal;
  $("sigCard").textContent = sig != null ? sig + "%" : "未知";
  $("sigCard").className = sig == null ? "value text-blue"
    : sig >= 60 ? "value text-green" : sig >= 40 ? "value text-amber" : "value text-red";
  $("sigSub").textContent = sig == null ? "当前模式不报告RSSI" : sig >= 60 ? "信号良好" : sig >= 40 ? "一般，注意遮挡" : "较弱";

  const itf = d.interference || {};
  $("interfCard").textContent = itf.score != null ? itf.level : "-";
  $("interfCard").className = itf.level === "高" ? "value text-red"
    : itf.level === "中" ? "value text-amber" : "value text-green";
  $("interfSub").textContent = itf.score != null ? "评分 " + itf.score + "/100" : "扫描中…";

  $("apCount").textContent = String((d.neighbors || []).length);
  $("apSub").textContent = d.ts ? "更新于 " + fmtTime(d.ts * 1000) : "-";

  $("chanCard").textContent = itf.our_channel != null ? itf.our_channel : "-";
  $("chanCard").className = "value text-blue";
  $("chanSub").textContent = itf.our_channel != null
    ? (itf.our_channel <= 14 ? "2.4GHz" : "5GHz") : "未知（热点模式）";

  $("wifiAdvice").textContent = itf.advice || "等待扫描…";
  $("wifiAdvice").className = "advice " +
    (itf.level === "高" ? "text-red" : itf.level === "中" ? "text-amber" : "text-green");

  const chans = d.channels || [];
  const labels = chans.map(c => String(c.ch));
  const counts = chans.map(c => c.count);
  const colors = chans.map(c =>
    c.ch === itf.our_channel ? "#f59e0b"
      : c.band === "2.4G" ? "rgba(167,139,250,0.75)" : "rgba(56,189,248,0.75)");
  if (!chanChart) {
    chanChart = new Chart($("chanChart"), {
      type: "bar",
      data: { labels, datasets: [{ label: "AP 数量", data: counts, backgroundColor: colors, borderRadius: 3 }] },
      options: {
        animation: false, responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { x: { title: { display: true, text: "信道" } }, y: { beginAtZero: true, ticks: { precision: 0 } } },
      },
    });
  } else {
    chanChart.data.labels = labels;
    chanChart.data.datasets[0].data = counts;
    chanChart.data.datasets[0].backgroundColor = colors;
    chanChart.update();
  }

  const tb = $("apTable").querySelector("tbody");
  const apSig = JSON.stringify([(itf.our_channel || null),
    (d.neighbors || []).map(a => [a.ssid, a.bssid, a.signal, a.channel, a.band])]);
  if (apSig === tb.dataset.last) return;
  tb.dataset.last = apSig;
  tb.innerHTML = "";
  for (const ap of (d.neighbors || []).slice(0, 30)) {
    const cls = ap.signal >= 60 ? "text-red" : ap.signal >= 40 ? "text-amber" : "";
    const rel = itf.our_channel != null && ap.channel === itf.our_channel
      ? '<b class="text-amber">同信道</b>'
      : (itf.our_channel != null && ap.channel != null && Math.abs(ap.channel - itf.our_channel) <= 4 && itf.our_channel <= 14
        ? "邻信道" : "");
    tb.insertAdjacentHTML("beforeend", `<tr>
      <td>${esc(ap.ssid)}</td><td class="mono small">${esc(ap.bssid || "-")}</td>
      <td class="${cls}">${ap.signal != null ? ap.signal + "%" : "-"}</td>
      <td>${ap.channel ?? "-"}</td><td>${ap.band ? ap.band + "GHz" : "-"}</td>
      <td>${rel}</td></tr>`);
  }
}

/* ---------- WebSocket ---------- */
let ws = null;
function connectWS() {
  ws = new WebSocket("ws://" + location.host + "/ws");
  ws.onmessage = ev => {
    const s = JSON.parse(ev.data);
    if (!s.ts) return;
    lastSnap = s;
    renderCards(s);
    pushPoint(s.ts, s.down_bps, s.up_bps, s.latency, s.jitter, s.loss_pct);
    const sig = s.wifi && s.wifi.signal;
    if (sig != null && sig !== sigData[sigData.length - 1]) {
      sigLabels.push(fmtTime(s.ts));
      sigData.push(sig);
      if (sigLabels.length > MAX_POINTS) { sigLabels.shift(); sigData.shift(); }
      sigChart.update();
    }
  };
  ws.onclose = () => setTimeout(connectWS, 2000);
  ws.onerror = () => ws.close();
}

async function loadHistory() {
  try {
    const r = await fetch("/api/history?seconds=600");
    const h = await r.json();
    if (labels.length === 0 && h.ts.length) {
      for (let i = 0; i < h.ts.length; i++) {
        pushPoint(h.ts[i], h.down_bps[i], h.up_bps[i], h.latency[i], h.jitter[i], h.loss_pct[i]);
      }
    }
    if (sigLabels.length === 0 && h.wifi_signal && h.ts.length) {
      for (let i = 0; i < h.ts.length; i++) {
        if (h.wifi_signal[i] == null) continue;
        sigLabels.push(fmtTime(h.ts[i]));
        sigData.push(h.wifi_signal[i]);
      }
      sigChart.update();
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
const initialTab = location.hash.replace("#", "");
activateTab(initialTab && document.getElementById("tab-" + initialTab) ? initialTab : "overview");
