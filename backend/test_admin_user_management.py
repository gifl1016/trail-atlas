"""
Test: Admin Nutzerverwaltung
=============================
Prüft alle Admin-Endpoints: list, delete, set/remove Garmin-Credentials.
"""
import os
import sys
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DB_PATH"] = _tmp.name
os.environ["SECRET_KEY"] = "test-admin-mgmt"
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

# Setup: 1 Admin, 1 normaler User mit Garmin, 1 normaler User ohne Garmin
admin_id = db.query("SELECT id FROM users WHERE username = 'admin'")[0]["id"]
admin_token = auth.create_session_token(admin_id, "admin")

bob_id = auth.create_user(db, "bob", "bobpass123", is_admin=False)
auth.save_garmin_credentials(db, bob_id, "bob@garmin.example", "bob-gpw")
bob_token = auth.create_session_token(bob_id, "bob")

carol_id = auth.create_user(db, "carol", "carolpass", is_admin=False)
carol_token = auth.create_session_token(carol_id, "carol")

# Test-Daten anlegen
db.execute(
    "INSERT INTO activities (activity_id, activity_type, start_date, start_lat, start_lng, user_id) "
    "VALUES (?, ?, ?, ?, ?, ?)",
    ("bob_act1", "hiking", "2025-01-01 10:00:00", 47.0, 11.0, bob_id)
)
db.executemany(
    "INSERT INTO gps_points (activity_id, lat, lng) VALUES (?, ?, ?)",
    [("bob_act1", 47.0, 11.0), ("bob_act1", 47.01, 11.01)]
)

# Sync-Log Einträge
db.execute(
    "INSERT INTO sync_log (user_id, status, started_at, finished_at, activities_imported, gps_points_imported, duration_s) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)",
    (bob_id, "ok", "2025-01-10T08:00:00", "2025-01-10T08:01:30", 5, 1500, 90.0)
)
db.execute(
    "INSERT INTO sync_log (user_id, status, started_at, finished_at, error_message, duration_s) "
    "VALUES (?, ?, ?, ?, ?, ?)",
    (carol_id, "error", "2025-01-10T08:01:30", "2025-01-10T08:01:35", "rate limit", 5.0)
)


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


# ── Test: GET /admin/users – Authorization ──────────────────────────────
print("\n═══ Test: GET /admin/users Authorization ═══")

r = client.get("/admin/users")
check("Ohne Auth → 401", r.status_code == 401, f"got {r.status_code}")

r = client.get("/admin/users", cookies={COOKIE_NAME: bob_token})
check("Normaler User → 403", r.status_code == 403, f"got {r.status_code}")

r = client.get("/admin/users", cookies={COOKIE_NAME: admin_token})
check("Admin → 200", r.status_code == 200, f"got {r.status_code}")


# ── Test: GET /admin/users – Inhalt ─────────────────────────────────────
print("\n═══ Test: GET /admin/users Inhalt ═══")
users = r.json()["users"]
check("3 User in Liste", len(users) == 3, f"got {len(users)}")

# Verify each user
admin_u = next(u for u in users if u["username"] == "admin")
bob_u = next(u for u in users if u["username"] == "bob")
carol_u = next(u for u in users if u["username"] == "carol")

check("Admin: is_admin=True", admin_u["is_admin"] == True)
check("Bob: is_admin=False", bob_u["is_admin"] == False)
check("Bob: has_garmin=True", bob_u["has_garmin"] == True)
check("Carol: has_garmin=False", carol_u["has_garmin"] == False)

check("Bob: 1 activity", bob_u["activity_count"] == 1)
check("Bob: 2 GPS points", bob_u["gps_point_count"] == 2)
check("Carol: 0 activities", carol_u["activity_count"] == 0)

# Last sync
check("Bob: last_sync vorhanden", bob_u["last_sync"] is not None)
check("Bob: last_sync.status=ok", bob_u["last_sync"]["status"] == "ok")
check("Bob: last_sync.activities_imported=5", bob_u["last_sync"]["activities_imported"] == 5)

check("Carol: last_sync.status=error", carol_u["last_sync"]["status"] == "error")
check("Carol: error_message vorhanden",
      carol_u["last_sync"]["error_message"] == "rate limit")

check("Admin: kein last_sync (noch nie gesynct)",
      admin_u["last_sync"] is None)


# ── Test: DELETE /admin/users/{id} – Authorization ──────────────────────
print("\n═══ Test: DELETE /admin/users/{id} Authorization ═══")

r = client.delete(f"/admin/users/{bob_id}")
check("Ohne Auth → 401", r.status_code == 401)

r = client.delete(f"/admin/users/{bob_id}", cookies={COOKIE_NAME: bob_token})
check("Normaler User → 403", r.status_code == 403)


# ── Test: DELETE – Admin schützt sich selbst ─────────────────────────────
print("\n═══ Test: DELETE Self-Protection ═══")
r = client.delete(f"/admin/users/{admin_id}", cookies={COOKIE_NAME: admin_token})
check("Admin kann sich nicht selbst löschen", r.status_code == 400,
      f"got {r.status_code}: {r.text}")


