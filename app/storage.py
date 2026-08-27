import json
import sqlite3
import threading
import time
from pathlib import Path


class Storage:
    def __init__(self, db_path="data/analyzer.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()
        self._init_schema()
        self._cleanup()

    def _init_schema(self):
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL, cause TEXT, severity TEXT,
                    title TEXT, detail TEXT
                );
                CREATE TABLE IF NOT EXISTS traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL, target TEXT, hops_json TEXT,
                    sig TEXT, first_loss_hop INTEGER, segment TEXT,
                    changed INTEGER
                );
                CREATE TABLE IF NOT EXISTS proc_snaps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL, proc_name TEXT, estab INTEGER, new_conn INTEGER
                );
                CREATE TABLE IF NOT EXISTS geo_cache (
                    ip TEXT PRIMARY KEY,
                    country TEXT, city TEXT, isp TEXT, ts REAL
                );
                CREATE TABLE IF NOT EXISTS ap_fingerprints (
                    bssid TEXT PRIMARY KEY,
                    ssid TEXT,
                    first_seen REAL, last_seen REAL,
                    seen_count INTEGER DEFAULT 0,
                    signal_last INTEGER, signal_min INTEGER, signal_max INTEGER,
                    signal_avg REAL, signal_samples INTEGER DEFAULT 0,
                    signal_std REAL DEFAULT 0,
                    channel INTEGER, channel_history TEXT DEFAULT '[]',
                    band TEXT,
                    is_current INTEGER DEFAULT 0,
                    corr_loss_samples INTEGER DEFAULT 0,
                    suspicion INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS lan_nodes (
                    ip TEXT PRIMARY KEY,
                    mac TEXT, role TEXT, gateway_score INTEGER,
                    reasons TEXT, open_ports TEXT,
                    first_seen REAL, last_seen REAL
                );
                CREATE TABLE IF NOT EXISTS server_profiles (
                    target TEXT PRIMARY KEY,
                    host TEXT, profile_json TEXT, ts REAL
                );
                CREATE TABLE IF NOT EXISTS history (
                    ts INTEGER PRIMARY KEY,
                    latency REAL, jitter REAL, loss REAL,
                    down INTEGER, up INTEGER
                );
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY, value TEXT
                );
            """)
            self._conn.commit()

    def _cleanup(self):
        cutoff = time.time() - 7 * 86400
        with self._lock:
            self._conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            self._conn.execute("DELETE FROM traces WHERE ts < ?", (cutoff,))
            self._conn.execute("DELETE FROM proc_snaps WHERE ts < ?", (cutoff,))
            self._conn.commit()

    def log_event(self, ts, cause, severity, title, detail):
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (ts, cause, severity, title, detail) VALUES (?,?,?,?,?)",
                (ts, cause, severity, title, detail))
            self._conn.commit()

    def save_trace(self, ts, target, hops, sig, first_loss_hop, segment, changed):
        with self._lock:
            self._conn.execute(
                "INSERT INTO traces (ts, target, hops_json, sig, first_loss_hop, segment, changed) "
                "VALUES (?,?,?,?,?,?,?)",
                (ts, target, json.dumps(hops), sig, first_loss_hop, segment,
                 1 if changed else 0))
            self._conn.commit()

    def get_traces(self, limit=100):
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, target, sig, first_loss_hop, segment, changed FROM traces "
                "ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [{"ts": r[0], "target": r[1], "sig": r[2], "first_loss_hop": r[3],
                 "segment": r[4], "changed": bool(r[5])} for r in rows]

    def save_proc_snapshot(self, ts, procs):
        with self._lock:
            for name, st in procs.items():
                if st.get("estab", 0) > 0 or st.get("new", 0) > 0:
                    self._conn.execute(
                        "INSERT INTO proc_snaps (ts, proc_name, estab, new_conn) VALUES (?,?,?,?)",
                        (ts, name, st.get("estab", 0), st.get("new", 0)))
            self._conn.commit()

    def save_geo(self, entries):
        with self._lock:
            for e in entries:
                self._conn.execute(
                    "INSERT OR REPLACE INTO geo_cache (ip, country, city, isp, ts) "
                    "VALUES (?,?,?,?,?)",
                    (e["ip"], e.get("country"), e.get("city"), e.get("isp"), time.time()))
            self._conn.commit()

    def load_geo(self):
        with self._lock:
            rows = self._conn.execute(
                "SELECT ip, country, city, isp FROM geo_cache").fetchall()
        return {r[0]: {"country": r[1], "city": r[2], "isp": r[3]} for r in rows}

    def upsert_ap(self, fp):
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO ap_fingerprints "
                "(bssid, ssid, first_seen, last_seen, seen_count, signal_last, signal_min, "
                " signal_max, signal_avg, signal_samples, signal_std, channel, channel_history, "
                " band, is_current, corr_loss_samples, suspicion) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (fp["bssid"], fp.get("ssid"), fp.get("first_seen"), fp.get("last_seen"),
                 fp.get("seen_count", 0), fp.get("signal_last"), fp.get("signal_min"),
                 fp.get("signal_max"), fp.get("signal_avg"), fp.get("signal_samples", 0),
                 fp.get("signal_std", 0), fp.get("channel"),
                 json.dumps(fp.get("channel_history", [])), fp.get("band"),
                 1 if fp.get("is_current") else 0,
                 fp.get("corr_loss_samples", 0), fp.get("suspicion", 0)))
            self._conn.commit()

    def load_aps(self):
        with self._lock:
            rows = self._conn.execute("SELECT * FROM ap_fingerprints").fetchall()
        cols = ["bssid", "ssid", "first_seen", "last_seen", "seen_count", "signal_last",
                "signal_min", "signal_max", "signal_avg", "signal_samples", "signal_std",
                "channel", "channel_history", "band", "is_current", "corr_loss_samples",
                "suspicion"]
        out = {}
        for r in rows:
            d = dict(zip(cols, r))
            d["channel_history"] = json.loads(d.get("channel_history") or "[]")
            out[d["bssid"]] = d
        return out

    def upsert_lan_node(self, node):
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO lan_nodes "
                "(ip, mac, role, gateway_score, reasons, open_ports, first_seen, last_seen) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (node["ip"], node.get("mac"), node.get("role"), node.get("gateway_score", 0),
                 json.dumps(node.get("reasons", [])), json.dumps(node.get("open_ports", [])),
                 node.get("first_seen"), node.get("last_seen")))
            self._conn.commit()

    def load_lan_nodes(self):
        with self._lock:
            rows = self._conn.execute("SELECT * FROM lan_nodes").fetchall()
        cols = ["ip", "mac", "role", "gateway_score", "reasons", "open_ports",
                "first_seen", "last_seen"]
        out = {}
        for r in rows:
            d = dict(zip(cols, r))
            d["reasons"] = json.loads(d.get("reasons") or "[]")
            d["open_ports"] = json.loads(d.get("open_ports") or "[]")
            out[d["ip"]] = d
        return out

    def save_server_profile(self, target, host, profile, ts):
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO server_profiles (target, host, profile_json, ts) "
                "VALUES (?,?,?,?)",
                (target, host, json.dumps(profile), ts))
            self._conn.commit()

    def load_server_profiles(self):
        with self._lock:
            rows = self._conn.execute(
                "SELECT target, profile_json FROM server_profiles").fetchall()
        out = {}
        for target, pj in rows:
            try:
                out[target] = json.loads(pj)
            except Exception:
                pass
        return out

    def save_history_batch(self, rows):
        with self._lock:
            self._conn.executemany(
                "INSERT OR IGNORE INTO history (ts, latency, jitter, loss, down, up) "
                "VALUES (?,?,?,?,?,?)",
                [(r["ts"], r["latency"], r["jitter"], r["loss_pct"],
                  r["down_bps"], r["up_bps"]) for r in rows])
            self._conn.commit()

    def get_history_range(self, start_ts, end_ts, step):
        bucket = f"(ts / {max(1, int(step) * 1000)})"
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {bucket} AS b, AVG(latency), AVG(jitter), AVG(loss), "
                "AVG(down), AVG(up), COUNT(*) FROM history "
                "WHERE ts >= ? AND ts <= ? GROUP BY b ORDER BY b",
                (start_ts, end_ts)).fetchall()
        return [{"b": r[0] * step * 1000, "latency": round(r[1] or 0, 1),
                 "jitter": round(r[2] or 0, 1), "loss": round(r[3] or 0, 1),
                 "down": int(r[4] or 0), "up": int(r[5] or 0), "n": r[6]}
                for r in rows]

    def history_ts_range(self):
        with self._lock:
            row = self._conn.execute(
                "SELECT MIN(ts), MAX(ts), COUNT(*) FROM history").fetchone()
        return {"min_ts": row[0], "max_ts": row[1], "count": row[2]}

    def get_meta(self, key):
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key, value):
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)",
                (key, str(value)))
            self._conn.commit()

    def list_recent_events(self, days=7, limit=200):
        cutoff = time.time() - days * 86400
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, cause, severity, title FROM events "
                "WHERE ts >= ? ORDER BY ts DESC LIMIT ?", (cutoff, limit)).fetchall()
        return [{"ts": r[0], "cause": r[1], "severity": r[2], "title": r[3]} for r in rows]

    def close(self):
        with self._lock:
            self._conn.close()
