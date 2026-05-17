"""
Trail Atlas – FastAPI Backend 
=============================
REST API für Aktivitätsdaten und GPS-Punkte.
Datenbank: SQLite (WAL-Modus, persistente Connection)

Endpoints:
    GET    /health                     → Health-Check (öffentlich)
    POST   /auth/login                 → Login → Session-Cookie
    POST   /auth/logout                → Logout → Cookie löschen
    GET    /auth/me                    → Aktueller User
    POST   /auth/signup                → Registrierung mit Invite-Code
    POST   /auth/invite                → Invite-Code generieren (Admin)
    GET    /activities                 → Alle Aktivitäten
    GET    /activities/{id}            → Eine Aktivität
    GET    /activities/{id}/gps        → GPS-Punkte einer Aktivität
    GET    /activities/gps/all         → Alle GPS-Punkte aller Aktivitäten (Bulk)
    DELETE /activities/{id}            → Aktivität + GPS löschen
    POST   /import/summary             → CSV Metadaten importieren
    POST   /import/gps                 → CSV GPS-Punkte importieren
    DELETE /db/reset                   → Alle Daten löschen
    DELETE /db/gps                     → Nur GPS-Punkte löschen
    GET    /db/stats                   → DB-Statistiken
    GET    /sync/status                → Letzte Sync-Einträge
    POST   /sync/log                   → Sync-Ergebnis protokollieren
"""

from fastapi import FastAPI, UploadFile, File, HTTPException, Body, Request, Response, Depends, Cookie
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional
from contextlib import asynccontextmanager
import csv
import io
import math
import os
import time
import logging

from database import Database
from auth import (
    create_session_token, verify_session_token, authenticate_user,
    get_user_by_id, create_user, ensure_admin_exists,
    create_invite_code, validate_invite_code, redeem_invite_code,
    save_garmin_credentials, get_all_garmin_users, has_garmin_credentials,
    SESSION_MAX_AGE,
)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("trail-atlas")

# ── App lifecycle ─────────────────────────────────────────────────────────────
db = Database()

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Trail Atlas Backend starting…")
    db.init()
    ensure_admin_exists(db)
    yield
    log.info("Trail Atlas Backend stopping…")
    db.close()

app = FastAPI(
    title="Trail Atlas API",
    version="1.6.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
    redirect_slashes=False,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://127.0.0.1"],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# ── Validation helpers ────────────────────────────────────────────────────────
SUMMARY_REQUIRED = {"activity_id", "activity_type", "start_date",
                    "start_latitude", "start_longitude"}
GPS_REQUIRED     = {"activity_id", "latitude", "longitude"}

def _validate_coord(lat: str, lng: str) -> tuple[float, float] | None:
    try:
        la, lo = float(lat), float(lng)
        if not (-90 <= la <= 90) or not (-180 <= lo <= 180):
            return None
        if math.isnan(la) or math.isnan(lo):
            return None
        return la, lo
    except (ValueError, TypeError):
        return None

def _parse_csv(content: bytes) -> tuple[list[dict], list[str]]:
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    headers = reader.fieldnames or []
    return rows, list(headers)

# ── Auth Dependency ───────────────────────────────────────────────────────────
# Jeder geschützte Endpoint bekommt den aktuellen User injected.
# Solange noch kein User in der DB ist, wird Auth übersprungen (Übergangsphase).
# Der Garmin-Sync-Cronjob authentifiziert sich via SYNC_API_KEY Header.

COOKIE_NAME = "trail_atlas_session"
SYNC_API_KEY = os.getenv("SYNC_API_KEY", "")


async def get_current_user(request: Request) -> dict | None:
    """
    FastAPI Dependency: prüft Session-Cookie ODER Sync-API-Key.

    Reihenfolge:
    1. SYNC_API_KEY im Header "X-Sync-Key" → Cronjob-Zugriff (kein User-Kontext)
    2. Keine Users in DB → Auth nicht aktiv, alles durchlassen
    3. Session-Cookie → normaler User-Login
    """
    # 1) Sync-API-Key: für den lokalen Cronjob (garmin_sync.py)
    sync_key = request.headers.get("X-Sync-Key", "")
    if SYNC_API_KEY and sync_key == SYNC_API_KEY:
        return None  # Autorisiert, aber kein User-Kontext

    # 2) Keine Users → Auth noch nicht aktiv
    user_count = db.query("SELECT COUNT(*) as n FROM users")[0]["n"]
    if user_count == 0:
        return None

    # 3) Session-Cookie prüfen
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Nicht eingeloggt")

    payload = verify_session_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Session abgelaufen")

    user = get_user_by_id(db, payload["uid"])
    if not user:
        raise HTTPException(status_code=401, detail="User nicht gefunden")

    return user


# ── User-ID Resolution ────────────────────────────────────────────────────────
# Eingeloggte User → user_id aus Session.
# Sync-Cronjob (user=None) → user_id aus Query-Parameter.
# Kein User-Kontext und kein Parameter → None (z.B. Auth noch nicht aktiv).

def _resolve_user_id(user: dict | None, request: Request) -> int | None:
    """
    Bestimmt die user_id für datenbezogene Operationen.

    - Session-User: user["id"]
    - Sync-Key (user=None): liest ?user_id= aus Query-Parametern
    - Auth nicht aktiv (user=None, kein Param): None → kein Filter
    """
    if user is not None:
        return user["id"]
    # Sync-Cronjob oder Auth-nicht-aktiv: optionaler Query-Parameter
    uid_param = request.query_params.get("user_id")
    if uid_param is not None:
        try:
            return int(uid_param)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="user_id muss eine Zahl sein")
    return None


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "version": "1.6.0"}