# ── Test: DELETE – User existiert nicht ──────────────────────────────────
print("\n═══ Test: DELETE Not Found ═══")
r = client.delete("/admin/users/9999", cookies={COOKIE_NAME: admin_token})
check("Nicht existenter User → 404", r.status_code == 404)


# ── Test: DELETE – Erfolgreich ───────────────────────────────────────────
print("\n═══ Test: DELETE User erfolgreich ═══")
r = client.delete(f"/admin/users/{carol_id}", cookies={COOKIE_NAME: admin_token})
check("Carol gelöscht: 200", r.status_code == 200)
check("Response: deleted", r.json()["status"] == "deleted")

# Verify: User weg
check("Carol nicht mehr in users-Tabelle",
      len(db.query("SELECT 1 FROM users WHERE id = ?", (carol_id,))) == 0)
check("Carols sync_log gelöscht",
      len(db.query("SELECT 1 FROM sync_log WHERE user_id = ?", (carol_id,))) == 0)


# ── Test: DELETE Bob – kaskadiert Activities + GPS ────────────────────────
print("\n═══ Test: DELETE kaskadiert Daten ═══")
r = client.delete(f"/admin/users/{bob_id}", cookies={COOKIE_NAME: admin_token})
check("Bob gelöscht: 200", r.status_code == 200)

check("Bobs Activities gelöscht",
      len(db.query("SELECT 1 FROM activities WHERE user_id = ?", (bob_id,))) == 0)
check("Bobs GPS-Punkte gelöscht",
      len(db.query("SELECT 1 FROM gps_points WHERE activity_id = 'bob_act1'")) == 0)
check("Bobs Garmin-Credentials gelöscht",
      not auth.has_garmin_credentials(db, bob_id))


# ── Test: Last Admin protection ──────────────────────────────────────────
print("\n═══ Test: Last Admin Protection ═══")
# Wir haben jetzt nur noch den Admin selbst. Versuche einen neuen User als Admin zu machen
# und dann den Original-Admin zu löschen. Das sollte aber nicht möglich sein, da man sich
# nicht selbst löschen kann. Test umgekehrt: zweiten Admin anlegen, dann Original löschen.

dave_id = auth.create_user(db, "dave", "davepass", is_admin=True)
r = client.delete(f"/admin/users/{dave_id}", cookies={COOKIE_NAME: admin_token})
check("Admin Dave löschen mit verbleibendem Admin → OK",
      r.status_code == 200, f"got {r.status_code}: {r.text}")

# Jetzt sind wir wieder bei 1 Admin (admin) + andere normale User
# Lass uns einen normalen User anlegen, dann diesen versuchen zu löschen geht
eve_id = auth.create_user(db, "eve", "evepass", is_admin=False)
# Admin in Admin verwandeln um Last-Admin-Test zu machen
# Wir können das nicht über API, also direkt in DB
db.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (eve_id,))
# Jetzt: 2 Admins. Lösche eve.
r = client.delete(f"/admin/users/{eve_id}", cookies={COOKIE_NAME: admin_token})
check("Zweiten Admin löschen mit anderem Admin → OK", r.status_code == 200)

# Jetzt nur noch 1 Admin (admin selbst). Anderen Admin anlegen.
frank_id = auth.create_user(db, "frank", "frankpass", is_admin=True)
# Lösche admin selbst (sollte fehlschlagen)
r = client.delete(f"/admin/users/{admin_id}", cookies={COOKIE_NAME: admin_token})
check("Admin selbst löschen → 400", r.status_code == 400)

# Mit frank-Token als Admin den admin löschen → sollte gehen, da frank auch Admin
frank_token = auth.create_session_token(frank_id, "frank")
# Original admin hat noch is_admin=1, frank auch. Frank löscht admin → 1 Admin übrig.
r = client.delete(f"/admin/users/{admin_id}", cookies={COOKIE_NAME: frank_token})
check("Admin löschen wenn noch ein Admin existiert → 200", r.status_code == 200)

# Jetzt nur noch frank als Admin. Versuche frank zu löschen (mit frank-Token) → self-protection
r = client.delete(f"/admin/users/{frank_id}", cookies={COOKIE_NAME: frank_token})
check("Letzten Admin nicht löschbar (self) → 400", r.status_code == 400)


# ── Test: POST /admin/users/{id}/garmin ──────────────────────────────────
print("\n═══ Test: POST /admin/users/{id}/garmin ═══")

# Neuen User anlegen ohne Garmin
gina_id = auth.create_user(db, "gina", "ginapass")
check("Gina hat keine Garmin-Creds initial",
      not auth.has_garmin_credentials(db, gina_id))

