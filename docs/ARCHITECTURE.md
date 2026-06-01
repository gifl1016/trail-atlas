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
                 │   Garmin-Credentials: Fernet-verschlüsselt  │
                 └───────────────────┬─────────────────────────┘
                                     │ persistent connection
                                     ▼
                 ┌─────────────────────────────────────────────┐
                 │   SQLite (WAL-Modus, Schema v4)             │
                 │   /var/lib/trail-atlas/trail_atlas.db       │
                 │                                             │
                 │   Tabellen: activities, gps_points,         │
                 │     sync_log, users, garmin_credentials,    │
                 │     invite_codes, activity_ratings          │
                 └─────────────────────────────────────────────┘

                 ┌─────────────────────────────────────────────┐
                 │   Garmin Sync (Cronjob, täglich 03:00)      │
                 │   Multi-User: iteriert über alle User mit   │
                 │   hinterlegten Garmin-Credentials           │
                 │   /opt/trail-atlas/garmin-sync/             │
                 │   /var/lib/trail-atlas/garmin_tokens/{uid}.json
                 │   Auth: X-Sync-Key Header (SYNC_API_KEY)   │
                 │   Login-Timeout + Fail-Tracking             │
                 └─────────────────────────────────────────────┘

                 ┌─────────────────────────────────────────────┐
                 │   GitHub Actions (CI/CD)                    │
                 │   Push auf main → automatisches Deployment  │
                 │   Path-basierte Job-Selektion:              │
                 │     src/         → Frontend                 │
                 │     backend/     → Backend (mit Rollback)   │
                 │     garmin-sync/ → Sync-Script              │
                 │     scripts/     → CLI + ggf. Deploy-Scripts│
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
  → GET /api/activities        → Liste aller Touren des Users (inkl. rating)
  → GET /api/activities/gps/all → ALLE GPS-Punkte des Users in einem Request
  → Karte rendern aus Cache (kein N+1 Problem)

Wenn User Admin: + GET /admin/users für die Nutzerverwaltung (4. Tab)
```

### Signup mit Garmin-Verknüpfung (Invite-Flow)

```
Admin → POST /api/auth/invite → generiert Einladungscode (7 Tage gültig)
  → Link an Freund: https://app.url/?invite=CODE
  → Freund öffnet → Signup-Formular (Username, Passwort, optional Garmin-Email+PW)
  → POST /api/auth/signup
    ├─ Falls Garmin angegeben:
    │    Email-Hash bilden → Duplikat-Check (UNIQUE Index)
    │    Bei Duplikat: 409, User wird NICHT angelegt, Invite bleibt gültig
    ├─ User anlegen (bcrypt-Hash)
    ├─ Invite einlösen
    ├─ Falls Garmin: Email+Passwort Fernet-verschlüsseln + speichern
    └─ Session-Cookie setzen → App startet
```

### Multi-User Garmin Sync (automatisch per Cronjob)

```
Cron 03:00 → garmin_sync.py
  → GET /api/sync/users (mit X-Sync-Key)
    → Liste aller User mit verschlüsselten Garmin-Credentials + updated_at
  → Für jeden User:
    → Fail-Check: letzten 2 Logins fehlgeschlagen? → User überspringen
      (Schutz vor Garmin-Account-Sperre)
    → Credentials entschlüsseln (Fernet, Key aus SECRET_KEY)
    → Token aus /var/lib/trail-atlas/garmin_tokens/{uid}.json laden
    → Garmin Login (mit 30s Timeout via threading)
      ├─ Auth-Fehler: Fail-Counter+1, nächster User
      └─ Erfolg: Token speichern, Fail-Counter reset
    → GET /api/activities?user_id=X → existierende IDs
    → Garmin API: lade Aktivitäten paginiert (max 1000)
    → Filter: nur IDs die noch nicht in DB sind
    → POST /api/import/summary?user_id=X (alle neuen Activities, auch ohne GPS)
    → Best-effort GPS-Fetch pro Activity (Fehler verlieren die Activity nicht)
    → POST /api/import/gps?user_id=X (nur die mit Track)
    → POST /api/sync/log mit user_id (für Per-User-Status)
