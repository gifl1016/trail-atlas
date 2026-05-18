#!/usr/bin/env python3
"""
garmin_sync.py – Multi-User Garmin Sync
========================================
Läd Aktivitäten  von Garmin Connect für ALLE User mit Garmin-Credentials
und postet sie an die Trail Atlas API (mit user_id-Parameter).

Verwendung:
    python3 garmin_sync.py                    # alle User, alle neuen Touren
    python3 garmin_sync.py --limit 1          # pro User max 1 neue Tour
    python3 garmin_sync.py --dry-run          # zeigt was gemacht würde
    python3 garmin_sync.py --full-resync      # auch existierende Touren
    python3 garmin_sync.py --user 2           # nur User mit ID 2 syncen

Konfiguration: /etc/trail-atlas/garmin.env
    → API_BASE, SYNC_API_KEY, SECRET_KEY (für Credential-Entschlüsselung)

Ablauf:
    1. GET /sync/users → Liste aller User mit verschlüsselten Garmin-Credentials
    2. Für jeden User: Credentials entschlüsseln → Garmin Login → Aktivitäten laden
    3. POST /import/summary?user_id=X + /import/gps?user_id=X
    4. POST /sync/log (Statistik)
"""

import argparse
import base64
import csv
import hashlib
import io
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ── Dependency check ──────────────────────────────────────────────────────────
try:
    from garminconnect import (
        Garmin,
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
        GarminConnectTooManyRequestsError,
    )
except ImportError:
    print("❌ garminconnect nicht installiert: pip install garminconnect")
    sys.exit(1)

try:
    from cryptography.fernet import Fernet
except ImportError:
    print("❌ cryptography nicht installiert: pip install cryptography")
    sys.exit(1)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("garmin-sync")

# ── Config aus garmin.env laden ───────────────────────────────────────────────
ENV_FILE = Path(os.getenv("GARMIN_ENV_FILE", "/etc/trail-atlas/garmin.env"))

def load_env():
    if not ENV_FILE.exists():
        log.error(f"Config nicht gefunden: {ENV_FILE}")
        log.error("Bitte garmin.env.example kopieren und ausfüllen.")
        sys.exit(1)
    env = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env

# ── Fernet für Credential-Entschlüsselung ────────────────────────────────────
# Identische Ableitung wie in auth.py: SHA256(SECRET_KEY) → base64 → Fernet-Key

def _init_fernet(secret_key: str) -> Fernet:
    fernet_key = base64.urlsafe_b64encode(
        hashlib.sha256(secret_key.encode()).digest()
    )
    return Fernet(fernet_key)

def decrypt_credentials(fernet: Fernet, encrypted: str) -> dict:
    """Entschlüsselt {"email": "...", "password": "..."}."""
    decrypted = fernet.decrypt(encrypted.encode("utf-8"))
    return json.loads(decrypted.decode("utf-8"))

# ── Aktivitätstyp-Mapping ────────────────────────────────────────────────────
ACTIVITY_TYPE_MAP = {
    "running":                 "running",
    "trail_running":           "running",
    "treadmill_running":       "running",
    "track_running":           "running",
    "indoor_running":          "running",
    "cycling":                 "cycling",
    "mountain_biking":         "cycling",
    "road_biking":             "cycling",
    "indoor_cycling":          "cycling",
    "gravel_cycling":          "cycling",
    "virtual_ride":            "cycling",
    "hiking":                  "hiking",
    "walking":                 "walking",
    "casual_walking":          "walking",
    "speed_walking":           "walking",
    "swimming":                "swimming",
    "open_water_swimming":     "swimming",
    "lap_swimming":            "swimming",
    "stand_up_paddleboarding": "stand_up_paddleboarding",
    "kayaking":                "kayaking",
    "alpine_skiing":           "alpine_skiing",
    "backcountry_skiing":      "alpine_skiing",
    "cross_country_skiing":    "alpine_skiing",
    "snowboarding":            "alpine_skiing",
    "kitesurfing":             "kitesurfing",
    "windsurfing":             "kitesurfing",
    "strength_training":       "strength_training",
    "yoga":                    "yoga",
}