# ── Auth Endpoints ────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class SignupRequest(BaseModel):
    username: str
    password: str
    invite_code: str
    garmin_email: Optional[str] = None
    garmin_password: Optional[str] = None


@app.post("/auth/login")
def login(body: LoginRequest, response: Response):
    """Login → Session-Cookie setzen."""
    user = authenticate_user(db, body.username, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Ungültige Zugangsdaten")

    token = create_session_token(user["id"], user["username"])
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=True,      # nur über HTTPS
        samesite="strict",
        path="/",
    )
    log.info(f"Login: {user['username']}")
    return {"status": "ok", "username": user["username"], "is_admin": user["is_admin"]}


@app.post("/auth/logout")
def logout(response: Response):
    """Session-Cookie löschen."""
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return {"status": "ok"}


@app.get("/auth/me")
def auth_me(user: dict | None = Depends(get_current_user)):
    """Aktuellen eingeloggten User zurückgeben (oder 401)."""
    if user is None:
        # Kein User in DB → Auth nicht aktiv
        return {"status": "ok", "auth_active": False, "user": None}
    return {"status": "ok", "auth_active": True, "user": {
        "id": user["id"], "username": user["username"], "is_admin": user["is_admin"]
    }}


@app.post("/auth/signup")
def signup(body: SignupRequest, response: Response):
    """Registrierung mit Invite-Code + optionale Garmin-Credentials."""
    # Invite validieren
    if not validate_invite_code(db, body.invite_code):
        raise HTTPException(status_code=400, detail="Ungültiger oder abgelaufener Einladungscode")

    # Username-Validierung
    username = body.username.strip()
    if len(username) < 3 or len(username) > 30:
        raise HTTPException(status_code=400, detail="Username muss 3-30 Zeichen lang sein")
    if not username.isalnum() and "_" not in username:
        raise HTTPException(status_code=400, detail="Username: nur Buchstaben, Zahlen, Unterstriche")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Passwort muss mindestens 8 Zeichen lang sein")

    # Garmin-Credentials validieren (wenn angegeben, müssen beide Felder da sein)
    garmin_email = (body.garmin_email or "").strip()
    garmin_password = (body.garmin_password or "").strip()
    has_garmin = bool(garmin_email and garmin_password)
    if bool(garmin_email) != bool(garmin_password):
        raise HTTPException(
            status_code=400,
            detail="Garmin: Email und Passwort müssen beide angegeben werden"
        )

    # User anlegen
    try:
        user_id = create_user(db, username, body.password, is_admin=False)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    # Invite einlösen
    redeem_invite_code(db, body.invite_code, user_id)

    # Garmin-Credentials verschlüsselt speichern
    if has_garmin:
        save_garmin_credentials(db, user_id, garmin_email, garmin_password)
        log.info(f"Signup: {username} – Garmin credentials saved")

    # Direkt einloggen
    token = create_session_token(user_id, username)
    response.set_cookie(
        key=COOKIE_NAME, value=token, max_age=SESSION_MAX_AGE,
        httponly=True, secure=True, samesite="strict", path="/",
    )
    log.info(f"Signup: {username} (invite redeemed, garmin={'yes' if has_garmin else 'no'})")
    return {"status": "ok", "username": username, "has_garmin": has_garmin}


