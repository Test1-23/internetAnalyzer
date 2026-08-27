import threading
import time

SUGGESTIONS = {
    "disconnect": "检测到连接中断。请检查网线/光猫/路由器电源与端口是否松动，尝试重启光猫和路由器；若仍频繁中断，建议联系运营商报障。",
    "high_loss": "丢包率偏高。可能为WiFi干扰、线缆老化或运营商线路质量问题，可先改用有线连接，或在路由器中更换WiFi信道/频段。",
    "jitter": "延迟抖动明显。多出现在网络高负载时段，或有其他设备（下载、视频、游戏）抢占带宽，可尝试限速或错峰使用。",
    "high_latency": "平均延迟较高。建议先测速确认实际带宽，并检查本机是否有代理/VPN/加速器在转发流量。",
    "latency_spike": "出现延迟尖峰。多为瞬时流量突增或无线干扰导致，若与下载/上传高峰同步，请限速或暂停大流量任务。",
    "congestion": "带宽已接近饱和，导致延迟上升。可能存在大流量下载、上传或P2P任务，请暂停或设置限速。",
    "dns": "DNS解析异常。建议将DNS服务器改为 223.5.5.5 / 119.29.29.29 / 1.1.1.1 后再试。",
    "dns_server_bad": "某个配置的DNS服务器响应异常。可在网卡设置中移除该DNS或调整顺序，优先使用响应最快的服务器。",
    "wifi_weak": "WiFi信号弱。请靠近路由器、避开墙体与金属遮挡，或改用5G频段/有线连接。",
    "link_flap": "网卡链路状态频繁切换。请检查网线是否松动、接口是否氧化，并确认路由器端口无故障。",
    "route_change": "运营商路由路径发生变化。热点/移动网络下常见，可能伴随短暂断流；若频繁出现属运营商侧问题。",
    "path_loss": "路由路径中出现持续丢包的节点。根据定位段落排查：本地段查WiFi/网线，运营商段建议截图报障。",
    "conn_storm": "某进程连接数异常激增，可能挤压NAT表导致断流（手机热点尤其明显）。请识别并限制该进程联网。",
    "link_speed_drop": "网卡链路速率大幅下降。WiFi场景多为信号劣化或干扰，请靠近路由器或检查网线规格。",
    "nic_errors": "网卡错误/丢包计数快速增长。可能是驱动问题或硬件故障，建议更新网卡驱动。",
    "public_ip_changed": "公网出口IP发生变化（重新拨号/热点重连）。伴随的瞬断属正常现象，频繁变化需注意流量套餐状态。",
    "cpu_high": "CPU占用过高与网络延迟尖峰同时出现。本机负载也可能造成卡顿，请检查高占用进程。",
    "proxy_active": "检测到代理接管全局流量，当前所有探测反映的是隧道内路径。若诊断结果异常，先尝试关闭代理对比验证。",
}

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _percentile(values, p):
    if not values:
        return 0
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    f = int(k)
    c = f + 1 if f + 1 < len(s) else f
    return s[f] + (s[c] - s[f]) * (k - f)


