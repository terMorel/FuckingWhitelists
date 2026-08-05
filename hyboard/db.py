from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users_meta (
    username TEXT PRIMARY KEY,
    note TEXT NOT NULL DEFAULT '',
    expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    action TEXT NOT NULL,
    username TEXT,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_log(created_at DESC);
CREATE TABLE IF NOT EXISTS traffic_totals (
    username TEXT PRIMARY KEY,
    tx_total INTEGER NOT NULL DEFAULT 0,
    rx_total INTEGER NOT NULL DEFAULT 0,
    raw_tx INTEGER NOT NULL DEFAULT 0,
    raw_rx INTEGER NOT NULL DEFAULT 0,
    connections INTEGER NOT NULL DEFAULT 0,
    sample_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS traffic_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    username TEXT NOT NULL,
    tx_rate REAL NOT NULL DEFAULT 0,
    rx_rate REAL NOT NULL DEFAULT 0,
    tx_total INTEGER NOT NULL DEFAULT 0,
    rx_total INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_traffic_samples_user_time
    ON traffic_samples(username, created_at DESC);
CREATE TABLE IF NOT EXISTS system_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    cpu_percent REAL NOT NULL DEFAULT 0,
    memory_percent REAL NOT NULL DEFAULT 0,
    disk_percent REAL NOT NULL DEFAULT 0,
    load1 REAL NOT NULL DEFAULT 0,
    net_rx_rate REAL NOT NULL DEFAULT 0,
    net_tx_rate REAL NOT NULL DEFAULT 0,
    raw_net_rx INTEGER NOT NULL DEFAULT 0,
    raw_net_tx INTEGER NOT NULL DEFAULT 0,
    udp_errors INTEGER NOT NULL DEFAULT 0,
    raw_udp_errors INTEGER NOT NULL DEFAULT 0,
    services_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_system_samples_time ON system_samples(created_at DESC);
CREATE TABLE IF NOT EXISTS external_probes (
    name TEXT PRIMARY KEY,
    last_seen TEXT NOT NULL,
    ok INTEGER NOT NULL,
    latency_ms REAL,
    detail TEXT NOT NULL DEFAULT '',
    network TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS monitor_status (
    name TEXT PRIMARY KEY,
    available INTEGER NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alert_state (
    key TEXT PRIMARY KEY,
    signature TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS active_alerts (
    key TEXT PRIMARY KEY,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path

    def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def metadata(self) -> dict[str, dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM users_meta").fetchall()
        return {row["username"]: dict(row) for row in rows}

    def upsert_user(self, username: str, note: str = "", expires_at: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO users_meta(username, note, expires_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(username) DO UPDATE SET
                     note=excluded.note,
                     expires_at=excluded.expires_at,
                     updated_at=excluded.updated_at""",
                (username, note, expires_at, now, now),
            )

    def delete_user(self, username: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM users_meta WHERE username = ?", (username,))

    def audit(self, action: str, username: str | None = None, detail: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO audit_log(created_at, action, username, detail) VALUES (?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), action, username, detail[:300]),
            )

    def recent_audit(self, limit: int = 20) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def due_users(self) -> list[str]:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT username FROM users_meta WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (now,),
            ).fetchall()
        return [row["username"] for row in rows]

    @staticmethod
    def _counter_delta(current: int, previous: int) -> int:
        return current - previous if current >= previous else current

    def record_traffic(self, users: dict[str, dict], now: datetime) -> None:
        stamp = now.isoformat()
        with self.connect() as conn:
            for username, values in users.items():
                current_tx = max(0, int(values.get("tx", 0)))
                current_rx = max(0, int(values.get("rx", 0)))
                connections = max(0, int(values.get("connections", 0)))
                previous = conn.execute(
                    "SELECT * FROM traffic_totals WHERE username = ?", (username,)
                ).fetchone()
                if previous:
                    elapsed = max(
                        1.0,
                        (now - datetime.fromisoformat(previous["sample_at"])).total_seconds(),
                    )
                    delta_tx = self._counter_delta(current_tx, previous["raw_tx"])
                    delta_rx = self._counter_delta(current_rx, previous["raw_rx"])
                    total_tx = previous["tx_total"] + delta_tx
                    total_rx = previous["rx_total"] + delta_rx
                    tx_rate = delta_tx / elapsed
                    rx_rate = delta_rx / elapsed
                else:
                    total_tx, total_rx = current_tx, current_rx
                    tx_rate = rx_rate = 0.0
                conn.execute(
                    """INSERT INTO traffic_totals
                       (username, tx_total, rx_total, raw_tx, raw_rx, connections, sample_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(username) DO UPDATE SET
                         tx_total=excluded.tx_total, rx_total=excluded.rx_total,
                         raw_tx=excluded.raw_tx, raw_rx=excluded.raw_rx,
                         connections=excluded.connections, sample_at=excluded.sample_at""",
                    (
                        username,
                        total_tx,
                        total_rx,
                        current_tx,
                        current_rx,
                        connections,
                        stamp,
                    ),
                )
                conn.execute(
                    """INSERT INTO traffic_samples
                       (created_at, username, tx_rate, rx_rate, tx_total, rx_total)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (stamp, username, tx_rate, rx_rate, total_tx, total_rx),
                )

    def record_system(self, values: dict, now: datetime) -> None:
        stamp = now.isoformat()
        raw_rx = max(0, int(values.get("net_rx_bytes", 0)))
        raw_tx = max(0, int(values.get("net_tx_bytes", 0)))
        raw_udp_errors = max(0, int(values.get("udp_errors", 0)))
        with self.connect() as conn:
            previous = conn.execute(
                "SELECT * FROM system_samples ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if previous:
                elapsed = max(
                    1.0,
                    (now - datetime.fromisoformat(previous["created_at"])).total_seconds(),
                )
                rx_rate = self._counter_delta(raw_rx, previous["raw_net_rx"]) / elapsed
                tx_rate = self._counter_delta(raw_tx, previous["raw_net_tx"]) / elapsed
                udp_error_delta = self._counter_delta(
                    raw_udp_errors, previous["raw_udp_errors"]
                )
            else:
                rx_rate = tx_rate = 0.0
                udp_error_delta = 0
            conn.execute(
                """INSERT INTO system_samples
                   (created_at, cpu_percent, memory_percent, disk_percent, load1,
                    net_rx_rate, net_tx_rate, raw_net_rx, raw_net_tx,
                    udp_errors, raw_udp_errors, services_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    stamp,
                    float(values.get("cpu_percent", 0)),
                    float(values.get("memory_percent", 0)),
                    float(values.get("disk_percent", 0)),
                    float(values.get("load1", 0)),
                    rx_rate,
                    tx_rate,
                    raw_rx,
                    raw_tx,
                    udp_error_delta,
                    raw_udp_errors,
                    json.dumps(values.get("services", {}), ensure_ascii=False),
                ),
            )

    def record_probe(
        self,
        name: str,
        ok: bool,
        latency_ms: float | None,
        detail: str,
        network: str,
        now: datetime,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO external_probes
                   (name, last_seen, ok, latency_ms, detail, network)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                     last_seen=excluded.last_seen, ok=excluded.ok,
                     latency_ms=excluded.latency_ms, detail=excluded.detail,
                     network=excluded.network""",
                (name, now.isoformat(), int(ok), latency_ms, detail[:200], network[:80]),
            )

    def probes(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM external_probes ORDER BY name"
            ).fetchall()
        return [{**dict(row), "ok": bool(row["ok"])} for row in rows]

    def set_monitor_status(
        self, name: str, available: bool, detail: str, now: datetime
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO monitor_status(name, available, detail, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                     available=excluded.available, detail=excluded.detail,
                     updated_at=excluded.updated_at""",
                (name, int(available), detail[:200], now.isoformat()),
            )

    def alert_states(self) -> dict[str, str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT key, signature FROM alert_state").fetchall()
        return {row["key"]: row["signature"] for row in rows}

    def set_alert_state(self, key: str, signature: str, now: datetime) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO alert_state(key, signature, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                     signature=excluded.signature, updated_at=excluded.updated_at""",
                (key, signature, now.isoformat()),
            )

    def delete_alert_state(self, key: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM alert_state WHERE key = ?", (key,))

    def replace_active_alerts(self, alerts: list[dict], now: datetime) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM active_alerts")
            conn.executemany(
                """INSERT INTO active_alerts(key, severity, title, detail, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                [
                    (
                        alert["key"],
                        alert["severity"],
                        alert["title"],
                        alert["detail"],
                        now.isoformat(),
                    )
                    for alert in alerts
                ],
            )

    def monitoring_summary(self, alerts: list[dict] | None = None) -> dict:
        with self.connect() as conn:
            totals = conn.execute("SELECT * FROM traffic_totals").fetchall()
            system = conn.execute(
                "SELECT * FROM system_samples ORDER BY id DESC LIMIT 1"
            ).fetchone()
            history = conn.execute(
                """SELECT created_at, cpu_percent, memory_percent, disk_percent,
                          net_rx_rate, net_tx_rate
                   FROM system_samples ORDER BY id DESC LIMIT 60"""
            ).fetchall()
            status_rows = conn.execute("SELECT * FROM monitor_status").fetchall()
            alert_rows = conn.execute(
                """SELECT key, severity, title, detail, updated_at FROM active_alerts
                   ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                            key"""
            ).fetchall()
        traffic = {
            row["username"]: {
                "tx_total": row["tx_total"],
                "rx_total": row["rx_total"],
                "tx_rate": self._latest_rate(row["username"], "tx_rate"),
                "rx_rate": self._latest_rate(row["username"], "rx_rate"),
                "connections": row["connections"],
                "online": row["connections"] > 0,
                "updated_at": row["sample_at"],
            }
            for row in totals
        }
        system_dict = dict(system) if system else {}
        if system_dict:
            system_dict["services"] = json.loads(system_dict.pop("services_json"))
        statuses = {
            row["name"]: {
                "available": bool(row["available"]),
                "detail": row["detail"],
                "updated_at": row["updated_at"],
            }
            for row in status_rows
        }
        return {
            "traffic": traffic,
            "system": system_dict,
            "history": [dict(row) for row in reversed(history)],
            "probes": self.probes(),
            "sources": statuses,
            "alerts": alerts if alerts is not None else [dict(row) for row in alert_rows],
        }

    def _latest_rate(self, username: str, column: str) -> float:
        if column not in {"tx_rate", "rx_rate"}:
            raise ValueError("invalid rate column")
        with self.connect() as conn:
            row = conn.execute(
                f"SELECT {column} AS rate FROM traffic_samples "  # noqa: S608
                "WHERE username = ? ORDER BY id DESC LIMIT 1",
                (username,),
            ).fetchone()
        return float(row["rate"]) if row else 0.0

    def prune_monitoring(self, days: int = 30) -> None:
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute("DELETE FROM traffic_samples WHERE created_at < ?", (cutoff_iso,))
            conn.execute("DELETE FROM system_samples WHERE created_at < ?", (cutoff_iso,))
