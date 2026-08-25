import re
import subprocess
import threading
import time
from collections import defaultdict, deque

from .monitor import _decode

SIGNAL_PAT = re.compile(r"(?:Signal|信号)\s*[:：]\s*(\d{1,3})\s*%")
CHANNEL_PAT = re.compile(r"(?:Channel|信道|频道)\s*[:：]\s*(\d{1,3})")
BAND_PAT = re.compile(r"(?:Band|频段|波段)\s*[:：]\s*(\d+(?:\.\d+)?)")
SSID_PAT = re.compile(r"\s*SSID\s+\d+\s*[:：]\s*(.*)$")
BSSID_PAT = re.compile(r"\s*BSSID\s+\d+\s*[:：]\s*([0-9a-fA-F:]{17})")
IFACE_CHANNEL_PAT = re.compile(r"(?:Channel|信道|频道)\s*[:：]\s*(\d{1,3})")


class WifiEnv:
    def __init__(self, monitor, interval=20.0):
        self.monitor = monitor
        self.interval = interval
        self.neighbors = []
        self.channels = []
        self.interference = {"score": None, "level": "未知", "our_channel": None,
                             "advice": "等待扫描…"}
        self.history = deque(maxlen=360)
        self.scanned_ts = None
        self._stop = threading.Event()
        self._fut = None
        self._executor = None

    def start(self):
        self._executor = threading.Thread(target=self._run, name="wifienv", daemon=True)
        self._executor.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.wait(3.0):
            try:
                self.scan()
            except Exception:
                pass
            self._stop.wait(self.interval)

    def get_state(self):
        return {
            "ts": self.scanned_ts,
            "neighbors": self.neighbors,
            "channels": self.channels,
            "interference": self.interference,
            "history": list(self.history),
            "our_signal": (self.monitor.wifi or {}).get("signal"),
        }

    @staticmethod
    def _scan_neighbors():
        p = subprocess.run(["netsh", "wlan", "show", "networks", "mode=bssid"],
                           capture_output=True, timeout=15)
        text = _decode(p.stdout)
        out = []
        for line in text.splitlines():
            m = SSID_PAT.match(line)
            if m:
                out.append({"ssid": m.group(1).strip() or "(隐藏网络)",
                            "bssid": None, "signal": None,
                            "channel": None, "band": None})
                continue
            m = BSSID_PAT.search(line)
            if m:
                out.append({"ssid": "(同上)", "bssid": m.group(1).lower(),
                            "signal": None, "channel": None, "band": None})
                continue
            if not out:
                continue
            last = out[-1]
            if last["signal"] is None:
                m2 = SIGNAL_PAT.search(line)
                if m2:
                    last["signal"] = int(m2.group(1))
                    continue
            if last["channel"] is None:
                m3 = CHANNEL_PAT.search(line)
                if m3:
                    last["channel"] = int(m3.group(1))
                    continue
            if last["band"] is None:
                m4 = BAND_PAT.search(line)
                if m4:
                    last["band"] = float(m4.group(1))
        merged = []
        for ap in out:
            if ap["bssid"] is None:
                continue
            if ap["ssid"] == "(同上)" and merged:
                ap["ssid"] = merged[-1]["ssid"]
            merged.append(ap)
        return merged

    @staticmethod
    def _current_channel():
        try:
            p = subprocess.run(["netsh", "wlan", "show", "interfaces"],
                               capture_output=True, timeout=10)
            m = IFACE_CHANNEL_PAT.search(_decode(p.stdout))
            return int(m.group(1)) if m else None
        except Exception:
            return None

    def scan(self):
        neighbors = self._scan_neighbors()
        now = time.time()
        our_ch = self._current_channel()
        chan_map = defaultdict(lambda: {"count": 0, "max_sig": 0})
        for ap in neighbors:
            if ap["channel"] is not None:
                c = chan_map[ap["channel"]]
                c["count"] += 1
                if ap["signal"] is not None:
                    c["max_sig"] = max(c["max_sig"], ap["signal"])
        channels = [{"ch": ch, "count": v["count"], "max_sig": v["max_sig"],
                     "band": "2.4G" if ch <= 14 else "5G"}
                    for ch, v in sorted(chan_map.items())]

        score, level, advice = self._assess(neighbors, our_ch)

        self.neighbors = sorted(
            neighbors,
            key=lambda a: -(a["signal"] or 0))
        self.channels = channels
        self.interference = {"score": score, "level": level,
                             "our_channel": our_ch, "advice": advice}
        self.scanned_ts = now

        sig = (self.monitor.wifi or {}).get("signal")
        self.history.append({"ts": int(now * 1000), "signal": sig,
                             "aps": len(neighbors), "score": score})

    @staticmethod
    def _assess(neighbors, our_ch):
        if our_ch is None:
            strong = sum(1 for a in neighbors if (a["signal"] or 0) >= 50)
            advice = "当前信道未知（热点/关联模式不报告），已统计周边AP分布。"
            if strong >= 8:
                return min(90, strong * 6), "较高", advice + "可见强信号AP较多，环境拥挤。"
            if strong >= 4:
                return min(60, strong * 7), "中等", advice + "存在一定数量的邻近AP。"
            return min(30, strong * 10), "较低", advice + "周边AP不多，干扰风险低。"

        co = [a for a in neighbors
              if a["channel"] == our_ch and a["bssid"]
              and (a["signal"] or 0) > 0]
        adj = [a for a in neighbors
               if a["channel"] is not None and a["channel"] != our_ch
               and abs(a["channel"] - our_ch) <= 4 and a["channel"] <= 14]
        co_w = sum(min(a["signal"] or 0, 100) for a in co) / 20
        adj_w = sum((a["signal"] or 0) / 40 for a in adj)
        score = round(min(100, co_w * 6 + adj_w * 4))

        same_band_24 = our_ch <= 14
        if score >= 65:
            level = "高"
        elif score >= 35:
            level = "中"
        else:
            level = "低"

        if same_band_24:
            counts = {ch: 0 for ch in (1, 6, 11)}
            for a in neighbors:
                if a["channel"] in counts:
                    counts[a["channel"]] += 1
            best = min(counts, key=counts.get)
            advice = f"2.4GHz信道{our_ch}：同信道AP {len(co)} 个、邻信道 {len(adj)} 个。" \
                     f"建议改用最空闲的信道 {best}（现有AP数 {counts[best]}）。"
        else:
            nearby = sorted(
                (a for a in neighbors if a["channel"] and abs(a["channel"] - our_ch) <= 8),
                key=lambda a: -(a["signal"] or 0))[:3]
            advice = f"5GHz信道{our_ch}：同信道AP {len(co)} 个。" \
                     + (f"最近强干扰源：" + "、".join(
                         f"ch{a['channel']}({a['signal']}%)" for a in nearby) if nearby else "周边干净。")
        return score, level, advice
