# Trail Atlas

Mobile-first Web-App zur Visualisierung und Verwaltung von Garmin GPS-Aktivitätsdaten.

Tracks aus Garmin Connect werden auf einer interaktiven Karte angezeigt, gefiltert und analysiert. Daten liegen zentral auf dem eigenen Server in einer SQLite-Datenbank.

---

## Architektur

```
                ┌──────────────────────────────────────┐
                │   Browser (HTML + JS + Leaflet)      │
                │   IndexedDB nicht mehr benötigt      │
                └──────────────────┬───────────────────┘
                                   │ HTTPS + Basic Auth
                                   ▼
                ┌──────────────────────────────────────┐
                │   Nginx (Reverse Proxy + TLS)        │
                │   Let's Encrypt via DuckDNS          │
                │   /         → /var/www/trail-atlas/  │
                │   /libs/    → lokale Bibliotheken    │
                │   /api/     → FastAPI Backend        │
                └──────────────────┬───────────────────┘
                                   │
                                   ▼
                ┌──────────────────────────────────────┐
                │   FastAPI (uvicorn, systemd)         │
                │   Port 127.0.0.1:8000                │
                └──────────────────┬───────────────────┘
                                   │
                                   ▼
                ┌──────────────────────────────────────┐
                │   SQLite (WAL-Modus)                 │
                │   /var/lib/trail-atlas/              │
                └──────────────────────────────────────┘
```

---

## Repository-Struktur

```
trail-atlas/
├── .github/
│   └── workflows/
│       └── deploy.yml          ← GitHub Actions CI/CD Pipeline
├── backend/                    ← Python Backend (FastAPI + SQLite)
│   ├── README.md               ← API Dokumentation
│   ├── main.py                 ← FastAPI Endpoints
│   ├── database.py             ← SQLite Wrapper
│   ├── requirements.txt        ← Python Dependencies
│   ├── trail-atlas.service     ← systemd Unit
│   ├── setup_backend.sh        ← Erstinstallation
│   └── nginx_api_snippet.conf  ← Nginx Proxy-Konfiguration
├── scripts/
│   ├── deploy.sh               ← Server-seitiges Frontend-Deploy
│   └── sudoers_trail_atlas.txt ← Sudoers-Konfiguration
├── src/
│   └── garmin_trail_atlas_*.html  ← Frontend (Leaflet + Vanilla JS)
└── README.md                   ← Diese Datei
```

---

## Features

**Karte**
- Alle Touren als farbige Polylines auf interaktiver Karte (Leaflet + Canvas Renderer)
- Drei Kartenstile: Dark · Topo · Satellit
- Adaptive Punkt-Reduktion bei vielen Tracks (Performance auf Mobile)
- Track-Selektion mit Highlight + Ausgrauen anderer Touren
- Klickbare Startpunkt-Marker ab Zoom 11

**Touren-Tab**
- Live-Statistiken (Anzahl, Kilometer, Stunden) reaktiv auf Filter
- Aggregations-Diagramm (Jahr/Quartal/Monat × Anzahl/km/Stunden)
- Filter nach Aktivitätstyp und Zeitraum
- Sortierung nach Datum/Distanz/Dauer

**Datenverwaltung**
- CSV-Import (Metadaten + GPS-Punkte)
- Automatische Validierung mit Qualitätsreport
- Einzelne Touren oder ganze Datenbank löschen
- Datenbank-Statistiken im Import-Tab

**Sicherheit**
- HTTPS mit Let's Encrypt (Auto-Renewal)
- HTTP Basic Auth für Frontend und API
- SRI-Hashes für alle JavaScript-Bibliotheken
- Content-Security-Policy
- Input-Validierung (Koordinaten-Range, Datumsformat, Pflichtfelder)
- XSS-Schutz durch HTML-Escaping aller benutzerdefinierten Daten

---

## Setup auf einer neuen VM

Komplette Erstinstallation in vier Phasen.

### Phase 1 – Domain und HTTPS

Detaillierte Anleitung in `docs/setup_https_duckdns.md` (falls vorhanden).

Kurzfassung:
1. DuckDNS Subdomain registrieren auf https://www.duckdns.org
2. Auto-Update Cronjob einrichten
3. Certbot installieren: `sudo apt install certbot python3-certbot-nginx`
4. Initiale Nginx-Config aktivieren
5. Zertifikat ausstellen: `sudo certbot --nginx -d trail-atlas.duckdns.org`

### Phase 2 – Frontend (HTML)

```bash
# Auf der VM
mkdir -p ~/trail-atlas/src ~/trail-atlas/scripts

# Sudoers für Frontend-Deploy
sudo visudo -f /etc/sudoers.d/trail-atlas
# → Inhalt aus scripts/sudoers_trail_atlas.txt einfügen, DEIN_USER ersetzen

# Erste HTML deployen via GitHub Actions oder manuell
bash ~/trail-atlas/scripts/deploy.sh
```

### Phase 3 – Backend (FastAPI + SQLite)

```bash
# ZIP auf VM kopieren
scp backend.zip user@trail-atlas.duckdns.org:~/

# Auf der VM
unzip backend.zip
cd backend
sudo bash setup_backend.sh
```

Das Setup-Script erstellt:
- System-User `trail-atlas`
- Python venv unter `/opt/trail-atlas/venv/`
- Backend-Code unter `/opt/trail-atlas/backend/`
- SQLite DB unter `/var/lib/trail-atlas/`
- systemd Service `trail-atlas.service`
- Nginx `/api/`-Proxy