class Analyzer:
    def __init__(self, monitor, storage=None, netinfo=None, dnsprobe=None,
                 connmon=None, pathmon=None, nicdiag=None, srvprobe=None, interval=5.0):
        self.monitor = monitor
        self.storage = storage
        self.netinfo = netinfo
        self.dnsprobe = dnsprobe
        self.connmon = connmon
        self.pathmon = pathmon
        self.nicdiag = nicdiag
        self.srvprobe = srvprobe
        self.interval = interval
        self._stop = threading.Event()
        self.events = {}
        self.last_report = {"summary": "正在采集数据…", "events": [], "suggestions": [],
                            "updated": time.time()}

    def start(self):
        threading.Thread(target=self._run, name="analyzer", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                self._analyze()
            except Exception:
                pass

    def _analyze(self):
        now = time.time()
        history = self.monitor.get_history(7200)
        active = set()

        def window_items(sec):
            cutoff = now - sec
            return [h for h in history if h["ts"] / 1000 >= cutoff]

        def trigger(cause, title, severity, detail):
            active.add(cause)
            ev = self.events.get(cause)
            is_new = ev is None
            if ev is None:
                ev = {"cause": cause, "title": title, "severity": severity, "detail": detail,
                      "first_seen": now, "last_seen": now, "count": 0, "active": True}
                self.events[cause] = ev
            ev["last_seen"] = now
            ev["count"] += 1
            ev["active"] = True
            if is_new and self.storage:
                self.storage.log_event(now, cause, severity, title, detail)

        w60 = window_items(60)
        if len(w60) >= 10:
            loss = sum(h["loss_pct"] for h in w60) / len(w60)
            jitter = sum(h["jitter"] for h in w60) / len(w60)
            lat_vals = [h["latency"] for h in w60 if h["latency"] is not None]
            lat = sum(lat_vals) / len(lat_vals) if lat_vals else None
            if lat is not None and lat >= 250:
                trigger("high_latency", "延迟偏高", "medium",
                        f"最近60秒平均延迟 {lat:.0f}ms，网页与视频体验会明显卡顿")
            if loss >= 10:
                trigger("high_loss", "丢包严重", "high",
                        f"最近60秒平均丢包率 {loss:.1f}%，数据包丢失会导致卡顿、断流")
            if jitter >= 40:
                trigger("jitter", "延迟抖动大", "medium",
                        f"最近60秒平均抖动 {jitter:.0f}ms，网络稳定性差")

        d = window_items(600)
        down_secs = sum(1 for h in d if h["status"] == "DOWN")
        if down_secs >= 5:
            trigger("disconnect", "连接中断", "high",
                    f"最近10分钟内检测到 {down_secs} 秒完全断网（所有探测目标均超时）")
        elif down_secs > 0:
            trigger("disconnect", "连接中断", "high",
                    f"最近10分钟内出现 {down_secs} 秒完全断网")
        if self.pathmon and (down_secs > 0 or any(
                e["cause"] in ("high_loss", "disconnect") for e in self.events.values() if e["active"])):
            self.pathmon.trigger()

        w300 = window_items(300)
        dns_fails = sum(1 for h in w300 if h["dns_ok"] is False)
        if dns_fails >= 5:
            trigger("dns", "DNS解析异常", "medium",
                    f"最近5分钟内 DNS 解析失败 {dns_fails} 次，网页可能无法打开")

        wifi = self.monitor.wifi or {}
        signal = wifi.get("signal")
        if signal is not None and signal < 40:
            ssid = f"（{wifi['ssid']}）" if wifi.get("ssid") else ""
            trigger("wifi_weak", "WiFi信号弱", "medium",
                    f"当前 WiFi 信号仅 {signal}%{ssid}，容易造成丢包和抖动")

        link_events = [l for l in self.monitor.link_history if l["ts"] / 1000 >= now - 600]
        if len(link_events) >= 2:
            trigger("link_flap", "网卡链路频繁切换", "high",
                    f"最近10分钟内网卡链路状态切换 {len(link_events)} 次，请检查网线连接")

        if len(history) >= 120:
            down_all = [h["down_bps"] for h in history]
            lat_all = [h["latency"] for h in history if h["latency"] is not None]
            ref = _percentile(down_all, 95)
            base_lat = _percentile(lat_all, 20)
            w120 = window_items(120)
            mean_down = sum(h["down_bps"] for h in w120) / len(w120)
            lat_vals = [h["latency"] for h in w120 if h["latency"] is not None]
            mean_lat = sum(lat_vals) / len(lat_vals) if lat_vals else 0
            if ref > 1e6 and mean_down > 0.7 * ref and mean_lat > max(120, base_lat + 80):
                trigger("congestion", "带宽饱和/拥塞", "medium",
                        f"最近2分钟平均下载 {mean_down / 1e6:.1f}Mbps，接近历史峰值 {ref / 1e6:.1f}Mbps，"
                        f"同时延迟升至 {mean_lat:.0f}ms（基准约 {base_lat:.0f}ms），呈典型拥塞特征")
            if lat_vals and base_lat > 0:
                spike = max(lat_vals)
                if spike > max(200, 3 * base_lat):
                    trigger("latency_spike", "延迟尖峰", "medium",
                            f"最近2分钟延迟最高达 {spike:.0f}ms（基准约 {base_lat:.0f}ms）")
                    if self.monitor.cpu is not None and self.monitor.cpu > 85:
                        trigger("cpu_high", "本机CPU过载", "low",
                                f"延迟尖峰时 CPU 占用 {self.monitor.cpu:.0f}%，本机负载也可能是卡顿原因之一")

        if self.dnsprobe:
            stats = self.dnsprobe.get_stats()
            bad = [(s, v) for s, v in stats.items()
                   if v.get("ok_pct") is not None and v["ok_pct"] < 50
                   and v.get("ok", 0) + v.get("fail", 0) >= 6]
            for s, v in bad:
                trigger("dns_server_bad", "DNS服务器响应异常", "medium",
                        f"DNS 服务器 {s} 成功率仅 {v['ok_pct']}%（均值 "
                        f"{v.get('avg_ms') if v.get('avg_ms') is not None else '超时'}ms），"
                        "建议更换或调整顺序")
                break

        if self.pathmon:
            tr = self.pathmon.get_latest()
            if tr:
                age = now - tr["ts"]
                if tr["changed"] and age < 900:
                    trigger("route_change", "路由路径变更", "high",
                            f"{age:.0f}秒前探测发现去往 {tr['target']} 的路径发生变化"
                            f"（{len(tr['hops'])} 跳），移动网络下常伴随瞬断")
                if tr["first_loss_hop"] and age < 900:
                    trigger("path_loss", "路径丢包定位", "high",
                            f"路径中第 {tr['first_loss_hop']} 跳（{tr['segment']}）出现持续丢包，"
                            f"责任段：{tr['segment']}；请按段排查或报障")

        if self.connmon:
            storms = [s for s in self.connmon.get_storms() if now - s["ts"] < 600]
            if storms:
                s = storms[-1]
                trigger("conn_storm", "进程连接风暴", "medium",
                        f"进程 {s['proc']} 活跃连接 {s['estab']} 个、"
                        f"每分钟新建 {s['new_per_min']} 个，可能挤压NAT表导致不稳定")

        speed_drop_ts = getattr(self.monitor, "_speed_drop_ts", None)
        if speed_drop_ts and now - speed_drop_ts < 1800:
            trigger("link_speed_drop", "链路速率骤降", "medium",
                    f"网卡链路速率降至 {self.monitor.nic_speed}Mbps"
                    f"（{now - speed_drop_ts:.0f}秒前），WiFi场景多为信号劣化")

        if self.monitor.nic_err_rate > 5:
            trigger("nic_errors", "网卡错误计数增长", "medium",
                    f"活动网卡 {self.monitor.nic} 错误/丢弃包速率 {self.monitor.nic_err_rate}/s，"
                    "建议更新驱动或检查硬件")

        if self.nicdiag:
            d = self.nicdiag.get()
            if d:
                for f in d.get("findings", []):
                    if f["level"] == "high":
                        trigger("nic_hw", "网卡硬件健康告警", "medium", f["text"])
                        break

        if self.srvprobe:
            now_p = time.time()
            for p in self.srvprobe.get_profiles():
                if now_p - p.get("ts", 0) > 600:
                    continue
                for f in p.get("findings", []):
                    if f["level"] == "high":
                        trigger("srv_issue", f"目标服务异常：{p['name']}", "medium",
                                f"{f['text']}（目标 {p['host']}，"
                                f"{'、'.join(x['text'] for x in p.get('findings', [])[:2])}）")
                        break

        if self.netinfo:
            ni = self.netinfo.get()
            if ni:
                changed_ts = ni.get("public_ip_changed_ts")
                if changed_ts and now - changed_ts < 3600:
                    pub = ni.get("public_ip") or {}
                    trigger("public_ip_changed", "公网出口IP变更", "low",
                            f"出口IP变更为 {pub.get('ip', '?')}（{pub.get('isp') or ''}），"
                            "重新拨号或热点重连会导致瞬断")
                has_proxy = bool(ni.get("virtual_adapters")) or \
                            (ni.get("proxy") or {}).get("enabled")
                if has_proxy:
                    names = "、".join(ni.get("virtual_adapters") or [])
                    trigger("proxy_active", "代理接管流量", "low",
                            f"检测到代理/虚拟网卡{('：' + names) if names else ''}"
                            f"{'，系统代理已开启' if (ni.get('proxy') or {}).get('enabled') else ''}。"
                            "当前探测反映隧道内路径，异常时请先关代理对比验证")

        now_ts = time.time()
        for cause, ev in self.events.items():
            if cause not in active and now_ts - ev["last_seen"] > 30:
                ev["active"] = False

        active_events = [e for e in self.events.values() if e["active"]]
        active_events.sort(key=lambda e: (SEVERITY_ORDER.get(e["severity"], 2), e["last_seen"]))
        if not active_events:
            summary = "当前网络状态良好，未检测到明显异常。"
            suggestions = []
        else:
            main = "、".join(e["title"] for e in active_events[:3])
            summary = f"检测到 {len(active_events)} 类异常，网络不稳定的主要原因：{main}"
            suggestions = list(dict.fromkeys(
                SUGGESTIONS.get(e["cause"]) for e in active_events if e["cause"] in SUGGESTIONS))

        for e in self.events.values():
            e["first_seen"] = round(e["first_seen"], 1)
            e["last_seen"] = round(e["last_seen"], 1)

        self.last_report = {
            "summary": summary,
            "events": sorted(
                (dict(e) for e in self.events.values()),
                key=lambda e: (not e["active"], SEVERITY_ORDER.get(e["severity"], 2)),
            ),
            "suggestions": suggestions,
            "updated": now_ts,
        }

    def get_report(self):
        return self.last_report
