# Operations

Tägliche Befehle und Wartung. Alle über den `trail-atlas` Helper:

```bash
sudo cp scripts/ops.sh /usr/local/bin/trail-atlas
sudo chmod +x /usr/local/bin/trail-atlas
```

(Wird auch automatisch vom GitHub-Actions `deploy-ops-cli` Job aktualisiert.)

---

## Quick Reference

```
trail-atlas status          Systemstatus auf einen Blick
trail-atlas health          API + HTTPS Health-Check
trail-atlas logs            Backend-Logs (live, Ctrl+C)
trail-atlas logs sync       Garmin-Sync-Logs (live)
trail-atlas logs nginx      Nginx Access-Log (live)
trail-atlas sync            Alle neuen Touren aller User syncen
trail-atlas sync 1          Test-Sync: max. 1 Tour pro User
trail-atlas sync dry        Dry-Run (kein Schreibzugriff in DB)
trail-atlas sync full       Re-Sync aller Touren
trail-atlas restart         Backend-Service neustart
trail-atlas db stats        DB-Statistik (JSON)
trail-atlas db backup       Manuelles DB-Backup
trail-atlas db backups      Vorhandene Backups auflisten
trail-atlas db shell        SQLite-Shell öffnen
trail-atlas help            Alle Befehle
```

---

## Typische Szenarien

### "Ist alles OK?"

```bash
trail-atlas status
```

Zeigt: Service-Status (Backend, Nginx, Cron), API-Version, DB-Statistik, letzter Sync, Backup-Anzahl.

### "Neue Garmin-Tour erscheint nicht"

```bash
# 1. Hat der Sync gelaufen?
trail-atlas logs sync

# 2. Manuell syncen
trail-atlas sync 1

# 3. DB prüfen
trail-atlas db stats

# 4. Browser: Hard-Reload (Ctrl+Shift+R)
```

### "App lädt nicht"

```bash
# 1. Backend läuft?
trail-atlas health

# 2. Nginx OK?
sudo nginx -t

# 3. Logs ansehen
trail-atlas logs
```

### "Etwas ist kaputt nach einem Update"

```bash
# Backend-Rollback (automatisch bei GitHub Actions Deploy-Fehler)
# Manueller Rollback:
trail-atlas db backups
# → Datum des letzten guten Backups notieren

sudo systemctl stop trail-atlas
sudo cp /var/lib/trail-atlas/backups/trail_atlas_TIMESTAMP.db /var/lib/trail-atlas/trail_atlas.db
sudo chown trail-atlas:trail-atlas /var/lib/trail-atlas/trail_atlas.db
sudo systemctl start trail-atlas
```

### "DB-Backup erstellen"

```bash
trail-atlas db backup
# → /var/lib/trail-atlas/backups/trail_atlas_20260510_manual.db
```

### "Wie viele Touren hab ich?"

```bash
trail-atlas db stats
# Oder direkt über die API (mit Sync-Key):
curl -H "X-Sync-Key: $(sudo grep SYNC_API_KEY /etc/trail-atlas/garmin.env | cut -d= -f2)" \
  http://127.0.0.1:8000/db/stats | python3 -m json.tool
```

> **Hinweis:** API-Endpoints benötigen Authentifizierung. Direkte `curl`-Befehle gegen die API funktionieren nur wenn (a) noch kein User in der DB ist, oder (b) der `SYNC_API_KEY` als Header mitgegeben wird. Der `trail-atlas` CLI-Helper nutzt den Key automatisch über die Umgebung.

### "Alle Daten löschen und neu syncen"

Drei verschiedene Reset-Optionen, je nach Zweck:

```bash
KEY=$(sudo grep SYNC_API_KEY /etc/trail-atlas/garmin.env | cut -d= -f2)

# 1. Backup machen
trail-atlas db backup

# 2a. Nur GPS-Punkte löschen (Activities + Bewertungen bleiben)
curl -H "X-Sync-Key: $KEY" -X DELETE http://127.0.0.1:8000/db/gps

# 2b. Alle Garmin-Daten löschen (Activities + GPS), Bewertungen BLEIBEN
curl -H "X-Sync-Key: $KEY" -X DELETE http://127.0.0.1:8000/db/garmin

# 2c. Alles löschen, inkl. Bewertungen
curl -H "X-Sync-Key: $KEY" -X DELETE http://127.0.0.1:8000/db/reset

# 3. Full Resync (Token wird invalidiert, alle User neu syncen)
trail-atlas sync full
```

