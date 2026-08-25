import re
import socket
import struct
import threading
import time
from collections import deque

QUERY_HOST = "www.baidu.com"
IPV4_PAT = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def _build_query(name, qid):
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    question = b"".join(bytes([len(p)]) + p.encode("ascii") for p in name.split(".")) + b"\x00"
    return header + question + struct.pack(">HH", 1, 1)


class DnsProbe:
    def __init__(self, netinfo, interval=5.0):
        self.netinfo = netinfo
        self.interval = interval
        self.stats = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._qid = int(time.time()) & 0xFFFF

    def start(self):
        threading.Thread(target=self._run, name="dnsprobe", daemon=True).start()

    def stop(self):
        self._stop.set()

    def get_stats(self):
        with self._lock:
            return {s: dict(v) for s, v in self.stats.items()}

    def _run(self):
        while not self._stop.wait(0):
            servers = []
            try:
                servers = self.netinfo.get().get("dns_servers") or []
            except Exception:
                pass
            if servers:
                threads = []
                for s in {x for x in servers if IPV4_PAT.match(x)}:
                    t = threading.Thread(target=self._probe_one, args=(s,), daemon=True)
                    t.start()
                    threads.append(t)
                for t in threads:
                    t.join(timeout=3.0)
                with self._lock:
                    for s in list(self.stats.keys()):
                        if s not in servers:
                            del self.stats[s]
            self._stop.wait(self.interval)

    def _probe_one(self, server):
        self._qid = (self._qid + 1) & 0xFFFF or 1
        qid = self._qid
        packet = _build_query(QUERY_HOST, qid)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2.0)
        ok, ms = False, None
        try:
            t0 = time.perf_counter()
            sock.sendto(packet, (server, 53))
            data, _ = sock.recvfrom(512)
            ms = round((time.perf_counter() - t0) * 1000, 1)
            if len(data) >= 12 and struct.unpack(">H", data[:2])[0] == qid:
                ok = True
        except OSError:
            ok = False
        finally:
            sock.close()

        with self._lock:
            st = self.stats.setdefault(server, {
                "results": deque(maxlen=60), "ok": 0, "fail": 0,
                "avg_ms": None, "ok_pct": 100.0,
            })
            st["results"].append(ms if ok else None)
            if ok:
                st["ok"] += 1
            else:
                st["fail"] += 1
            total = st["ok"] + st["fail"]
            vals = [v for v in st["results"] if v is not None]
            st["avg_ms"] = round(sum(vals) / len(vals), 1) if vals else None
            st["ok_pct"] = round(st["ok"] / total * 100, 1) if total else 100.0
