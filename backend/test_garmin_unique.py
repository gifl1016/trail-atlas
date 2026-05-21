"""
Test: Garmin-Account Eindeutigkeit
====================================
Ein Garmin-Account darf nur EINMAL in Trail Atlas registriert werden.
Verhindert Activity-ID-Kollisionen / gegenseitiges Überschreiben beim Sync.
"""
import os
import sys
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DB_PATH"] = _tmp.name
os.environ["SECRET_KEY"] = "test-garmin-unique"
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
auth.backfill_garmin_email_hashes(db)

client = TestClient(main.app)
COOKIE_NAME = "trail_atlas_session"

admin_id = db.query("SELECT id FROM users WHERE username = 'admin'")[0]["id"]
admin_token = auth.create_session_token(admin_id, "admin")

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


# ── Test: Email-Hash Normalisierung ──────────────────────────────────────
print("\n═══ Test: Email-Hash Normalisierung ═══")
h1 = auth._garmin_email_hash("test@garmin.com")
h2 = auth._garmin_email_hash("  TEST@Garmin.COM  ")
h3 = auth._garmin_email_hash("other@garmin.com")
check("Gleiche Email (versch. Schreibweise) → gleicher Hash", h1 == h2)
check("Andere Email → anderer Hash", h1 != h3)
check("Hash ist SHA-256 (64 hex chars)", len(h1) == 64)
check("Hash enthält nicht die Klartext-Email", "test@garmin.com" not in h1)


# ── Test: find_user_by_garmin_email ──────────────────────────────────────
print("\n═══ Test: find_user_by_garmin_email ═══")
check("Unbekannte Email → None",
      auth.find_user_by_garmin_email(db, "nobody@garmin.com") is None)

# Admin Credentials geben
auth.save_garmin_credentials(db, admin_id, "shared@garmin.com", "pw1")
check("Nach save: Email gefunden",
      auth.find_user_by_garmin_email(db, "shared@garmin.com") == admin_id)
check("Case-insensitive Lookup",
      auth.find_user_by_garmin_email(db, "SHARED@GARMIN.COM") == admin_id)


# ── Test: save_garmin_credentials – Duplikat abgelehnt ───────────────────
print("\n═══ Test: save_garmin_credentials Duplikat ═══")
bob_id = auth.create_user(db, "bob", "bobpass123")

# Bob versucht den gleichen Garmin-Account
try:
    auth.save_garmin_credentials(db, bob_id, "shared@garmin.com", "pw2")
    check("Doppel-Registrierung wirft GarminAccountInUse", False, "keine Exception")
except auth.GarminAccountInUse as e:
    check("Doppel-Registrierung wirft GarminAccountInUse", True)
    check("Exception nennt den bestehenden User", e.existing_user_id == admin_id)

# Bob mit eigenem Account → OK
try:
    auth.save_garmin_credentials(db, bob_id, "bob@garmin.com", "pw2")
    check("Eigener Account für Bob → OK", True)
except auth.GarminAccountInUse:
    check("Eigener Account für Bob → OK", False)

# Admin darf seine eigenen Credentials updaten (gleiche Email, gleicher User)
try:
    auth.save_garmin_credentials(db, admin_id, "shared@garmin.com", "newpw")
    check("Eigene Credentials updaten → OK", True)
except auth.GarminAccountInUse:
    check("Eigene Credentials updaten → OK", False)


# ── Test: Signup mit bereits verwendetem Garmin-Account ──────────────────
print("\n═══ Test: Signup mit Duplikat-Garmin ═══")
r = client.post("/auth/invite", cookies={COOKIE_NAME: admin_token})
invite1 = r.json()["invite_code"]

# Carol versucht sich mit Admins Garmin-Account zu registrieren
r = client.post("/auth/signup", json={
    "username": "carol",
    "password": "carolpass123",
    "invite_code": invite1,
    "garmin_email": "shared@garmin.com",
    "garmin_password": "whatever",
})
check("Signup mit fremdem Garmin-Account → 409", r.status_code == 409,
      f"got {r.status_code}: {r.text}")

