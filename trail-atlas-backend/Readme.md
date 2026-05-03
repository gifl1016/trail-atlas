# Trail Atlas Backend

FastAPI + SQLite REST-API für die Trail Atlas Web-App.  
Stellt Aktivitäts-Metadaten und GPS-Punkte bereit, ersetzt die bisherige IndexedDB im Browser.

---

## Architektur

```
                 ┌──────────────────────────────────────┐
                 │  Trail Atlas Web App (HTML/JS)       │
                 │  https://trail-atlas.duckdns.org/    │
                 └─────────────────┬────────────────────┘
                                   │ HTTPS + Basic Auth
                                   ▼
                 ┌──────────────────────────────────────┐
                 │  Nginx (Reverse Proxy + TLS)         │
                 │  /            → /var/www/...         │
                 │  /libs/       → /var/www/.../libs/   │
                 │  /api/        → 127.0.0.1:8000       │
                 └─────────────────┬────────────────────┘
                                   │ HTTP localhost
                                   ▼
                 ┌──────────────────────────────────────┐
                 │  FastAPI (uvicorn, single worker)    │
                 │  systemd: trail-atlas.service        │
                 │  /opt/trail-atlas/backend/main.py    │
                 └─────────────────┬────────────────────┘
                                   │ persistent connection
                                   ▼
                 ┌──────────────────────────────────────┐
                 │  SQLite (WAL-Modus)                  │
                 │  /var/lib/trail-atlas/trail_atlas.db │
                 └──────────────────────────────────────┘
```

---

## Datei-Layout auf der VM

```
/opt/trail-atlas/
├── backend/
│   ├── main.py             ← FastAPI Endpoints
│   └── database.py         ← SQLite Wrapper
└── venv/                   ← Python virtualenv

/var/lib/trail-atlas/
└── trail_atlas.db          ← SQLite Datenbank (+ WAL/SHM Files)

/etc/systemd/system/
└── trail-atlas.service     ← Systemd Unit
```

---

## API Endpoints

Alle Endpoints sind durch **Nginx Basic Auth** geschützt.  
Base-URL: `https://trail-atlas.duckdns.org/api`

### Health

| Method | Path | Beschreibung |
|--------|------|--------------|
| `GET`  | `/health` | Verfügbarkeitscheck, gibt Version zurück |

### Activities

| Method | Path | Beschreibung |
|--------|------|--------------|
| `GET`    | `/activities` | Alle Aktivitäten |
| `GET`    | `/activities/{id}` | Einzelne Aktivität |
| `GET`    | `/activities/{id}/gps` | GPS-Punkte als `[[lat, lng], ...]` |
| `DELETE` | `/activities/{id}` | Aktivität + alle GPS-Punkte löschen |

### Import

| Method | Path | Body | Beschreibung |
|--------|------|------|--------------|
| `POST` | `/import/summary` | `multipart/form-data: file` | CSV mit Metadaten (`activity_summary_*.csv`) |
| `POST` | `/import/gps`     | `multipart/form-data: file` | CSV mit GPS-Punkten (`all_fit_data_*.csv`) |

Pflichtfelder werden serverseitig validiert. Antwort enthält Statistik:
```json
{ "imported": 485, "skipped": 0, "duplicates": 0, "elapsed_s": 1.42 }
```

### DB Management

| Method | Path | Beschreibung |
|--------|------|--------------|
| `GET`    | `/db/stats` | Anzahl Aktivitäten, GPS-Punkte, ohne GPS, nach Typ |
| `DELETE` | `/db/reset` | Alle Daten löschen + VACUUM |
| `DELETE` | `/db/gps`   | Nur GPS-Punkte löschen, Metadaten bleiben |

### Swagger UI

Interaktive API-Dokumentation: `https://trail-atlas.duckdns.org/api/docs`

---

## Beispiele

```bash
# Health-Check
curl -u "user:pass" https://trail-atlas.duckdns.org/api/health

# Alle Touren auflisten
curl -u "user:pass" https://trail-atlas.duckdns.org/api/activities

# Einzelne Tour löschen
curl -u "user:pass" -X DELETE \
  https://trail-atlas.duckdns.org/api/activities/20240925181045

# CSV importieren
curl -u "user:pass" -X POST \
  -F "file=@activity_summary.csv" \
  https://trail-atlas.duckdns.org/api/import/summary

# Datenbank-Statistik
curl -u "user:pass" https://trail-atlas.duckdns.org/api/db/stats

# Komplette DB zurücksetzen
curl -u "user:pass" -X DELETE \
  https://trail-atlas.duckdns.org/api/db/reset
```

