import threading
import time
from collections import defaultdict, deque

import psutil

STORM_CONNS = 150
STORM_NEW_PER_MIN = 60


class ConnMon:
    def __init__(self, storage, interval=2.0):
        self.storage = storage
        self.interval = interval
        self.current = []
        self.summary = {}
        self.storms = deque(maxlen=50)
        self.history = deque(maxlen=450)
        self._names = {}
        self._prev_keys = set()
        self._new_events = defaultdict(lambda: deque(maxlen=120))
        self._last_snap_save = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._run, name="connmon", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _pname(self, pid):
        if pid in self._names:
            return self._names[pid]
        name = "?"
        try:
            name = psutil.Process(pid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
        except Exception:
            pass
        self._names[pid] = name
        return name

    def _run(self):
        while not self._stop.wait(0):
            try:
                self.sample()
            except Exception:
                pass
            self._stop.wait(self.interval)

    def sample(self):
        now = time.time()
        rows = []
        per_proc = defaultdict(lambda: {"estab": 0, "listen": 0, "other": 0,
                                        "remotes": set(), "new": 0})
        current_keys = set()
        for c in psutil.net_connections(kind="inet"):
            pname = self._pname(c.pid)
            key = (c.pid, str(c.laddr), str(c.raddr), c.status)
            current_keys.add(key)
            is_new = key not in self._prev_keys and c.status == psutil.CONN_ESTABLISHED
            st = per_proc[pname]
            if c.status == psutil.CONN_ESTABLISHED:
                st["estab"] += 1
                if c.raddr:
                    st["remotes"].add(c.raddr.ip)
                if is_new:
                    st["new"] += 1
                    self._new_events[pname].append(now)
            elif c.status == psutil.CONN_LISTEN:
                st["listen"] += 1
            else:
                st["other"] += 1
            rows.append({
                "pid": c.pid, "proc": pname, "status": c.status,
                "laddr": f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "",
                "raddr": f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "",
            })
        self._prev_keys = current_keys

        summary = {}
        for pname, st in per_proc.items():
            new_per_min = sum(1 for t in self._new_events[pname] if now - t <= 60)
            summary[pname] = {
                "estab": st["estab"], "listen": st["listen"],
                "remotes": len(st["remotes"]), "new": st["new"],
                "new_per_min": new_per_min,
            }
            if (st["estab"] >= STORM_CONNS or new_per_min >= STORM_NEW_PER_MIN) \
                    and not any(s["proc"] == pname and now - s["ts"] < 300 for s in self.storms):
                self.storms.append({"ts": now, "proc": pname,
                                    "estab": st["estab"], "new_per_min": new_per_min})

        with self._lock:
            self.current = rows
            self.summary = dict(sorted(summary.items(),
                                       key=lambda kv: kv[1]["estab"], reverse=True))
            self.history.append({"ts": now,
                                 "procs": {k: v["estab"] for k, v in summary.items()}})

        if now - self._last_snap_save >= 30:
            self._last_snap_save = now
            if self.storage:
                top = {k: v for k, v in list(summary.items())[:20]}
                self.storage.save_proc_snapshot(now, top)

    def get_current(self, limit=200):
        with self._lock:
            rows = list(self.current)
            summary = dict(self.summary)
        grouped = {}
        for r in rows:
            g = grouped.setdefault(r["proc"], {
                "proc": r["proc"], "pid": r["pid"], "estab": 0, "listen": 0,
                "remotes": {},
            })
            if r["status"] == "ESTABLISHED":
                g["estab"] += 1
                ip = r["raddr"].rsplit(":", 1)[0] if r["raddr"] else "-"
                g["remotes"][ip] = g["remotes"].get(ip, 0) + 1
            elif r["status"] == "LISTEN":
                g["listen"] += 1
        out = []
        for name, g in grouped.items():
            remotes = sorted(g["remotes"].items(), key=lambda x: x[1], reverse=True)[:8]
            out.append({**g, "top_remotes": [ip for ip, _ in remotes],
                        "new_per_min": summary.get(name, {}).get("new_per_min", 0)})
        out.sort(key=lambda x: x["estab"], reverse=True)
        return out[:limit]

    def get_summary(self):
        with self._lock:
            return dict(self.summary)

    def get_storms(self):
        with self._lock:
            return list(self.storms)