r = client.post(
    f"/admin/users/{gina_id}/garmin",
    json={"email": "gina@garmin.example", "password": "gina-gpw"},
    cookies={COOKIE_NAME: frank_token}
)
check("Garmin-Creds gesetzt: 200", r.status_code == 200, r.text)
check("Gina hat jetzt Garmin-Creds",
      auth.has_garmin_credentials(db, gina_id))

# Entschlüsselung prüfen
creds = auth.get_garmin_credentials(db, gina_id)
check("Garmin-Email korrekt entschlüsselt", creds["email"] == "gina@garmin.example")
check("Garmin-Password korrekt entschlüsselt", creds["password"] == "gina-gpw")


# ── Test: DELETE /admin/users/{id}/garmin ────────────────────────────────
print("\n═══ Test: DELETE /admin/users/{id}/garmin ═══")
r = client.delete(
    f"/admin/users/{gina_id}/garmin",
    cookies={COOKIE_NAME: frank_token}
)
check("Garmin-Verknüpfung entfernt: 200", r.status_code == 200)
check("Gina hat keine Garmin-Creds mehr",
      not auth.has_garmin_credentials(db, gina_id))

# User selbst sollte noch existieren
check("Gina existiert noch",
      auth.get_user_by_id(db, gina_id) is not None)


# ── Test: Normale User können Admin-Endpoints nicht ──────────────────────
print("\n═══ Test: Normale User dürfen keine Admin-Endpoints ═══")
gina_token = auth.create_session_token(gina_id, "gina")

r = client.get("/admin/users", cookies={COOKIE_NAME: gina_token})
check("Gina kann nicht listen", r.status_code == 403)

r = client.delete(f"/admin/users/{gina_id}", cookies={COOKIE_NAME: gina_token})
check("Gina kann sich nicht selbst per Admin-API löschen", r.status_code == 403)

r = client.post(f"/admin/users/{gina_id}/garmin",
                json={"email": "x@y.z", "password": "p"},
                cookies={COOKIE_NAME: gina_token})
check("Gina kann keine Garmin-Creds setzen", r.status_code == 403)


# ── Test: Sync Status per-user filter ────────────────────────────────────
print("\n═══ Test: GET /sync/status per-user filter ═══")

# Sync-Log Einträge für gina anlegen
db.execute(
    "INSERT INTO sync_log (user_id, status, started_at, finished_at, activities_imported, duration_s) "
    "VALUES (?, ?, ?, ?, ?, ?)",
    (gina_id, "ok", "2025-02-01T08:00:00", "2025-02-01T08:01:00", 10, 60.0)
)

# Gina sieht nur ihre eigenen Logs
r = client.get("/sync/status", cookies={COOKIE_NAME: gina_token})
check("Gina sieht /sync/status", r.status_code == 200)
data = r.json()
check("Gina: nur eigene Sync-Logs",
      all(e["user_id"] == gina_id for e in data["recent"]),
      f"got user_ids: {[e['user_id'] for e in data['recent']]}")

# Frank (Admin) sieht ohne Filter alle eigenen Logs (Admin filtert wie normale User per default)
r = client.get("/sync/status", cookies={COOKIE_NAME: frank_token})
check("Frank ohne Filter: nur eigene Logs",
      all(e["user_id"] == frank_id for e in r.json()["recent"]),
      "Admin sollte ohne user_id-Param auch nur seine eigenen sehen")

# Frank mit ?user_id=gina_id → sieht Ginas Logs
r = client.get(f"/sync/status?user_id={gina_id}", cookies={COOKIE_NAME: frank_token})
check("Admin mit user_id-Filter sieht andere User",
      r.status_code == 200 and all(e["user_id"] == gina_id for e in r.json()["recent"]))

# Normaler User mit ?user_id=... → wird ignoriert, sieht weiter nur seine eigenen
r = client.get(f"/sync/status?user_id={frank_id}", cookies={COOKIE_NAME: gina_token})
check("Normaler User kann nicht andere User abfragen",
      all(e["user_id"] == gina_id for e in r.json()["recent"]),
      "user_id-Param sollte für Nicht-Admins ignoriert werden")


# ── Test: sync_log mit user_id (POST) ────────────────────────────────────
print("\n═══ Test: POST /sync/log mit user_id ═══")
r = client.post(
    "/sync/log",
    json={
        "status": "ok",
        "started_at": "2025-03-01T08:00:00",
        "finished_at": "2025-03-01T08:01:00",
        "activities_imported": 3,
        "gps_points_imported": 500,
        "duration_s": 60.0,
        "user_id": gina_id,
    },
    headers=SYNC_HEADERS,
)
check("Sync-Log mit user_id POST", r.status_code == 200)

row = db.query(
    "SELECT user_id, activities_imported FROM sync_log "
    "WHERE user_id = ? AND activities_imported = 3", (gina_id,)
)
check("Eintrag in DB mit gina_id", len(row) == 1 and row[0]["user_id"] == gina_id)


# ── Summary ──────────────────────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"  {passed} passed, {failed} failed")
print(f"{'='*50}")

os.unlink(os.environ["DB_PATH"])
sys.exit(1 if failed else 0)
