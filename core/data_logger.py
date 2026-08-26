"""
SQLite-based data logger for PUMPGURU snapshots.
Stores every poll so reports can compute trends, uptime, and fault history.
"""

import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "pumpguru.db")


class DataLogger:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                measurements_json TEXT,
                faults_json TEXT,
                setpoints_json TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS fault_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                fault_name TEXT NOT NULL,
                state TEXT NOT NULL  -- 'ACTIVE' or 'CLEARED'
            )
        """)
        conn.commit()
        conn.close()

    def log_snapshot(self, snapshot: dict, previous_faults: dict = None):
        """Save a snapshot and detect fault transitions (for event log)."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO snapshots (timestamp, measurements_json, faults_json, setpoints_json) VALUES (?, ?, ?, ?)",
            (
                snapshot["timestamp"],
                json.dumps(snapshot["measurements"]),
                json.dumps(snapshot["faults"]),
                json.dumps(snapshot["setpoints"]),
            ),
        )

        # detect state transitions for event log
        if previous_faults is not None:
            for fault_name, is_active in snapshot["faults"].items():
                was_active = previous_faults.get(fault_name)
                if is_active != was_active and is_active is not None:
                    state = "ACTIVE" if is_active else "CLEARED"
                    cur.execute(
                        "INSERT INTO fault_events (timestamp, fault_name, state) VALUES (?, ?, ?)",
                        (snapshot["timestamp"], fault_name, state),
                    )
        conn.commit()
        conn.close()

    def get_snapshots(self, since: str = None, until: str = None):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        query = "SELECT * FROM snapshots WHERE 1=1"
        params = []
        if since:
            query += " AND timestamp >= ?"
            params.append(since)
        if until:
            query += " AND timestamp <= ?"
            params.append(until)
        query += " ORDER BY timestamp ASC"
        cur.execute(query, params)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        for r in rows:
            r["measurements"] = json.loads(r["measurements_json"])
            r["faults"] = json.loads(r["faults_json"])
            r["setpoints"] = json.loads(r["setpoints_json"])
        return rows

    def get_fault_events(self, since: str = None, until: str = None):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        query = "SELECT * FROM fault_events WHERE 1=1"
        params = []
        if since:
            query += " AND timestamp >= ?"
            params.append(since)
        if until:
            query += " AND timestamp <= ?"
            params.append(until)
        query += " ORDER BY timestamp ASC"
        cur.execute(query, params)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
