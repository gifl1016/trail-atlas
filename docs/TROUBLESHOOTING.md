# Troubleshooting

Alle Probleme die während der Entwicklung aufgetreten sind und ihre Lösungen.

---

## Frontend

### "L is not defined" beim Laden der App

**Symptom:** Fehlermeldung im Splash-Screen, Karte lädt nicht.
**Ursache:** Leaflet.js wurde nicht geladen. Häufigste Gründe:

1. **SRI-Hash stimmt nicht:** CDN liefert leicht andere Datei als beim Hash-Berechnen.
   → Fix: SRI-Hashes von den tatsächlich geladenen Dateien berechnen (das `deploy.sh` macht das automatisch bei lokalen Libraries).

2. **CSP blockiert externe Scripts:** `script-src` erlaubt die CDN-Domain nicht.
   → Fix: Libraries lokal hosten (aktueller Stand) oder CDN-Domain in CSP aufnehmen.

3. **Mixed Content:** App auf HTTP, Libraries auf HTTPS → Browser blockiert.
   → Fix: App immer über HTTPS ausliefern.

### "Failed to fetch" / "Server nicht erreichbar"

**Symptom:** App-Splash zeigt Verbindungsfehler, obwohl Backend läuft.
**Ursache:** CSP `connect-src 'none'` blockiert ALLE fetch()-Requests, auch zur eigenen Domain.
**Fix:** CSP ändern auf `connect-src 'self'`.

**Diagnose:** Browser DevTools → Console zeigt:
```
Refused to connect to 'https://trail-atlas.duckdns.org/api/...' 
because it violates the Content Security Policy directive: "connect-src 'none'"
```

### Browser zeigt alte Version

**Symptom:** Nach Deploy sieht die App noch aus wie vorher.
**Ursache:** Browser-Cache.
**Fix:**
- Hard-Reload: `Ctrl+Shift+R` (Desktop)
- Incognito-Tab öffnen
- Cache leeren in Browser-Einstellungen (Android)

**Prävention:** `Cache-Control: no-cache, must-revalidate` Header auf `index.html` (in Nginx Config gesetzt).

### Touren aus alter IndexedDB sichtbar trotz API-Umstellung

**Symptom:** Nach Wechsel von IndexedDB auf API-Version sind noch alte Daten sichtbar.
**Ursache:** IndexedDB-Daten persistieren im Browser unabhängig vom Server.
**Fix:** Browser-Daten löschen → Settings → Site Data → trail-atlas.duckdns.org löschen.

---

## Backend (API)

### DELETE gibt 405 Method Not Allowed

**Symptom:** `DELETE /api/db/reset` oder `DELETE /api/activities/{id}` → 405.
**Ursache:** FastAPI's `redirect_slashes=True` (Default) wandelt DELETE auf URLs mit Trailing-Slash in 307 Redirect um. Browser/Clients konvertieren dabei DELETE→GET → 405.
**Fix:** `redirect_slashes=False` in FastAPI-App.

### DELETE gibt 200 aber Daten sind noch da

**Symptom:** `DELETE /api/activities/{id}` antwortet 200, aber `GET /api/activities` zeigt den Eintrag noch.
**Ursache:** WAL-Race-Condition. Alte `database.py` öffnete pro Request eine neue SQLite-Connection. Neue Connections können im WAL-Modus kurzzeitig alte Snapshots sehen.
**Fix:** Persistente Single-Connection statt Connection-per-Request (aktueller Stand in `database.py`).

### GPS-Import erzeugt Duplikate

**Symptom:** Doppelter CSV-Import verdoppelt GPS-Punkte.
**Ursache:** GPS-Tabelle hat Auto-Increment-PK, kein Unique-Constraint.
**Fix:** `import_gps` Endpoint löscht vor dem Insert alle existierenden GPS-Punkte für die betroffenen Activity-IDs (idempotent). Response enthält `replaced`-Zähler.

### Service startet nicht nach Backend-Update

**Diagnose:**
```bash
sudo systemctl status trail-atlas
sudo journalctl -u trail-atlas -n 30
```