Details siehe `backend/README.md`.

### Phase 4 – CI/CD (GitHub Actions)

1. SSH-Deploy-Key auf VM autorisieren
2. GitHub Repository Secrets anlegen:
   - `SSH_PRIVATE_KEY` – privater Deploy-Key
   - `VM_HOST` – `trail-atlas.duckdns.org`
   - `VM_USER` – SSH-Username
   - `BASIC_AUTH_USER`, `BASIC_AUTH_PASS` – für Health-Check
3. Push auf `main` → automatisches Deployment

---

## Workflow für neue Versionen

```bash
# 1. Neue HTML-Datei in src/ ablegen
cp garmin_trail_atlas_v3.5_api_local.html src/

# 2. Committen + pushen
git add src/
git commit -m "feat: v3.5 - neue Features"
git push origin main

# 3. GitHub Actions deployt automatisch
# 4. Browser öffnen: https://trail-atlas.duckdns.org
```

Die GitHub Action ermittelt automatisch die zuletzt geänderte HTML-Datei aus der Git-History und deployt diese.

---

## CSV-Import

Garmin Connect erlaubt den Export aller Aktivitäten als CSV. Zwei Dateien werden erwartet:

| Datei | Inhalt |
|-------|--------|
| `activity_summary_*.csv` | Metadaten (ID, Typ, Datum, Start/Ende-Koordinaten) |
| `all_fit_data_*.csv`     | GPS-Punkte (lat, lng, activity_id) |

Upload entweder über die App (Tab „Import") oder direkt per API:

```bash
curl -u "user:pass" -X POST \
  -F "file=@activity_summary.csv" \
  https://trail-atlas.duckdns.org/api/import/summary

curl -u "user:pass" -X POST \
  -F "file=@all_fit_data.csv" \
  https://trail-atlas.duckdns.org/api/import/gps
```

---

## Diagnose und Wartung

```bash
# Backend-Status
sudo systemctl status trail-atlas
sudo journalctl -u trail-atlas -f

# Nginx
sudo nginx -t
sudo systemctl status nginx

# DB-Inhalt prüfen
sudo -u trail-atlas sqlite3 /var/lib/trail-atlas/trail_atlas.db
sqlite> SELECT COUNT(*) FROM activities;
sqlite> SELECT COUNT(*) FROM gps_points;

# API direkt ansprechen (umgeht Nginx)
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/db/stats

# Deploy-Log
cat ~/trail-atlas/deploy.log

# DB-Backup
sudo -u trail-atlas sqlite3 /var/lib/trail-atlas/trail_atlas.db \
  ".backup /tmp/trail_atlas_backup_$(date +%F).db"
```

---

## Versionshistorie

| Version | Highlights |
|---------|------------|
| v1.0    | Karte, Tracks, IndexedDB, CSV-Import |
| v1.2    | Stats-Banner, Diagramm, Zeitfilter, Distanz |
| v2.0    | Performance-Optimierungen, Marker, Kartenstile, Dropdowns |
| v2.2    | fitBounds-Control, Dim-Overlay, Diverse Bugfixes |
| v2.3    | Datenverwaltung, Import-Validierung, Tour-Löschen |
| v2.4c   | Track-Selektion mit Highlight, Canvas-Renderer für Performance |
| v2.5    | Empty States, Loading Screen mit Fortschrittsanzeige |
| v2.6    | XSS-Escaping, SRI-Hashes, CSP-Header, lokale Bibliotheken |
| v3.0    | API-basierte Architektur (Backend, SQLite, FastAPI) |
| v3.2    | CSP für API-Calls, persistente DB-Connection, redirect_slashes Fix |

---

## Tech Stack

**Frontend**
- HTML/CSS/JavaScript (vanilla, kein Framework)
- Leaflet 1.9.4 (Karten)
- PapaParse 5.4.1 (CSV in der API)

**Backend**
- Python 3.10+
- FastAPI 0.115
- uvicorn (ASGI Server)
- SQLite (WAL-Modus)

**Infrastruktur**
- Nginx (Reverse Proxy + TLS)
- Let's Encrypt (Zertifikate)
- DuckDNS (Dynamic DNS)
- systemd (Service Management)
- GitHub Actions (CI/CD)

---

## Bekannte Einschränkungen

- **Single-User:** Keine Login-Verwaltung, alle Nutzer mit Basic Auth Credentials sehen dieselben Daten
- **Backup-Strategie:** Keine automatischen Backups – sollte manuell/per Cronjob ergänzt werden
- **Skalierung:** SQLite und Single-Worker reichen für persönliche Nutzung mit hunderten Touren

---

## Roadmap

- Garmin-Sync direkt über die API (Python-Script auf der VM, ersetzt manuellen CSV-Upload)
- Höhenprofil pro Tour
- Jahres-Heatmap (Aktivitätsgrid)
- Persönliche Rekorde
- GPX-Export einzelner Touren
- Automatisches DB-Backup als API-Endpoint
- Multi-User-Support mit Login

---

## Lizenz

Privates Projekt für persönliche Nutzung.

Externe Bibliotheken unter ihren jeweiligen Lizenzen:
- Leaflet (BSD-2-Clause)
- PapaParse (MIT)
- FastAPI (MIT)
- Dexie (Apache-2.0) – nicht mehr verwendet ab v3.0
