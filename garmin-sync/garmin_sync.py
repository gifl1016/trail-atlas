#!/usr/bin/env python3
"""
garmin_sync.py
==============
Läd Aktivitäten von Garmin Connect und postet sie direkt an die Trail Atlas API.

Verwendung:
    python3 garmin_sync.py                    # alle neuen Touren
    python3 garmin_sync.py --limit 1          # nur die neueste neue Tour
    python3 garmin_sync.py --limit 5          # die 5 neuesten neuen Touren
    python3 garmin_sync.py --dry-run          # zeigt was gemacht würde
    python3 garmin_sync.py --full-resync      # alle Touren der letzten N Tage
                                              # (auch wenn schon in DB)

Konfiguration: /etc/trail-atlas/garmin.env
"""

import argparse
import csv
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

# ── Token Management ─────────────────────────────────────────────────────────
TOKEN_FILE = Path(os.getenv("GARMIN_TOKEN_FILE", "/var/lib/trail-atlas/garmin_token.json"))

def load_token():
    if TOKEN_FILE.exists():
        try:
            return json.loads(TOKEN_FILE.read_text())
        except Exception as e:
            log.warning(f"Token-Datei korrupt: {e}")
    return None

def save_token(token):
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(token))
    TOKEN_FILE.chmod(0o600)

def login_garmin(env) -> Garmin:
    saved = load_token()
    if saved:
        log.info("Verwende gespeichertes Garmin-Token")
        try:
            client = Garmin()
            client.login(saved)
            return client
        except Exception as e:
            log.warning(f"Token ungültig, fresh login: {e}")

    email    = env.get("GARMIN_EMAIL")
    password = env.get("GARMIN_PASSWORD")
    if not email or not password:
        log.error("GARMIN_EMAIL/GARMIN_PASSWORD fehlen in garmin.env")
        sys.exit(1)

    log.info(f"Login: {email}")
    client = Garmin(email=email, password=password)
    try:
        client.login()
    except GarminConnectAuthenticationError:
        log.error("Login fehlgeschlagen – Email/Passwort prüfen")
        raise

    try:
        save_token(client.garth.dumps())
        log.info(f"Token gespeichert: {TOKEN_FILE}")
    except Exception as e:
        log.warning(f"Token-Speichern fehlgeschlagen: {e}")
    return client

