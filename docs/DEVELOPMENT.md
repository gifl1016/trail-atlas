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
| `garmin-sync/*` | Sync Deploy | Syntax-Check → Script kopieren → `--check` Smoke-Test (kein Garmin-Login) |
| `scripts/ops.sh` (oder beliebige scripts/) | CLI Deploy | Kopiert nach `/usr/local/bin/trail-atlas` |
| `scripts/deploy.sh` | Frontend Deploy | + CLI Deploy |
| `scripts/deploy_backend.sh` | Backend Deploy | + CLI Deploy |
| `scripts/deploy_garmin_sync.sh` | Sync Deploy | + CLI Deploy |

Manueller Trigger: GitHub → Actions → "Deploy Trail Atlas" → Run workflow → Target wählen.

---

## Frontend ändern

### Neue HTML-Version

1. HTML von Claude generieren lassen (immer die `_local` Variante mit SRI-Platzhaltern)
2. **Version hochzählen** (siehe Versionsschema unten)
3. In GitHub → `src/` → "Upload files"
4. Commit → Pipeline deployt automatisch
5. Browser: Hard-Reload (`Ctrl+Shift+R`) oder Incognito-Tab

### HTML-Konventionen

- Dateiname: `garmin_trail_atlas_vX.Y_api_local.html`
- SRI-Platzhalter müssen vorhanden sein: `LEAFLET_CSS_SRI`, `LEAFLET_JS_SRI`, `PAPAPARSE_SRI`
- CSP Meta-Tag mit `connect-src 'self'` (nicht `'none'`!)
- Versionsnummer sichtbar in Title, Splash, Import-Tab, Login-Screen, version-badge, `APP_VERSION`
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

SQLite hat kein automatisches Migrations-System. Neue Tabellen werden in `database.py` → `init()` via `CREATE TABLE IF NOT EXISTS` angelegt (idempotent beim nächsten Restart). Schema-Änderungen auf bestehenden Tabellen werden in eigenen Migrationsmethoden behandelt:

```python
def _migrate_<name>(self):
    cols = {row[1] for row in
        self._conn.execute("PRAGMA table_info(<table>)").fetchall()}
    if "<new_column>" not in cols:
        with self._lock:
            self._conn.execute("ALTER TABLE ... ADD COLUMN ...")
        log.info("Migration: ... added")
```

**Wichtige Regeln für Migrationen:**

- **Indizes** die auf neuen Spalten basieren werden NICHT im `CREATE TABLE`-Block des ursprünglichen Schemas angelegt – sonst schlägt der Start auf alten DBs fehl (Spalte existiert noch nicht). Stattdessen den Index in der Migrationsmethode anlegen, mit `IF NOT EXISTS` damit es auch auf frischer DB funktioniert (wo die Spalte direkt in der `CREATE TABLE` ist).
- **Bei `DROP TABLE` mit Foreign Keys:** `PRAGMA foreign_keys=OFF` davor + `ON` danach, sonst löschen CASCADE-FKs die Daten in referenzierenden Tabellen. Beispiel: `_migrate_nullable_coords` sichert `gps_points` zusätzlich in eine Temp-Tabelle ab.
- **Schema-Version-Comment** im Log-Output bei `init()` aktualisieren wenn eine neue Version etabliert ist.

Falls doch manuell nötig:

```bash
trail-atlas db shell
sqlite> CREATE TABLE IF NOT EXISTS neue_tabelle (...);
sqlite> .quit
```

### Backend-Tests

Die Test-Suites im `backend/`-Verzeichnis decken die wichtigsten Features ab:

| Test-Datei | Was getestet wird |
|-----------|------------------|
| `test_user_isolation.py` | Pro-User-Datenisolation (Activities, GPS, Sync) |
| `test_garmin_credentials.py` | Fernet-Verschlüsselung, save/get/decrypt |
| `test_admin_user_management.py` | Admin-Endpoints: list, delete, garmin set/remove |
| `test_nullable_coords.py` | Import von Activities ohne Koordinaten |
| `test_migration_sync_log.py` | Migration `sync_log.user_id` auf bestehender v1-DB |
| `test_garmin_unique.py` | Email-Hash UNIQUE-Constraint, Duplikat-Schutz |
| `test_garmin_backfill.py` | Backfill von `email_hash` auf bestehender DB |
| `test_activity_ratings.py` | Ratings setzen/ändern/entfernen, Überleben des Garmin-Resets |

Lokal ausführen:

```bash
cd backend && python3 test_user_isolation.py
# oder alle:
for t in test_*.py; do echo "=== $t ==="; python3 "$t" 2>&1 | tail -3; done
```

---

## Garmin Sync ändern

1. Änderungen an `garmin-sync/garmin_sync.py`
2. Push → Pipeline kopiert Script auf VM + `--check` Smoke-Test
3. Kein Service-Restart nötig (Cronjob lädt Script bei jedem Lauf neu)
4. Manueller Test: `trail-atlas sync 1`

**Hinweis:** Der `--check`-Modus prüft nur Config + API-Connectivity (kein Garmin-Login). Das ist gewollt, weil mit vielen Usern der Dry-Run sonst sehr lange dauert und Garmin-Logins kostet. Für echte Sync-Tests `--dry-run` oder ohne Flag verwenden.

---

## CLI ändern

`scripts/ops.sh` ist der `trail-atlas`-Befehl. Bei jeder Änderung daran (oder beliebig anderes unter `scripts/`) deployt der `deploy-ops-cli` Job das CLI nach `/usr/local/bin/trail-atlas`.

---

## Versionsschema

### Frontend
```
garmin_trail_atlas_vX.Y[_qualifier]_local.html

Beispiele:
  v2.6_local.html          ← IndexedDB-Version (alt)
  v3.3_api_local.html      ← mit Sync-Status
  v3.6_api_local.html      ← mit Invite + Signup
  v3.7_api_local.html      ← Multi-User komplett, Bewertungen, Admin-Panel
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
  GET /health → {"status":"ok","version":"1.8.0"}
  GET /api/docs → Swagger UI zeigt Version
```

Bei jeder Backend-Änderung die Version in `main.py` hochzählen:
- **Patch** (z.B. 1.8.0 → 1.8.1) für Bugfixes
- **Minor** (1.8.0 → 1.9.0) für neue Features
- **Major** (1.8.0 → 2.0.0) für Breaking Changes

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
curl -H "X-Sync-Key: $KEY" http://127.0.0.1:8000/db/stats   # DB-Inhalt
```

### Garmin Sync

```bash
trail-atlas logs sync         # Sync-Logs
trail-atlas sync 1            # Test mit 1 Tour pro User
curl -H "X-Sync-Key: $KEY" http://127.0.0.1:8000/sync/status   # Letzter Sync-Status

# Fail-Counter eines Users prüfen / zurücksetzen
sudo cat /var/lib/trail-atlas/garmin_tokens/_login_failures.json
sudo rm /var/lib/trail-atlas/garmin_tokens/_login_failures.json   # alle Counter zurücksetzen
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