```

### Rating-Flow

```
User klickt auf Tour → openActivity()
  → showSheet(act) mit Sterne-Block
  → Klick auf Stern N:
    ├─ Wenn act.rating === N → DELETE /activities/{id}/rating (zurück auf unbewertet)
    └─ Sonst                 → PUT /activities/{id}/rating {rating: N}
  → Lokaler State aktualisiert (act.rating, activities-Liste)
  → Aktivitätenliste neu gerendert (Sterne-Badge)
```

### Reset-Optionen

```
DELETE /db/gps       → nur GPS-Punkte des Users, Activities + Ratings bleiben
DELETE /db/garmin    → Activities + GPS des Users, RATINGS BLEIBEN
                       (Activity-Ratings haben KEINEN FK auf activities → überlebt)
DELETE /db/reset     → alles inkl. Ratings
```

Beim nächsten Sync nach `/db/garmin` tauchen die Activities wieder auf und die Ratings sind sofort wieder sichtbar (verknüpft über `activity_id`).

---

## API Endpoints

| Method | Endpoint | Auth | Beschreibung |
|--------|----------|------|-------------|
| GET | `/health` | – | Health-Check (öffentlich) |
| POST | `/auth/login` | – | Login → Session-Cookie |
| POST | `/auth/logout` | – | Session-Cookie löschen |
| GET | `/auth/me` | Session | Aktueller User (oder auth_active=false) |
| POST | `/auth/signup` | – | Registrierung mit Invite-Code + optional Garmin-Creds |
| POST | `/auth/invite` | Admin | Invite-Code generieren |
| GET | `/activities` | Session/Key | Activities des Users (inkl. eigenes Rating) |
| GET | `/activities/{id}` | Session/Key | Eine Aktivität (mit Rating) |
| GET | `/activities/{id}/gps` | Session/Key | GPS-Punkte einer Aktivität |
| GET | `/activities/gps/all` | Session/Key | Alle GPS-Punkte des Users (Bulk) |
| DELETE | `/activities/{id}` | Session/Key | Aktivität + GPS löschen |
| PUT | `/activities/{id}/rating` | Session | Bewertung 1–5 setzen |
| DELETE | `/activities/{id}/rating` | Session | Bewertung entfernen |
| POST | `/import/summary` | Session/Key | CSV Metadaten importieren |
| POST | `/import/gps` | Session/Key | CSV GPS-Punkte importieren |
| GET | `/db/stats` | Session/Key | DB-Statistiken |
| DELETE | `/db/reset` | Session/Key | Alle Daten löschen (inkl. Ratings) |
| DELETE | `/db/garmin` | Session/Key | Garmin-Daten löschen, Ratings bleiben |
| DELETE | `/db/gps` | Session/Key | Nur GPS-Punkte löschen |
| GET | `/sync/status` | Session/Key | Letzte Sync-Einträge (per-User gefiltert) |
| POST | `/sync/log` | Key | Sync-Ergebnis protokollieren (mit user_id) |
| GET | `/sync/users` | Key | Alle User mit Garmin-Credentials (für Sync-Script) |
| GET | `/admin/users` | Admin | Übersicht aller User (mit Stats + letztem Sync) |
| DELETE | `/admin/users/{id}` | Admin | User komplett löschen (kaskadiert alles) |
| POST | `/admin/users/{id}/garmin` | Admin | Garmin-Credentials für User setzen |
| DELETE | `/admin/users/{id}/garmin` | Admin | Garmin-Verknüpfung entfernen |

**Auth-Legende:** `–` = öffentlich, `Session` = Session-Cookie, `Key` = X-Sync-Key Header, `Admin` = Session-Cookie + is_admin=1. Admin-Endpoints lehnen Sync-Key-Auth ab (kein User-Kontext).

---

## Key Architecture Decisions

### Warum SQLite und nicht PostgreSQL?

Die VM hat 1GB RAM und 1 vCPU. SQLite verbraucht ~5MB RAM statt ~100MB+ für PostgreSQL. Für einen Multi-User-Anwendungsfall mit wenigen Nutzern, hunderten Touren und hunderttausenden GPS-Punkten ist SQLite die richtige Wahl. WAL-Modus ermöglicht concurrent Reads während Writes laufen.

### Warum Single-Worker uvicorn?

SQLite unterstützt nur einen schreibenden Prozess gleichzeitig. Mehrere uvicorn-Worker würden zu Lock-Contention führen. Ein Worker reicht für den Traffic-Level dieser App aus (wenige Nutzer, gelegentliche API-Calls).

### Warum persistente DB-Connection statt Connection-per-Request?

Frühere Version öffnete pro API-Call eine neue SQLite-Connection. Im WAL-Modus können neue Connections kurzzeitig alte Snapshots sehen (WAL-Race-Condition). Symptom war: DELETE gibt 200 zurück, aber nachfolgendes GET zeigt den gelöschten Eintrag noch. Fix: eine persistente Connection für alle Operationen, serialisiert durch Threading-Lock.

### Warum Vanilla JS und kein Framework (React, Vue)?

Die App ist eine einzelne HTML-Datei. Ein Framework würde Build-Tools, Node.js, und einen Build-Prozess erfordern. Für den Umfang dieser App (Karte + Liste + Diagramm + Import + Auth + Admin) ist Vanilla JS mit Leaflet ausreichend. Die Datei lässt sich direkt deployen ohne Kompilierung.

### Warum Canvas-Renderer statt SVG?

Leaflet rendert Polylines standardmäßig als SVG-Elemente. Bei 400+ Tracks führt das zu hunderten DOM-Elementen die bei Style-Änderungen (z.B. Selektion) jeweils einzeln repainted werden. Der Canvas-Renderer zeichnet alle Tracks auf ein einziges `<canvas>`-Element – ein einziger Repaint statt N.

### Warum Bulk-GPS-Endpoint statt N einzelne Calls?

v3.4 führte `GET /activities/gps/all` ein, der alle GPS-Punkte in einem Request liefert. Vorher: N sequenzielle API-Calls (ein Call pro Tour), was bei 50+ Touren zu 15-60 Sekunden Ladezeit führte. Nachher: ein einziger Request, 1-3 Sekunden.

### Warum Session-Auth statt JWT?

Sessions sind einfacher zu implementieren, zu debuggen und sofort invalidierbar (Logout). JWT-Tokens sind nach Erstellung gültig bis sie ablaufen – ein Logout erfordert eine Blacklist. Für 1-5 User ist Session-Auth der pragmatischere Ansatz.

### Warum Fernet für Garmin-Credentials?

Garmin-Credentials werden im Klartext für den Login benötigt – also können sie nicht wie Passwörter mit bcrypt gehashed werden. Lösung: symmetrische Fernet-Verschlüsselung. Der Key wird deterministisch aus `SECRET_KEY` abgeleitet (SHA256 → base64 → Fernet-Key). Damit braucht das Sync-Script nur den `SECRET_KEY` aus `garmin.env` und kann selbst entschlüsseln, ohne dass Klartext-Passwörter über die API gehen.

### Warum `email_hash` (one-way) statt direkter Vergleich?

Ein Garmin-Account darf nur einmal in Trail Atlas registriert werden – sonst überschreiben sich User beim Sync die Activities gegenseitig (gleiche `activity_id`). Direkter Email-Vergleich geht nicht: Fernet erzeugt bei jedem Verschlüsseln anderen Ciphertext. Lösung: SHA-256 der normalisierten Email als zusätzliche Spalte mit UNIQUE-Index. Der Hash erlaubt Duplikat-Checks ohne die Email im Klartext zu speichern.

### Warum `activity_ratings` ohne FK auf `activities`?

Bewertungen sollen einen DB-Reset überleben. Ein Foreign Key mit `ON DELETE CASCADE` würde sie beim `DELETE FROM activities` automatisch mitlöschen. Stattdessen referenziert `activity_ratings` nur `users` (mit CASCADE) und enthält die `activity_id` als reines Datenfeld. Konsequenz: Eine Bewertung kann kurzzeitig auf eine `activity_id` zeigen, die gerade nicht als Activity existiert. Nach dem nächsten Sync ist sie sofort wieder sichtbar.

### Warum Login-Timeout + Fail-Tracking im Garmin-Sync?

Die garminconnect-Library hat interne Cloudflare-Retries die bei falschen Credentials in eine Endlosschleife laufen. Außerdem sperrt Garmin Accounts temporär (24h) nach mehreren fehlgeschlagenen Login-Versuchen. Schutzmaßnahmen:
1. **Login-Timeout 30s** via Threading – verhindert hängende Cron-Läufe
2. **Fail-Tracking** in `/var/lib/trail-atlas/garmin_tokens/_login_failures.json`: nach 2 fehlgeschlagenen Logins wird der User übersprungen
3. **Auto-Reset** des Counters wenn `updated_at` der Credentials neuer ist als `last_fail` (Admin hat sie aktualisiert)

### Warum redirect_slashes=False in FastAPI?

Standard-Verhalten von FastAPI: wenn ein Client `DELETE /db/reset/` (mit Trailing-Slash) sendet, antwortet FastAPI mit `307 Redirect` auf `/db/reset`. Browser und manche HTTP-Clients konvertieren bei Redirects die Methode von DELETE zu GET. FastAPI antwortet dann mit `405 Method Not Allowed`. Fix: `redirect_slashes=False` – beide URL-Varianten werden direkt bedient.

### Warum Libraries lokal statt CDN?

SRI-Hashes auf CDN-Ressourcen schlugen in der Praxis fehl (unpkg.com lieferte je nach Request leicht unterschiedliche Builds). Lokales Hosting eliminiert diese Abhängigkeit, ermöglicht korrekte SRI-Berechnung beim Deploy, und funktioniert auch wenn der CDN nicht erreichbar ist.

### Warum zweistufiges Activity-Type-Mapping?

Garmin liefert sehr feingranulare Typen (`treadmill_running`, `indoor_cardio`, `resort_skiing_snowboarding_ws`). Wir haben zwei Mapping-Stufen, beide bewusst:

1. **Sync-Stufe** (`garmin_sync.py` → `ACTIVITY_TYPE_MAP`): Garmin-typeKey → DB-Wert. Fasst Varianten desselben Sports zusammen (z.B. `treadmill_running` → `running`). Dieser DB-Wert ist die "Wahrheit" und wird in Aktivitätenliste + Deep-Dive angezeigt.

2. **Frontend-Stufe** (HTML → `TYPE_GROUP`): DB-Wert → UI-Gruppe. Nur für **Karten-Legende** und **Filter-Dropdown**. Mergt verwandte Kategorien (Wandern+Gehen, Schwimmen+Wasser, Kraft+Cardio) für die Übersichtlichkeit. In der Liste/im Detail sieht der User weiterhin den echten DB-Wert.

---

## Database Schema (v4)

```sql
-- Aktivitäten (Touren-Metadaten)
CREATE TABLE activities (
    activity_id   TEXT PRIMARY KEY,
    activity_type TEXT NOT NULL DEFAULT 'unknown',
    start_date    TEXT NOT NULL,
    end_date      TEXT,
    start_lat     REAL,             -- v3: nullable für Activities ohne GPS
    start_lng     REAL,             -- v3: nullable
    user_id       INTEGER REFERENCES users(id)  -- v2: Multi-User
);

-- GPS-Trackpunkte
CREATE TABLE gps_points (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id   TEXT NOT NULL,
    lat           REAL NOT NULL,
    lng           REAL NOT NULL,
    FOREIGN KEY (activity_id) REFERENCES activities(activity_id) ON DELETE CASCADE
);

-- Sync-Protokoll (v3: pro User)
CREATE TABLE sync_log (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              INTEGER REFERENCES users(id) ON DELETE SET NULL,
    status               TEXT NOT NULL,       -- 'ok' oder 'error'
    started_at           TEXT NOT NULL,
    finished_at          TEXT,
    activities_imported  INTEGER DEFAULT 0,
    gps_points_imported  INTEGER DEFAULT 0,
    activities_skipped   INTEGER DEFAULT 0,
    error_message        TEXT,
    duration_s           REAL
);

-- User-Verwaltung (v2)
CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,              -- bcrypt
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Garmin-Zugangsdaten pro User (v2 + v3-Erweiterung)
CREATE TABLE garmin_credentials (
    user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    token_json  TEXT NOT NULL,             -- Fernet-verschlüsselt {email, password}
    email_hash  TEXT,                       -- SHA-256 der normalisierten Email (UNIQUE)
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Einmal-Einladungscodes (v2)
CREATE TABLE invite_codes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL UNIQUE,
    created_by  INTEGER REFERENCES users(id),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    expires_at  TEXT NOT NULL,
    used_at     TEXT,
    used_by     INTEGER REFERENCES users(id)
);