@app.post("/auth/invite")
def generate_invite(user: dict | None = Depends(get_current_user)):
    """Neuen Invite-Code generieren (nur Admin)."""
    if user is None:
        raise HTTPException(status_code=401, detail="Auth nicht aktiv")
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Nur Admins können Einladungen erstellen")

    code = create_invite_code(db, created_by=user["id"])
    return {"status": "ok", "invite_code": code}


# ── Activities ────────────────────────────────────────────────────────────────

@app.get("/activities")
def get_activities(request: Request, user: dict | None = Depends(get_current_user)):
    uid = _resolve_user_id(user, request)
    if uid is not None:
        rows = db.query(
            "SELECT activity_id, activity_type, start_date, end_date, "
            "start_lat, start_lng FROM activities "
            "WHERE user_id = ? ORDER BY start_date DESC",
            (uid,)
        )
    else:
        rows = db.query(
            "SELECT activity_id, activity_type, start_date, end_date, "
            "start_lat, start_lng FROM activities ORDER BY start_date DESC"
        )
    return [dict(r) for r in rows]


@app.get("/activities/gps/all")
def get_all_gps(request: Request, user: dict | None = Depends(get_current_user)):
    """
    Alle GPS-Punkte der Aktivitäten des aktuellen Users in einem Response.
    Ersetzt N einzelne /activities/{id}/gps Calls beim App-Start.

    Response-Format: {"points": {"activity_id_1": [[lat,lng],...], ...}}

    Performance: Eine einzige SQLite-Abfrage statt N separate Queries.
    Bei 100 Touren mit je 500 Punkten: ~200-400KB JSON, <100ms Abfrage.
    """
    uid = _resolve_user_id(user, request)
    if uid is not None:
        rows = db.query(
            "SELECT g.activity_id, g.lat, g.lng FROM gps_points g "
            "JOIN activities a ON g.activity_id = a.activity_id "
            "WHERE a.user_id = ? ORDER BY g.activity_id, g.id",
            (uid,)
        )
    else:
        rows = db.query(
            "SELECT activity_id, lat, lng FROM gps_points ORDER BY activity_id, id"
        )
    result: dict[str, list] = {}
    for r in rows:
        aid = r["activity_id"]
        if aid not in result:
            result[aid] = []
        result[aid].append([r["lat"], r["lng"]])
    return {"points": result}


@app.get("/activities/{activity_id}")
def get_activity(activity_id: str, request: Request, user: dict | None = Depends(get_current_user)):
    uid = _resolve_user_id(user, request)
    if uid is not None:
        rows = db.query(
            "SELECT * FROM activities WHERE activity_id = ? AND user_id = ?",
            (activity_id, uid)
        )
    else:
        rows = db.query(
            "SELECT * FROM activities WHERE activity_id = ?", (activity_id,)
        )
    if not rows:
        raise HTTPException(status_code=404, detail="Aktivität nicht gefunden")
    return dict(rows[0])


@app.get("/activities/{activity_id}/gps")
def get_gps(activity_id: str, request: Request, user: dict | None = Depends(get_current_user)):
    # Ownership prüfen: gehört die Activity dem User?
    uid = _resolve_user_id(user, request)
    if uid is not None:
        owner = db.query(
            "SELECT activity_id FROM activities WHERE activity_id = ? AND user_id = ?",
            (activity_id, uid)
        )
        if not owner:
            raise HTTPException(status_code=404, detail="Aktivität nicht gefunden")
    rows = db.query(
        "SELECT lat, lng FROM gps_points WHERE activity_id = ? ORDER BY id",
        (activity_id,)
    )
    return {"activity_id": activity_id, "points": [[r["lat"], r["lng"]] for r in rows]}


