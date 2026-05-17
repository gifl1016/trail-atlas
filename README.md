# Trail Atlas

Mobile-first Web-App zur Visualisierung und Verwaltung von Garmin GPS-Aktivitätsdaten.

Tracks aus Garmin Connect werden automatisch auf einer interaktiven Karte angezeigt, gefiltert und analysiert. Daten liegen zentral auf dem eigenen Server in einer SQLite-Datenbank. Multi-User-fähig mit Invite-basierter Registrierung.

---

## Architektur

```
                ┌──────────────────────────────────────┐
                │   Browser (HTML + JS + Leaflet)      │
                │   Session-Cookie Auth                │
                └──────────────────┬───────────────────┘
                                   │ HTTPS
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
                │   Session-Auth + Sync-API-Key        │
                │   Port 127.0.0.1:8000                │
                └──────────────────┬───────────────────┘
                                   │
                                   ▼
                ┌──────────────────────────────────────┐
                │   SQLite (WAL-Modus, Schema v2)      │
                │   /var/lib/trail-atlas/              │
                └──────────────────────────────────────┘

                ┌──────────────────────────────────────┐
                │   Garmin Sync (Cronjob, täglich 03:00)│
                │   Auth via X-Sync-Key Header         │
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
│   ├── main.py                 ← FastAPI Endpoints + Auth-Routes
│   ├── auth.py                 ← Session-Management, Invite-Codes, Passwort-Hashing
│   ├── database.py             ← SQLite Wrapper (Schema v2)
│   ├── requirements.txt        ← Python Dependencies
│   ├── trail-atlas.service     ← systemd Unit
│   ├── setup_backend.sh        ← Erstinstallation
│   └── nginx_api_snippet.conf  ← Nginx Proxy-Konfiguration
├── garmin-sync/
│   ├── garmin_sync.py          ← Garmin Connect → Trail Atlas API
│   └── garmin.env.example      ← Konfigurations-Template
├── scripts/
│   ├── deploy.sh               ← Server-seitiges Frontend-Deploy
│   ├── deploy_backend.sh       ← Backend-Deploy mit Backup + Rollback
│   ├── deploy_garmin_sync.sh   ← Sync-Script Deploy
│   ├── ops.sh                  ← CLI-Helper (trail-atlas Befehl)
│   └── sudoers_trail_atlas.txt ← Sudoers-Konfiguration
├── src/
│   └── garmin_trail_atlas_*.html  ← Frontend (Leaflet + Vanilla JS)
├── docs/                       ← Dokumentation
│   ├── ARCHITECTURE.md         ← Systemarchitektur + DB-Schema
│   ├── DEVELOPMENT.md          ← CI/CD + Workflow-Konventionen
│   ├── HISTORY.md              ← Versionshistorie
│   ├── OPERATIONS.md           ← Tägliche Befehle + Wartung
│   ├── SECURITY.md             ← Sicherheitskonzept
│   ├── SETUP.md                ← Erstinstallation (Schritt für Schritt)
│   └── TROUBLESHOOTING.md      ← Bekannte Probleme + Lösungen
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
- Bulk-GPS-Laden: alle Tracks in einem API-Call (kein N+1 Problem)

**Touren-Tab**
- Live-Statistiken (Anzahl, Kilometer, Stunden) reaktiv auf Filter
- Aggregations-Diagramm (Jahr/Quartal/Monat × Anzahl/km/Stunden)
- Filter nach Aktivitätstyp und Zeitraum
- Sortierung nach Datum/Distanz/Dauer

**Datenverwaltung**
- Automatischer Garmin-Sync (Cronjob, täglich 03:00)
- CSV-Import (Metadaten + GPS-Punkte)
- Automatische Validierung mit Qualitätsreport
- Einzelne Touren oder ganze Datenbank löschen
- Datenbank-Statistiken + Sync-Status im Import-Tab

**Multi-User + Auth**
- Session-basiertes Login (bcrypt + signierte Cookies)
- Invite-Code-System (Admin generiert Einladungslinks)
- Selbstregistrierung über `?invite=CODE` URL
- Admin- und Normal-User-Rollen

**Sicherheit**
- HTTPS mit Let's Encrypt (Auto-Renewal)
- Session-Auth mit signierten Cookies (httponly, secure, samesite)
- SRI-Hashes für alle JavaScript-Bibliotheken
- Content-Security-Policy
- Input-Validierung (Koordinaten-Range, Datumsformat, Pflichtfelder)
- XSS-Schutz durch HTML-Escaping aller benutzerdefinierten Daten

---

## Schnellstart

Detaillierte Erstinstallation: siehe [`docs/SETUP.md`](docs/SETUP.md).

Kurzfassung:

1. **Domain + HTTPS:** DuckDNS Subdomain → Nginx → Certbot
2. **Backend:** System-User + venv + FastAPI deployen + systemd Service
3. **Konfiguration:** `garmin.env` mit Garmin-Credentials, SECRET_KEY, ADMIN_USER, SYNC_API_KEY
4. **Garmin-Sync:** Cronjob in `/etc/cron.d/trail-atlas-garmin-sync`
5. **CI/CD:** GitHub Actions mit SSH Deploy-Key

---

## Workflow für neue Versionen

```bash
# 1. Neue HTML-Datei in src/ ablegen
cp garmin_trail_atlas_v3.6_api_local.html src/

# 2. Committen + pushen
git add src/
git commit -m "feat: v3.6 - Invite + Signup Flow"
git push origin main

# 3. GitHub Actions deployt automatisch
# 4. Browser öffnen: https://trail-atlas.duckdns.org
```

Die GitHub Action ermittelt automatisch die zuletzt geänderte HTML-Datei aus der Git-History und deployt diese.

---

## Diagnose und Wartung

Alle über den `trail-atlas` CLI-Helper (siehe [`docs/OPERATIONS.md`](docs/OPERATIONS.md)):

```bash
trail-atlas status        # Systemstatus auf einen Blick
trail-atlas health        # API + HTTPS Health-Check
trail-atlas logs          # Backend-Logs (live)
trail-atlas sync          # Garmin-Sync manuell starten
trail-atlas sync dry      # Dry-Run (kein Schreibzugriff)
trail-atlas db stats      # DB-Statistik
trail-atlas db backup     # Manuelles Backup
```

---

## Tech Stack

**Frontend:** HTML/CSS/JavaScript (vanilla), Leaflet 1.9.4, PapaParse 5.4.1

**Backend:** Python 3.10+, FastAPI 0.115, uvicorn, SQLite (WAL), bcrypt, itsdangerous

**Infrastruktur:** Nginx, Let's Encrypt, DuckDNS, systemd, GitHub Actions

---

## Dokumentation

| Dokument | Inhalt |
|----------|--------|
| [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Systemdiagramm, DB-Schema, Data Flows, Architekturentscheidungen |
| [`SETUP.md`](docs/SETUP.md) | Komplette Erstinstallation in 4 Phasen |
| [`OPERATIONS.md`](docs/OPERATIONS.md) | Tägliche Befehle, Wartung, Automatische Prozesse |
| [`DEVELOPMENT.md`](docs/DEVELOPMENT.md) | CI/CD Pipeline, Versionsschema, Debugging |
| [`SECURITY.md`](docs/SECURITY.md) | Auth-System, TLS, CSP, SRI, Credential-Speicherung |
| [`HISTORY.md`](docs/HISTORY.md) | Versionshistorie, Architektur-Evolution, Roadmap |
| [`TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | Bekannte Probleme + Lösungen |

---

## Roadmap

**Nächste Schritte:**
- Garmin-Sync pro User (eigene Garmin-Credentials je Account)
- Touren-Bewertung (Sterne-System)
- Touren nach User filtern

**Mittelfristig:**
- Höhenprofil pro Tour
- Jahres-Heatmap (Aktivitätsgrid)
- GPX-Export
- PWA (Homescreen-Icon, Offline-Fähigkeit)

---

## Lizenz

Privates Projekt für persönliche Nutzung.

Externe Bibliotheken unter ihren jeweiligen Lizenzen:
- Leaflet (BSD-2-Clause)
- PapaParse (MIT)
- FastAPI (MIT)
