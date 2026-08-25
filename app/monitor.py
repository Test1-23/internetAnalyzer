import re
import socket
import subprocess
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import psutil

PROBE_TARGETS = [
    ("223.5.5.5", 53, "阿里DNS"),
    ("114.114.114.114", 53, "114DNS"),
    ("8.8.8.8", 53, "谷歌DNS"),
    ("www.baidu.com", 443, "百度"),
    ("www.qq.com", 443, "腾讯"),
    ("www.taobao.com", 443, "淘宝"),
    ("www.microsoft.com", 443, "微软"),
]

GATEWAY_PORTS = (80, 443)
PROBE_TIMEOUT = 2.0
DNS_HOSTS = ["www.baidu.com", "www.qq.com"]

STATUS_OK = "OK"
STATUS_DEGRADED = "DEGRADED"
STATUS_DOWN = "DOWN"


def _decode(b: bytes) -> str:
    for enc in ("utf-8", "gbk"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")


class NetworkMonitor:
    def __init__(self, history_seconds: int = 21600):
        self.history = deque(maxlen=history_seconds)
        self.probes_history = deque(maxlen=900)
        self.link_history = deque(maxlen=900)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=10)
        self._last_total = psutil.net_io_counters()
        self._last_pernic = psutil.net_io_counters(pernic=True)
        self._wifi_fut = None
        self._wifi_last = 0.0
        self._gw_fut = None
        self._dns_fut = None
        self._last_gw = 0.0
        self._last_dns = 0.0
        self.start_time = time.time()
        self.latest = {}
        self.wifi = None
        self.gateway_ms = None
        self.dns_ok = None
        self.dns_ms = None
        self.cpu = None
        self.mem = None
        self.nic_speed = None
        self.nic_err_rate = 0.0
        self._last_nic_err = None
        self._speed_drop_ts = None
        psutil.cpu_percent(interval=None)
        self.gateway = self._discover_gateway()
        self.nic = self._pick_active_nic() or "unknown"

    def start(self):
        threading.Thread(target=self._run, name="monitor", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _discover_gateway(self):
        try:
            out = subprocess.run(["route", "print", "-4"], capture_output=True, timeout=5)
            best_gw, best_metric = None, None
            for line in _decode(out.stdout).splitlines():
                if not re.match(r"\s*0\.0\.0\.0\s+0\.0\.0\.0\s+", line):
                    continue
                nums = re.findall(r"[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+", line)
                if len(nums) < 3:
                    continue
                m = re.search(r"(\d+)\s*$", line)
                metric = int(m.group(1)) if m else 99999
                gw = nums[2]
                if gw.startswith("127.") or gw == "0.0.0.0":
                    continue
                if best_metric is None or metric < best_metric:
                    best_gw, best_metric = gw, metric
            return best_gw
        except Exception:
            pass
        return None

    def _pick_active_nic(self):
        best, best_total = None, 0
        for name, io in psutil.net_io_counters(pernic=True).items():
            total = io.bytes_sent + io.bytes_recv
            if total > best_total:
                best, best_total = name, total
        return best

    def _tcp_probe(self, host, port):
        t0 = time.perf_counter()
        try:
            with socket.create_connection((host, port), timeout=PROBE_TIMEOUT):
                return round((time.perf_counter() - t0) * 1000, 1)
        except OSError:
            return None

    def _probe_gateway(self):
        futs = [self._executor.submit(self._tcp_probe, self.gateway, p) for p in GATEWAY_PORTS]
        best = None
        for f in futs:
            try:
                ms = f.result(timeout=PROBE_TIMEOUT)
            except Exception:
                ms = None
            if ms is not None and (best is None or ms < best):
                best = ms
        return best

    def _dns_check(self):
        t0 = time.perf_counter()
        for host in DNS_HOSTS:
            try:
                socket.getaddrinfo(host, 80, socket.AF_INET)
                return True, round((time.perf_counter() - t0) * 1000, 1)
            except OSError:
                continue
        return False, None

    def _wifi_info(self):
        try:
            p = subprocess.run(["netsh", "wlan", "show", "interfaces"],
                               capture_output=True, timeout=5)
            text = _decode(p.stdout)
            m = re.search(r"(?:Signal|信号)\s*[:：]?\s*(\d{1,3})\s*%", text)
            s = re.search(r"(?:SSID|SSID\s*名称)\s*[:：]\s*(.+)", text)
            connected = "已连接" in text or "关联" in text or bool(
                re.search(r"(?:State|状态)\s*[:：]\s*(?:connected|associated)", text, re.I))
            if not connected:
                return None
            return {
                "ssid": s.group(1).strip() if s else None,
                "signal": int(m.group(1)) if m else None,
                "connected": True,
            }
        except Exception:
            return None

    def _run(self):
        latency_ring = deque(maxlen=5)
        all_fail_streak = 0
        prev_link = None
        while not self._stop.wait(1.0):
            now = time.time()
            ts = int(now * 1000)

            total = psutil.net_io_counters()
            down = max(0, total.bytes_recv - self._last_total.bytes_recv)
            up = max(0, total.bytes_sent - self._last_total.bytes_sent)
            self._last_total = total

            pernic = psutil.net_io_counters(pernic=True)
            diffs = {}
            for name, io in pernic.items():
                prev = self._last_pernic.get(name)
                if prev is not None:
                    d = (io.bytes_recv + io.bytes_sent) - (prev.bytes_recv + prev.bytes_sent)
                    if d >= 0:
                        diffs[name] = d
            self._last_pernic = pernic
            if diffs:
                self.nic = max(diffs, key=diffs.get)

            tasks = [(name, self._executor.submit(self._tcp_probe, host, port))
                     for host, port, name in PROBE_TARGETS]
            if self._gw_fut is None and now - self._last_gw >= 3:
                self._last_gw = now
                self._gw_fut = self._executor.submit(self._probe_gateway)
            if self._dns_fut is None and now - self._last_dns >= 5:
                self._last_dns = now
                self._dns_fut = self._executor.submit(self._dns_check)

            probe_ms = {}
            for name, fut in tasks:
                try:
                    probe_ms[name] = fut.result(timeout=PROBE_TIMEOUT + 1.0)
                except Exception:
                    probe_ms[name] = None

            if self._gw_fut is not None and self._gw_fut.done():
                try:
                    self.gateway_ms = self._gw_fut.result()
                except Exception:
                    self.gateway_ms = None
                self._gw_fut = None
            if self._dns_fut is not None and self._dns_fut.done():
                try:
                    self.dns_ok, self.dns_ms = self._dns_fut.result()
                except Exception:
                    self.dns_ok, self.dns_ms = False, None
                self._dns_fut = None

            values = sorted(v for v in probe_ms.values() if v is not None)
            latency = values[len(values) // 2] if values else None
            loss_pct = round(sum(1 for v in probe_ms.values() if v is None) / len(probe_ms) * 100, 1)
            latency_ring.append(latency)
            pts = [x for x in latency_ring if x is not None]
            if len(pts) >= 2:
                jitter = round(sum(abs(pts[i] - pts[i - 1]) for i in range(1, len(pts))) / (len(pts) - 1), 1)
            else:
                jitter = 0.0

            if all(v is None for v in probe_ms.values()):
                all_fail_streak += 1
            else:
                all_fail_streak = 0

            if all_fail_streak >= 4:
                status = STATUS_DOWN
            elif latency is None or loss_pct >= 10 or latency > 300 or (self.dns_ok is not None and not self.dns_ok):
                status = STATUS_DEGRADED
            else:
                status = STATUS_OK

            stats = psutil.net_if_stats()
            nic_stat = stats.get(self.nic)
            link_up = bool(nic_stat and nic_stat.isup) if self.nic else None
            speed = getattr(nic_stat, "speed", 0) or 0 if nic_stat else 0
            if speed > 0:
                prev_speed = self.nic_speed
                self.nic_speed = speed
                if prev_speed and speed < prev_speed * 0.6:
                    self._speed_drop_ts = now
                elif prev_speed and speed >= prev_speed:
                    pass
            err_now = None
            if self.nic in pernic:
                c = pernic[self.nic]
                err_now = c.errin + c.errout + c.dropin + c.dropout
            if err_now is not None and self._last_nic_err is not None:
                d = max(0, err_now - self._last_nic_err)
                self.nic_err_rate = d
            if err_now is not None:
                self._last_nic_err = err_now

            try:
                self.cpu = psutil.cpu_percent(interval=None)
                self.mem = psutil.virtual_memory().percent
            except Exception:
                pass
            if prev_link is None:
                prev_link = link_up
            elif link_up != prev_link:
                self.link_history.append({"ts": ts, "up": link_up})
                prev_link = link_up

            if self._wifi_fut is None and now - self._wifi_last >= 10:
                self._wifi_last = now
                self._wifi_fut = self._executor.submit(self._wifi_info)
            if self._wifi_fut is not None and self._wifi_fut.done():
                try:
                    self.wifi = self._wifi_fut.result()
                except Exception:
                    self.wifi = None
                self._wifi_fut = None

            snap = {
                "ts": ts,
                "status": status,
                "latency": latency,
                "jitter": jitter,
                "loss_pct": loss_pct,
                "down_bps": down,
                "up_bps": up,
                "probes": [{"name": n, "ms": probe_ms[n]} for _, _, n in PROBE_TARGETS],
                "gateway": self.gateway,
                "gateway_ms": self.gateway_ms,
                "dns_ok": self.dns_ok,
                "dns_ms": self.dns_ms,
                "nic": self.nic,
                "link_up": link_up,
                "nic_speed": self.nic_speed,
                "nic_err_rate": self.nic_err_rate,
                "speed_drop_ts": int(self._speed_drop_ts * 1000) if self._speed_drop_ts else None,
                "cpu": self.cpu,
                "mem": self.mem,
                "wifi": self.wifi,
                "uptime_s": int(now - self.start_time),
                "totals_mb": {
                    "down": round(total.bytes_recv / 1048576, 1),
                    "up": round(total.bytes_sent / 1048576, 1),
                },
            }
            with self._lock:
                self.latest = snap
                self.history.append({
                    "ts": ts,
                    "status": status,
                    "latency": latency,
                    "jitter": jitter,
                    "loss_pct": loss_pct,
                    "down_bps": down,
                    "up_bps": up,
                    "dns_ok": self.dns_ok,
                    "wifi_signal": (self.wifi or {}).get("signal"),
                })
                self.probes_history.append({"ts": ts, "probes": probe_ms, "gateway_ms": self.gateway_ms})

    def get_snapshot(self):
        with self._lock:
            return self.latest

    def get_history(self, seconds):
        with self._lock:
            items = list(self.history)
        cutoff = time.time() * 1000 - seconds * 1000
        return [h for h in items if h["ts"] >= cutoff]

    def get_probes(self, seconds):
        with self._lock:
            items = list(self.probes_history)
        cutoff = time.time() * 1000 - seconds * 1000
        return [p for p in items if p["ts"] >= cutoff]
