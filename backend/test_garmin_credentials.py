"""
Test: Teil B – Garmin-Credentials im Signup + Sync-Users-Endpoint
==================================================================
Prüft Fernet-Verschlüsselung, Signup mit Garmin-Feldern, /sync/users Endpoint,
und Admin-Migration der Garmin-Credentials aus garmin.env.
"""
import os
import sys
import tempfile

# Verwende eine temporäre DB
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DB_PATH"] = _tmp.name
os.environ["SECRET_KEY"] = "test-secret-key-for-garmin-creds"
os.environ["SYNC_API_KEY"] = "test-sync-key"
os.environ["ADMIN_USER"] = "admin"
os.environ["ADMIN_PASS"] = "adminpass123"
os.environ["GARMIN_EMAIL"] = "admin@garmin.example"
os.environ["GARMIN_PASSWORD"] = "admin-garmin-pw"
_tmp.close()

from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from fastapi.testclient import TestClient
from database import Database
import auth
import main

db = main.db
db.path = Path(os.environ["DB_PATH"])
db.init()

# Bootstrap: Admin + Garmin-Migration
auth.ensure_admin_exists(db)

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


# ── Test: Fernet encrypt/decrypt ─────────────────────────────────────────
print("\n═══ Test: Fernet Verschlüsselung ═══")
encrypted = auth.encrypt_garmin_credentials("test@example.com", "secret123")
check("Verschlüsselter String ist nicht leer", len(encrypted) > 0)
check("Verschlüsselter String != Klartext", "test@example.com" not in encrypted)

decrypted = auth.decrypt_garmin_credentials(encrypted)
check("Entschlüsselung: Email korrekt", decrypted["email"] == "test@example.com")
check("Entschlüsselung: Passwort korrekt", decrypted["password"] == "secret123")


# ── Test: Admin-Garmin-Migration ─────────────────────────────────────────
print("\n═══ Test: Admin-Garmin-Migration ═══")
admin_id = db.query("SELECT id FROM users WHERE username = 'admin'")[0]["id"]
check("Admin existiert", admin_id is not None)
check("Admin hat Garmin-Credentials",
      auth.has_garmin_credentials(db, admin_id))

admin_creds = auth.get_garmin_credentials(db, admin_id)
check("Admin Garmin-Email korrekt",
      admin_creds["email"] == "admin@garmin.example",
      f"got {admin_creds}")
check("Admin Garmin-Passwort korrekt",
      admin_creds["password"] == "admin-garmin-pw")


# ── Test: Signup mit Garmin-Credentials ──────────────────────────────────
print("\n═══ Test: Signup mit Garmin-Credentials ═══")

# Erst einen Invite-Code generieren (als Admin)
admin_token = auth.create_session_token(admin_id, "admin")
r = client.post("/auth/invite", cookies={COOKIE_NAME: admin_token})
check("Invite-Code generiert", r.status_code == 200)
invite_code = r.json()["invite_code"]

# Signup mit Garmin-Credentials
r = client.post("/auth/signup", json={
    "username": "bob",
    "password": "bobpass123",
    "invite_code": invite_code,
    "garmin_email": "bob@garmin.example",
    "garmin_password": "bob-garmin-pw",
})
check("Signup erfolgreich", r.status_code == 200, f"status {r.status_code}: {r.text}")
check("Response hat has_garmin=True", r.json().get("has_garmin") == True)

bob_id = db.query("SELECT id FROM users WHERE username = 'bob'")[0]["id"]
check("Bob hat Garmin-Credentials in DB",
      auth.has_garmin_credentials(db, bob_id))

bob_creds = auth.get_garmin_credentials(db, bob_id)
check("Bob Garmin-Email korrekt", bob_creds["email"] == "bob@garmin.example")
check("Bob Garmin-Passwort korrekt", bob_creds["password"] == "bob-garmin-pw")


# ── Test: Signup OHNE Garmin-Credentials ─────────────────────────────────
print("\n═══ Test: Signup ohne Garmin-Credentials ═══")
r = client.post("/auth/invite", cookies={COOKIE_NAME: admin_token})
invite_code2 = r.json()["invite_code"]

r = client.post("/auth/signup", json={
    "username": "carol",
    "password": "carolpass123",
    "invite_code": invite_code2,
})
check("Signup ohne Garmin erfolgreich", r.status_code == 200)
check("Response hat has_garmin=False", r.json().get("has_garmin") == False)

carol_id = db.query("SELECT id FROM users WHERE username = 'carol'")[0]["id"]
check("Carol hat KEINE Garmin-Credentials",
      not auth.has_garmin_credentials(db, carol_id))


# ── Test: Signup mit nur einem Garmin-Feld → Fehler ─────────────────────
print("\n═══ Test: Signup mit unvollständigen Garmin-Credentials ═══")
r = client.post("/auth/invite", cookies={COOKIE_NAME: admin_token})
invite_code3 = r.json()["invite_code"]

r = client.post("/auth/signup", json={
    "username": "dave",
    "password": "davepass123",
    "invite_code": invite_code3,
    "garmin_email": "dave@garmin.example",
    # garmin_password fehlt
})
check("Fehlende Garmin-Password → 400", r.status_code == 400,
      f"status {r.status_code}: {r.text}")


# ── Test: GET /sync/users ────────────────────────────────────────────────
print("\n═══ Test: GET /sync/users ═══")

# Via Sync-Key → sollte funktionieren
r = client.get("/sync/users", headers=SYNC_HEADERS)
check("Sync-Key Zugriff erfolgreich", r.status_code == 200, f"{r.status_code}: {r.text}")

users = r.json()["users"]
check("2 User mit Garmin-Credentials (admin + bob)", len(users) == 2,
      f"got {len(users)}: {[u['username'] for u in users]}")

user_ids = {u["user_id"] for u in users}
check("Admin in sync/users", admin_id in user_ids)
check("Bob in sync/users", bob_id in user_ids)

# Carol hat keine Garmin-Credentials → nicht in der Liste
carol_in_list = any(u["user_id"] == carol_id for u in users)
check("Carol NICHT in sync/users (keine Garmin-Creds)", not carol_in_list)

# Verschlüsselte Credentials werden mitgeliefert
for u in users:
    check(f"User {u['username']} hat encrypted_credentials",
          "encrypted_credentials" in u and len(u["encrypted_credentials"]) > 0)

# Via Session-Cookie → sollte 403 sein (nur Sync-Key)
r = client.get("/sync/users", cookies={COOKIE_NAME: admin_token})
check("Session-Cookie → 403 (nur Sync-Key)", r.status_code == 403,
      f"got {r.status_code}")

# Ohne Auth → 401
r = client.get("/sync/users")
check("Ohne Auth → 401", r.status_code == 401, f"got {r.status_code}")


# ── Test: get_all_garmin_users Helper ────────────────────────────────────
print("\n═══ Test: get_all_garmin_users Helper ═══")
all_users = auth.get_all_garmin_users(db)
check("get_all_garmin_users gibt 2 User zurück", len(all_users) == 2)

for u in all_users:
    check(f"User {u['username']} hat entschlüsselte email",
          "@" in u["email"])
    check(f"User {u['username']} hat entschlüsseltes password",
          len(u["password"]) > 0)


# ── Summary ──────────────────────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"  {passed} passed, {failed} failed")
print(f"{'='*50}")

# Cleanup
os.unlink(os.environ["DB_PATH"])
sys.exit(1 if failed else 0)