def normalize_type(t: str) -> str:
    return ACTIVITY_TYPE_MAP.get(t.lower(), t.lower())

# ── Token Management (pro User) ─────────────────────────────────────────────
TOKEN_DIR = Path(os.getenv("GARMIN_TOKEN_DIR", "/var/lib/trail-atlas/garmin_tokens"))

def _token_path(user_id: int) -> Path:
    return TOKEN_DIR / f"{user_id}.json"

def load_token(user_id: int):
    path = _token_path(user_id)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception as e:
            log.warning(f"Token-Datei korrupt für user {user_id}: {e}")
    return None

def save_token(user_id: int, token):
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    path = _token_path(user_id)
    path.write_text(json.dumps(token))
    path.chmod(0o600)

def login_garmin_for_user(user_id: int, email: str, password: str) -> Garmin:
    """Garmin-Login für einen bestimmten User (Token-Cache pro User)."""
    # 1. Gespeichertes Token versuchen
    saved = load_token(user_id)
    if saved:
        log.info(f"   Verwende gespeichertes Token")
        try:
            client = Garmin()
            client.login(saved)
            return client
        except Exception as e:
            log.warning(f"   Token ungültig, fresh login: {e}")

    # 2. Fresh login mit Email/Passwort
    log.info(f"   Login: {email}")
    client = Garmin(email=email, password=password)
    try:
        client.login()
    except GarminConnectAuthenticationError:
        log.error(f"   Login fehlgeschlagen für {email}")
        raise

    # 3. Token speichern
    try:
        save_token(user_id, client.garth.dumps())
        log.info(f"   Token gespeichert: {_token_path(user_id)}")
    except Exception as e:
        log.warning(f"   Token-Speichern fehlgeschlagen: {e}")
    return client

# ── API Helpers ──────────────────────────────────────────────────────────────
class TrailAtlasAPI:
    def __init__(self, env):
        self.base = env["API_BASE"].rstrip("/")
        self.session = requests.Session()
        sync_key = env.get("SYNC_API_KEY", "")
        if sync_key:
            self.session.headers["X-Sync-Key"] = sync_key
        else:
            log.warning("SYNC_API_KEY nicht in garmin.env – API-Calls könnten fehlschlagen")

    def _url(self, path):
        return f"{self.base}{path}"

    def get_sync_users(self) -> list[dict]:
        """Alle User mit Garmin-Credentials vom Backend holen."""
        r = self.session.get(self._url("/sync/users"), timeout=30)
        r.raise_for_status()
        return r.json()["users"]

    def get_existing_ids(self, user_id: int) -> set[str]:
        """Existierende activity_ids für einen bestimmten User."""
        r = self.session.get(
            self._url("/activities"),
            params={"user_id": user_id},
            timeout=30,
        )
        r.raise_for_status()
        return {a["activity_id"] for a in r.json()}

    def import_summary_csv(self, csv_text: str, user_id: int) -> dict:
        files = {"file": ("summary.csv", csv_text.encode("utf-8"), "text/csv")}
        r = self.session.post(
            self._url("/import/summary"),
            params={"user_id": user_id},
            files=files, timeout=120,
        )
        r.raise_for_status()
        return r.json()

    def import_gps_csv(self, csv_text: str, user_id: int) -> dict:
        files = {"file": ("gps.csv", csv_text.encode("utf-8"), "text/csv")}
        r = self.session.post(
            self._url("/import/gps"),
            params={"user_id": user_id},
            files=files, timeout=300,
        )
        r.raise_for_status()
        return r.json()

    def post_sync_log(self, entry: dict):
        try:
            r = self.session.post(self._url("/sync/log"),
                                  json=entry, timeout=15)
            r.raise_for_status()
        except Exception as e:
            log.warning(f"Sync-Log konnte nicht gepostet werden: {e}")