@app.delete("/activities/{activity_id}")
def delete_activity(activity_id: str, request: Request, user: dict | None = Depends(get_current_user)):
    uid = _resolve_user_id(user, request)
    if uid is not None:
        existing = db.query(
            "SELECT activity_id FROM activities WHERE activity_id = ? AND user_id = ?",
            (activity_id, uid)
        )
    else:
        existing = db.query(
            "SELECT activity_id FROM activities WHERE activity_id = ?", (activity_id,)
        )
    if not existing:
        raise HTTPException(status_code=404, detail="Aktivität nicht gefunden")

    gps_count = db.query(
        "SELECT COUNT(*) as n FROM gps_points WHERE activity_id = ?", (activity_id,)
    )[0]["n"]

    db.execute("DELETE FROM gps_points WHERE activity_id = ?", (activity_id,))
    db.execute("DELETE FROM activities WHERE activity_id = ?", (activity_id,))
    log.info(f"Deleted activity {activity_id} + {gps_count} GPS points")

    return {"deleted": activity_id, "gps_points_deleted": gps_count}


# ── Import ────────────────────────────────────────────────────────────────────

@app.post("/import/summary")
async def import_summary(request: Request, file: UploadFile = File(...), user: dict | None = Depends(get_current_user)):
    t0 = time.monotonic()
    uid = _resolve_user_id(user, request)
    content = await file.read()

    rows, headers = _parse_csv(content)
    if not rows:
        raise HTTPException(status_code=400, detail="CSV ist leer")

    missing = SUMMARY_REQUIRED - set(headers)
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Pflichtfelder fehlen: {', '.join(sorted(missing))}"
        )

    inserted = skipped = duplicates = 0
    seen_ids: set[str] = set()
    batch = []

    for r in rows:
        aid = (r.get("activity_id") or "").strip()
        if not aid:
            skipped += 1; continue
        if aid in seen_ids:
            duplicates += 1; continue
        seen_ids.add(aid)

        coords = _validate_coord(
            r.get("start_latitude", ""), r.get("start_longitude", "")
        )
        if not coords:
            skipped += 1; continue

        start = (r.get("start_date") or "").strip()
        if not start:
            skipped += 1; continue

        batch.append((
            aid,
            (r.get("activity_type") or "unknown").strip(),
            start,
            (r.get("end_date") or "").strip() or None,
            coords[0],
            coords[1],
            uid,
        ))

    # INSERT OR REPLACE – wenn activity_id schon existiert wird der Datensatz
    # aktualisiert (vorhandene GPS-Punkte bleiben durch FOREIGN KEY erhalten)
    if batch:
        db.executemany(
            """INSERT INTO activities
               (activity_id, activity_type, start_date, end_date, start_lat, start_lng, user_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(activity_id) DO UPDATE SET
                 activity_type = excluded.activity_type,
                 start_date    = excluded.start_date,
                 end_date      = excluded.end_date,
                 start_lat     = excluded.start_lat,
                 start_lng     = excluded.start_lng,
                 user_id       = excluded.user_id
            """,
            batch
        )
        inserted = len(batch)

    elapsed = round(time.monotonic() - t0, 2)
    log.info(f"Import summary: {inserted} inserted, {skipped} skipped, {duplicates} duplicates ({elapsed}s)")

    return {
        "imported":   inserted,
        "skipped":    skipped,
        "duplicates": duplicates,
        "elapsed_s":  elapsed,
    }


