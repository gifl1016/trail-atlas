# Architecture

## System Overview

```
                 ┌─────────────────────────────────────────────┐
                 │   Browser (HTML / CSS / Vanilla JS)         │
                 │   Leaflet 1.9.4 (Canvas Renderer)           │
                 │   PapaParse 5.4.1 (CSV Upload)              │
                 │                                             │
                 │   Libraries served locally from /libs/       │
                 │   SRI-Hashes computed at deploy time         │
                 └───────────────────┬─────────────────────────┘
                                     │ HTTPS + Basic Auth
                                     ▼
                 ┌─────────────────────────────────────────────┐
                 │   Nginx (Reverse Proxy + TLS)               │
                 │   Let's Encrypt (auto-renewal via Certbot)  │
                 │   DuckDNS (Dynamic DNS)                     │
                 │                                             │
                 │   /          → /var/www/trail-atlas/        │
                 │   /libs/     → lokale JS/CSS Bibliotheken   │
                 │   /api/      → 127.0.0.1:8000 (FastAPI)    │
                 └───────────────────┬─────────────────────────┘
                                     │ HTTP (localhost only)
                                     ▼
                 ┌─────────────────────────────────────────────┐
                 │   FastAPI (uvicorn, 1 Worker)               │
                 │   systemd: trail-atlas.service              │
                 │   /opt/trail-atlas/backend/                 │
                 └───────────────────┬─────────────────────────┘
                                     │ persistent connection
                                     ▼
                 ┌─────────────────────────────────────────────┐
                 │   SQLite (WAL-Modus)                        │
                 │   /var/lib/trail-atlas/trail_atlas.db       │
                 └─────────────────────────────────────────────┘

                 ┌─────────────────────────────────────────────┐
                 │   Garmin Sync (Cronjob, täglich 03:00)      │
                 │   /opt/trail-atlas/garmin-sync/             │
                 │   garminconnect Library (inoffiziell)       │
                 │                                             │
                 │   Garmin Connect ← fetch activities + GPS   │
                 │   Trail Atlas API ← POST /import/*          │
                 └─────────────────────────────────────────────┘

                 ┌─────────────────────────────────────────────┐
                 │   GitHub Actions (CI/CD)                    │
                 │   Push auf main → automatisches Deployment  │
                 │   Path-basierte Job-Selektion               │
                 │   Backend-Deploy mit DB-Backup + Rollback   │
                 └─────────────────────────────────────────────┘
```

---

## Data Flow

### CSV Import (manuell über die App)

```
User wählt CSV-Dateien
  → Browser liest Dateien
    → POST /api/import/summary   (Metadaten)
    → POST /api/import/gps       (GPS-Punkte)
      → FastAPI validiert Felder, Koordinaten, Duplikate
        → SQLite INSERT (idempotent: ON CONFLICT für Metadaten,
                         DELETE+INSERT für GPS-Punkte)
          → App lädt Daten neu via GET /api/activities
```

### Garmin Sync (automatisch per Cronjob)

```
Cron 03:00 → garmin_sync.py
  → GET /api/activities → sammle existierende IDs
  → Garmin Connect Login (Token oder Passwort)
  → Garmin API: lade Aktivitäten paginiert (max 1000)
  → Filter: nur IDs die noch nicht in DB sind
  → Pro neue Aktivität: lade GPS-Polyline
    → Überspringe Aktivitäten ohne GPS
  → Baue CSV im Speicher
  → POST /api/import/summary + /api/import/gps
  → POST /api/sync/log (Statistik für Frontend)
```

### Track Rendering (Browser)

```
App Boot → GET /api/activities → Liste aller Touren
  → Karten-Tab:
    → Für jede gefilterte Tour: GET /api/activities/{id}/gps
    → Canvas Renderer zeichnet Polylines (alle auf einem <canvas>)
    → Adaptive Punkt-Reduktion pro Track
  → Detail-Sheet bei Klick:
    → Distanz aus Cache oder nachladen
    → Selektion: gewählter Track highlighted, andere ausgegraut
```

---

## Key Architecture Decisions

### Warum SQLite und nicht PostgreSQL?

Die VM hat 1GB RAM und 1 vCPU. SQLite verbraucht ~5MB RAM statt ~100MB+ für PostgreSQL. Für einen Single-User-Anwendungsfall mit hunderten Touren und hunderttausenden GPS-Punkten ist SQLite die richtige Wahl. WAL-Modus ermöglicht concurrent Reads während Writes laufen.

### Warum Single-Worker uvicorn?

SQLite unterstützt nur einen schreibenden Prozess gleichzeitig. Mehrere uvicorn-Worker würden zu Lock-Contention führen. Ein Worker reicht für den Traffic-Level dieser App aus (einzelner Nutzer, gelegentliche API-Calls).

### Warum persistente DB-Connection statt Connection-per-Request?

