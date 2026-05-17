# Operations

Tägliche Befehle und Wartung. Alle über den `trail-atlas` Helper:

```bash
sudo cp scripts/ops.sh /usr/local/bin/trail-atlas
sudo chmod +x /usr/local/bin/trail-atlas
```

---

## Quick Reference

```
trail-atlas status          Systemstatus auf einen Blick
trail-atlas health          API + HTTPS Health-Check
trail-atlas logs            Backend-Logs (live, Ctrl+C)
trail-atlas logs sync       Garmin-Sync-Logs (live)
trail-atlas logs nginx      Nginx Access-Log (live)
trail-atlas sync            Alle neuen Touren syncen
trail-atlas sync 1          Test-Sync: nur 1 Tour
trail-atlas sync dry        Dry-Run (kein Schreibzugriff)
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
# Oder direkt über die API (benötigt Auth – Sync-Key oder Session-Cookie):
curl -s http://127.0.0.1:8000/db/stats | python3 -m json.tool
```

> **Hinweis:** Seit v1.5.0 benötigen API-Endpoints Authentifizierung. Direkte `curl`-Befehle gegen die API funktionieren nur wenn (a) noch kein User in der DB ist, oder (b) der `SYNC_API_KEY` als Header mitgegeben wird: `curl -H "X-Sync-Key: DEIN_KEY" http://127.0.0.1:8000/db/stats`. Der `trail-atlas` CLI-Helper läuft als Systemuser und nutzt den Key automatisch über die Umgebung.

### "Alle Daten löschen und neu syncen"

```bash
# 1. Backup
trail-atlas db backup

# 2. Reset (als trail-atlas User oder mit Sync-Key)
curl -H "X-Sync-Key: $(sudo grep SYNC_API_KEY /etc/trail-atlas/garmin.env | cut -d= -f2)" \
  -X DELETE http://127.0.0.1:8000/db/reset

# 3. Full Resync
trail-atlas sync full
```

### "Neuen User einladen"

```bash
# 1. Im Browser: einloggen → Import-Tab → "Einladungscode generieren"
# 2. Link kopieren und an den Freund schicken
# 3. Freund öffnet den Link → Signup-Formular → fertig

# Alternativ über die API:
curl -H "X-Sync-Key: $(sudo grep SYNC_API_KEY /etc/trail-atlas/garmin.env | cut -d= -f2)" \
  -X POST http://127.0.0.1:8000/auth/invite
```

---

## Automatische Prozesse

| Was | Wann | Konfiguration |
|-----|------|--------------|
| Garmin Sync | Täglich 03:00 | `/etc/cron.d/trail-atlas-garmin-sync` |
| TLS-Zertifikat Renewal | ~alle 60 Tage | `systemctl status certbot.timer` |
| DuckDNS IP-Update | Alle 5 Minuten | `crontab -l` |
| Logrotate | Wöchentlich | `/etc/logrotate.d/trail-atlas` |
| DB-Backup bei Backend-Deploy | Bei jedem Push | GitHub Actions |

### Cronjob-Details

Der Garmin-Sync-Cronjob ist in `/etc/cron.d/trail-atlas-garmin-sync` definiert:

```
0 3 * * * trail-atlas /opt/trail-atlas/venv/bin/python3 /opt/trail-atlas/garmin-sync/garmin_sync.py >> /var/log/trail-atlas/garmin_sync.log 2>&1
```

Läuft als User `trail-atlas`, nutzt die venv unter `/opt/trail-atlas/venv/`. Das Script liest die Konfiguration (inkl. `SYNC_API_KEY`) aus `/etc/trail-atlas/garmin.env` via eigener `load_env()`-Funktion.

Manueller Test:
```bash
sudo -u trail-atlas /opt/trail-atlas/venv/bin/python3 \
  /opt/trail-atlas/garmin-sync/garmin_sync.py --dry-run
```

---

## Log-Dateien

| Log | Befehl |
|-----|--------|
| Backend (FastAPI) | `trail-atlas logs` oder `sudo journalctl -u trail-atlas` |
| Garmin Sync | `trail-atlas logs sync` oder `sudo tail -f /var/log/trail-atlas/garmin_sync.log` |
| Nginx Access | `trail-atlas logs nginx` |
| Nginx Errors | `trail-atlas logs nginx-error` |
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

---

## Wartungsfenster

Die App hat kein echtes Wartungsfenster. Kurze Unterbrechungen:

- **Backend-Restart:** ~3 Sekunden Downtime. App zeigt "Server nicht erreichbar".
- **Nginx-Reload:** 0 Downtime (graceful reload).
- **DB-Backup:** 0 Downtime (SQLite-Backup funktioniert online dank WAL).