Im Frontend (Import-Tab → Datenbank-Box) gibt es dieselben drei Buttons. Für **alle Reset-Varianten gilt:** sie wirken nur auf die Daten des eingeloggten Users (Pro-User-Isolation).

### "Neuen User einladen"

```bash
# 1. Im Browser: einloggen → Import-Tab → "Einladungscode generieren"
# 2. Link kopieren und an den Freund schicken
# 3. Freund öffnet den Link → Signup-Formular → fertig
#    Optional gibt der Freund direkt seine Garmin-Credentials an
#    (ausklappbare Sektion "▶ Garmin Connect verknüpfen")
```

### "Garmin-Verknüpfung eines Users ändern"

Per Admin-UI (vierter Tab in der App, nur sichtbar für Admin-User):

- "Garmin verknüpfen" / "Garmin ändern" → Email + Passwort eingeben → Speichern
- "Garmin entfernen" → Verknüpfung löschen (Activities bleiben in DB)

Die Eindeutigkeit ist sichergestellt: derselbe Garmin-Account kann nicht zwei Trail-Atlas-Usern zugewiesen werden. Versuch lehnt mit 409 ab.

Per API:

```bash
# Auth: als Admin-User einloggen und Session-Cookie verwenden (nicht Sync-Key,
# der reicht für Admin-Endpoints nicht)

# Setzen:
curl -X POST -H "Content-Type: application/json" \
  -d '{"email":"...", "password":"..."}' \
  --cookie "trail_atlas_session=$COOKIE" \
  http://127.0.0.1:8000/admin/users/2/garmin

# Entfernen:
curl -X DELETE --cookie "trail_atlas_session=$COOKIE" \
  http://127.0.0.1:8000/admin/users/2/garmin
```

### "User löschen"

Per Admin-UI: Tab "Admin" → User-Karte → "User löschen" → Bestätigung. Das löscht kaskadiert alle Activities, GPS-Punkte, Garmin-Credentials, Sync-Logs und Bewertungen des Users.

Self-Protection: Admins können sich nicht selbst löschen. Der letzte Admin kann nicht gelöscht werden (Last-Admin-Protection).

### "Garmin-Account ist nach Sync-Versuchen gesperrt / Fail-Counter zurücksetzen"

Wenn ein User wegen mehrfach falscher Garmin-Credentials geblockt ist, hat das Sync-Script ihn nach 2 Fehlversuchen übersprungen (Schutz vor Garmin-Account-Sperre).

**Reset-Optionen:**

1. **Empfohlen: Credentials in der App aktualisieren.** Admin-Tab → User → "Garmin ändern" mit korrektem Passwort. Beim nächsten Sync wird der Counter automatisch zurückgesetzt (das Script vergleicht `updated_at` der Credentials mit `last_fail`).

2. **Manuell: Fail-Counter komplett löschen.**
   ```bash
   # Bestimmten User:
   sudo -u trail-atlas python3 -c "
   import json, pathlib
   f = pathlib.Path('/var/lib/trail-atlas/garmin_tokens/_login_failures.json')
   d = json.loads(f.read_text()) if f.exists() else {}
   d.pop('6', None)  # User-ID
   f.write_text(json.dumps(d, indent=2))
   "

   # Oder alle:
   sudo rm /var/lib/trail-atlas/garmin_tokens/_login_failures.json
   ```

3. **Status prüfen:**
   ```bash
   sudo cat /var/lib/trail-atlas/garmin_tokens/_login_failures.json
   ```

---

## Automatische Prozesse

| Was | Wann | Konfiguration |
|-----|------|--------------|
| Garmin Sync (Multi-User) | Täglich 03:00 | `/etc/cron.d/trail-atlas-garmin-sync` |
| TLS-Zertifikat Renewal | ~alle 60 Tage | `systemctl status certbot.timer` |
| DuckDNS IP-Update | Alle 5 Minuten | `crontab -l` |
| Logrotate | Wöchentlich | `/etc/logrotate.d/trail-atlas` |
| DB-Backup bei Backend-Deploy | Bei jedem Push | GitHub Actions |
| CLI-Update | Bei jeder `scripts/`-Änderung | GitHub Actions `deploy-ops-cli` Job |