# Wichtig: Carol darf NICHT angelegt worden sein
carol_exists = db.query("SELECT 1 FROM users WHERE username = 'carol'")
check("Carol wurde NICHT angelegt (kein halber Account)", len(carol_exists) == 0)

# Invite-Code muss noch gültig sein (nicht verbraucht)
check("Invite-Code nach fehlgeschlagenem Signup noch gültig",
      auth.validate_invite_code(db, invite1))


# ── Test: Signup mit eigenem Garmin-Account → OK ─────────────────────────
print("\n═══ Test: Signup mit eigenem Garmin-Account ═══")
r = client.post("/auth/signup", json={
    "username": "carol",
    "password": "carolpass123",
    "invite_code": invite1,
    "garmin_email": "carol@garmin.com",
    "garmin_password": "carolgarmin",
})
check("Signup mit eigenem Garmin → 200", r.status_code == 200, r.text)
carol_id = db.query("SELECT id FROM users WHERE username = 'carol'")[0]["id"]
check("Carol hat Garmin-Credentials", auth.has_garmin_credentials(db, carol_id))


# ── Test: Signup ohne Garmin → immer OK ──────────────────────────────────
print("\n═══ Test: Signup ohne Garmin ═══")
r = client.post("/auth/invite", cookies={COOKIE_NAME: admin_token})
invite2 = r.json()["invite_code"]
r = client.post("/auth/signup", json={
    "username": "dave",
    "password": "davepass123",
    "invite_code": invite2,
})
check("Signup ohne Garmin → 200", r.status_code == 200)


# ── Test: Admin-Endpoint lehnt Duplikat ab ───────────────────────────────
print("\n═══ Test: Admin POST garmin – Duplikat ═══")
# Dave die Garmin-Creds von Carol geben → sollte fehlschlagen
r = client.post(
    f"/admin/users/{db.query(chr(83)+'ELECT id FROM users WHERE username=' + chr(39) + 'dave' + chr(39))[0]['id']}/garmin",
    json={"email": "carol@garmin.com", "password": "x"},
    cookies={COOKIE_NAME: admin_token}
)
check("Admin: fremder Garmin-Account → 409", r.status_code == 409, f"got {r.status_code}")
check("Fehlermeldung nennt bestehenden User", "carol" in r.text.lower())

dave_id = db.query("SELECT id FROM users WHERE username = 'dave'")[0]["id"]

# Dave mit eigenem Account → OK
r = client.post(
    f"/admin/users/{dave_id}/garmin",
    json={"email": "dave@garmin.com", "password": "davepw"},
    cookies={COOKIE_NAME: admin_token}
)
check("Admin: eigener Garmin-Account → 200", r.status_code == 200, r.text)


# ── Test: Nach Entfernen kann Account neu vergeben werden ────────────────
print("\n═══ Test: Garmin-Account-Recycling ═══")
# Carols Garmin entfernen
r = client.delete(f"/admin/users/{carol_id}/garmin", cookies={COOKIE_NAME: admin_token})
check("Carols Garmin entfernt", r.status_code == 200)

# Jetzt darf Dave carol@garmin.com bekommen
r = client.post(
    f"/admin/users/{dave_id}/garmin",
    json={"email": "carol@garmin.com", "password": "x"},
    cookies={COOKIE_NAME: admin_token}
)
check("Freigegebener Account neu vergebbar → 200", r.status_code == 200, r.text)


# ── Test: UNIQUE-Index existiert ─────────────────────────────────────────
print("\n═══ Test: UNIQUE-Index ═══")
idx = db.query(
    "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_garmin_email_hash'"
)
check("Index idx_garmin_email_hash existiert", len(idx) == 1)
# Bei sauberer DB sollte es ein UNIQUE-Index sein
if idx:
    check("Index ist UNIQUE", "UNIQUE" in (idx[0]["sql"] or ""),
          f"got: {idx[0]['sql']}")


# ── Summary ──────────────────────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"  {passed} passed, {failed} failed")
print(f"{'='*50}")

os.unlink(os.environ["DB_PATH"])
sys.exit(1 if failed else 0)
