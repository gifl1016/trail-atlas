"""
Test: Activity Ratings
=======================
Prüft Sterne-Bewertungen: setzen, ändern, entfernen, User-Isolation,
und das wichtigste Feature – Ratings überleben einen Garmin-Daten-Reset.
"""
import os
import sys
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DB_PATH"] = _tmp.name
os.environ["SECRET_KEY"] = "test-ratings"
os.environ["SYNC_API_KEY"] = "test-sync-key"
os.environ["ADMIN_USER"] = "admin"
os.environ["ADMIN_PASS"] = "adminpass123"
_tmp.close()

from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from fastapi.testclient import TestClient
import auth
import main

db = main.db
db.path = Path(os.environ["DB_PATH"])
db.init()
auth.ensure_admin_exists(db)

client = TestClient(main.app)
COOKIE_NAME = "trail_atlas_session"
SYNC_HEADERS = {"X-Sync-Key": "test-sync-key"}

admin_id = db.query("SELECT id FROM users WHERE username = 'admin'")[0]["id"]
admin_token = auth.create_session_token(admin_id, "admin")

bob_id = auth.create_user(db, "bob", "bobpass123")
bob_token = auth.create_session_token(bob_id, "bob")

# Test-Aktivitäten anlegen
for aid, uid in [("act_a", admin_id), ("act_b", admin_id), ("act_bob", bob_id)]:
    db.execute(
        "INSERT INTO activities (activity_id, activity_type, start_date, start_lat, start_lng, user_id) "
        "VALUES (?, 'hiking', '2025-01-01 10:00:00', 47.0, 11.0, ?)",
        (aid, uid)
    )

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


# ── Test: Schema ─────────────────────────────────────────────────────────
print("\n═══ Test: Schema ═══")
tables = db.query("SELECT name FROM sqlite_master WHERE type='table' AND name='activity_ratings'")
check("activity_ratings Tabelle existiert", len(tables) == 1)

# KEIN FK auf activities (das ist der Kern – Ratings überleben Reset)
fks = db.query("PRAGMA foreign_key_list(activity_ratings)")
fk_tables = {f[2] for f in fks}
check("FK auf users vorhanden", "users" in fk_tables)
check("KEIN FK auf activities", "activities" not in fk_tables)


# ── Test: Rating setzen ──────────────────────────────────────────────────
print("\n═══ Test: Rating setzen ═══")
r = client.put("/activities/act_a/rating", json={"rating": 4},
                cookies={COOKIE_NAME: admin_token})
check("Rating 4 setzen → 200", r.status_code == 200, r.text)
check("Response rating=4", r.json()["rating"] == 4)

# In activities-Liste sichtbar
r = client.get("/activities", cookies={COOKIE_NAME: admin_token})
act_a = next(a for a in r.json() if a["activity_id"] == "act_a")
check("Rating in /activities sichtbar", act_a["rating"] == 4)
act_b = next(a for a in r.json() if a["activity_id"] == "act_b")
check("Unbewertete Activity hat rating=null", act_b["rating"] is None)

# In Deep-Dive sichtbar
r = client.get("/activities/act_a", cookies={COOKIE_NAME: admin_token})
check("Rating in Deep-Dive sichtbar", r.json()["rating"] == 4)


# ── Test: Rating ändern ──────────────────────────────────────────────────
print("\n═══ Test: Rating ändern ═══")
r = client.put("/activities/act_a/rating", json={"rating": 2},
                cookies={COOKIE_NAME: admin_token})
check("Rating ändern 4→2 → 200", r.status_code == 200)
r = client.get("/activities/act_a", cookies={COOKIE_NAME: admin_token})
check("Rating ist jetzt 2", r.json()["rating"] == 2)

# Kein Duplikat in DB
cnt = db.query("SELECT COUNT(*) as n FROM activity_ratings WHERE activity_id='act_a'")[0]["n"]
check("Nur 1 Rating-Zeile (UPSERT, kein Duplikat)", cnt == 1)


# ── Test: Rating entfernen ───────────────────────────────────────────────
print("\n═══ Test: Rating entfernen ═══")
r = client.delete("/activities/act_a/rating", cookies={COOKIE_NAME: admin_token})
check("Rating entfernen → 200", r.status_code == 200)
check("Response rating=null", r.json()["rating"] is None)
r = client.get("/activities/act_a", cookies={COOKIE_NAME: admin_token})
check("Rating ist wieder null", r.json()["rating"] is None)


# ── Test: Validierung ────────────────────────────────────────────────────
print("\n═══ Test: Validierung ═══")
r = client.put("/activities/act_a/rating", json={"rating": 0},
                cookies={COOKIE_NAME: admin_token})
check("Rating 0 → 422 (ungültig)", r.status_code == 422)
r = client.put("/activities/act_a/rating", json={"rating": 6},
                cookies={COOKIE_NAME: admin_token})
check("Rating 6 → 422 (ungültig)", r.status_code == 422)