### Cronjob-Details

Der Garmin-Sync-Cronjob ist in `/etc/cron.d/trail-atlas-garmin-sync` definiert:

```
0 3 * * * trail-atlas /opt/trail-atlas/venv/bin/python3 /opt/trail-atlas/garmin-sync/garmin_sync.py >> /var/log/trail-atlas/garmin_sync.log 2>&1
```

Läuft als User `trail-atlas`, nutzt die venv unter `/opt/trail-atlas/venv/`. Das Script liest `SECRET_KEY` und `SYNC_API_KEY` aus `/etc/trail-atlas/garmin.env`, holt sich dann via `GET /sync/users` alle User mit hinterlegten Garmin-Credentials und syncet sie nacheinander.

Manueller Test:
```bash
# Schneller Config-Check (kein Garmin-Login):
sudo -u trail-atlas /opt/trail-atlas/venv/bin/python3 \
  /opt/trail-atlas/garmin-sync/garmin_sync.py --check

# Dry-Run (mit Garmin-Login pro User, aber kein DB-Write):
sudo -u trail-atlas /opt/trail-atlas/venv/bin/python3 \
  /opt/trail-atlas/garmin-sync/garmin_sync.py --dry-run

# Nur einen bestimmten User syncen:
sudo -u trail-atlas /opt/trail-atlas/venv/bin/python3 \
  /opt/trail-atlas/garmin-sync/garmin_sync.py --user 2 --limit 1
```

---

## Log-Dateien

| Log | Befehl |
|-----|--------|
| Backend (FastAPI) | `trail-atlas logs` oder `sudo journalctl -u trail-atlas` |
| Garmin Sync | `trail-atlas logs sync` oder `sudo tail -f /var/log/trail-atlas/garmin_sync.log` |
| Nginx Access | `trail-atlas logs nginx` |
| Nginx Errors | `trail-atlas logs nginx-error` |
| Login-Failures (Garmin) | `sudo cat /var/lib/trail-atlas/garmin_tokens/_login_failures.json` |
| Deploy-History | `cat ~/trail-atlas/deploy.log` |

---

## Backup-Strategie

**Automatisch:** Bei jedem Backend-Deploy via GitHub Actions wird ein DB-Backup mit Timestamp erstellt. Die letzten 10 werden behalten.

**Manuell:** `trail-atlas db backup` erstellt ein Backup mit `_manual` Suffix.

**Externe Sicherung (empfohlen):**
```bash
# Cronjob für tägliches externes Backup (z.B. auf einen anderen Server)
0 4 * * * sudo -u trail-atlas sqlite3 /var/lib/trail-atlas/trail_atlas.db \
  ".backup '/tmp/trail_atlas_$(date +\%F).db'" && \
  scp /tmp/trail_atlas_$(date +\%F).db user@backup-server:~/
```

**Was sichern, was nicht:**

| Sichern | Begründung |
|---------|-----------|
| `/var/lib/trail-atlas/trail_atlas.db` | Activities, GPS, Users, Bewertungen, Credentials |
| `/etc/trail-atlas/garmin.env` | Secrets (sonst keine Entschlüsselung der Credentials möglich!) |

`garmin_tokens/` muss nicht gesichert werden – die Tokens werden beim nächsten Sync neu generiert. `_login_failures.json` erst recht nicht.

> **Wichtig:** Wenn `SECRET_KEY` in `garmin.env` verloren geht, sind die in der DB verschlüsselt gespeicherten Garmin-Credentials unwiederbringlich verloren. Die User müssen sie neu eintragen.

---

## Wartungsfenster

Die App hat kein echtes Wartungsfenster. Kurze Unterbrechungen:

- **Backend-Restart:** ~3 Sekunden Downtime. App zeigt "Server nicht erreichbar".
- **Nginx-Reload:** 0 Downtime (graceful reload).
- **DB-Backup:** 0 Downtime (SQLite-Backup funktioniert online dank WAL).
- **Schema-Migrationen:** laufen beim Backend-Start automatisch (idempotent), keine separaten Wartungsschritte.