**Häufige Ursachen:**
- Syntax-Fehler in Python-Dateien → `python3 -c "import ast; ast.parse(open('main.py').read())"`
- Falsche Berechtigungen → `sudo chown trail-atlas:trail-atlas /opt/trail-atlas/backend/*.py`
- Fehlende Dependency → `sudo /opt/trail-atlas/venv/bin/pip install -r requirements.txt`
- Python-Version zu alt → Type Hints wie `dict | None` brauchen Python 3.10+

### sync_log Tabelle fehlt (500 bei POST /sync/log)

**Symptom:** Garmin-Sync postet Log, bekommt 500.
**Ursache:** Tabelle existiert nicht in bestehender DB (wurde nach Backend-Update nicht migriert).
**Fix:**
```bash
sudo -u trail-atlas sqlite3 /var/lib/trail-atlas/trail_atlas.db \
  "CREATE TABLE IF NOT EXISTS sync_log (id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, activities_imported INTEGER DEFAULT 0, gps_points_imported INTEGER DEFAULT 0, activities_skipped INTEGER DEFAULT 0, error_message TEXT, duration_s REAL);"
```

### API gibt 401 bei curl-Befehlen

**Symptom:** `curl http://127.0.0.1:8000/activities` → `{"detail":"Nicht eingeloggt"}`.
**Ursache:** Seit v1.5.0 brauchen alle Endpoints Authentifizierung (Session-Cookie oder Sync-API-Key).
**Fix:** Sync-API-Key als Header mitgeben:
```bash
curl -H "X-Sync-Key: DEIN_KEY" http://127.0.0.1:8000/activities
```
Oder den Key automatisch aus der env-Datei lesen:
```bash
curl -H "X-Sync-Key: $(sudo grep SYNC_API_KEY /etc/trail-atlas/garmin.env | cut -d= -f2)" \
  http://127.0.0.1:8000/activities
```

---

## Auth + Session

### SECRET_KEY / ADMIN_USER nicht gesetzt (Warning im Log)

**Symptom:** Log zeigt `SECRET_KEY nicht gesetzt – generierter temporärer Key` oder `ADMIN_USER/ADMIN_PASS nicht gesetzt`.
**Ursache:** Die Umgebungsvariablen aus `garmin.env` werden nicht vom systemd Service geladen.
**Fix:** EnvironmentFile in systemd einrichten:
```bash
sudo systemctl edit trail-atlas
```
Einfügen:
```ini
[Service]
EnvironmentFile=/etc/trail-atlas/garmin.env
```
Dann:
```bash
sudo systemctl daemon-reload
sudo systemctl restart trail-atlas
```
Prüfen ob es wirkt: `sudo systemctl show trail-atlas | grep EnvironmentFile`

### Permission denied beim manuellen Sync-Test

**Symptom:** `sudo -u trail-atlas python3 ~/trail-atlas/garmin-sync/garmin_sync.py` → `Permission denied`.
**Ursache:** `~` expandiert zum Home des aufrufenden Users (`/home/ubuntu`), aber der `trail-atlas` Systemuser hat dort keine Leserechte.
**Fix:** Den vollen Deploy-Pfad verwenden:
```bash
sudo -u trail-atlas /opt/trail-atlas/venv/bin/python3 \
  /opt/trail-atlas/garmin-sync/garmin_sync.py --dry-run
```

---

## Garmin Sync

### "Permission denied: garmin.env"

**Symptom:** Sync-Script crasht mit `PermissionError: [Errno 13] Permission denied: '/etc/trail-atlas/garmin.env'`.
**Ursache:** `/etc/trail-atlas/` gehört root, nicht trail-atlas.
**Fix:**
```bash
sudo chown -R trail-atlas:trail-atlas /etc/trail-atlas
sudo chmod 750 /etc/trail-atlas
sudo chmod 600 /etc/trail-atlas/garmin.env
```

### "NoneType has no attribute 'get'" beim GPS-Fetch

**Symptom:** Script crasht bei bestimmten Aktivitäten.
**Ursache:** Garmin API liefert `None` statt Dict für Aktivitäten ohne GPS-Details (z.B. Krafttraining).
**Fix:** Defensives None-Handling in `fetch_gps_polyline()` – alle `.get()`-Chains prüfen vorher auf `None` und `isinstance(dict)`.