@app.post("/import/gps")
async def import_gps(file: UploadFile = File(...), user: dict | None = Depends(get_current_user)):
    """
    GPS-Punkte-Import. **Idempotent**: Existierende GPS-Punkte einer
    Activity werden vor dem Insert gelöscht. Das ermöglicht problemloses
    Re-Importieren derselben Tour ohne Duplikate.
    """
    t0 = time.monotonic()
    content = await file.read()

    rows, headers = _parse_csv(content)
    if not rows:
        raise HTTPException(status_code=400, detail="CSV ist leer")

    missing = GPS_REQUIRED - set(headers)
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Pflichtfelder fehlen: {', '.join(sorted(missing))}"
        )

    # ── Pass 1: alle gültigen Punkte sammeln + Activity-IDs erfassen ──────────
    all_points: list[tuple] = []
    activity_ids: set[str] = set()
    skipped = 0

    for r in rows:
        aid = (r.get("activity_id") or "").strip()
        if not aid:
            skipped += 1; continue

        coords = _validate_coord(r.get("latitude", ""), r.get("longitude", ""))
        if not coords:
            skipped += 1; continue

        all_points.append((aid, coords[0], coords[1]))
        activity_ids.add(aid)

    # ── Pass 2: alte GPS-Punkte für betroffene Activities löschen ────────────
    # Verhindert Duplikate bei Re-Import. SQLite-Limit für Variablen ist 999,
    # daher in Chunks löschen.
    deleted_count = 0
    if activity_ids:
        ids_list = list(activity_ids)
        DELETE_CHUNK = 500   # safe well below SQLite's 999 limit
        for i in range(0, len(ids_list), DELETE_CHUNK):
            chunk = ids_list[i:i+DELETE_CHUNK]
            placeholders = ",".join("?" * len(chunk))
            existing = db.query(
                f"SELECT COUNT(*) as n FROM gps_points "
                f"WHERE activity_id IN ({placeholders})",
                tuple(chunk)
            )[0]["n"]
            deleted_count += existing
            db.execute(
                f"DELETE FROM gps_points WHERE activity_id IN ({placeholders})",
                tuple(chunk)
            )

    # ── Pass 3: neue GPS-Punkte in Batches einfügen ──────────────────────────
    BATCH_SIZE = 5000
    for i in range(0, len(all_points), BATCH_SIZE):
        batch = all_points[i:i+BATCH_SIZE]
        db.executemany(
            "INSERT INTO gps_points (activity_id, lat, lng) VALUES (?, ?, ?)",
            batch
        )

    elapsed = round(time.monotonic() - t0, 2)
    total   = len(all_points)
    log.info(
        f"Import GPS: {total} points imported for {len(activity_ids)} activities, "
        f"{deleted_count} replaced, {skipped} skipped ({elapsed}s)"
    )

    return {
        "imported":  total,
        "skipped":   skipped,
        "replaced":  deleted_count,
        "activities_touched": len(activity_ids),
        "elapsed_s": elapsed,
    }


# ── DB Management ─────────────────────────────────────────────────────────────

@app.get("/db/stats")
def db_stats(request: Request, user: dict | None = Depends(get_current_user)):
    uid = _resolve_user_id(user, request)
    if uid is not None:
        act_count = db.query(
            "SELECT COUNT(*) as n FROM activities WHERE user_id = ?", (uid,)
        )[0]["n"]
        gps_count = db.query(
            "SELECT COUNT(*) as n FROM gps_points g "
            "JOIN activities a ON g.activity_id = a.activity_id "
            "WHERE a.user_id = ?", (uid,)
        )[0]["n"]
        no_gps = db.query(
            "SELECT COUNT(*) as n FROM activities a "
            "WHERE a.user_id = ? AND NOT EXISTS "
            "(SELECT 1 FROM gps_points g WHERE g.activity_id = a.activity_id)",
            (uid,)
        )[0]["n"]
        types = db.query(
            "SELECT activity_type, COUNT(*) as n FROM activities "
            "WHERE user_id = ? GROUP BY activity_type ORDER BY n DESC",
            (uid,)
        )
    else:
        act_count = db.query("SELECT COUNT(*) as n FROM activities")[0]["n"]
        gps_count = db.query("SELECT COUNT(*) as n FROM gps_points")[0]["n"]
        no_gps = db.query(
            "SELECT COUNT(*) as n FROM activities a "
            "WHERE NOT EXISTS (SELECT 1 FROM gps_points g WHERE g.activity_id = a.activity_id)"
        )[0]["n"]
        types = db.query(
            "SELECT activity_type, COUNT(*) as n FROM activities GROUP BY activity_type ORDER BY n DESC"
        )
    return {
        "activities":         act_count,
        "gps_points":         gps_count,
        "activities_no_gps":  no_gps,
        "by_type":            {r["activity_type"]: r["n"] for r in types},
    }


@app.delete("/db/reset")
def db_reset(request: Request, user: dict | None = Depends(get_current_user)):
    uid = _resolve_user_id(user, request)
    if uid is not None:
        # Nur die eigenen Aktivitäten + GPS löschen
        db.execute(
            "DELETE FROM gps_points WHERE activity_id IN "
            "(SELECT activity_id FROM activities WHERE user_id = ?)",
            (uid,)
        )
        db.execute("DELETE FROM activities WHERE user_id = ?", (uid,))
        log.info(f"Database reset for user {uid}")
        return {"status": "reset", "message": "Alle eigenen Daten gelöscht"}
    else:
        # Kein User-Kontext (Auth nicht aktiv) → alles löschen
        db.execute("DELETE FROM gps_points")
        db.execute("DELETE FROM activities")
        db.execute("VACUUM")
        log.info("Database reset (all data deleted)")
        return {"status": "reset", "message": "Alle Daten gelöscht"}