# ── Activity Fetching ────────────────────────────────────────────────────────
def fetch_recent_activities(client: Garmin, max_pages: int = 10) -> list[dict]:
    """Lade Aktivitäten paginiert, max_pages × 100 Stück."""
    all_acts = []
    BATCH = 100
    for page in range(max_pages):
        offset = page * BATCH
        try:
            batch = client.get_activities(offset, BATCH)
        except GarminConnectTooManyRequestsError:
            log.warning("Rate-limit – warte 30s")
            time.sleep(30)
            batch = client.get_activities(offset, BATCH)
        if not batch:
            break
        all_acts.extend(batch)
        if len(batch) < BATCH:
            break
        time.sleep(1.0)  # rate limiting
    return all_acts

def fetch_gps_polyline(client: Garmin, activity_id: str) -> list[tuple[float, float]]:
    """GPS-Punkte für eine Aktivität laden, in zwei API-Formaten.
    Robust gegen fehlende oder None-Werte in der Garmin-API-Antwort."""
    points: list[tuple[float, float]] = []
    try:
        details = client.get_activity_details(activity_id)
    except Exception as e:
        log.warning(f"  GPS fetch failed for {activity_id}: {e}")
        return []

    if not details or not isinstance(details, dict):
        return []

    # Format 1: detailedMetrics
    detailed = details.get("detailedMetrics") or details.get("metrics") or []
    if not isinstance(detailed, list):
        detailed = []

    for m in detailed:
        if not isinstance(m, dict):
            continue
        metrics = m.get("metrics") or {}
        if not isinstance(metrics, dict):
            metrics = {}
        lat = metrics.get("directLatitude") or m.get("lat")
        lng = metrics.get("directLongitude") or m.get("lon")
        if lat is not None and lng is not None:
            try:
                points.append((float(lat), float(lng)))
            except (ValueError, TypeError):
                pass

    # Format 2: geoPolylineDTO
    if not points:
        geo_dto = details.get("geoPolylineDTO")
        if isinstance(geo_dto, dict):
            polyline = geo_dto.get("polyline") or []
            if isinstance(polyline, list):
                for pt in polyline:
                    if not isinstance(pt, dict):
                        continue
                    if pt.get("lat") is not None and pt.get("lon") is not None:
                        try:
                            points.append((float(pt["lat"]), float(pt["lon"])))
                        except (ValueError, TypeError):
                            pass

    return points

# ── CSV Builder ──────────────────────────────────────────────────────────────
def build_summary_csv(activities: list[dict]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=[
        "activity_id", "activity_type", "start_date", "end_date",
        "start_latitude", "start_longitude",
    ])
    writer.writeheader()

    for act in activities:
        aid = str(act.get("activityId", ""))
        act_type = normalize_type(
            act.get("activityType", {}).get("typeKey", "unknown")
        )

        start_raw = act.get("startTimeGMT") or act.get("startTimeLocal", "")
        end_raw = ""
        try:
            start_dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
            duration_s = float(act.get("duration") or act.get("elapsedDuration", 0))
            end_dt = start_dt + timedelta(seconds=duration_s)
            start_raw = start_dt.strftime("%Y-%m-%d %H:%M:%S+00:00")
            end_raw = end_dt.strftime("%Y-%m-%d %H:%M:%S+00:00")
        except Exception:
            pass

        writer.writerow({
            "activity_id": aid,
            "activity_type": act_type,
            "start_date": start_raw,
            "end_date": end_raw,
            "start_latitude": act.get("startLatitude", ""),
            "start_longitude": act.get("startLongitude", ""),
        })
    return out.getvalue()

def build_gps_csv(activity_id: str, points: list[tuple[float, float]]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=["activity_id", "latitude", "longitude"])
    writer.writeheader()
    for lat, lng in points:
        writer.writerow({
            "activity_id": activity_id,
            "latitude": lat,
            "longitude": lng,
        })
    return out.getvalue()

# ── Single-User Sync ────────────────────────────────────────────────────────
def sync_user(
    api: TrailAtlasAPI,
    user_id: int,
    username: str,
    email: str,
    password: str,
    args,
) -> dict:
    """
    Sync für einen einzelnen User durchführen.
    Returns: sync_status dict mit Ergebnis.
    """
    started_at = datetime.now(timezone.utc).isoformat()
    t0 = time.monotonic()

    status = {
        "status": "ok",
        "started_at": started_at,
        "finished_at": None,
        "activities_imported": 0,
        "gps_points_imported": 0,
        "activities_skipped": 0,
        "error_message": None,
        "duration_s": None,
    }

    try:
        # 1. Existierende IDs für diesen User
        existing_ids: set[str] = set()
        if not args.full_resync:
            try:
                existing_ids = api.get_existing_ids(user_id)
                log.info(f"   Bereits in DB: {len(existing_ids)} Aktivitäten")
            except Exception as e:
                log.warning(f"   API-Liste nicht ladbar, full sync: {e}")

        # 2. Garmin Login
        client = login_garmin_for_user(user_id, email, password)
        log.info(f"   Login erfolgreich")

        # 3. Recent activities laden
        log.info(f"   Lade Garmin-Aktivitäten (max {args.max_pages * 100})…")
        all_acts = fetch_recent_activities(client, args.max_pages)
        log.info(f"   {len(all_acts)} Garmin-Aktivitäten gefunden")

        # 4. Filter: nur neue
        new_acts = [a for a in all_acts if str(a.get("activityId")) not in existing_ids]
        log.info(f"   {len(new_acts)} davon neu")

        if args.limit is not None and len(new_acts) > args.limit:
            new_acts = new_acts[:args.limit]
            log.info(f"   Auf {args.limit} begrenzt (--limit)")

        if not new_acts:
            log.info(f"   Keine neuen Aktivitäten – fertig")
            status["finished_at"] = datetime.now(timezone.utc).isoformat()
            status["duration_s"] = round(time.monotonic() - t0, 2)
            return status

        # 5. GPS-Punkte laden
        log.info(f"   Lade GPS-Punkte für {len(new_acts)} Aktivitäten…")
        acts_with_gps: list[dict] = []
        all_gps_points: list[tuple[str, float, float]] = []

        for i, act in enumerate(new_acts, 1):
            aid = str(act.get("activityId", ""))
            name = act.get("activityName", aid)[:40]
            log.info(f"   [{i:>3}/{len(new_acts)}] {name}")

            try:
                points = fetch_gps_polyline(client, aid)
            except Exception as e:
                log.warning(f"      Fehler bei {aid}: {e} – übersprungen")
                status["activities_skipped"] += 1
                continue

            if not points:
                log.info(f"      keine GPS-Punkte – übersprungen")
                status["activities_skipped"] += 1
                continue

            acts_with_gps.append(act)
            for lat, lng in points:
                all_gps_points.append((aid, lat, lng))

            log.info(f"      {len(points)} GPS-Punkte")
            time.sleep(1.0)  # Rate limiting

        if not acts_with_gps:
            log.info(f"   Keine Aktivitäten mit GPS – fertig")
            status["finished_at"] = datetime.now(timezone.utc).isoformat()
            status["duration_s"] = round(time.monotonic() - t0, 2)
            return status

        # 6. CSVs bauen
        summary_csv = build_summary_csv(acts_with_gps)
        gps_lines = ["activity_id,latitude,longitude"]
        for aid, lat, lng in all_gps_points:
            gps_lines.append(f"{aid},{lat},{lng}")
        gps_csv = "\n".join(gps_lines) + "\n"

        # 7. Upload (mit user_id)
        if args.dry_run:
            log.info(f"   DRY-RUN: würde {len(acts_with_gps)} Aktivitäten + "
                     f"{len(all_gps_points)} GPS-Punkte importieren")
            status["activities_imported"] = len(acts_with_gps)
            status["gps_points_imported"] = len(all_gps_points)
        else:
            log.info(f"   Posting {len(acts_with_gps)} activities…")
            r1 = api.import_summary_csv(summary_csv, user_id)
            log.info(f"      → {r1}")

            log.info(f"   Posting {len(all_gps_points):,} GPS points…")
            r2 = api.import_gps_csv(gps_csv, user_id)
            log.info(f"      → {r2}")

            status["activities_imported"] = r1.get("imported", 0)
            status["gps_points_imported"] = r2.get("imported", 0)

    except Exception as e:
        log.error(f"   ❌ Sync fehlgeschlagen: {e}", exc_info=True)
        status["status"] = "error"
        status["error_message"] = str(e)[:500]

    finally:
        status["finished_at"] = datetime.now(timezone.utc).isoformat()
        status["duration_s"] = round(time.monotonic() - t0, 2)

    return status


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Garmin Connect → Trail Atlas API Sync (Multi-User)",
    )
    parser.add_argument("--limit", type=int, default=None,
                        help="Pro User max N neue Aktivitäten syncen")
    parser.add_argument("--dry-run", action="store_true",
                        help="Zeigt was gemacht würde, ohne API-Schreibzugriff")
    parser.add_argument("--full-resync", action="store_true",
                        help="Auch existierende Touren neu syncen")
    parser.add_argument("--max-pages", type=int, default=10,
                        help="Max Garmin-Seiten pro User (100 Aktivitäten/Seite)")
    parser.add_argument("--user", type=int, default=None,
                        help="Nur einen bestimmten User syncen (user_id)")
    args = parser.parse_args()

    global_t0 = time.monotonic()
    env = load_env()

    # SECRET_KEY für Fernet-Entschlüsselung
    secret_key = env.get("SECRET_KEY", "")
    if not secret_key:
        log.error("SECRET_KEY fehlt in garmin.env – kann Credentials nicht entschlüsseln")
        sys.exit(1)
    fernet = _init_fernet(secret_key)

    api = TrailAtlasAPI(env)
    log.info("⛰  Trail Atlas Garmin Sync (Multi-User)")
    log.info(f"   API: {env['API_BASE']}")

    # 1. User-Liste vom Backend holen
    try:
        sync_users = api.get_sync_users()
    except Exception as e:
        log.error(f"❌ Konnte User-Liste nicht laden: {e}")
        sys.exit(1)

    if not sync_users:
        log.info("   Keine User mit Garmin-Credentials – nichts zu tun")
        return

    # Optional: nur einen bestimmten User
    if args.user is not None:
        sync_users = [u for u in sync_users if u["user_id"] == args.user]
        if not sync_users:
            log.error(f"   User {args.user} hat keine Garmin-Credentials")
            sys.exit(1)

    log.info(f"   {len(sync_users)} User zu syncen: "
             f"{', '.join(u['username'] for u in sync_users)}")

    # 2. Pro User syncen
    total_imported = 0
    total_gps = 0
    errors = 0

    for u in sync_users:
        user_id = u["user_id"]
        username = u["username"]

        log.info(f"\n{'─'*50}")
        log.info(f"   User: {username} (id={user_id})")
        log.info(f"{'─'*50}")

        # Credentials entschlüsseln
        try:
            creds = decrypt_credentials(fernet, u["encrypted_credentials"])
        except Exception as e:
            log.error(f"   ❌ Credentials nicht entschlüsselbar: {e}")
            errors += 1
            continue

        # Sync durchführen
        status = sync_user(
            api=api,
            user_id=user_id,
            username=username,
            email=creds["email"],
            password=creds["password"],
            args=args,
        )

        # Sync-Log posten
        if not args.dry_run:
            api.post_sync_log(status)

        total_imported += status["activities_imported"]
        total_gps += status["gps_points_imported"]
        if status["status"] == "error":
            errors += 1

        log.info(f"   → {status['status']}: "
                 f"{status['activities_imported']} acts, "
                 f"{status['gps_points_imported']} pts "
                 f"({status['duration_s']}s)")

    # 3. Zusammenfassung
    elapsed = round(time.monotonic() - global_t0, 2)
    log.info(f"\n{'═'*50}")
    log.info(f"✅ Sync abgeschlossen in {elapsed}s")
    log.info(f"   {len(sync_users)} User, {total_imported} Aktivitäten, {total_gps} GPS-Punkte")
    if errors:
        log.warning(f"   ⚠ {errors} User mit Fehlern")
    log.info(f"{'═'*50}")


if __name__ == "__main__":
    main()
