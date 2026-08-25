import threading
import time


def _update_signal_stats(fp, sig):
    n = fp.get("signal_samples", 0)
    avg = fp.get("signal_avg") or sig
    std = fp.get("signal_std", 0)
    new_avg = avg + (sig - avg) / (n + 1)
    std = std + (sig - avg) * (sig - new_avg)
    fp["signal_avg"] = round(new_avg, 1)
    fp["signal_std"] = round(max(0.0, std / (n + 1)) ** 0.5, 1)
    fp["signal_samples"] = n + 1
    fp["signal_last"] = sig
    fp["signal_min"] = min(fp.get("signal_min", 100), sig)
    fp["signal_max"] = max(fp.get("signal_max", 0), sig)


class APTracker:
    def __init__(self, monitor, wifienv, storage, interval=20.0):
        self.monitor = monitor
        self.wifienv = wifienv
        self.storage = storage
        self.interval = interval
        self.aps = {}
        if storage:
            try:
                self.aps = storage.load_aps()
            except Exception:
                self.aps = {}
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._run, name="aptracker", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.wait(5.0):
            try:
                self.update()
            except Exception:
                pass
            self._stop.wait(self.interval)

    def get_aps(self):
        return sorted(self.aps.values(),
                      key=lambda a: (not a.get("is_current"), -(a.get("signal_last") or 0)))

    def update(self):
        now = time.time()
        neighbors = self.wifienv.neighbors or []
        our_ch = (self.wifienv.interference or {}).get("our_channel")
        our_ssid = (self.monitor.wifi or {}).get("ssid")
        loss_recent = self._recent_loss()
        seen_bssids = set()

        for ap in neighbors:
            bssid = ap.get("bssid")
            if not bssid:
                continue
            seen_bssids.add(bssid)
            fp = self.aps.setdefault(bssid, {
                "bssid": bssid, "ssid": ap.get("ssid"),
                "first_seen": now, "last_seen": now, "seen_count": 0,
                "signal_min": 100, "signal_max": 0,
                "signal_samples": 0, "channel_history": [],
                "corr_loss_samples": 0, "suspicion": 0,
            })
            fp["last_seen"] = now
            fp["seen_count"] = fp.get("seen_count", 0) + 1
            if ap.get("ssid"):
                fp["ssid"] = ap["ssid"]

            sig = ap.get("signal")
            if sig is not None:
                _update_signal_stats(fp, sig)

            ch = ap.get("channel")
            if ch is not None and fp.get("channel") and ch != fp["channel"]:
                hist = fp.get("channel_history", [])
                hist.append({"from": fp["channel"], "to": ch, "ts": now})
                fp["channel_history"] = hist[-20:]
            if ch is not None:
                fp["channel"] = ch
            if ap.get("band"):
                fp["band"] = ap["band"]

            is_current = bool(
                (our_ch and ch == our_ch) or
                (our_ssid and fp.get("ssid") == our_ssid))
            fp["is_current"] = is_current

            if is_current and loss_recent and sig is not None:
                fp["corr_loss_samples"] = fp.get("corr_loss_samples", 0) + 1

            weak = max(0, 60 - (fp.get("signal_avg") or 60))
            fp["suspicion"] = min(100, int(
                fp.get("corr_loss_samples", 0) * 4 + weak * 0.5
                + min(20, len(fp.get("channel_history", [])) * 5)))

            if self.storage:
                try:
                    self.storage.upsert_ap(fp)
                except Exception:
                    pass

        for bssid, fp in list(self.aps.items()):
            if bssid not in seen_bssids and fp.get("is_current"):
                fp["is_current"] = False
                if self.storage:
                    try:
                        self.storage.upsert_ap(fp)
                    except Exception:
                        pass

    def _recent_loss(self):
        hist = self.monitor.get_history(60)
        if len(hist) < 20:
            return False
        avg_loss = sum(h["loss_pct"] for h in hist) / len(hist)
        return avg_loss >= 5
