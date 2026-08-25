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
    "wifi_weak": "WiFi信号弱。请靠近路由器、避开墙体与金属遮挡，或改用5G频段/有线连接。",
    "link_flap": "网卡链路状态频繁切换。请检查网线是否松动、接口是否氧化，并确认路由器端口无故障。",
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
    def __init__(self, monitor, interval=5.0):
        self.monitor = monitor
        self.interval = interval
        self._stop = threading.Event()
        self.events = {}
        self.last_report = {"summary": "正在采集数据…", "events": [], "suggestions": [], "updated": time.time()}

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
            if ev is None:
                ev = {"cause": cause, "title": title, "severity": severity, "detail": detail,
                      "first_seen": now, "last_seen": now, "count": 0, "active": True}
                self.events[cause] = ev
            ev["last_seen"] = now
            ev["count"] += 1
            ev["active"] = True

        w60 = window_items(60)
        if len(w60) >= 10:
            loss = sum(h["loss_pct"] for h in w60) / len(w60)
            jitter = sum(h["jitter"] for h in w60) / len(w60)
            lat_vals = [h["latency"] for h in w60 if h["latency"] is not None]
            if lat_vals:
                lat = sum(lat_vals) / len(lat_vals)
                if lat >= 250:
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
