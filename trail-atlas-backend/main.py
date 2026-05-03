"""
Trail Atlas – FastAPI Backend
=============================
REST API für Aktivitätsdaten und GPS-Punkte.
Datenbank: SQLite (WAL-Modus, persistente Connection)

Endpoints:
    GET    /health                     → Health-Check
    GET    /activities                 → Alle Aktivitäten
    GET    /activities/{id}            → Eine Aktivität
    GET    /activities/{id}/gps        → GPS-Punkte einer Aktivität
    DELETE /activities/{id}            → Aktivität + GPS löschen
    POST   /import/summary             → CSV Metadaten importieren
    POST   /import/gps                 → CSV GPS-Punkte importieren
    DELETE /db/reset                   → Alle Daten löschen
    DELETE /db/gps                     → Nur GPS-Punkte löschen
    GET    /db/stats                   → DB-Statistiken
"""

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import csv
import io
import math
import time
import logging

from database import Database

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
    yield
    log.info("Trail Atlas Backend stopping…")
    db.close()

app = FastAPI(
    title="Trail Atlas API",
    version="1.1.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
    # WICHTIG: redirect_slashes=False verhindert dass DELETE/POST mit
    # Trailing-Slash zu 307 Redirect führen (Browser/Clients verlieren dabei
    # die Methode → "405 Method Not Allowed").
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

# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "version": "1.1.0"}


# ── Activities ────────────────────────────────────────────────────────────────

@app.get("/activities")
def get_activities():
    rows = db.query(
        "SELECT activity_id, activity_type, start_date, end_date, "
        "start_lat, start_lng FROM activities ORDER BY start_date DESC"
    )
    return [dict(r) for r in rows]


@app.get("/activities/{activity_id}")
def get_activity(activity_id: str):
    rows = db.query(
        "SELECT * FROM activities WHERE activity_id = ?", (activity_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Aktivität nicht gefunden")
    return dict(rows[0])


@app.get("/activities/{activity_id}/gps")
def get_gps(activity_id: str):
    rows = db.query(
        "SELECT lat, lng FROM gps_points WHERE activity_id = ? ORDER BY id",
        (activity_id,)
    )
    return {"activity_id": activity_id, "points": [[r["lat"], r["lng"]] for r in rows]}


@app.delete("/activities/{activity_id}")
def delete_activity(activity_id: str):
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
async def import_summary(file: UploadFile = File(...)):
    t0 = time.monotonic()
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
        ))

    # INSERT OR REPLACE – wenn activity_id schon existiert wird der Datensatz
    # aktualisiert (vorhandene GPS-Punkte bleiben durch FOREIGN KEY erhalten)
    if batch:
        db.executemany(
            """INSERT INTO activities
               (activity_id, activity_type, start_date, end_date, start_lat, start_lng)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(activity_id) DO UPDATE SET
                 activity_type = excluded.activity_type,
                 start_date    = excluded.start_date,
                 end_date      = excluded.end_date,
                 start_lat     = excluded.start_lat,
                 start_lng     = excluded.start_lng
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
async def import_gps(file: UploadFile = File(...)):
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

    batch: list[tuple] = []
    skipped = 0
    BATCH_SIZE = 5000

    for r in rows:
        aid = (r.get("activity_id") or "").strip()
        if not aid:
            skipped += 1; continue

        coords = _validate_coord(r.get("latitude", ""), r.get("longitude", ""))
        if not coords:
            skipped += 1; continue

        batch.append((aid, coords[0], coords[1]))

        if len(batch) >= BATCH_SIZE:
            db.executemany(
                "INSERT INTO gps_points (activity_id, lat, lng) VALUES (?, ?, ?)",
                batch
            )
            batch.clear()

    if batch:
        db.executemany(
            "INSERT INTO gps_points (activity_id, lat, lng) VALUES (?, ?, ?)",
            batch
        )

    elapsed = round(time.monotonic() - t0, 2)
    total   = len(rows) - skipped
    log.info(f"Import GPS: {total} points, {skipped} skipped ({elapsed}s)")

    return {
        "imported":  total,
        "skipped":   skipped,
        "elapsed_s": elapsed,
    }


# ── DB Management ─────────────────────────────────────────────────────────────

@app.get("/db/stats")
def db_stats():
    act_count = db.query("SELECT COUNT(*) as n FROM activities")[0]["n"]
    gps_count = db.query("SELECT COUNT(*) as n FROM gps_points")[0]["n"]
    no_gps    = db.query(
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
def db_reset():
    # VACUUM darf nicht in einer Transaktion stehen
    db.execute("DELETE FROM gps_points")
    db.execute("DELETE FROM activities")
    # VACUUM separat (autocommit-Modus erlaubt das)
    db.execute("VACUUM")
    log.info("Database reset (all data deleted)")
    return {"status": "reset", "message": "Alle Daten gelöscht"}


@app.delete("/db/gps")
def db_delete_gps():
    count = db.query("SELECT COUNT(*) as n FROM gps_points")[0]["n"]
    db.execute("DELETE FROM gps_points")
    log.info(f"GPS points deleted: {count}")
    return {"status": "ok", "gps_points_deleted": count}
