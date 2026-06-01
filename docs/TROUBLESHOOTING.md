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

### Browser zeigt alte Version

**Symptom:** Nach Deploy sieht die App noch aus wie vorher.
**Ursache:** Browser-Cache.
**Fix:** Hard-Reload `Ctrl+Shift+R` (Desktop), oder Incognito-Tab. **Prävention:** `Cache-Control: no-cache, must-revalidate` Header auf `index.html` (in Nginx Config gesetzt).

### Touren aus alter IndexedDB sichtbar trotz API-Umstellung

**Symptom:** Nach Wechsel von IndexedDB auf API-Version sind noch alte Daten sichtbar.
**Ursache:** IndexedDB-Daten persistieren im Browser unabhängig vom Server.
**Fix:** Browser-Daten löschen → Settings → Site Data → trail-atlas.duckdns.org löschen.

### Aktivitätenliste zeigt "Wandern" statt echtem Garmin-Typ

**Symptom:** Indoor-Cycling wird als "Radfahren" angezeigt, treadmill_running als "Laufen".
**Ursache (kein Bug, gewollt):** Backend-Sync mappt Garmin-Varianten auf einheitliche DB-Typen (`treadmill_running` → `running`). Frontend zeigt diesen DB-Wert. Die noch gröbere Gruppierung (Wandern+Gehen, Schwimmen+Wasser, Kraft+Cardio) gibt es nur in der **Karten-Legende und im Filter-Dropdown** – nicht in Liste oder Detail.
**Mehr Info:** siehe [`ARCHITECTURE.md`](ARCHITECTURE.md) → "Warum zweistufiges Activity-Type-Mapping?"

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
- Fehlende Dependency → `sudo /opt/trail-atlas/venv/bin/pip install -r requirements.txt`. Insbesondere `cryptography` (für Fernet) seit Backend 1.7.0.
- Python-Version zu alt → Type Hints wie `dict | None` brauchen Python 3.10+

### Schema-Migration scheitert / Backend startet nicht beim ersten Start nach Update

**Symptom:** Log zeigt "no such column: ..." oder "table X has no column Y" beim Backend-Start.
**Ursache:** Migration läuft erst _nach_ der initialen `CREATE TABLE`-Phase. Wenn ein Index auf einer neuen Spalte schon im initialen Block angelegt wird, schlägt der Start auf einer alten DB fehl.
**Fix:** Indizes auf migrierten Spalten gehören in die `_migrate_*`-Methode, nicht in den initialen `CREATE TABLE`-Block. Beispiel: `idx_sync_log_user` wird in `_migrate_sync_log_user_id` angelegt, nicht in der `CREATE TABLE sync_log` Definition.

### Migration löscht versehentlich Daten (FK-Cascade beim Table-Rebuild)

**Symptom:** Nach einer Migration die eine Tabelle neu erstellt (z.B. `_migrate_nullable_coords`), sind plötzlich GPS-Punkte oder andere abhängige Daten weg.
**Ursache:** Bei `DROP TABLE activities` triggert `ON DELETE CASCADE` auf `gps_points` und löscht alle GPS-Punkte. SQLite hat `foreign_keys=ON` per Default in unserer Connection.
**Fix:** In jeder Migration die `DROP TABLE` macht:
```python
with self._lock:
    self._conn.execute("PRAGMA foreign_keys=OFF")
    # Optional: gps_points in temporäre Tabelle sichern
    self._conn.executescript("""
        BEGIN;
        CREATE TEMP TABLE _gps_backup AS SELECT * FROM gps_points;
        DROP TABLE activities;
        CREATE TABLE activities (...);  -- neue Definition
        -- ... data migration ...
        INSERT INTO gps_points SELECT * FROM _gps_backup;
        DROP TABLE _gps_backup;
        COMMIT;
    """)
    self._conn.execute("PRAGMA foreign_keys=ON")
```

> **Historisch:** Bei einem frühen Deploy ging genau das schief und der Admin-User verlor alle GPS-Punkte. Garmin-Resync stellte sie wieder her, aber der Verlust war vermeidbar.

