"""
Test: Nullable Koordinaten + Import ohne GPS
==============================================
Prüft dass Activities ohne Startkoordinaten importiert werden können
und die Schema-Migration korrekt funktioniert.
"""
import os
import sys
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DB_PATH"] = _tmp.name
os.environ["SECRET_KEY"] = "test-secret-nullable-coords"
os.environ["SYNC_API_KEY"] = "test-sync-key"
os.environ["ADMIN_USER"] = "admin"
os.environ["ADMIN_PASS"] = "adminpass123"
_tmp.close()

from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from fastapi.testclient import TestClient
from database import Database
from auth import create_session_token, ensure_admin_exists
import main

db = main.db
db.path = Path(os.environ["DB_PATH"])
db.init()
ensure_admin_exists(db)

admin_id = db.query("SELECT id FROM users WHERE username = 'admin'")[0]["id"]
admin_token = create_session_token(admin_id, "admin")

client = TestClient(main.app)
COOKIE_NAME = "trail_atlas_session"
SYNC_HEADERS = {"X-Sync-Key": "test-sync-key"}

passed = 0
failed = 0

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        print(f"  ✅ {name}")
        passed += 1
    else:
        print(f"  ❌ {name} — {detail}")
        failed += 1


# ── Test: Schema-Migration (nullable coords) ────────────────────────────
print("\n═══ Test: Schema v3 – nullable Koordinaten ═══")
cols = db.query("PRAGMA table_info(activities)")
lat_col = [c for c in cols if c[1] == "start_lat"][0]
lng_col = [c for c in cols if c[1] == "start_lng"][0]
check("start_lat ist nullable", lat_col[3] == 0, f"notnull={lat_col[3]}")
check("start_lng ist nullable", lng_col[3] == 0, f"notnull={lng_col[3]}")


# ── Test: Import mit Koordinaten (wie bisher) ───────────────────────────
print("\n═══ Test: Import MIT Koordinaten ═══")
csv_with_coords = (
    "activity_id,activity_type,start_date,end_date,start_latitude,start_longitude\n"
    "hiking_1,hiking,2025-01-01 10:00:00,,47.5,11.5\n"
    "running_1,running,2025-01-02 08:00:00,,48.0,12.0\n"
)
r = client.post(
    f"/import/summary?user_id={admin_id}",
    files={"file": ("s.csv", csv_with_coords.encode(), "text/csv")},
    headers=SYNC_HEADERS,
)
check("Import mit Koordinaten erfolgreich", r.status_code == 200, r.text)
check("2 importiert", r.json()["imported"] == 2, f"got {r.json()}")

row = db.query("SELECT start_lat, start_lng FROM activities WHERE activity_id = 'hiking_1'")[0]
check("hiking_1 hat lat=47.5", row["start_lat"] == 47.5)
check("hiking_1 hat lng=11.5", row["start_lng"] == 11.5)


# ── Test: Import OHNE Koordinaten ────────────────────────────────────────
print("\n═══ Test: Import OHNE Koordinaten ═══")
csv_no_coords = (
    "activity_id,activity_type,start_date,end_date,start_latitude,start_longitude\n"
    "yoga_1,yoga,2025-01-03 07:00:00,2025-01-03 08:00:00,,\n"
    "strength_1,strength_training,2025-01-04 18:00:00,2025-01-04 19:00:00,,\n"
)
r = client.post(
    f"/import/summary?user_id={admin_id}",
    files={"file": ("s.csv", csv_no_coords.encode(), "text/csv")},
    headers=SYNC_HEADERS,
)
check("Import ohne Koordinaten erfolgreich", r.status_code == 200, r.text)
check("2 importiert (nicht geskippt!)", r.json()["imported"] == 2, f"got {r.json()}")

row = db.query("SELECT start_lat, start_lng FROM activities WHERE activity_id = 'yoga_1'")[0]
check("yoga_1 hat lat=NULL", row["start_lat"] is None, f"got {row['start_lat']}")
check("yoga_1 hat lng=NULL", row["start_lng"] is None, f"got {row['start_lng']}")


# ── Test: Import ohne Koordinaten-Spalten ────────────────────────────────
print("\n═══ Test: Import ohne Koordinaten-Spalten (minimales CSV) ═══")
csv_minimal = (
    "activity_id,activity_type,start_date\n"
    "indoor_1,indoor_cycling,2025-01-05 10:00:00\n"
)
r = client.post(
    f"/import/summary?user_id={admin_id}",
    files={"file": ("s.csv", csv_minimal.encode(), "text/csv")},
    headers=SYNC_HEADERS,
)
check("Minimales CSV (ohne Koord-Spalten) erfolgreich", r.status_code == 200, r.text)
check("1 importiert", r.json()["imported"] == 1, f"got {r.json()}")

row = db.query("SELECT start_lat, start_lng FROM activities WHERE activity_id = 'indoor_1'")[0]
check("indoor_1 hat lat=NULL", row["start_lat"] is None)
check("indoor_1 hat lng=NULL", row["start_lng"] is None)


# ── Test: Alle Activities sind in der API sichtbar ───────────────────────
print("\n═══ Test: Alle Activities über API abrufbar ═══")
r = client.get("/activities", cookies={COOKIE_NAME: admin_token})
acts = r.json()
check("5 Activities total", len(acts) == 5, f"got {len(acts)}")

ids = {a["activity_id"] for a in acts}
check("hiking_1 vorhanden", "hiking_1" in ids)
check("yoga_1 vorhanden", "yoga_1" in ids)
check("strength_1 vorhanden", "strength_1" in ids)
check("indoor_1 vorhanden", "indoor_1" in ids)

# Verify NULL coords are returned as null in JSON
yoga = next(a for a in acts if a["activity_id"] == "yoga_1")
check("yoga_1 hat start_lat=null in JSON", yoga["start_lat"] is None)


# ── Test: DB Stats zählen alle Activities ────────────────────────────────
print("\n═══ Test: DB Stats ═══")
r = client.get("/db/stats", cookies={COOKIE_NAME: admin_token})
stats = r.json()
check("Stats: 5 Activities", stats["activities"] == 5, f"got {stats['activities']}")
check("Stats: by_type enthält yoga", "yoga" in stats["by_type"])
check("Stats: by_type enthält strength_training", "strength_training" in stats["by_type"])


# ── Summary ──────────────────────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"  {passed} passed, {failed} failed")
print(f"{'='*50}")

os.unlink(os.environ["DB_PATH"])
sys.exit(1 if failed else 0)
