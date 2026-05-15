"""
Trail Atlas – Auth-Layer
=========================
Session-basierte Authentifizierung mit signierten Cookies.

Warum Sessions statt JWT?
- Einfacher zu verstehen und debuggen
- Server kann Sessions sofort invalidieren (Logout)
- Kein Token-Refresh nötig
- Für 1-5 User mehr als ausreichend

Abhängigkeiten: bcrypt, itsdangerous (in requirements.txt)
"""

import os
import secrets
import logging
from datetime import datetime, timedelta, timezone

import bcrypt
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from database import Database

log = logging.getLogger("trail-atlas.auth")

# ── Config ────────────────────────────────────────────────────────────────────
# SECRET_KEY wird aus Umgebungsvariable gelesen.
# Falls nicht gesetzt: beim ersten Start einen generieren und warnen.
_secret = os.getenv("SECRET_KEY")
if not _secret:
    _secret = secrets.token_hex(32)
    log.warning(
        "SECRET_KEY nicht gesetzt – generierter temporärer Key. "
        "Sessions überleben keinen Restart! "
        "Setze SECRET_KEY in /etc/trail-atlas/garmin.env"
    )
SECRET_KEY: str = _secret

SESSION_MAX_AGE = 30 * 24 * 3600  # 30 Tage in Sekunden
INVITE_VALID_DAYS = 7             # Einladung gültig für N Tage

serializer = URLSafeTimedSerializer(SECRET_KEY, salt="trail-atlas-session")


# ── Passwort-Hashing ─────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    """Bcrypt-Hash erzeugen (60 Zeichen)."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Passwort gegen bcrypt-Hash prüfen."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ── Session-Token ─────────────────────────────────────────────────────────────

def create_session_token(user_id: int, username: str) -> str:
    """Signierten Session-Token erzeugen (URL-safe string)."""
    return serializer.dumps({"uid": user_id, "u": username})


def verify_session_token(token: str) -> dict | None:
    """
    Token verifizieren und Payload zurückgeben.
    Returns {"uid": int, "u": str} oder None bei ungültig/abgelaufen.
    """
    try:
        data = serializer.loads(token, max_age=SESSION_MAX_AGE)
        return data
    except (BadSignature, SignatureExpired, Exception):
        return None


# ── User-Management ──────────────────────────────────────────────────────────

def create_user(db: Database, username: str, password: str, is_admin: bool = False) -> int:
    """
    Neuen User anlegen.
    Returns: user_id
    Raises: ValueError wenn Username schon existiert.
    """
    existing = db.query("SELECT id FROM users WHERE username = ?", (username,))
    if existing:
        raise ValueError(f"Username '{username}' existiert bereits")

    pw_hash = hash_password(password)
    db.execute(
        "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
        (username, pw_hash, 1 if is_admin else 0)
    )
    row = db.query("SELECT id FROM users WHERE username = ?", (username,))
    user_id = row[0]["id"]
    log.info(f"User created: {username} (id={user_id}, admin={is_admin})")
    return user_id


def authenticate_user(db: Database, username: str, password: str) -> dict | None:
    """
    Login-Versuch. Returns User-Dict oder None.
    """
    rows = db.query(
        "SELECT id, username, password_hash, is_admin FROM users WHERE username = ?",
        (username,)
    )
    if not rows:
        return None
    user = dict(rows[0])
    if not verify_password(password, user["password_hash"]):
        return None
    return {"id": user["id"], "username": user["username"], "is_admin": user["is_admin"]}


def get_user_by_id(db: Database, user_id: int) -> dict | None:
    """User by ID laden (für Session-Validierung)."""
    rows = db.query(
        "SELECT id, username, is_admin FROM users WHERE id = ?",
        (user_id,)
    )
    if not rows:
        return None
    return dict(rows[0])


# ── Invite-Codes ─────────────────────────────────────────────────────────────

def create_invite_code(db: Database, created_by: int | None = None) -> str:
    """
    Neuen Einladungscode generieren.
    Returns: code (32-Zeichen URL-safe string)
    """
    code = secrets.token_urlsafe(24)  # 32 Zeichen
    expires = (datetime.now(timezone.utc) + timedelta(days=INVITE_VALID_DAYS)).isoformat()
    db.execute(
        "INSERT INTO invite_codes (code, created_by, expires_at) VALUES (?, ?, ?)",
        (code, created_by, expires)
    )
    log.info(f"Invite code created (expires {expires})")
    return code


def validate_invite_code(db: Database, code: str) -> bool:
    """Prüfe ob ein Invite-Code gültig und noch nicht verwendet ist."""
    rows = db.query(
        "SELECT id, expires_at, used_at FROM invite_codes WHERE code = ?",
        (code,)
    )
    if not rows:
        return False
    invite = dict(rows[0])
    if invite["used_at"] is not None:
        return False
    # Ablauf prüfen
    try:
        expires = datetime.fromisoformat(invite["expires_at"])
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires:
            return False
    except Exception:
        return False
    return True


def redeem_invite_code(db: Database, code: str, user_id: int):
    """Invite-Code als verwendet markieren."""
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "UPDATE invite_codes SET used_at = ?, used_by = ? WHERE code = ?",
        (now, user_id, code)
    )
    log.info(f"Invite code redeemed by user {user_id}")


# ── Bootstrap ─────────────────────────────────────────────────────────────────

def ensure_admin_exists(db: Database):
    """
    Wird beim App-Start aufgerufen. Falls noch kein User existiert:
    - Erstelle Admin-User aus Umgebungsvariablen ADMIN_USER / ADMIN_PASS
    - Weise alle bestehenden Aktivitäten (user_id=NULL) diesem User zu

    Das ist der Übergang von Basic-Auth zu App-Auth:
    Dein bestehender Single-User wird zum ersten Admin.
    """
    count = db.query("SELECT COUNT(*) as n FROM users")[0]["n"]
    if count > 0:
        return  # Users existieren bereits, nichts zu tun

    admin_user = os.getenv("ADMIN_USER", "").strip()
    admin_pass = os.getenv("ADMIN_PASS", "").strip()

    if not admin_user or not admin_pass:
        log.warning(
            "Keine Users in DB und ADMIN_USER/ADMIN_PASS nicht gesetzt. "
            "Auth ist deaktiviert bis ein Admin erstellt wird. "
            "Setze ADMIN_USER und ADMIN_PASS in /etc/trail-atlas/garmin.env"
        )
        return

    try:
        user_id = create_user(db, admin_user, admin_pass, is_admin=True)

        # Bestehende Aktivitäten dem Admin zuweisen
        orphan_count = db.query(
            "SELECT COUNT(*) as n FROM activities WHERE user_id IS NULL"
        )[0]["n"]
        if orphan_count > 0:
            db.execute("UPDATE activities SET user_id = ? WHERE user_id IS NULL", (user_id,))
            log.info(f"Assigned {orphan_count} existing activities to admin '{admin_user}'")

    except ValueError as e:
        log.warning(f"Admin-Erstellung übersprungen: {e}")