### `sqlite3.IntegrityError: FOREIGN KEY constraint failed` beim User-Löschen

**Symptom:** `DELETE /admin/users/{id}` → 500, Log zeigt FK-Constraint Verletzung.
**Ursache:** `invite_codes.created_by` und `invite_codes.used_by` haben FKs auf `users(id)` ohne `ON DELETE SET NULL` oder CASCADE. SQLite verhindert das User-Löschen wenn er noch in `invite_codes` referenziert wird.
**Fix:** `delete_user` in `auth.py` muss vorher die Referenzen auf NULL setzen:
```python
db.execute("UPDATE invite_codes SET created_by = NULL WHERE created_by = ?", (user_id,))
db.execute("UPDATE invite_codes SET used_by = NULL WHERE used_by = ?", (user_id,))
```

### "Dieser Garmin-Account ist bereits verknüpft" (409 beim Signup oder Admin-Set)

**Symptom:** Signup mit Garmin-Credentials oder Admin-Setzen schlägt mit 409 fehl.
**Ursache:** Der Garmin-Account ist bereits einem anderen Trail-Atlas-User zugeordnet. UNIQUE-Index auf `garmin_credentials.email_hash` verhindert Duplikate.
**Fix:** Beim anderen User die Garmin-Verknüpfung entfernen (Admin-Tab → "Garmin entfernen"), dann den Account beim gewünschten User neu setzen. **Achtung:** Wenn beide User bereits Activities mit denselben `activity_id`s haben (altes Bug-Szenario), kann ein Reset des Empfänger-Users sinnvoll sein. Siehe [`OPERATIONS.md`](OPERATIONS.md) → "Alle Daten löschen und neu syncen".

### API gibt 401 bei curl-Befehlen

**Symptom:** `curl http://127.0.0.1:8000/activities` → `{"detail":"Nicht eingeloggt"}`.
**Ursache:** Seit v1.5.0 brauchen alle Endpoints Authentifizierung.
**Fix:** Sync-API-Key als Header mitgeben:
```bash
curl -H "X-Sync-Key: $(sudo grep SYNC_API_KEY /etc/trail-atlas/garmin.env | cut -d= -f2)" \
  http://127.0.0.1:8000/activities
```
**Achtung:** Admin-Endpoints (`/admin/users/*`) akzeptieren keinen Sync-Key, sondern brauchen einen echten Admin-Session-Cookie.

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

### Garmin-Credentials lassen sich nicht entschlüsseln nach SECRET_KEY-Wechsel

**Symptom:** Sync-Script wirft `cryptography.fernet.InvalidToken`. App-Login funktioniert noch.
**Ursache:** `SECRET_KEY` wurde geändert. App-Sessions (signiert mit altem Key) sind invalidiert (User müssen sich neu einloggen) – das ist OK. Aber die Garmin-Credentials in der DB wurden mit dem alten Key Fernet-verschlüsselt und können nicht mehr entschlüsselt werden.
**Fix:** Alten `SECRET_KEY` wiederherstellen, oder alle User müssen ihre Garmin-Credentials in der App neu eintragen. Siehe [`SECURITY.md`](SECURITY.md) → "Garmin-Credential-Verschlüsselung".

---

## Garmin Sync

### Script hängt bei "Portal login: waiting Xs to avoid Cloudflare rate limiting..."

**Symptom:** Sync-Script bleibt minutenlang in der Login-Schleife stecken, keine Reaktion.
**Ursache:** Die `garminconnect`-Library hat interne Cloudflare-Retries die bei falschen Credentials endlos laufen.
**Fix:** Login läuft in einem separaten Thread mit 30s Timeout (`LOGIN_TIMEOUT_S` in `garmin_sync.py`). Beim Timeout wird der User als fehlgeschlagen markiert und der nächste User versucht.

### "RuntimeError: No active exception to reraise"

**Symptom:** Script crasht mit diesem Fehler nach einem Garmin-Login-Fehler.
**Ursache:** Bare `raise` (ohne Argument) funktioniert nur in einem `except`-Block. Wenn die Exception in einem separaten Thread gefangen und gespeichert wurde, ist sie im aktuellen Stack nicht "aktiv".
**Fix:** Statt `raise` → `raise err` mit der gespeicherten Exception verwenden. Aktuelle `garmin_sync.py` macht das korrekt.