---

## Konsistenz: GUI ↔ API

Die App und die API arbeiten auf **derselben** SQLite-Datenbank. Eine Änderung über die API ist sofort in der App sichtbar (und umgekehrt) – ein Refresh genügt.

**Wichtig zur Konsistenz:**  
Das Backend nutzt eine **persistente Single-Connection** statt für jeden Request eine neue zu öffnen. Damit sind Writes für nachfolgende Reads sofort sichtbar (kein WAL-Race-Condition mehr).

**Wichtig für DELETE-Methoden:**  
FastAPI ist mit `redirect_slashes=False` konfiguriert. Damit verursachen DELETE-Calls auf URLs mit oder ohne Trailing-Slash kein `307`-Redirect, das Clients/Browser zu `GET` umwandeln würden (was zu `405 Method Not Allowed` führt).

---

## Deployment

### Erstinstallation

```bash
# ZIP auf VM kopieren und entpacken
scp trail-atlas-backend.zip user@vm:~/
ssh user@vm "unzip trail-atlas-backend.zip"

# Installation ausführen
cd ~/trail-atlas-backend
sudo bash setup_backend.sh
```

Das Setup-Script erstellt System-User, Python venv, kopiert Code, richtet systemd ein und ergänzt die Nginx-Config um den `/api/`-Proxy.

### Updates

Wenn nur `main.py` oder `database.py` geändert wurden:

```bash
sudo cp main.py database.py /opt/trail-atlas/backend/
sudo systemctl restart trail-atlas
```

---

## Logs & Diagnose

```bash
# Service-Status
sudo systemctl status trail-atlas

# Live-Logs (Errors, Imports, Deletes etc.)
sudo journalctl -u trail-atlas -f

# Letzte 50 Zeilen
sudo journalctl -u trail-atlas -n 50

# Direkt auf API zugreifen (umgeht Nginx)
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/db/stats

# Datenbank manuell prüfen
sudo -u trail-atlas sqlite3 /var/lib/trail-atlas/trail_atlas.db
sqlite> SELECT COUNT(*) FROM activities;
sqlite> SELECT COUNT(*) FROM gps_points;
sqlite> .quit
```

---

## Performance-Charakteristik

Auf einer 1GB RAM / 1 vCPU VM mit 485 Touren und ~485.000 GPS-Punkten:

| Operation | Dauer |
|-----------|-------|
| `GET /activities` | < 5 ms |
| `GET /activities/{id}/gps` (1.000 Punkte) | < 10 ms |
| `POST /import/summary` (485 Zeilen) | ~ 0.3 s |
| `POST /import/gps` (485.000 Punkte) | ~ 8 s |
| `DELETE /activities/{id}` | < 20 ms |
| `DELETE /db/reset` | < 50 ms |

**RAM-Verbrauch des Backend-Prozesses:** ~60–80 MB (idle), ~120 MB (während Import).

---

## Sicherheit

- **Keine offenen Ports:** uvicorn lauscht nur auf `127.0.0.1:8000`. Erreichbar ausschließlich über Nginx.
- **Authentifizierung:** Nginx Basic Auth mit `htpasswd` – vor jedem API-Call.
- **Berechtigungen:** systemd-User `trail-atlas` ohne Login-Shell. Schreibzugriff nur auf `/var/lib/trail-atlas`.
- **systemd-Hardening:** `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`.
- **CORS:** nur Same-Origin erlaubt.
- **Input Validation:** alle CSV-Felder werden serverseitig validiert (Koordinaten-Range, Datumsformat, Pflichtfelder).

---

## Bekannte Einschränkungen

- **Single-User:** Keine Login-Verwaltung, kein Multi-Tenant. Alle Nutzer mit Basic Auth Credentials sehen dieselben Daten.
- **Backup:** Die SQLite-DB liegt nur auf der VM. Für Persistenz sollte regelmäßig die `/var/lib/trail-atlas/trail_atlas.db` gesichert werden.

---

## Roadmap

- Garmin-Sync direkt über die API (statt CSV-Upload)
- Multi-User-Support mit Login-System
- Automatisches DB-Backup als zusätzlicher API-Endpoint
- Streaming-Endpoint für GPS-Punkte (lazy loading bei vielen Touren)
