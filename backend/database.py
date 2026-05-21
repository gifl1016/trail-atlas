"""
Trail Atlas – Datenbank-Layer
==============================
SQLite mit persistenter Single-Connection und WAL-Modus.

Wichtig: Wir nutzen EINE persistente Connection für alle Operationen, damit
Writes für nachfolgende Reads sofort sichtbar sind. Mit FastAPI Single-Worker
(siehe systemd Service) ist das konsistent und thread-safe via Lock.

Schema-Version: 2  (Multi-User-Fundament)
  v1 → v2: users, garmin_credentials, invite_codes hinzugefügt.
            activities.user_id (nullable für Rückwärtskompatibilität).
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
        """DB-Tabellen erstellen und ggf. migrieren, persistente Connection öffnen."""
        log.info(f"Database: {self.path}")
        self._conn = self._open()

        with self._lock:
            # ── v1-Tabellen (unverändert) ─────────────────────────────────────
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

                CREATE TABLE IF NOT EXISTS sync_log (
                    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id              INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    status               TEXT    NOT NULL,
                    started_at           TEXT    NOT NULL,
                    finished_at          TEXT,
                    activities_imported  INTEGER DEFAULT 0,
                    gps_points_imported  INTEGER DEFAULT 0,
                    activities_skipped   INTEGER DEFAULT 0,
                    error_message        TEXT,
                    duration_s           REAL
                );

                CREATE INDEX IF NOT EXISTS idx_sync_log_started
                    ON sync_log (started_at DESC);

                COMMIT;
            """)

            # ── v2: Multi-User-Tabellen ───────────────────────────────────────
            # CREATE TABLE IF NOT EXISTS ist sicher idempotent –
            # auf frischer DB sofort angelegt, auf bestehender v1-DB
            # beim ersten Neustart nachgezogen.
            self._conn.executescript("""
                BEGIN;

                -- Nutzer-Tabelle
                -- password_hash: bcrypt-Hash (60 Zeichen)
                CREATE TABLE IF NOT EXISTS users (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    username      TEXT    NOT NULL UNIQUE,
                    password_hash TEXT    NOT NULL,
                    is_admin      INTEGER NOT NULL DEFAULT 0,
                    created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                );

                CREATE INDEX IF NOT EXISTS idx_users_username
                    ON users (username);

                -- Garmin-Zugangsdaten pro Nutzer
                -- token_json: verschlüsselter JSON-Blob (Fernet, Schlüssel aus garmin.env)
                -- email_hash: SHA-256 der normalisierten Garmin-Email (deterministisch).
                --   Dient dem UNIQUE-Constraint: ein Garmin-Account darf nur einmal
                --   registriert werden (sonst Activity-ID-Kollisionen beim Sync).
                CREATE TABLE IF NOT EXISTS garmin_credentials (
                    user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    token_json  TEXT    NOT NULL,
                    email_hash  TEXT,
                    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                );

                -- Einmal-Einladungscodes für Registrierung
                -- code:       zufälliger URL-safe Token (32 Zeichen)
                -- used_at:    NULL = noch nicht eingelöst
                -- expires_at: ISO-8601, nach diesem Datum abgelaufen
                CREATE TABLE IF NOT EXISTS invite_codes (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    code        TEXT    NOT NULL UNIQUE,
                    created_by  INTEGER REFERENCES users(id),
                    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                    expires_at  TEXT    NOT NULL,
                    used_at     TEXT,
                    used_by     INTEGER REFERENCES users(id)
                );

                CREATE INDEX IF NOT EXISTS idx_invite_code
                    ON invite_codes (code);

                COMMIT;
            """)

        # ── Migration: activities.user_id nachträglich hinzufügen ─────────────
        self._migrate_activities_user_id()

        # ── Migration: start_lat/start_lng nullable machen (Schema v3) ────
        self._migrate_nullable_coords()

        # ── Migration: sync_log.user_id nachträglich hinzufügen ────────────
        self._migrate_sync_log_user_id()

        # ── Migration: garmin_credentials.email_hash nachträglich hinzufügen ──
        self._migrate_garmin_email_hash()

        log.info("Database initialized (schema v3)")

    def _migrate_activities_user_id(self):
        """
        Fügt activities.user_id hinzu falls noch nicht vorhanden.

        Warum hier und nicht im executescript oben?
        SQLite unterstützt kein 'ALTER TABLE ADD COLUMN IF NOT EXISTS'.
        Wir lesen die vorhandenen Spalten aus und entscheiden dann.

        Bestehende Aktivitäten bekommen user_id = NULL.
        Das ist gewollt: in Schritt 2 (Auth-Layer) wird der erste
        angelegte User als Eigentümer aller NULL-Aktivitäten gesetzt.
        """
        cols = [
            row[1]
            for row in self._conn.execute("PRAGMA table_info(activities)")
        ]
        if "user_id" not in cols:
            with self._lock:
                self._conn.execute(
                    "ALTER TABLE activities ADD COLUMN user_id INTEGER "
                    "REFERENCES users(id)"
                )
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_activities_user "
                    "ON activities (user_id)"
                )
            log.info("Migration: activities.user_id + index added (existing rows → NULL)")
        else:
            log.debug("Migration: activities.user_id already present, skipping")

    def _migrate_sync_log_user_id(self):
        """Fügt sync_log.user_id hinzu für Per-User Sync-Status."""
        cols = {
            row[1] for row in
            self._conn.execute("PRAGMA table_info(sync_log)").fetchall()
        }
        with self._lock:
            if "user_id" not in cols:
                # Bestehende DB: Spalte nachträglich hinzufügen
                self._conn.execute(
                    "ALTER TABLE sync_log ADD COLUMN user_id INTEGER "
                    "REFERENCES users(id) ON DELETE SET NULL"
                )
                log.info("Migration: sync_log.user_id column added")
            else:
                log.debug("Migration: sync_log.user_id already present")

            # Index immer (idempotent) erstellen – deckt frische DB
            # (Spalte schon da) UND migrierte DB (Spalte gerade ergänzt) ab.
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sync_log_user "
                "ON sync_log (user_id, started_at DESC)"
            )

    def _migrate_garmin_email_hash(self):
        """
        Fügt garmin_credentials.email_hash hinzu.

        email_hash = SHA-256 der normalisierten Garmin-Email. Erlaubt einen
        UNIQUE-Constraint, damit ein Garmin-Account nur einmal registriert
        werden kann (sonst Activity-ID-Kollisionen beim Sync).

        Das Backfilling bestehender Zeilen (Hash aus token_json berechnen)
        passiert in auth.py beim App-Start, weil dort der Fernet-Key liegt.
        Der UNIQUE-Index wird ebenfalls erst nach dem Backfilling erstellt.
        """
        cols = {
            row[1] for row in
            self._conn.execute("PRAGMA table_info(garmin_credentials)").fetchall()
        }
        if "email_hash" not in cols:
            with self._lock:
                self._conn.execute(
                    "ALTER TABLE garmin_credentials ADD COLUMN email_hash TEXT"
                )
            log.info("Migration: garmin_credentials.email_hash column added")
        else:
            log.debug("Migration: garmin_credentials.email_hash already present")

    def _migrate_nullable_coords(self):
        """
        Schema v3: start_lat/start_lng von NOT NULL → nullable.

        Warum? Activities ohne GPS-Track (Strength Training, Yoga, Indoor Cycling)
        haben oft keine Startkoordinaten. Bisher wurden diese im Sync übersprungen.
        Ab v3 werden alle Activities gespeichert, auch ohne Koordinaten.

        SQLite kann NOT NULL nicht direkt entfernen. Lösung: Tabelle neu erstellen
        und Daten migrieren. Nur nötig wenn die Spalten noch NOT NULL sind.
        """
        # Prüfe ob start_lat NOT NULL ist
        cols = self._conn.execute("PRAGMA table_info(activities)").fetchall()
        for col in cols:
            # col: (cid, name, type, notnull, dflt_value, pk)
            if col[1] == "start_lat" and col[3] == 1:  # notnull=1 → Migration nötig
                log.info("Migration v3: start_lat/start_lng → nullable")
                with self._lock:
                    # Foreign Keys temporär deaktivieren – sonst blockt SQLite
                    # das DROP TABLE wegen gps_points REFERENCES activities.
                    # PRAGMA foreign_keys kann nicht innerhalb einer Transaktion
                    # geändert werden, daher vor BEGIN.
                    self._conn.execute("PRAGMA foreign_keys=OFF")
                    self._conn.executescript("""
                        BEGIN;

                        CREATE TABLE activities_new (
                            activity_id   TEXT PRIMARY KEY,
                            activity_type TEXT NOT NULL DEFAULT 'unknown',
                            start_date    TEXT NOT NULL,
                            end_date      TEXT,
                            start_lat     REAL,
                            start_lng     REAL,
                            user_id       INTEGER REFERENCES users(id)
                        );

                        INSERT INTO activities_new
                            SELECT activity_id, activity_type, start_date, end_date,
                                   start_lat, start_lng, user_id
                            FROM activities;

                        DROP TABLE activities;
                        ALTER TABLE activities_new RENAME TO activities;

                        CREATE INDEX IF NOT EXISTS idx_activities_date
                            ON activities (start_date DESC);
                        CREATE INDEX IF NOT EXISTS idx_activities_type
                            ON activities (activity_type);
                        CREATE INDEX IF NOT EXISTS idx_activities_user
                            ON activities (user_id);

                        COMMIT;
                    """)
                    self._conn.execute("PRAGMA foreign_keys=ON")
                log.info("Migration v3: activities table recreated (nullable coords + indices)")
                return
        log.debug("Migration v3: start_lat already nullable, skipping")

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
