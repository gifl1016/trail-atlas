# Development Workflow

Wie Änderungen in die Produktion kommen.

---

## CI/CD Pipeline

```
Push auf main → GitHub Actions → automatisches Deployment
```

Der Workflow erkennt automatisch welche Dateien sich geändert haben und startet nur die relevanten Jobs:

| Geänderter Pfad | Triggered Job | Was passiert |
|-----------------|---------------|-------------|
| `src/*.html` | Frontend Deploy | HTML patchen (SRI-Hashes) → Nginx |
| `backend/*.py` | Backend Deploy | Syntax-Check → DB-Backup → Code kopieren → pip install → Service-Restart → Health-Check → bei Fehler: Rollback |
| `garmin-sync/*` | Sync Deploy | Syntax-Check → Script kopieren → Dry-Run |
| `scripts/*` | Alle drei | Alle Komponenten werden neu deployed |

Manueller Trigger: GitHub → Actions → "Deploy Trail Atlas" → Run workflow → Target wählen.

---

## Frontend ändern

### Neue HTML-Version

1. HTML von Claude generieren lassen (immer die `_local` Variante mit SRI-Platzhaltern)
2. In GitHub → `src/` → "Upload files"
3. Commit → Pipeline deployt automatisch
4. Browser: Hard-Reload (`Ctrl+Shift+R`) oder Incognito-Tab

### HTML-Konventionen

- Dateiname: `garmin_trail_atlas_vX.Y_api_local.html`
- SRI-Platzhalter müssen vorhanden sein: `LEAFLET_CSS_SRI`, `LEAFLET_JS_SRI`, `PAPAPARSE_SRI`
- CSP Meta-Tag mit `connect-src 'self'` (nicht `'none'`!)
- Versionsnummer sichtbar in Title, Splash, Import-Tab
- Das Deploy-Script wählt die zuletzt in Git geänderte Datei

---

## Backend ändern

### Python-Code

1. Änderungen an `backend/main.py`, `backend/auth.py` oder `backend/database.py`
2. Push → Pipeline macht automatisch:
   - Python-Syntax-Check
   - DB-Backup mit Timestamp
   - Code-Backup für Rollback
   - `pip install -r requirements.txt`
   - Files kopieren + Service-Restart
   - Health-Check
   - Bei Fehler: alte Files restoren, Service neustart
3. API-Version prüfen: `curl http://127.0.0.1:8000/health`

### Neue Abhängigkeit

1. `backend/requirements.txt` ergänzen
2. Push → Pipeline installiert automatisch via `pip install -r requirements.txt`

### Schema-Migration

SQLite hat kein automatisches Migrations-System. Neue Tabellen werden in `database.py` → `init()` via `CREATE TABLE IF NOT EXISTS` angelegt (idempotent beim nächsten Restart). Neue Spalten auf bestehenden Tabellen werden in eigenen Migrationsmethoden behandelt (z.B. `_migrate_activities_user_id()` prüft per `PRAGMA table_info` ob die Spalte existiert und fügt sie ggf. hinzu).

Falls doch manuell nötig:

```bash
trail-atlas db shell
sqlite> CREATE TABLE IF NOT EXISTS neue_tabelle (...);
sqlite> .quit
```

---

## Garmin Sync ändern

1. Änderungen an `garmin-sync/garmin_sync.py`
2. Push → Pipeline kopiert Script auf VM + Dry-Run
3. Kein Service-Restart nötig (Cronjob lädt Script bei jedem Lauf neu)
4. Manueller Test: `trail-atlas sync 1`

---

## Versionsschema

### Frontend
```
garmin_trail_atlas_vX.Y[_qualifier]_local.html

Beispiele:
  v2.6_local.html          ← IndexedDB-Version (alt)
  v3.3_api_local.html      ← mit Sync-Status
  v3.6_api_local.html      ← mit Invite + Signup
  v3.7_api_local.html      ← Multi-User, Admin-Panel, harmonisierte Typen
```

Major: Architektur-Wechsel (v2→v3 war IndexedDB→API)
Minor: Neue Features, Bugfixes
Qualifier: `_api` (API-basiert), `_local` (lokale Libraries, SRI-Platzhalter)

**Wichtig:** Bei jeder Frontend-Änderung die Version in `<title>`, `APP_VERSION`,
Login-Screen, version-badge und Dateiname hochzählen. Die GitHub Action deployed
die neueste HTML-Datei basierend auf dem Git-Log.

### Backend
```
version in main.py → app = FastAPI(version="X.Y.Z")

Sichtbar unter:
  GET /health → {"status":"ok","version":"1.7.0"}
  GET /api/docs → Swagger UI zeigt Version
```

Bei jeder Backend-Änderung die Version in `main.py` hochzählen (Patch für Bugfixes,
Minor für Features, Major für Breaking Changes).

---

## Debugging

### Frontend

- Browser DevTools → Console: JavaScript-Fehler
- Browser DevTools → Network: API-Calls und Responses
- Browser-Tab-Titel zeigt aktuelle Version
- `console.log("Trail Atlas " + APP_VERSION)` beim App-Start

### Backend

```bash
trail-atlas logs              # Live Backend-Logs
trail-atlas health            # Quick Health-Check
curl http://127.0.0.1:8000/db/stats   # DB-Inhalt
```

### Garmin Sync

```bash
trail-atlas logs sync         # Sync-Logs
trail-atlas sync dry          # Dry-Run (kein Schreibzugriff)
trail-atlas sync 1            # Test mit 1 Tour
curl http://127.0.0.1:8000/sync/status   # Letzter Sync-Status
```

---

## Rollback

### Frontend

Die letzte Datei in `src/` manuell auf eine ältere Version überschreiben → Push → deployed.

### Backend

Automatisch bei fehlgeschlagenem Deploy (Health-Check).
Manuell:
```bash
sudo ls /var/lib/trail-atlas/backups/code_*/
sudo cp /var/lib/trail-atlas/backups/code_TIMESTAMP/*.py /opt/trail-atlas/backend/
sudo chown -R trail-atlas:trail-atlas /opt/trail-atlas/backend
sudo systemctl restart trail-atlas
```

### Datenbank

```bash
# Verfügbare Backups
trail-atlas db backups

# Restore
sudo systemctl stop trail-atlas
sudo cp /var/lib/trail-atlas/backups/trail_atlas_TIMESTAMP.db /var/lib/trail-atlas/trail_atlas.db
sudo chown trail-atlas:trail-atlas /var/lib/trail-atlas/trail_atlas.db
sudo systemctl start trail-atlas
```