### Login schlägt fehl / MFA-Problem

**Symptom:** `GarminConnectAuthenticationError`.
**Ursache:** Garmin verlangt manchmal MFA-Bestätigung bei Login von neuer IP.
**Fix:**
1. Token löschen: `sudo rm /var/lib/trail-atlas/garmin_token.json`
2. Im Browser auf garmin.com einloggen (von derselben IP oder die VM-IP whitelisten)
3. Erneut versuchen: `trail-atlas sync 1`

### Sync gibt 401/403 nach Auth-Umstellung

**Symptom:** Sync-Log zeigt `401 Unauthorized` oder `403 Forbidden`.
**Ursache:** `garmin_sync.py` nutzt noch Basic Auth (`API_USER`/`API_PASS`) statt den neuen `SYNC_API_KEY`.
**Fix:**
1. Aktuelles `garmin_sync.py` deployen (nutzt `X-Sync-Key` Header)
2. `SYNC_API_KEY` in `/etc/trail-atlas/garmin.env` setzen
3. Test: `trail-atlas sync dry`

---

## Deployment (GitHub Actions)

### Pipeline erkennt falsche HTML-Datei

**Symptom:** Alte HTML-Version wird deployed statt der neuen.
**Ursache:** `ls -t` sortiert nach Datei-Mtime, aber Git-Checkout normalisiert Mtimes.
**Fix:** Pipeline nutzt `git log --diff-filter=AM` um die zuletzt in Git geänderte Datei zu finden (nicht Filesystem-Mtime).

### Backend-Deploy bricht ab bei "DB-Backup…"

**Symptom:** Script endet nach "💾 DB-Backup…" ohne Fehlertext.
**Ursache:** `du -sh` auf Backup-Datei schlägt fehl weil der deploy-User keine Leserechte auf `/var/lib/trail-atlas/backups/` hat. `set -euo pipefail` bricht bei leerem Output ab.
**Fix:** `sudo du -sh ...` statt `du -sh ...`, plus `|| echo "?"` als Fallback.

---

## Berechtigungen (Permission Denied)

### Shell-Redirection `>>` vs sudo

**Symptom:** `sudo -u trail-atlas python3 script.py >> /var/log/.../log` → Permission denied.
**Ursache:** Die `>>` Redirection wird vom **aktuellen Shell-Prozess** (dein User) ausgeführt, nicht vom `sudo`-User.
**Fix:**
```bash
# Richtig:
sudo -u trail-atlas bash -c 'python3 script.py >> /var/log/.../log 2>&1'

# Oder:
sudo -u trail-atlas python3 script.py 2>&1 | sudo tee -a /var/log/.../log
```

Im Cronjob ist das kein Problem weil cron den gesamten Befehl als der angegebene User ausführt.

### sqlite3 CLI nicht installiert

**Symptom:** `sqlite3: command not found`.
**Ursache:** Python's `import sqlite3` installiert nicht das CLI-Tool.
**Fix:** `sudo apt install -y sqlite3`

### Sudoers Wildcards matchen nicht

**Symptom:** `sudo -u trail-atlas mkdir -p /var/lib/...` fragt nach Passwort.
**Ursache:** Sudoers-Wildcard `*` matcht aus Sicherheitsgründen nicht Argumente mit `-` oder `/` Prefix.
**Fix:** Explizite Regeln statt Wildcards:
```
ubuntu ALL=(trail-atlas) NOPASSWD: /usr/bin/mkdir -p /var/lib/trail-atlas/backups
```

---

## Quick-Diagnose Cheat Sheet

```bash
# Ist alles am Laufen?
trail-atlas status

# Backend-Fehler finden
trail-atlas logs

# Sync-Fehler finden
trail-atlas logs sync

# API erreichbar?
curl http://127.0.0.1:8000/health

# Nginx Config OK?
sudo nginx -t

# DB-Inhalt prüfen
trail-atlas db stats

# Berechtigungen prüfen
sudo ls -la /opt/trail-atlas/backend/
sudo ls -la /var/lib/trail-atlas/
sudo ls -la /etc/trail-atlas/

# EnvironmentFile geladen?
sudo systemctl show trail-atlas | grep EnvironmentFile
```
