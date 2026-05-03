"""
Trail Atlas – Datenbank-Layer
==============================
SQLite mit persistenter Single-Connection und WAL-Modus.

Wichtig: Wir nutzen EINE persistente Connection für alle Operationen, damit
Writes für nachfolgende Reads sofort sichtbar sind. Mit FastAPI Single-Worker
(siehe systemd Service) ist das konsistent und thread-safe via Lock.
"""

import sqlite3
import threading
import logging
import os
from pathlib import Path

log = logging.getLogger("trail-atlas.db")

DB_PATH = Path(os.getenv("DB_PATH", "/var/lib/trail-atlas/trail_atlas.db"))


class Database:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()   # serialisiert Writes

    def _open(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            isolation_level=None,    # autocommit – wir managen Transaktionen selbst
            timeout=30.0,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-32000")   # 32 MB
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA temp_store=MEMORY")
        return conn

    def init(self):
        """DB-Tabellen erstellen, persistente Connection öffnen."""
        log.info(f"Database: {self.path}")
        self._conn = self._open()

        with self._lock:
            self._conn.executescript("""
                BEGIN;

                CREATE TABLE IF NOT EXISTS activities (
                    activity_id   TEXT PRIMARY KEY,
                    activity_type TEXT NOT NULL DEFAULT 'unknown',
                    start_date    TEXT NOT NULL,
                    end_date      TEXT,
                    start_lat     REAL NOT NULL,
                    start_lng     REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS gps_points (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    activity_id   TEXT    NOT NULL,
                    lat           REAL    NOT NULL,
                    lng           REAL    NOT NULL,
                    FOREIGN KEY (activity_id) REFERENCES activities(activity_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_gps_activity
                    ON gps_points (activity_id);

                CREATE INDEX IF NOT EXISTS idx_activities_date
                    ON activities (start_date DESC);

                CREATE INDEX IF NOT EXISTS idx_activities_type
                    ON activities (activity_type);

                COMMIT;
            """)

        log.info("Database initialized")

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            log.info("Database connection closed")

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        """Read-Operation."""
        if self._conn is None:
            raise RuntimeError("Database not initialized")
        cur = self._conn.execute(sql, params)
        return cur.fetchall()

    def execute(self, sql: str, params: tuple = ()):
        """Single-Write – durch Lock serialisiert."""
        if self._conn is None:
            raise RuntimeError("Database not initialized")
        with self._lock:
            self._conn.execute(sql, params)

    def executemany(self, sql: str, params: list[tuple]):
        """Bulk-Write in einer Transaktion – deutlich schneller."""
        if self._conn is None:
            raise RuntimeError("Database not initialized")
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.executemany(sql, params)
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def execute_script(self, sql: str):
        """Mehrere Statements am Stück."""
        if self._conn is None:
            raise RuntimeError("Database not initialized")
        with self._lock:
            self._conn.executescript(sql)