-- Activity-Bewertungen (v4) – KEIN FK auf activities (überlebt DB-Reset)
CREATE TABLE activity_ratings (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    activity_id TEXT    NOT NULL,
    rating      INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (user_id, activity_id)
);
```

**Indizes:** `idx_gps_activity`, `idx_activities_date`, `idx_activities_type`, `idx_activities_user`, `idx_sync_log_started`, `idx_sync_log_user`, `idx_users_username`, `idx_invite_code`, `idx_garmin_email_hash` (UNIQUE), `idx_ratings_activity`.

### Schema-Migrationen

SQLite hat kein automatisches Migrations-System. `database.py → init()` legt v1-Tabellen idempotent via `CREATE TABLE IF NOT EXISTS` an. Nachträgliche Änderungen laufen in expliziten Migrationsmethoden:

| Migration | Wann | Was passiert |
|-----------|------|-------------|
| `_migrate_activities_user_id` | bei v1→v2 | `ALTER TABLE activities ADD COLUMN user_id` |
| `_migrate_nullable_coords` | bei v2→v3 | Tabelle neu erstellen (FK temporär OFF + Backup von gps_points) damit `NOT NULL` entfernt wird; sichert Daten ab |
| `_migrate_sync_log_user_id` | bei v2/v3→v4 | `ALTER TABLE sync_log ADD COLUMN user_id` + neuer Index |
| `_migrate_garmin_email_hash` | bei v2/v3→v4 | `ALTER TABLE garmin_credentials ADD COLUMN email_hash` |
| `backfill_garmin_email_hashes` (auth.py, beim Startup) | nach Migration | Hashes für bestehende Credentials berechnen, dann UNIQUE-Index anlegen. Bei bestehenden Duplikaten (altes Bug-Szenario) wird nur non-unique Index angelegt + Warnung geloggt |

Die `activity_ratings`-Tabelle (v4) wird in einem eigenen `CREATE TABLE IF NOT EXISTS`-Block nach den v1/v2-Tabellen angelegt – also idempotent bei jedem Start.

---

## File Layout on VM

```
/opt/trail-atlas/
├── backend/
│   ├── main.py              ← FastAPI Endpoints + Auth + Admin + Ratings
│   ├── auth.py              ← Session, Invite, Garmin-Credentials (Fernet)
│   ├── database.py          ← SQLite Wrapper + Migrationen (Schema v4)
│   └── requirements.txt     ← fastapi, uvicorn, bcrypt, itsdangerous, cryptography
├── garmin-sync/
│   └── garmin_sync.py       ← Multi-User Sync mit Fail-Tracking
└── venv/                    ← Python Virtual Environment