@app.delete("/db/gps")
def db_delete_gps(request: Request, user: dict | None = Depends(get_current_user)):
    uid = _resolve_user_id(user, request)
    if uid is not None:
        count = db.query(
            "SELECT COUNT(*) as n FROM gps_points g "
            "JOIN activities a ON g.activity_id = a.activity_id "
            "WHERE a.user_id = ?", (uid,)
        )[0]["n"]
        db.execute(
            "DELETE FROM gps_points WHERE activity_id IN "
            "(SELECT activity_id FROM activities WHERE user_id = ?)",
            (uid,)
        )
        log.info(f"GPS points deleted for user {uid}: {count}")
    else:
        count = db.query("SELECT COUNT(*) as n FROM gps_points")[0]["n"]
        db.execute("DELETE FROM gps_points")
        log.info(f"GPS points deleted: {count}")
    return {"status": "ok", "gps_points_deleted": count}


# ── Sync Log ──────────────────────────────────────────────────────────────────

class SyncLogEntry(BaseModel):
    """Eintrag der vom garmin_sync.py Script geschrieben wird."""
    status:               str            = Field(..., description="ok | error")
    started_at:           str            = Field(..., description="ISO 8601 timestamp")
    finished_at:          Optional[str]  = None
    activities_imported:  int            = 0
    gps_points_imported:  int            = 0
    activities_skipped:   int            = 0
    error_message:        Optional[str]  = None
    duration_s:           Optional[float] = None


@app.get("/sync/status")
def get_sync_status(limit: int = 10, user: dict | None = Depends(get_current_user)):
    """
    Letzte N Sync-Läufe zurückgeben (default 10).
    Wird vom Frontend angezeigt damit der User sieht ob der Cronjob durchläuft.
    """
    rows = db.query(
        "SELECT * FROM sync_log ORDER BY id DESC LIMIT ?",
        (limit,)
    )
    entries = [dict(r) for r in rows]
    last_ok = next((e for e in entries if e["status"] == "ok"), None)
    last_err = next((e for e in entries if e["status"] == "error"), None)
    return {
        "last_ok":    last_ok,
        "last_error": last_err,
        "recent":     entries,
    }


@app.post("/sync/log")
def post_sync_log(entry: SyncLogEntry):
    """
    Endpoint den das garmin_sync.py Script aufruft um einen Sync-Lauf zu protokollieren.
    """
    db.execute(
        """INSERT INTO sync_log
           (status, started_at, finished_at, activities_imported,
            gps_points_imported, activities_skipped, error_message, duration_s)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            entry.status,
            entry.started_at,
            entry.finished_at,
            entry.activities_imported,
            entry.gps_points_imported,
            entry.activities_skipped,
            entry.error_message,
            entry.duration_s,
        )
    )
    log.info(f"Sync log: {entry.status} ({entry.activities_imported} acts, {entry.gps_points_imported} pts)")
    return {"status": "logged"}


@app.get("/sync/users")
def get_sync_users(request: Request, user: dict | None = Depends(get_current_user)):
    """
    Alle User mit Garmin-Credentials zurückgeben (nur via Sync-Key).
    Der Sync-Cronjob nutzt diesen Endpoint um zu wissen, für welche User
    er Garmin-Daten laden soll.

    Response: {"users": [{"user_id": 1, "username": "alice", "email": "..."}, ...]}
    Hinweis: Passwörter werden hier NICHT zurückgegeben – der Cronjob
    bekommt die verschlüsselten Credentials direkt und entschlüsselt selbst.
    """
    # Nur via Sync-Key erlaubt (user=None bei Key-Auth)
    if user is not None:
        raise HTTPException(status_code=403, detail="Nur via Sync-Key zugänglich")

    rows = db.query(
        "SELECT gc.user_id, u.username, gc.token_json "
        "FROM garmin_credentials gc "
        "JOIN users u ON gc.user_id = u.id"
    )
    users = []
    for r in rows:
        users.append({
            "user_id": r["user_id"],
            "username": r["username"],
            "encrypted_credentials": r["token_json"],
        })
    return {"users": users}
