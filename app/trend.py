import threading
import time


class TrendAnalyzer:
    def __init__(self, monitor, storage, interval=60.0):
        self.monitor = monitor
        self.storage = storage
        self.interval = interval
        self._stop = threading.Event()
        self._last_saved = 0.0

    def start(self):
        threading.Thread(target=self._run, name="trend", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.wait(30.0):
            try:
                self.persist()
            except Exception:
                pass
            self._stop.wait(self.interval)

    def persist(self):
        hist = self.monitor.get_history(600)
        if not hist:
            return
        watermark = self._last_saved
        if not watermark:
            meta = self.storage.get_meta("history_watermark") if self.storage else None
            watermark = int(meta) if meta else 0
        rows = [h for h in hist if h["ts"] > watermark]
        if rows and self.storage:
            self.storage.save_history_batch(rows)
        if rows:
            self._last_saved = rows[-1]["ts"]
            if self.storage:
                self.storage.set_meta("history_watermark", rows[-1]["ts"])

    def get_buckets(self, hours):
        now = time.time()
        start = int((now - hours * 3600) * 1000)
        end = int(now * 1000)
        step = max(1, int(hours * 3600 / 72))
        if self.storage:
            buckets = self.storage.get_history_range(start, end, step)
            if buckets:
                return buckets
        hist = self.monitor.get_history(min(hours * 3600, 21600))
        if not hist:
            return []
        buckets = {}
        for h in hist:
            b = int(h["ts"] / (step * 1000)) * step * 1000
            bs = buckets.setdefault(b, {"n": 0, "lat": 0.0, "jit": 0.0,
                                        "loss": 0.0, "down": 0, "up": 0})
            bs["n"] += 1
            bs["lat"] += h["latency"] or 0
            bs["jit"] += h["jitter"] or 0
            bs["loss"] += h["loss_pct"]
            bs["down"] += h["down_bps"]
            bs["up"] += h["up_bps"]
        return [{"b": b, "latency": round(v["lat"] / v["n"], 1),
                 "jitter": round(v["jit"] / v["n"], 1),
                 "loss": round(v["loss"] / v["n"], 1),
                 "down": int(v["down"] / v["n"]), "up": int(v["up"] / v["n"]),
                 "n": v["n"]} for b, v in sorted(buckets.items())]

    def find_windows(self, hours, loss_pct=5.0, min_sec=90):
        start = int((time.time() - hours * 3600) * 1000)
        buckets = self.get_buckets(hours)
        if not buckets:
            return []
        windows = []
        run = []
        for b in buckets:
            bad = b["n"] and (b["loss"] >= loss_pct or b["latency"] > 400)
            if bad:
                run.append(b)
            elif run:
                if _window_seconds(run) >= min_sec:
                    windows.append(_mk_window(run))
                run = []
        if run and _window_seconds(run) >= min_sec:
            windows.append(_mk_window(run))
        return windows

    def daily_report(self):
        now = time.time()
        ti = time.localtime()
        today_start = now - (ti.tm_hour * 3600 + ti.tm_min * 60 + ti.tm_sec)
        buckets = self.storage.get_history_range(
            int(today_start * 1000), int(now * 1000), 600) if self.storage else []
        if not buckets:
            buckets = self.get_buckets((now - today_start) / 3600)
        total_down = sum(b["down"] for b in buckets) * 600 if buckets else 0
        sample_n = sum(b["n"] for b in buckets)
        avg_lat = sum(b["latency"] * b["n"] for b in buckets) / sample_n if sample_n else 0
        worst = max(buckets, key=lambda b: b["loss"] or 0) if buckets else None

        windows = self.find_windows(min(24, max(1, (now - today_start) / 3600)))
        events = self.storage.list_recent_events(1, 500) if self.storage else []
        today_events = [e for e in events if e["ts"] >= today_start]
        causes = {}
        for e in today_events:
            causes[e["cause"]] = causes.get(e["cause"], 0) + 1
        top_events = sorted(causes.items(), key=lambda kv: -kv[1])[:6]

        sev_high = sum(1 for e in today_events if e["severity"] == "high")
        report = {
            "date": time.strftime("%Y-%m-%d"),
            "sample_seconds": sample_n,
            "avg_latency_ms": round(avg_lat, 1),
            "total_down_gb": round(total_down / 8 / 1e9, 2),
            "worst_loss_pct": worst["loss"] if worst else 0,
            "windows": windows,
            "events_today": len(today_events),
            "high_events": sev_high,
            "top_causes": [{"cause": c, "count": n} for c, n in top_events],
        }
        return report


def _window_seconds(buckets):
    if len(buckets) < 2:
        return (buckets[-1]["b"] - buckets[0]["b"]) / 1000 if buckets else 0
    return (buckets[-1]["b"] - buckets[0]["b"]) / 1000 + 60


def _mk_window(run):
    w = {
        "start": run[0]["b"], 
        "end": run[-1]["b"],
        "max_loss": max(b["loss"] for b in run),
        "max_latency": max(b["latency"] for b in run),
        "avg_latency": round(sum(b["latency"] for b in run) / len(run), 1),
    }
    return w
