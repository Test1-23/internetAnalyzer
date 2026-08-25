import threading
import time
from collections import deque

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


class Attribution:
    def __init__(self, monitor, analyzer, pathmon, wifienv, connmon, netinfo, interval=10.0):
        self.monitor = monitor
        self.analyzer = analyzer
        self.pathmon = pathmon
        self.wifienv = wifienv
        self.connmon = connmon
        self.netinfo = netinfo
        self.interval = interval
        self.current = None
        self.history = deque(maxlen=120)
        self._stop = threading.Event()
        self._last_side = None
        self._stable_since = None

    def start(self):
        threading.Thread(target=self._run, name="attribution", daemon=True).start()

    def stop(self):
        self._stop.set()

    def get(self):
        return self.current

    def get_history(self):
        return list(self.history)

    def _run(self):
        while not self._stop.wait(5.0):
            try:
                self.compute()
            except Exception:
                pass
            self._stop.wait(self.interval)

    def compute(self):
        now = time.time()
        scores = {"local": 0, "lan": 0, "isp": 0, "target": 0}
        evidence = []

        def add(side, weight, text):
            scores[side] += weight
            evidence.append({"side": side, "weight": weight, "text": text})

        active = {e["cause"]: e for e in self.analyzer.events.values() if e["active"]}
        snap = self.monitor.get_snapshot()
        gw = self.monitor.gateway
        gw_ms = snap.get("gateway_ms")
        latency = snap.get("latency")
        status = snap.get("status")

        if status == "DOWN":
            if gw_ms is None:
                add("lan", 3, "完全断网且网关无响应，故障在本地链路（网卡/网线/WiFi/网关）")
            else:
                add("isp", 2, "完全断网但网关可达，故障在网关之外（运营商侧）")

        if gw_ms is not None and gw_ms > 60:
            add("lan", 2, f"网关延迟异常偏高（{gw_ms:.0f}ms），本地链路或网关设备过载")

        probes = snap.get("probes") or []
        ok_probes = [p for p in probes if p["ms"] is not None]
        timeout_probes = [p for p in probes if p["ms"] is None]
        if gw_ms is not None and len(timeout_probes) >= 2 and len(ok_probes) >= 1:
            add("isp", 2, f"网关正常但 {len(timeout_probes)} 个外网目标超时，出口链路异常")

        if len(ok_probes) >= 3:
            vals = sorted(p["ms"] for p in ok_probes)
            spread = vals[-1] - vals[0]
            if spread > 150:
                worst = max(probes, key=lambda p: p["ms"] if p["ms"] is not None else 0)
                add("target", 2,
                    f"仅 {worst['name']}（{worst['ms']:.0f}ms）明显劣于其他目标，偏向该服务/节点自身问题")

        trace = self.pathmon.get_latest()
        if trace and now - trace["ts"] < 900:
            if trace.get("first_loss_hop"):
                seg = trace.get("segment") or ""
                if "家庭" in seg or "内网" in seg or "本地" in seg:
                    add("lan", 3, f"首丢跳定位在{seg}（第{trace['first_loss_hop']}跳）")
                elif "运营商" in seg or "骨干" in seg or "NAT" in seg:
                    add("isp", 3, f"首丢跳定位在{seg}（第{trace['first_loss_hop']}跳）")
                else:
                    add("isp", 1, f"路径第{trace['first_loss_hop']}跳丢包（{seg}）")
            if trace.get("changed"):
                add("isp", 2, "近期路由路径发生变更（运营商侧切换链路）")

        ni = self.netinfo.get() or {}
        if ni.get("public_ip_changed_ts") and now - ni["public_ip_changed_ts"] < 3600:
            add("isp", 2, "公网出口IP近期变更（重新拨号/热点重连）")

        itf = self.wifienv.interference or {}
        if itf.get("level") in ("中", "高"):
            add("lan", 2, f"WiFi干扰评估为「{itf.level}」（评分{itf.get('score')}），"
                          f"信道{itf.get('our_channel') or '未知'}")
        wifi = snap.get("wifi") or {}
        if wifi.get("signal") is not None and wifi["signal"] < 40:
            add("lan", 2, f"WiFi信号仅{wifi['signal']}%，无线链路质量差")

        if snap.get("nic_err_rate", 0) > 5:
            add("local", 3, f"网卡错误/丢弃包 {snap['nic_err_rate']}/s，本机网卡或驱动异常")
        if snap.get("cpu", 0) > 85:
            add("local", 1, f"CPU占用{snap['cpu']:.0f}%，本机负载可能造成卡顿")
        if active.get("conn_storm"):
            add("local", 2, f"进程连接风暴：{active['conn_storm']['detail'][:60]}")
        if active.get("proxy_active") and not active.get("disconnect"):
            add("local", 1, "代理接管全部流量，隧道内异常会表现为全网异常")

        if active.get("dns_server_bad"):
            dns_in_lan = any(d and gw and d.startswith(_subnet_of(gw))
                             for d in (ni.get("dns_servers") or []))
            if dns_in_lan:
                add("lan", 1, "内网DNS响应异常")
            else:
                add("isp", 1, "外部DNS响应异常")

        if latency is not None and latency > 300 and gw_ms is not None and gw_ms < 20:
            add("isp", 1, f"外网延迟{latency:.0f}ms而网关仅{gw_ms:.0f}ms，延迟产生于出口之外")

        total = sum(scores.values()) or 1
        top_side = max(scores, key=scores.get)
        top_score = scores[top_side]
        if top_score == 0:
            result = {
                "ts": now,
                "responsibility": "none",
                "label": "未检测到异常",
                "confidence": 0,
                "scores": scores,
                "evidence": [],
                "conclusion": "当前各项指标正常，无需归因。",
            }
        else:
            conf = round(top_score / total * 100)
            labels = {
                "local": "本机（软件/网卡/负载）",
                "lan": "本地网络（WiFi链路/网关/内网）",
                "isp": "网络提供侧（运营商/出口）",
                "target": "目标服务侧",
            }
            proxy_note = ""
            if (ni.get("virtual_adapters") or (ni.get("proxy") or {}).get("enabled")) \
                    and top_side in ("isp", "target"):
                proxy_note = "（注意：当前流量经代理隧道，观测到的远端异常可能来自代理本身）"
            healthy = status == "OK" and not any(
                k in active for k in ("disconnect", "high_loss", "path_loss", "route_change"))
            if healthy:
                conclusion = (f"网络整体正常，但存在指向「{labels[top_side]}」的风险点"
                              f"（置信度约{conf}%，证据{len(evidence)}条，"
                              f"得分 local:{scores['local']} lan:{scores['lan']} "
                              f"isp:{scores['isp']} target:{scores['target']}）。"
                              f"若出现卡顿请优先按此侧排查。{proxy_note}")
            else:
                conclusion = (f"当前不稳定主要源自「{labels[top_side]}」，"
                              f"置信度约{conf}%（证据{len(evidence)}条，"
                              f"得分 local:{scores['local']} lan:{scores['lan']} "
                              f"isp:{scores['isp']} target:{scores['target']}）。{proxy_note}")
            result = {
                "ts": now,
                "responsibility": top_side,
                "label": labels[top_side],
                "confidence": conf,
                "scores": scores,
                "evidence": sorted(evidence, key=lambda e: -e["weight"]),
                "conclusion": conclusion,
            }

        if result["responsibility"] != self._last_side:
            self._last_side = result["responsibility"]
            self._stable_since = now
            self.history.append(dict(result))
        result["since"] = self._stable_since
        self.current = result


def _subnet_of(ip):
    parts = ip.split(".")
    return ".".join(parts[:3]) + "."
