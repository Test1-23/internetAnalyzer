import hashlib
import re
import subprocess
import threading
import time
from collections import deque

from .monitor import _decode
from .storage import Storage

TRACE_INTERVAL = 600
TRIGGER_COOLDOWN = 60
IP_PAT = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def _parse_times(toks):
    times = []
    for t in toks:
        if t == "*":
            times.append(None)
        elif t == "<1":
            times.append(0.5)
        elif re.fullmatch(r"\d+", t):
            times.append(float(t))
    while len(times) < 3:
        times.append(None)
    return times[:3]


def _parse_hop_line(line):
    toks = line.split()
    if len(toks) < 3 or not toks[0].isdigit():
        return None
    ip = toks[-1]
    if not IP_PAT.match(ip):
        return None
    times = _parse_times(toks[1:-1])
    return {"ip": ip, "times": times}


def _classify(ip, gateway):
    if gateway and ip == gateway:
        return "gateway"
    parts = [int(x) for x in ip.split(".")]
    if parts[0] == 10 or (parts[0] == 192 and parts[1] == 168) \
            or (parts[0] == 172 and 16 <= parts[1] <= 31):
        return "private"
    if parts[0] == 100 and 64 <= parts[1] <= 127:
        return "carrier_nat"
    return "public"


class PathMonitor:
    def __init__(self, monitor, storage: Storage, target="223.5.5.5", interval=TRACE_INTERVAL):
        self.monitor = monitor
        self.storage = storage
        self.target = target
        self.interval = interval
        self.latest = None
        self.history = deque(maxlen=72)
        self._stop = threading.Event()
        self._trigger = threading.Event()
        self._last_trigger = 0.0
        self._prev_sig = None

    def start(self):
        threading.Thread(target=self._run, name="pathmon", daemon=True).start()

    def stop(self):
        self._stop.set()

    def trigger(self):
        now = time.time()
        if now - self._last_trigger >= TRIGGER_COOLDOWN:
            self._last_trigger = now
            self._trigger.set()

    def get_latest(self):
        return self.latest

    def get_history(self):
        return list(self.history)

    def _run(self):
        while not self._stop.wait(2.0):
            fired = False
            if self._trigger.is_set():
                self._trigger.clear()
                fired = True
            else:
                deadline = getattr(self, "_next_run", 0)
                if time.time() >= deadline:
                    fired = True
            if fired:
                self._next_run = time.time() + self.interval
                try:
                    self._run_trace()
                except Exception:
                    pass

    def _run_trace(self):
        gw = self.monitor.gateway
        p = subprocess.run(["tracert", "-d", "-w", "500", "-h", "20", "-4", self.target],
                           capture_output=True, timeout=90)
        text = _decode(p.stdout)
        hops = []
        for line in text.splitlines():
            parsed = _parse_hop_line(line)
            if not parsed:
                continue
            loss = sum(1 for t in parsed["times"] if t is None)
            valid = [t for t in parsed["times"] if t is not None]
            hops.append({
                "hop": len(hops) + 1,
                "ip": parsed["ip"],
                "times": parsed["times"],
                "loss": loss,
                "avg": round(sum(valid) / len(valid), 1) if valid else None,
            })
        if not hops:
            return
        sig = hashlib.md5("|".join(h["ip"] for h in hops).encode()).hexdigest()[:12]
        changed = bool(self._prev_sig and sig != self._prev_sig)
        self._prev_sig = sig
        first_loss_hop, segment = self._locate_loss(hops, gw)
        now = time.time()
        result = {
            "ts": now,
            "target": self.target,
            "hops": hops,
            "sig": sig,
            "changed": changed,
            "first_loss_hop": first_loss_hop,
            "segment": segment,
        }
        self.latest = result
        self.history.append({k: result[k] for k in ("ts", "target", "sig", "changed",
                                                    "first_loss_hop", "segment")})
        if self.storage:
            self.storage.save_trace(now, self.target, hops, sig, first_loss_hop, segment, changed)

    @staticmethod
    def _locate_loss(hops, gateway):
        n = len(hops)
        for i, h in enumerate(hops):
            if h["loss"] >= 2:
                downstream_ok = any(
                    hops[j]["loss"] <= 1 for j in range(i + 1, n))
                if not downstream_ok:
                    continue
                seg = _classify(h["ip"], gateway)
                label = {"gateway": "家庭网关段", "private": "本地/内网段",
                         "carrier_nat": "运营商NAT段", "public": "公网骨干段"}[seg]
                return h["hop"], label
        return None, None
