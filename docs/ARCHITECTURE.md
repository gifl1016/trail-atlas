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
                                     │ HTTPS + Session-Cookie
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
                 │   EnvironmentFile: /etc/trail-atlas/garmin.env
                 │   /opt/trail-atlas/backend/                 │
                 │                                             │
                 │   Auth: Session-Cookies (itsdangerous)      │
                 │         + Sync-API-Key (X-Sync-Key Header)  │
                 └───────────────────┬─────────────────────────┘
                                     │ persistent connection
                                     ▼
                 ┌─────────────────────────────────────────────┐
                 │   SQLite (WAL-Modus, Schema v2)             │
                 │   /var/lib/trail-atlas/trail_atlas.db       │
                 │                                             │
                 │   Tabellen: activities, gps_points,         │
                 │     sync_log, users, garmin_credentials,    │
                 │     invite_codes                            │
                 └─────────────────────────────────────────────┘

                 ┌─────────────────────────────────────────────┐
                 │   Garmin Sync (Cronjob, täglich 03:00)      │
                 │   /opt/trail-atlas/garmin-sync/             │
                 │   Auth: X-Sync-Key Header (SYNC_API_KEY)   │
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

### App Boot + Auth

```
Browser → GET /api/health
  → GET /api/auth/me
    ├─ 200 {auth_active: false} → App startet (keine Users in DB)
    ├─ 200 {user: {...}}       → App startet (eingeloggt)
    └─ 401                     → Login-Screen zeigen
       → User gibt Credentials ein
       → POST /api/auth/login → Session-Cookie gesetzt
       → App startet

App Start:
  → GET /api/activities        → Liste aller Touren
  → GET /api/activities/gps/all → ALLE GPS-Punkte in einem Request
  → Karte rendern aus Cache (kein N+1 Problem)
```

### CSV Import (manuell über die App)

```
User wählt CSV-Dateien
  → Browser liest Dateien
    → POST /api/import/summary   (Metadaten)
    → POST /api/import/gps       (GPS-Punkte)
      → FastAPI validiert Felder, Koordinaten, Duplikate
        → SQLite INSERT (idempotent: ON CONFLICT für Metadaten,
                         DELETE+INSERT für GPS-Punkte)
          → App lädt Daten neu via GET /api/activities + /gps/all
```

### Garmin Sync (automatisch per Cronjob)

```
Cron 03:00 → garmin_sync.py
  → Auth via X-Sync-Key Header (SYNC_API_KEY)
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
App Boot → GET /api/activities/gps/all → kompletter GPS-Cache
  → Karten-Tab:
    → Für jede gefilterte Tour: GPS aus Cache lesen (kein API-Call)
    → Canvas Renderer zeichnet Polylines (alle auf einem <canvas>)
    → Adaptive Punkt-Reduktion pro Track
  → Detail-Sheet bei Klick:
    → Distanz aus Cache berechnen
    → Selektion: gewählter Track highlighted, andere ausgegraut
```

### User-Registrierung (Invite-Flow)

```
Admin → POST /api/auth/invite → generiert Einladungscode (7 Tage gültig)
  → Admin schickt Link an Freund: https://app.url/?invite=CODE
  → Freund öffnet Link → Signup-Formular mit vorausgefülltem Code
  → POST /api/auth/signup (code + username + password)
    → Code validiert + eingelöst
    → User angelegt (bcrypt-Hash)
    → Session-Cookie gesetzt → App startet
```

---

## Key Architecture Decisions

### Warum SQLite und nicht PostgreSQL?

Die VM hat 1GB RAM und 1 vCPU. SQLite verbraucht ~5MB RAM statt ~100MB+ für PostgreSQL. Für einen Multi-User-Anwendungsfall mit wenigen Nutzern, hunderten Touren und hunderttausenden GPS-Punkten ist SQLite die richtige Wahl. WAL-Modus ermöglicht concurrent Reads während Writes laufen.

### Warum Single-Worker uvicorn?

SQLite unterstützt nur einen schreibenden Prozess gleichzeitig. Mehrere uvicorn-Worker würden zu Lock-Contention führen. Ein Worker reicht für den Traffic-Level dieser App aus (wenige Nutzer, gelegentliche API-Calls).

### Warum persistente DB-Connection statt Connection-per-Request?

Frühere Version öffnete pro API-Call eine neue SQLite-Connection. Im WAL-Modus können neue Connections kurzzeitig alte Snapshots sehen (WAL-Race-Condition). Symptom war: DELETE gibt 200 zurück, aber nachfolgendes GET zeigt den gelöschten Eintrag noch. Fix: eine persistente Connection für alle Operationen, serialisiert durch Threading-Lock.

### Warum Vanilla JS und kein Framework (React, Vue)?

Die App ist eine einzelne HTML-Datei. Ein Framework würde Build-Tools, Node.js, und einen Build-Prozess erfordern. Für den Umfang dieser App (Karte + Liste + Diagramm + Import + Auth) ist Vanilla JS mit Leaflet ausreichend. Die Datei lässt sich direkt deployen ohne Kompilierung.

