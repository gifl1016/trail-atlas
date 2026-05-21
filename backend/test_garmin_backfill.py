"""
Test: Backfilling auf bestehender DB mit Garmin-Duplikaten
============================================================
Simuliert den aktuellen Produktionsstand: zwei User teilen einen Garmin-Account
(altes Bug-Szenario). Das Backfilling muss das erkennen und KEINEN
UNIQUE-Index erstellen (sonst Crash), sondern warnen.
"""
import os
import sys
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DB_PATH"] = _tmp.name
os.environ["SECRET_KEY"] = "test-backfill-dupes"
_tmp.close()

from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import auth
from database import Database

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


# ── Setup: DB mit zwei Usern, gleiche Garmin-Email, KEIN email_hash ─────
print("\n═══ Setup: Bestehende DB mit Garmin-Duplikat ═══")
db = Database(Path(_tmp.name))
db.init()

# Zwei User
u1 = auth.create_user(db, "user1", "pass1234")
u2 = auth.create_user(db, "user2", "pass1234")

# Beide bekommen den GLEICHEN Garmin-Account – aber wir simulieren den
# alten Zustand: email_hash wird manuell auf NULL gesetzt
auth.save_garmin_credentials(db, u1, "shared@garmin.com", "pw", enforce_unique=False)
auth.save_garmin_credentials(db, u2, "shared@garmin.com", "pw", enforce_unique=False)
db.execute("UPDATE garmin_credentials SET email_hash = NULL")

# Verify: beide haben NULL hash
null_count = db.query(
    "SELECT COUNT(*) as n FROM garmin_credentials WHERE email_hash IS NULL"
)[0]["n"]
check("Setup: 2 Credentials ohne email_hash", null_count == 2)


# ── Test: Backfilling erkennt Duplikat ───────────────────────────────────
print("\n═══ Test: Backfilling mit Duplikat ═══")
# Sollte NICHT crashen, sondern warnen und normalen Index erstellen
try:
    auth.backfill_garmin_email_hashes(db)
    check("Backfilling crasht nicht bei Duplikaten", True)
except Exception as e:
    check("Backfilling crasht nicht bei Duplikaten", False, str(e))

# Beide haben jetzt einen hash
null_after = db.query(
    "SELECT COUNT(*) as n FROM garmin_credentials WHERE email_hash IS NULL"
)[0]["n"]
check("Alle email_hash befüllt", null_after == 0)

# Hashes sind identisch (gleicher Account)
hashes = db.query("SELECT email_hash FROM garmin_credentials")
check("Beide Hashes identisch (= Duplikat erkannt)",
      hashes[0]["email_hash"] == hashes[1]["email_hash"])

# Index existiert, ist aber NICHT unique (wegen Duplikat)
idx = db.query(
    "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_garmin_email_hash'"
)
check("Index wurde erstellt", len(idx) == 1)
if idx:
    check("Index ist NICHT unique (Duplikat verhindert das)",
          "UNIQUE" not in (idx[0]["sql"] or ""),
          f"got: {idx[0]['sql']}")

db.close()
os.unlink(_tmp.name)


# ── Test: Saubere DB → UNIQUE-Index ──────────────────────────────────────
print("\n═══ Test: Backfilling auf sauberer DB ═══")
_tmp2 = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp2.close()
os.environ["DB_PATH"] = _tmp2.name

import importlib, database as dbmod
importlib.reload(dbmod)
db2 = dbmod.Database(Path(_tmp2.name))
db2.init()

a = auth.create_user(db2, "alice", "pass1234")
b = auth.create_user(db2, "bob", "pass1234")
auth.save_garmin_credentials(db2, a, "alice@garmin.com", "pw", enforce_unique=False)
auth.save_garmin_credentials(db2, b, "bob@garmin.com", "pw", enforce_unique=False)
db2.execute("UPDATE garmin_credentials SET email_hash = NULL")

auth.backfill_garmin_email_hashes(db2)

idx = db2.query(
    "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_garmin_email_hash'"
)
check("Saubere DB: Index ist UNIQUE",
      len(idx) == 1 and "UNIQUE" in (idx[0]["sql"] or ""),
      f"got: {idx[0]['sql'] if idx else 'kein Index'}")

# Nach Backfilling: Duplikat-Check funktioniert
try:
    auth.save_garmin_credentials(db2, b, "alice@garmin.com", "pw")
    check("Nach Backfill: Duplikat wird abgelehnt", False, "keine Exception")
except auth.GarminAccountInUse:
    check("Nach Backfill: Duplikat wird abgelehnt", True)

db2.close()
os.unlink(_tmp2.name)


print(f"\n{'='*50}")
print(f"  {passed} passed, {failed} failed")
print(f"{'='*50}")
sys.exit(1 if failed else 0)
