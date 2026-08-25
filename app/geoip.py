import ipaddress
import json
import re
import threading
import time
import urllib.request

API = "http://ip-api.com/batch?fields=query,country,city,isp&lang=zh-CN"
BATCH_SIZE = 60
MIN_INTERVAL = 6.0

PRIVATE_NETS = [
    ipaddress.ip_network(n) for n in (
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
        "169.254.0.0/16", "100.64.0.0/10", "198.18.0.0/15", "26.0.0.0/8",
        "224.0.0.0/4", "0.0.0.0/8",
    )
]


def is_public_ip(ip):
    if not re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", ip):
        return False
    addr = ipaddress.ip_address(ip)
    return not any(addr in net for net in PRIVATE_NETS)


class GeoIP:
    def __init__(self, storage):
        self.storage = storage
        self.cache = {}
        if storage:
            try:
                self.cache.update(storage.load_geo())
            except Exception:
                pass
        self._queue = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._last_fetch = 0.0

    def start(self):
        threading.Thread(target=self._run, name="geoip", daemon=True).start()

    def stop(self):
        self._stop.set()

    def get(self, ips):
        with self._lock:
            for ip in ips:
                if ip in self.cache or not is_public_ip(ip) or ip in self._queue:
                    continue
                self._queue.append(ip)
        return {ip: self.cache.get(ip) for ip in ips}

    def _run(self):
        while not self._stop.wait(1.5):
            now = time.time()
            if now - self._last_fetch < MIN_INTERVAL:
                continue
            with self._lock:
                batch, self._queue = self._queue[:BATCH_SIZE], self._queue[BATCH_SIZE:]
            if not batch:
                continue
            self._last_fetch = now
            results = self._fetch(batch)
            if results:
                for r in results:
                    ip = r.get("query")
                    if ip:
                        self.cache[ip] = {"country": r.get("country"),
                                          "city": r.get("city"), "isp": r.get("isp")}
                if self.storage:
                    try:
                        self.storage.save_geo(
                            [{"ip": r["query"], **{"country": r.get("country"),
                                                   "city": r.get("city"), "isp": r.get("isp")}}
                             for r in results if r.get("query")])
                    except Exception:
                        pass

    @staticmethod
    def _fetch(ips):
        try:
            req = urllib.request.Request(
                API,
                data=json.dumps(ips).encode(),
                headers={"Content-Type": "application/json",
                         "User-Agent": "net-analyzer"},
                method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []
