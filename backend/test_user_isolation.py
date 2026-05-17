"""
Test: Teil A – Touren-Isolation
================================
Prüft dass User nur ihre eigenen Aktivitäten sehen/löschen können.
"""
import os
import sys
import tempfile

# Verwende eine temporäre DB
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DB_PATH"] = _tmp.name
os.environ["SECRET_KEY"] = "test-secret-key-for-isolation-tests"
os.environ["SYNC_API_KEY"] = "test-sync-key"
_tmp.close()

from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from fastapi.testclient import TestClient
from database import Database
from auth import create_user, create_session_token
import main

db = main.db
db.path = Path(os.environ["DB_PATH"])
db.init()

# ── Setup: Zwei User anlegen ─────────────────────────────────────────────
from auth import ensure_admin_exists
os.environ["ADMIN_USER"] = "alice"
os.environ["ADMIN_PASS"] = "password123"
ensure_admin_exists(db)

alice_id = db.query("SELECT id FROM users WHERE username = 'alice'")[0]["id"]
bob_id = create_user(db, "bob", "password456", is_admin=False)

alice_token = create_session_token(alice_id, "alice")
bob_token = create_session_token(bob_id, "bob")

client = TestClient(main.app)

COOKIE_NAME = "trail_atlas_session"


def cookies_for(token):
    return {COOKIE_NAME: token}


# ── Testdaten einfügen ──────────────────────────────────────────────────
# Alice: 2 Aktivitäten
db.execute(
    "INSERT INTO activities (activity_id, activity_type, start_date, start_lat, start_lng, user_id) "
    "VALUES (?, ?, ?, ?, ?, ?)",
    ("alice_act_1", "hiking", "2025-01-01 10:00:00", 47.0, 11.0, alice_id)
)
db.execute(
    "INSERT INTO activities (activity_id, activity_type, start_date, start_lat, start_lng, user_id) "
    "VALUES (?, ?, ?, ?, ?, ?)",
    ("alice_act_2", "running", "2025-01-02 08:00:00", 47.1, 11.1, alice_id)
)
# Bob: 1 Aktivität
db.execute(
    "INSERT INTO activities (activity_id, activity_type, start_date, start_lat, start_lng, user_id) "
    "VALUES (?, ?, ?, ?, ?, ?)",
    ("bob_act_1", "cycling", "2025-01-03 09:00:00", 48.0, 12.0, bob_id)
)

# GPS-Punkte
db.executemany(
    "INSERT INTO gps_points (activity_id, lat, lng) VALUES (?, ?, ?)",
    [
        ("alice_act_1", 47.0, 11.0), ("alice_act_1", 47.01, 11.01),
        ("alice_act_2", 47.1, 11.1), ("alice_act_2", 47.11, 11.11),
        ("bob_act_1", 48.0, 12.0), ("bob_act_1", 48.01, 12.01),
    ]
)

# ── Tests ────────────────────────────────────────────────────────────────
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


print("\n═══ Test: GET /activities ═══")
r = client.get("/activities", cookies=cookies_for(alice_token))
acts = r.json()
check("Alice sieht 2 Aktivitäten", len(acts) == 2, f"got {len(acts)}")
check("Alice sieht nur eigene IDs",
      {a["activity_id"] for a in acts} == {"alice_act_1", "alice_act_2"},
      f"got {[a['activity_id'] for a in acts]}")

r = client.get("/activities", cookies=cookies_for(bob_token))
acts = r.json()
check("Bob sieht 1 Aktivität", len(acts) == 1, f"got {len(acts)}")
check("Bob sieht nur eigene ID",
      acts[0]["activity_id"] == "bob_act_1",
      f"got {acts[0]['activity_id']}")


print("\n═══ Test: GET /activities/gps/all ═══")
r = client.get("/activities/gps/all", cookies=cookies_for(alice_token))
pts = r.json()["points"]
check("Alice bekommt GPS für 2 Activities", len(pts) == 2, f"got {len(pts)}")
check("Kein bob_act_1 in Alices GPS", "bob_act_1" not in pts)

r = client.get("/activities/gps/all", cookies=cookies_for(bob_token))
pts = r.json()["points"]
check("Bob bekommt GPS für 1 Activity", len(pts) == 1, f"got {len(pts)}")
check("Nur bob_act_1 in Bobs GPS", "bob_act_1" in pts)


print("\n═══ Test: GET /activities/{id} – Ownership ═══")
r = client.get("/activities/alice_act_1", cookies=cookies_for(alice_token))
check("Alice kann eigene Activity abrufen", r.status_code == 200)

r = client.get("/activities/alice_act_1", cookies=cookies_for(bob_token))
check("Bob kann Alices Activity NICHT abrufen", r.status_code == 404)

r = client.get("/activities/bob_act_1", cookies=cookies_for(bob_token))
check("Bob kann eigene Activity abrufen", r.status_code == 200)

r = client.get("/activities/bob_act_1", cookies=cookies_for(alice_token))
check("Alice kann Bobs Activity NICHT abrufen", r.status_code == 404)


print("\n═══ Test: GET /activities/{id}/gps – Ownership ═══")
r = client.get("/activities/alice_act_1/gps", cookies=cookies_for(alice_token))
check("Alice bekommt eigene GPS-Punkte", r.status_code == 200 and len(r.json()["points"]) == 2)

r = client.get("/activities/alice_act_1/gps", cookies=cookies_for(bob_token))
check("Bob bekommt KEINE GPS-Punkte von Alice", r.status_code == 404)


print("\n═══ Test: DELETE /activities/{id} – Ownership ═══")
r = client.delete("/activities/alice_act_1", cookies=cookies_for(bob_token))
check("Bob kann Alices Activity NICHT löschen", r.status_code == 404)

# Prüfen dass alice_act_1 noch da ist
r = client.get("/activities/alice_act_1", cookies=cookies_for(alice_token))
check("Alice Activity existiert noch", r.status_code == 200)


print("\n═══ Test: GET /db/stats – User-gefiltert ═══")
r = client.get("/db/stats", cookies=cookies_for(alice_token))
stats = r.json()
check("Alice Stats: 2 Aktivitäten", stats["activities"] == 2, f"got {stats['activities']}")
check("Alice Stats: 4 GPS-Punkte", stats["gps_points"] == 4, f"got {stats['gps_points']}")

r = client.get("/db/stats", cookies=cookies_for(bob_token))
stats = r.json()
check("Bob Stats: 1 Aktivität", stats["activities"] == 1, f"got {stats['activities']}")
check("Bob Stats: 2 GPS-Punkte", stats["gps_points"] == 2, f"got {stats['gps_points']}")


print("\n═══ Test: Import mit user_id (Sync-Key) ═══")
import io
summary_csv = "activity_id,activity_type,start_date,start_latitude,start_longitude\nsync_act_1,hiking,2025-02-01 10:00:00,47.5,11.5\n"
r = client.post(
    "/import/summary?user_id=" + str(bob_id),
    files={"file": ("summary.csv", summary_csv.encode(), "text/csv")},
    headers={"X-Sync-Key": "test-sync-key"},
)
check("Sync-Import mit user_id erfolgreich", r.status_code == 200, f"status {r.status_code}: {r.text}")

# Prüfen: sync_act_1 gehört Bob
row = db.query("SELECT user_id FROM activities WHERE activity_id = 'sync_act_1'")
check("Importierte Activity hat Bobs user_id", row[0]["user_id"] == bob_id, f"got {row[0]['user_id']}")

# Alice sieht sync_act_1 NICHT
r = client.get("/activities", cookies=cookies_for(alice_token))
alice_ids = {a["activity_id"] for a in r.json()}
check("Alice sieht sync_act_1 nicht", "sync_act_1" not in alice_ids)

# Bob sieht sync_act_1
r = client.get("/activities", cookies=cookies_for(bob_token))
bob_ids = {a["activity_id"] for a in r.json()}
check("Bob sieht sync_act_1", "sync_act_1" in bob_ids)


print("\n═══ Test: DELETE /db/reset – nur eigene Daten ═══")
r = client.delete("/db/reset", cookies=cookies_for(bob_token))
check("Bob Reset erfolgreich", r.status_code == 200)

# Bob hat keine Activities mehr
r = client.get("/activities", cookies=cookies_for(bob_token))
check("Bob hat 0 Activities nach Reset", len(r.json()) == 0, f"got {len(r.json())}")

# Alice hat weiterhin ihre Activities
r = client.get("/activities", cookies=cookies_for(alice_token))
check("Alice hat noch 2 Activities nach Bobs Reset",
      len(r.json()) == 2, f"got {len(r.json())}")


# ── Summary ──────────────────────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"  {passed} passed, {failed} failed")
print(f"{'='*50}")

# Cleanup
os.unlink(os.environ["DB_PATH"])

sys.exit(1 if failed else 0)