# ── API Helpers ──────────────────────────────────────────────────────────────
class TrailAtlasAPI:
    def __init__(self, env):
        self.base = env["API_BASE"].rstrip("/")
        self.auth = (env["API_USER"], env["API_PASS"])
        self.session = requests.Session()

    def _url(self, path):
        return f"{self.base}{path}"

    def get_existing_ids(self) -> set[str]:
        """Liste aller bereits in der DB vorhandenen activity_ids."""
        r = self.session.get(self._url("/activities"), auth=self.auth, timeout=30)
        r.raise_for_status()
        return {a["activity_id"] for a in r.json()}

    def import_summary_csv(self, csv_text: str) -> dict:
        files = {"file": ("summary.csv", csv_text.encode("utf-8"), "text/csv")}
        r = self.session.post(self._url("/import/summary"),
                              auth=self.auth, files=files, timeout=120)
        r.raise_for_status()
        return r.json()

    def import_gps_csv(self, csv_text: str) -> dict:
        files = {"file": ("gps.csv", csv_text.encode("utf-8"), "text/csv")}
        r = self.session.post(self._url("/import/gps"),
                              auth=self.auth, files=files, timeout=300)
        r.raise_for_status()
        return r.json()

    def post_sync_log(self, entry: dict):
        try:
            r = self.session.post(self._url("/sync/log"),
                                  auth=self.auth, json=entry, timeout=15)
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

    # Falls die API gar nichts liefert
    if not details or not isinstance(details, dict):
        return []

    # Format 1: detailedMetrics – kann None oder Liste sein
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

    # Format 2: geoPolylineDTO – kann None sein
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

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Garmin Connect → Trail Atlas API Sync",
    )
    parser.add_argument("--limit", type=int, default=None,
                        help="Maximal N neue Aktivitäten syncen (für Tests)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Zeigt was gemacht würde, ohne API-Schreibzugriff")
    parser.add_argument("--full-resync", action="store_true",
                        help="Auch existierende Touren neu syncen")
    parser.add_argument("--max-pages", type=int, default=10,
                        help="Max Garmin-Seiten zu durchsuchen (100 Aktivitäten/Seite)")
    args = parser.parse_args()

    started_at = datetime.now(timezone.utc).isoformat()
    t0 = time.monotonic()

    # Status Tracking für Sync-Log
    sync_status = {
        "status": "ok",
        "started_at": started_at,
        "finished_at": None,
        "activities_imported": 0,
        "gps_points_imported": 0,
        "activities_skipped": 0,
        "error_message": None,
        "duration_s": None,
    }

    api = None
    try:
        env = load_env()
        api = TrailAtlasAPI(env)
        log.info("⛰  Trail Atlas Garmin Sync")
        log.info(f"   API: {env['API_BASE']}")

        # 1. Existierende IDs aus der API holen (Inkrementell-Logik)
        existing_ids: set[str] = set()
        if not args.full_resync:
            try:
                existing_ids = api.get_existing_ids()
                log.info(f"   Bereits in DB: {len(existing_ids)} Aktivitäten")
            except Exception as e:
                log.warning(f"   API-Liste konnte nicht geladen werden, full sync: {e}")

        # 2. Garmin login
        client = login_garmin(env)
        log.info("   Login erfolgreich")

        # 3. Recent activities laden
        log.info(f"   Lade Garmin-Aktivitäten (max {args.max_pages*100})…")
        all_acts = fetch_recent_activities(client, args.max_pages)
        log.info(f"   {len(all_acts)} Garmin-Aktivitäten gefunden")

        # 4. Filter: nur neue
        new_acts = [a for a in all_acts if str(a.get("activityId")) not in existing_ids]
        log.info(f"   {len(new_acts)} davon neu")

        if args.limit is not None and len(new_acts) > args.limit:
            new_acts = new_acts[:args.limit]
            log.info(f"   Auf {args.limit} begrenzt (--limit)")

        if not new_acts:
            log.info("   Keine neuen Aktivitäten – fertig")
            sync_status["finished_at"] = datetime.now(timezone.utc).isoformat()
            sync_status["duration_s"] = round(time.monotonic() - t0, 2)
            if not args.dry_run and api:
                api.post_sync_log(sync_status)
            return

        # 5. GPS-Punkte für jede neue Aktivität laden
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
                log.warning(f"      Fehler bei Aktivität {aid}: {e} – übersprungen")
                sync_status["activities_skipped"] += 1
                continue

            if not points:
                log.info(f"      keine GPS-Punkte – übersprungen")
                sync_status["activities_skipped"] += 1
                continue

            acts_with_gps.append(act)
            for lat, lng in points:
                all_gps_points.append((aid, lat, lng))

            log.info(f"      {len(points)} GPS-Punkte")
            time.sleep(1.0)  # Rate limiting

        if not acts_with_gps:
            log.info("   Keine Aktivitäten mit GPS – fertig")
            sync_status["finished_at"] = datetime.now(timezone.utc).isoformat()
            sync_status["duration_s"] = round(time.monotonic() - t0, 2)
            if not args.dry_run and api:
                api.post_sync_log(sync_status)
            return

        # 6. CSVs bauen
        summary_csv = build_summary_csv(acts_with_gps)
        gps_lines = ["activity_id,latitude,longitude"]
        for aid, lat, lng in all_gps_points:
            gps_lines.append(f"{aid},{lat},{lng}")
        gps_csv = "\n".join(gps_lines) + "\n"

        # 7. Dry-run oder echter Upload
        if args.dry_run:
            log.info(f"   DRY-RUN: würde {len(acts_with_gps)} Aktivitäten + "
                     f"{len(all_gps_points)} GPS-Punkte importieren")
            sync_status["activities_imported"] = len(acts_with_gps)
            sync_status["gps_points_imported"] = len(all_gps_points)
        else:
            log.info(f"   Posting {len(acts_with_gps)} activities to /import/summary…")
            r1 = api.import_summary_csv(summary_csv)
            log.info(f"      → {r1}")

            log.info(f"   Posting {len(all_gps_points):,} GPS points to /import/gps…")
            r2 = api.import_gps_csv(gps_csv)
            log.info(f"      → {r2}")

            sync_status["activities_imported"] = r1.get("imported", 0)
            sync_status["gps_points_imported"] = r2.get("imported", 0)

    except Exception as e:
        log.error(f"❌  Sync fehlgeschlagen: {e}", exc_info=True)
        sync_status["status"] = "error"
        sync_status["error_message"] = str(e)[:500]

    finally:
        sync_status["finished_at"] = datetime.now(timezone.utc).isoformat()
        sync_status["duration_s"] = round(time.monotonic() - t0, 2)
        if not args.dry_run and api is not None:
            api.post_sync_log(sync_status)
        log.info(f"✅ Done in {sync_status['duration_s']}s "
                 f"(status: {sync_status['status']}, "
                 f"imported: {sync_status['activities_imported']} acts, "
                 f"{sync_status['gps_points_imported']} pts)")

if __name__ == "__main__":
    main()