### Garmin-Account gesperrt nach mehreren Sync-Versuchen

**Symptom:** User-Login bei Garmin schlägt fehl, Garmin schreibt "Konto vorübergehend gesperrt".
**Ursache:** Garmin sperrt Accounts temporär (24h) nach mehreren fehlgeschlagenen Login-Versuchen.
**Schutz:** Trail Atlas Sync-Script trackt Fehlversuche in `/var/lib/trail-atlas/garmin_tokens/_login_failures.json`. Nach 2 fehlgeschlagenen Logins wird der User komplett übersprungen. Damit bleiben die echten Login-Versuche weit unter dem Garmin-Lockout.

**Recovery:**
1. 24h warten oder Garmin-Passwort über die Garmin-Webseite zurücksetzen
2. Im Trail-Atlas Admin-Tab Garmin-Credentials des Users aktualisieren
3. Beim nächsten Sync wird der Failure-Counter automatisch zurückgesetzt (`updated_at` der Credentials > `last_fail`)

### Login schlägt fehl trotz korrektem Passwort

**Symptom:** `GarminConnectAuthenticationError` obwohl Credentials in der Garmin-Webseite funktionieren.
**Ursachen + Fixes:**
1. **Token kaputt:** Token-Datei löschen: `sudo rm /var/lib/trail-atlas/garmin_tokens/{user_id}.json`
2. **Garmin MFA aktiviert:** garminconnect-Library unterstützt MFA nicht zuverlässig. MFA in Garmin Connect deaktivieren.
3. **Garmin verlangt CAPTCHA:** Manuell auf garmin.com einloggen (von einer IP nahe der VM), Captcha lösen, dann erneut syncen.

### "NoneType has no attribute 'get'" beim GPS-Fetch

**Symptom:** Script crasht bei bestimmten Aktivitäten.
**Ursache:** Garmin API liefert `None` statt Dict für Aktivitäten ohne GPS-Details (z.B. Krafttraining).
**Fix:** Defensives None-Handling in `fetch_gps_polyline()`. Best-effort GPS-Fetch: Activities werden auch importiert wenn GPS fehlschlägt.

### "Permission denied: garmin.env"

**Symptom:** Sync-Script crasht mit `PermissionError: [Errno 13] Permission denied: '/etc/trail-atlas/garmin.env'`.
**Ursache:** `/etc/trail-atlas/` gehört root, nicht trail-atlas.
**Fix:**
```bash
sudo chown -R trail-atlas:trail-atlas /etc/trail-atlas
sudo chmod 750 /etc/trail-atlas
sudo chmod 600 /etc/trail-atlas/garmin.env
```

### Sync gibt 401/403 nach Auth-Umstellung

**Symptom:** Sync-Log zeigt `401 Unauthorized` oder `403 Forbidden`.
**Ursache:** Altes `garmin_sync.py` nutzt noch Basic Auth statt `SYNC_API_KEY`.
**Fix:**
1. Aktuelles `garmin_sync.py` deployen (nutzt `X-Sync-Key` Header)
2. `SYNC_API_KEY` in `/etc/trail-atlas/garmin.env` setzen
3. Test: `python3 garmin_sync.py --check`

---

## Bewertungen

### Bewertung verschwindet nach "Alle Daten zurücksetzen"

**Symptom:** Nach `DELETE /db/reset` sind Bewertungen weg.
**Erwartet:** Ja, das ist korrekt – `/db/reset` löscht alles inkl. Ratings.
**Stattdessen:** Wenn die Bewertungen erhalten bleiben sollen, "Alle Garmin-Daten zurücksetzen" verwenden (`DELETE /db/garmin`). Das löscht Activities + GPS, behält aber Bewertungen.

### Bewertung wird nicht gespeichert (401 Unauthorized)

**Symptom:** Klick auf Stern → kein Update, in der Console: 401.
**Ursache:** Bewertungen erfordern einen eingeloggten User. Wenn die Session abgelaufen ist (>30 Tage), gibt es 401.
**Fix:** Logout + neuer Login.

