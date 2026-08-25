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

    def close(self):
        with self._lock:
            self._conn.close()