Frühere Version öffnete pro API-Call eine neue SQLite-Connection. Im WAL-Modus können neue Connections kurzzeitig alte Snapshots sehen (WAL-Race-Condition). Symptom war: DELETE gibt 200 zurück, aber nachfolgendes GET zeigt den gelöschten Eintrag noch. Fix: eine persistente Connection für alle Operationen, serialisiert durch Threading-Lock.

### Warum Vanilla JS und kein Framework (React, Vue)?

Die App ist eine einzelne HTML-Datei. Ein Framework würde Build-Tools, Node.js, und einen Build-Prozess erfordern. Für den Umfang dieser App (Karte + Liste + Diagramm + Import) ist Vanilla JS mit Leaflet ausreichend. Die Datei lässt sich direkt deployen ohne Kompilierung.

### Warum Canvas-Renderer statt SVG?

Leaflet rendert Polylines standardmäßig als SVG-Elemente. Bei 400+ Tracks führt das zu hunderten DOM-Elementen die bei Style-Änderungen (z.B. Selektion) jeweils einzeln repainted werden. Der Canvas-Renderer zeichnet alle Tracks auf ein einziges `<canvas>`-Element – ein einziger Repaint statt N.

### Warum redirect_slashes=False in FastAPI?

Standard-Verhalten von FastAPI: wenn ein Client `DELETE /db/reset/` (mit Trailing-Slash) sendet, antwortet FastAPI mit `307 Redirect` auf `/db/reset`. Browser und manche HTTP-Clients konvertieren bei Redirects die Methode von DELETE zu GET. FastAPI antwortet dann mit `405 Method Not Allowed`. Fix: `redirect_slashes=False` – beide URL-Varianten werden direkt bedient.

### Warum Libraries lokal statt CDN?

SRI-Hashes auf CDN-Ressourcen schlugen in der Praxis fehl (unpkg.com lieferte je nach Request leicht unterschiedliche Builds). Lokales Hosting eliminiert diese Abhängigkeit, ermöglicht korrekte SRI-Berechnung beim Deploy, und funktioniert auch wenn der CDN nicht erreichbar ist.

### Warum CSP connect-src 'self' statt 'none'?

Die v2.x-Version hatte `connect-src 'none'` weil alle Daten in IndexedDB lagen und kein Netzwerkzugriff nötig war. Die v3.x-API-Version braucht `connect-src 'self'` damit `fetch()`-Calls an die eigene API funktionieren. `'self'` erlaubt Same-Origin-Requests aber blockiert weiterhin Calls an externe Domains.

---

## Database Schema

```sql
CREATE TABLE activities (
    activity_id   TEXT PRIMARY KEY,
    activity_type TEXT NOT NULL DEFAULT 'unknown',
    start_date    TEXT NOT NULL,
    end_date      TEXT,
    start_lat     REAL NOT NULL,
    start_lng     REAL NOT NULL
);

CREATE TABLE gps_points (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id   TEXT NOT NULL,
    lat           REAL NOT NULL,
    lng           REAL NOT NULL,
    FOREIGN KEY (activity_id) REFERENCES activities(activity_id) ON DELETE CASCADE
);

CREATE TABLE sync_log (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    status               TEXT NOT NULL,       -- 'ok' oder 'error'
    started_at           TEXT NOT NULL,       -- ISO 8601
    finished_at          TEXT,
    activities_imported  INTEGER DEFAULT 0,
    gps_points_imported  INTEGER DEFAULT 0,
    activities_skipped   INTEGER DEFAULT 0,
    error_message        TEXT,
    duration_s           REAL
);
```

Indizes: `idx_gps_activity (activity_id)`, `idx_activities_date (start_date DESC)`, `idx_activities_type (activity_type)`, `idx_sync_log_started (started_at DESC)`.

---

## File Layout on VM

```
/opt/trail-atlas/
├── backend/
│   ├── main.py              ← FastAPI Endpoints
│   └── database.py          ← SQLite Wrapper
├── garmin-sync/
│   └── garmin_sync.py       ← Garmin Sync Script
└── venv/                    ← Python Virtual Environment

/var/lib/trail-atlas/
├── trail_atlas.db           ← SQLite Datenbank
├── trail_atlas.db-wal       ← WAL-Datei (automatisch)
├── trail_atlas.db-shm       ← Shared Memory (automatisch)
├── garmin_token.json        ← Garmin Session Token
└── backups/                 ← automatische DB-Backups

/var/www/trail-atlas/
├── index.html               ← App (deployed von GitHub Actions)
└── libs/
    ├── leaflet.css
    ├── leaflet.js
    ├── papaparse.min.js
    └── sri_hashes.txt

/etc/trail-atlas/
└── garmin.env               ← Garmin Credentials (chmod 600)

/etc/cron.d/
└── trail-atlas-garmin-sync  ← Cronjob Definition

/etc/nginx/sites-available/
└── trail-atlas              ← Nginx Server-Konfiguration

/etc/systemd/system/
└── trail-atlas.service      ← Backend systemd Unit

/var/log/trail-atlas/
└── garmin_sync.log          ← Sync-Logs
```