# ── Test: User-Isolation ─────────────────────────────────────────────────
print("\n═══ Test: User-Isolation ═══")
# Admin bewertet act_a mit 5
client.put("/activities/act_a/rating", json={"rating": 5},
           cookies={COOKIE_NAME: admin_token})
# Bob darf act_a nicht bewerten (gehört Admin)
r = client.put("/activities/act_a/rating", json={"rating": 1},
               cookies={COOKIE_NAME: bob_token})
check("Bob kann fremde Activity nicht bewerten → 404", r.status_code == 404)

# Bob bewertet seine eigene
client.put("/activities/act_bob/rating", json={"rating": 3},
           cookies={COOKIE_NAME: bob_token})
# Admin sieht Bobs Rating NICHT in seiner Liste (sieht act_bob gar nicht)
r = client.get("/activities", cookies={COOKIE_NAME: admin_token})
admin_act_ids = {a["activity_id"] for a in r.json()}
check("Admin sieht Bobs Activity nicht", "act_bob" not in admin_act_ids)

# Admin's act_a rating ist 5, unbeeinflusst von Bob
r = client.get("/activities/act_a", cookies={COOKIE_NAME: admin_token})
check("Admins Rating unbeeinflusst (=5)", r.json()["rating"] == 5)


# ── Test: Auth erforderlich ──────────────────────────────────────────────
print("\n═══ Test: Auth erforderlich ═══")
r = client.put("/activities/act_a/rating", json={"rating": 3})
check("Rating ohne Login → 401", r.status_code == 401)
r = client.put("/activities/act_a/rating", json={"rating": 3}, headers=SYNC_HEADERS)
check("Rating mit Sync-Key → 401", r.status_code == 401)


# ── Test: Ratings überleben Garmin-Reset (KERN-FEATURE) ──────────────────
print("\n═══ Test: Ratings überleben /db/garmin Reset ═══")
# Admin hat act_a=5. Jetzt act_b auch bewerten.
client.put("/activities/act_b/rating", json={"rating": 3},
           cookies={COOKIE_NAME: admin_token})
ratings_before = db.query(
    "SELECT COUNT(*) as n FROM activity_ratings WHERE user_id = ?", (admin_id,)
)[0]["n"]
check("Vor Reset: 2 Ratings", ratings_before == 2)

# Garmin-Daten zurücksetzen
r = client.delete("/db/garmin", cookies={COOKIE_NAME: admin_token})
check("/db/garmin → 200", r.status_code == 200)

# Activities sind weg
acts = db.query("SELECT COUNT(*) as n FROM activities WHERE user_id = ?", (admin_id,))[0]["n"]
check("Activities gelöscht", acts == 0)

# ABER Ratings sind noch da!
ratings_after = db.query(
    "SELECT COUNT(*) as n FROM activity_ratings WHERE user_id = ?", (admin_id,)
)[0]["n"]
check("Ratings ÜBERLEBEN den Garmin-Reset", ratings_after == 2,
      f"erwartet 2, got {ratings_after}")

# Activity neu importieren (simuliert nächsten Sync)
db.execute(
    "INSERT INTO activities (activity_id, activity_type, start_date, start_lat, start_lng, user_id) "
    "VALUES ('act_a', 'hiking', '2025-01-01 10:00:00', 47.0, 11.0, ?)",
    (admin_id,)
)
# Rating ist sofort wieder sichtbar
r = client.get("/activities/act_a", cookies={COOKIE_NAME: admin_token})
check("Nach Re-Import: Rating sofort wieder da (=5)", r.json()["rating"] == 5)


# ── Test: /db/reset löscht Ratings ───────────────────────────────────────
print("\n═══ Test: /db/reset löscht Ratings ═══")
r = client.delete("/db/reset", cookies={COOKIE_NAME: admin_token})
check("/db/reset → 200", r.status_code == 200)
ratings_final = db.query(
    "SELECT COUNT(*) as n FROM activity_ratings WHERE user_id = ?", (admin_id,)
)[0]["n"]
check("Nach /db/reset: Ratings gelöscht", ratings_final == 0)

# Bobs Rating unberührt (anderer User)
bob_ratings = db.query(
    "SELECT COUNT(*) as n FROM activity_ratings WHERE user_id = ?", (bob_id,)
)[0]["n"]
check("Bobs Ratings durch Admins Reset unberührt", bob_ratings == 1)


# ── Test: User löschen entfernt Ratings (CASCADE) ────────────────────────
print("\n═══ Test: User löschen → Ratings CASCADE ═══")
auth.delete_user(db, bob_id)
bob_ratings = db.query(
    "SELECT COUNT(*) as n FROM activity_ratings WHERE user_id = ?", (bob_id,)
)[0]["n"]
check("Bobs Ratings nach User-Löschung weg", bob_ratings == 0)


# ── Summary ──────────────────────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"  {passed} passed, {failed} failed")
print(f"{'='*50}")

os.unlink(os.environ["DB_PATH"])
sys.exit(1 if failed else 0)
