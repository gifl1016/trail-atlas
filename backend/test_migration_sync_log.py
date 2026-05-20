"""
Test: Migration auf bestehender Produktions-DB
================================================
Reproduziert den Deploy-Fehler: eine alte DB hat sync_log OHNE user_id-Spalte.
Die init() muss das ohne Fehler migrieren.
"""
import os
import sys
import sqlite3
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

passed = 0
failed = 0
def check(name, cond, detail=""):
    global passed, failed
    if cond:
        print(f"  ✅ {name}")
        passed += 1
    else:
        print(f"  ❌ {name} — {detail}")
        failed += 1


# ── Szenario 1: Bestehende v1-DB (sync_log OHNE user_id) ─────────────────
print("\n═══ Szenario 1: Bestehende DB ohne sync_log.user_id ═══")

tmp1 = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
tmp1.close()

# Alte DB simulieren – sync_log wie in v1, ohne user_id
conn = sqlite3.connect(tmp1.name)
conn.executescript("""
    CREATE TABLE activities (
        activity_id TEXT PRIMARY KEY,
        activity_type TEXT NOT NULL DEFAULT 'unknown',
        start_date TEXT NOT NULL,
        end_date TEXT,
        start_lat REAL NOT NULL,
        start_lng REAL NOT NULL
    );
    CREATE TABLE gps_points (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        activity_id TEXT NOT NULL,
        lat REAL NOT NULL, lng REAL NOT NULL,
        FOREIGN KEY (activity_id) REFERENCES activities(activity_id) ON DELETE CASCADE
    );
    CREATE TABLE sync_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        status TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        activities_imported INTEGER DEFAULT 0,
        gps_points_imported INTEGER DEFAULT 0,
        activities_skipped INTEGER DEFAULT 0,
        error_message TEXT,
        duration_s REAL
    );
    CREATE INDEX idx_sync_log_started ON sync_log (started_at DESC);
""")
# Ein paar alte Sync-Log-Einträge
conn.execute("INSERT INTO sync_log (status, started_at) VALUES ('ok', '2025-01-01T08:00:00')")
conn.execute("INSERT INTO activities VALUES ('a1','hiking','2025-01-01',NULL,47.0,11.0)")
conn.commit()
conn.close()

# Verify: sync_log has NO user_id column
conn = sqlite3.connect(tmp1.name)
cols = {r[1] for r in conn.execute("PRAGMA table_info(sync_log)").fetchall()}
conn.close()
check("Vorher: sync_log hat KEINE user_id", "user_id" not in cols)

# Now run Database.init() – this must NOT crash
os.environ["DB_PATH"] = tmp1.name
from database import Database
try:
    db = Database()
    db.path = __import__("pathlib").Path(tmp1.name)
    db.init()
    check("init() läuft ohne Fehler durch", True)
except Exception as e:
    check("init() läuft ohne Fehler durch", False, str(e))
    import traceback; traceback.print_exc()

# Verify migration worked
cols = {r[1] for r in db._conn.execute("PRAGMA table_info(sync_log)").fetchall()}
check("Nachher: sync_log hat user_id", "user_id" in cols)

# Verify index exists
idx = db._conn.execute(
    "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_sync_log_user'"
).fetchall()
check("Index idx_sync_log_user existiert", len(idx) == 1)

# Old data preserved
old_log = db._conn.execute("SELECT COUNT(*) FROM sync_log").fetchone()[0]
check("Alte sync_log-Einträge erhalten", old_log == 1)

db.close()
os.unlink(tmp1.name)


# ── Szenario 2: Frische DB (von scratch) ─────────────────────────────────
print("\n═══ Szenario 2: Frische DB ═══")

tmp2 = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
tmp2.close()
os.environ["DB_PATH"] = tmp2.name

# Reload module to get fresh instance
import importlib, database as database_mod
importlib.reload(database_mod)

try:
    db2 = database_mod.Database()
    db2.path = __import__("pathlib").Path(tmp2.name)
    db2.init()
    check("Frische DB init() ohne Fehler", True)
except Exception as e:
    check("Frische DB init() ohne Fehler", False, str(e))
    import traceback; traceback.print_exc()

cols = {r[1] for r in db2._conn.execute("PRAGMA table_info(sync_log)").fetchall()}
check("Frische DB: sync_log hat user_id", "user_id" in cols)

idx = db2._conn.execute(
    "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_sync_log_user'"
).fetchall()
check("Frische DB: Index idx_sync_log_user existiert", len(idx) == 1)

# Insert a sync_log row with user_id to verify it works
# (need a user first because of FK constraint)
db2._conn.execute(
    "INSERT INTO users (username, password_hash) VALUES ('testuser', 'hash')"
)
db2._conn.commit()
uid = db2._conn.execute("SELECT id FROM users WHERE username='testuser'").fetchone()[0]
db2._conn.execute(
    "INSERT INTO sync_log (user_id, status, started_at) VALUES (?, 'ok', '2025-01-01T08:00:00')",
    (uid,)
)
db2._conn.commit()
row = db2._conn.execute("SELECT user_id FROM sync_log").fetchone()
check("user_id schreibbar/lesbar", row[0] == uid)

db2.close()
os.unlink(tmp2.name)


# ── Szenario 3: Doppelter init() (idempotent) ────────────────────────────
print("\n═══ Szenario 3: init() zweimal (idempotent) ═══")
tmp3 = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
tmp3.close()
os.environ["DB_PATH"] = tmp3.name
importlib.reload(database_mod)
try:
    db3 = database_mod.Database()
    db3.path = __import__("pathlib").Path(tmp3.name)
    db3.init()
    db3.close()
    # Second init on same DB
    db3b = database_mod.Database()
    db3b.path = __import__("pathlib").Path(tmp3.name)
    db3b.init()
    check("Zweiter init() ohne Fehler (idempotent)", True)
    db3b.close()
except Exception as e:
    check("Zweiter init() ohne Fehler (idempotent)", False, str(e))
    import traceback; traceback.print_exc()
os.unlink(tmp3.name)


print(f"\n{'='*50}")
print(f"  {passed} passed, {failed} failed")
print(f"{'='*50}")
sys.exit(1 if failed else 0)