/var/lib/trail-atlas/
├── trail_atlas.db           ← SQLite Datenbank
├── trail_atlas.db-wal       ← WAL-Datei (automatisch)
├── trail_atlas.db-shm       ← Shared Memory (automatisch)
├── garmin_tokens/           ← Garmin Tokens PRO USER
│   ├── 1.json               ← Token für User-ID 1
│   ├── 2.json               ← Token für User-ID 2
│   └── _login_failures.json ← Persistenter Fail-Counter
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
                                SECRET_KEY (Pflicht), ADMIN_USER, ADMIN_PASS
                                SYNC_API_KEY (Pflicht)
                                GARMIN_EMAIL, GARMIN_PASSWORD (optional,
                                  nur für Admin-Migration beim ersten Start)

/etc/cron.d/
└── trail-atlas-garmin-sync  ← Cronjob Definition

/etc/nginx/sites-available/
└── trail-atlas              ← Nginx Server-Konfiguration

/etc/systemd/system/
├── trail-atlas.service      ← Backend systemd Unit
└── trail-atlas.service.d/
    └── override.conf        ← EnvironmentFile Override

/usr/local/bin/
└── trail-atlas              ← CLI-Helper (Symlink/Kopie von scripts/ops.sh)

/var/log/trail-atlas/
└── garmin_sync.log          ← Sync-Logs
```