---

## CLI / Status

### `trail-atlas status` zeigt "DB-Datei nicht gefunden" obwohl DB existiert

**Symptom:** Der Status-Befehl behauptet die DB sei weg, obwohl `sudo ls /var/lib/trail-atlas/` sie zeigt.
**Ursache:** Die DB-Datei gehört `trail-atlas` und hat Mode 600. Der CLI-Check `[ -f $DB_FILE ]` läuft als der aufrufende User (`ubuntu`) und sieht die Datei nicht.
**Fix:** Aktuelles `scripts/ops.sh` deployen – nutzt `sudo test -f` statt `[ -f ]`. Auch der Backup-Check (`sudo test -d`) und sync-Status-Lookup haben dieses Pattern.

### `trail-atlas` Befehl nach Update der `ops.sh` nicht aktualisiert

**Symptom:** Änderungen an `scripts/ops.sh` im Repo sind nicht in `/usr/local/bin/trail-atlas` sichtbar.
**Ursache:** Vor dem Fix der Pipeline triggerte nur eine begrenzte Pfad-Liste den CLI-Deploy.
**Fix:** Aktuelle `deploy.yml` deployt CLI bei _jeder_ Änderung unter `scripts/`. Falls trotzdem manuell:
```bash
sudo cp ~/trail-atlas/scripts/ops.sh /usr/local/bin/trail-atlas
sudo chmod +x /usr/local/bin/trail-atlas
```

---

## Deployment (GitHub Actions)

### Pipeline erkennt falsche HTML-Datei

**Symptom:** Alte HTML-Version wird deployed statt der neuen.
**Ursache:** `ls -t` sortiert nach Datei-Mtime, aber Git-Checkout normalisiert Mtimes.
**Fix:** Pipeline nutzt `git log --diff-filter=AM` um die zuletzt in Git geänderte Datei zu finden (nicht Filesystem-Mtime).

### Backend-Deploy bricht ab bei "DB-Backup…"

**Symptom:** Script endet nach "💾 DB-Backup…" ohne Fehlertext.
**Ursache:** `du -sh` auf Backup-Datei schlägt fehl weil der deploy-User keine Leserechte hat. `set -euo pipefail` bricht bei leerem Output ab.
**Fix:** `sudo du -sh ...` statt `du -sh ...`, plus `|| echo "?"` als Fallback.

### Garmin-Sync Smoke-Test im Deploy dauert ewig (mit vielen Usern)

**Symptom:** `deploy_garmin_sync.sh` läuft mehrere Minuten weil der Dry-Run sich bei jedem User einloggt.
**Fix:** Aktueller Stand nutzt `--check` statt `--dry-run --limit 1`. Der Check prüft nur Config + API + Decryption – keine Garmin-Logins. Läuft in ~1 Sekunde unabhängig von der User-Anzahl.

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
**Fix:** Explizite Regeln statt Wildcards.

---

## Quick-Diagnose Cheat Sheet

```bash
# Ist alles am Laufen?
trail-atlas status

# Backend-Fehler finden
trail-atlas logs

# Sync-Fehler finden
trail-atlas logs sync

# Garmin Fail-Counter anschauen
sudo cat /var/lib/trail-atlas/garmin_tokens/_login_failures.json

# API erreichbar?
curl http://127.0.0.1:8000/health

# Nginx Config OK?
sudo nginx -t

# DB-Inhalt prüfen
trail-atlas db stats

# Berechtigungen prüfen
sudo ls -la /opt/trail-atlas/backend/
sudo ls -la /var/lib/trail-atlas/
sudo ls -la /var/lib/trail-atlas/garmin_tokens/
sudo ls -la /etc/trail-atlas/

# EnvironmentFile geladen?
sudo systemctl show trail-atlas | grep EnvironmentFile

# Schema-Version prüfen
sudo -u trail-atlas sqlite3 /var/lib/trail-atlas/trail_atlas.db ".schema activity_ratings"
# → wenn leer: noch nicht auf Schema v4
```