### Warum Canvas-Renderer statt SVG?

Leaflet rendert Polylines standardmäßig als SVG-Elemente. Bei 400+ Tracks führt das zu hunderten DOM-Elementen die bei Style-Änderungen (z.B. Selektion) jeweils einzeln repainted werden. Der Canvas-Renderer zeichnet alle Tracks auf ein einziges `<canvas>`-Element – ein einziger Repaint statt N.

### Warum Bulk-GPS-Endpoint statt N einzelne Calls?

v3.4 führte `GET /activities/gps/all` ein, der alle GPS-Punkte in einem Request liefert. Vorher: N sequenzielle API-Calls (ein Call pro Tour), was bei 50+ Touren zu 15-60 Sekunden Ladezeit führte. Nachher: ein einziger Request, 1-3 Sekunden.

### Warum Session-Auth statt JWT?

Sessions sind einfacher zu implementieren, zu debuggen und sofort invalidierbar (Logout). JWT-Tokens sind nach Erstellung gültig bis sie ablaufen – ein Logout erfordert eine Blacklist. Für 1-5 User ist Session-Auth der pragmatischere Ansatz.

### Warum redirect_slashes=False in FastAPI?

Standard-Verhalten von FastAPI: wenn ein Client `DELETE /db/reset/` (mit Trailing-Slash) sendet, antwortet FastAPI mit `307 Redirect` auf `/db/reset`. Browser und manche HTTP-Clients konvertieren bei Redirects die Methode von DELETE zu GET. FastAPI antwortet dann mit `405 Method Not Allowed`. Fix: `redirect_slashes=False` – beide URL-Varianten werden direkt bedient.

### Warum Libraries lokal statt CDN?

SRI-Hashes auf CDN-Ressourcen schlugen in der Praxis fehl (unpkg.com lieferte je nach Request leicht unterschiedliche Builds). Lokales Hosting eliminiert diese Abhängigkeit, ermöglicht korrekte SRI-Berechnung beim Deploy, und funktioniert auch wenn der CDN nicht erreichbar ist.

---

## Database Schema (v2)

```sql
-- Aktivitäten (Touren-Metadaten)
CREATE TABLE activities (
    activity_id   TEXT PRIMARY KEY,
    activity_type TEXT NOT NULL DEFAULT 'unknown',
    start_date    TEXT NOT NULL,
    end_date      TEXT,
    start_lat     REAL NOT NULL,
    start_lng     REAL NOT NULL,
    user_id       INTEGER REFERENCES users(id)    -- seit Schema v2
);

-- GPS-Trackpunkte
CREATE TABLE gps_points (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id   TEXT NOT NULL,
    lat           REAL NOT NULL,
    lng           REAL NOT NULL,
    FOREIGN KEY (activity_id) REFERENCES activities(activity_id) ON DELETE CASCADE
);

-- Sync-Protokoll
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

-- User-Verwaltung (seit Schema v2)
CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,              -- bcrypt
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Garmin-Zugangsdaten pro User (vorbereitet, noch nicht genutzt)
CREATE TABLE garmin_credentials (
    user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    token_json  TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Einmal-Einladungscodes
CREATE TABLE invite_codes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL UNIQUE,
    created_by  INTEGER REFERENCES users(id),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    expires_at  TEXT NOT NULL,
    used_at     TEXT,
    used_by     INTEGER REFERENCES users(id)
);
```

Indizes: `idx_gps_activity`, `idx_activities_date`, `idx_activities_type`, `idx_activities_user`, `idx_sync_log_started`, `idx_users_username`, `idx_invite_code`.

Schema-Migration: `database.py` → `init()` erstellt alle Tabellen idempotent via `CREATE TABLE IF NOT EXISTS`. Neue Spalten (wie `activities.user_id`) werden in `_migrate_activities_user_id()` über `ALTER TABLE ADD COLUMN` nachgezogen falls noch nicht vorhanden.

---

## File Layout on VM

```
/opt/trail-atlas/
├── backend/
│   ├── main.py              ← FastAPI Endpoints + Auth
│   ├── auth.py              ← Session-Management, Invite-Codes
│   ├── database.py          ← SQLite Wrapper (Schema v2)
│   └── requirements.txt     ← fastapi, uvicorn, bcrypt, itsdangerous
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
└── garmin.env               ← Alle Secrets (chmod 600)
                                GARMIN_EMAIL, GARMIN_PASSWORD
                                SECRET_KEY, ADMIN_USER, ADMIN_PASS
                                SYNC_API_KEY

/etc/cron.d/
└── trail-atlas-garmin-sync  ← Cronjob Definition

/etc/nginx/sites-available/
└── trail-atlas              ← Nginx Server-Konfiguration

/etc/systemd/system/
├── trail-atlas.service      ← Backend systemd Unit
└── trail-atlas.service.d/
    └── override.conf        ← EnvironmentFile Override

/var/log/trail-atlas/
└── garmin_sync.log          ← Sync-Logs
```
