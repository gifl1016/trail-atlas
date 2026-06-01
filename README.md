# Trail Atlas

Mobile-first Web-App zur Visualisierung und Verwaltung von Garmin GPS-Aktivitätsdaten.

Tracks aus Garmin Connect werden automatisch auf einer interaktiven Karte angezeigt, gefiltert, bewertet und analysiert. Daten liegen zentral auf dem eigenen Server in einer SQLite-Datenbank. Multi-User-fähig mit Invite-basierter Registrierung und Admin-Nutzerverwaltung.

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
                │   SQLite (WAL-Modus, Schema v4)      │
                │   /var/lib/trail-atlas/              │
                └──────────────────────────────────────┘

                ┌──────────────────────────────────────┐
                │   Garmin Sync (Cronjob, täglich 03:00)│
                │   Multi-User, pro User Token-Cache   │
                │   Auth via X-Sync-Key Header         │
                └──────────────────────────────────────┘
```

---

## Repository-Struktur

```
trail-atlas/
├── .github/
│   └── workflows/
│       └── deploy.yml          ← GitHub Actions: Frontend, Backend, Sync, CLI
├── backend/                    ← Python Backend (FastAPI + SQLite)
│   ├── main.py                 ← FastAPI Endpoints + Auth-Routes
│   ├── auth.py                 ← Session, Invite-Codes, Garmin-Credentials (Fernet)
│   ├── database.py             ← SQLite Wrapper + Schema-Migrationen (v1→v4)
│   ├── test_*.py               ← Backend Test-Suites (>200 Tests)
│   ├── requirements.txt        ← Python Dependencies (inkl. cryptography)
│   ├── trail-atlas.service     ← systemd Unit
│   └── nginx_api_snippet.conf  ← Nginx Proxy-Konfiguration
├── garmin-sync/
│   ├── garmin_sync.py          ← Multi-User Garmin Sync mit Fail-Tracking
│   └── garmin.env.example      ← Konfigurations-Template
├── scripts/
│   ├── deploy.sh               ← Frontend-Deploy
│   ├── deploy_backend.sh       ← Backend-Deploy mit Backup + Rollback
│   ├── deploy_garmin_sync.sh   ← Sync-Script Deploy
│   ├── ops.sh                  ← CLI-Helper (trail-atlas Befehl)
│   └── sudoers_trail_atlas.txt ← Sudoers-Konfiguration
├── src/
│   └── garmin_trail_atlas_*.html  ← Frontend (Leaflet + Vanilla JS)
├── docs/                       ← Dokumentation
│   ├── ARCHITECTURE.md         ← Systemarchitektur + DB-Schema (v4)
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
- Drei Kartenstile: Dark · Topo · Satellit (Auswahl-Button links unten)
- Adaptive Punkt-Reduktion bei vielen Tracks (Performance auf Mobile)
- Track-Selektion mit Highlight + Ausgrauen anderer Touren
- Klickbare Startpunkt-Marker auch für Activities ohne GPS-Track
- Bulk-GPS-Laden: alle Tracks in einem API-Call (kein N+1 Problem)

**Aktivitäten-Tab**
- Live-Statistiken (Anzahl, Kilometer, Stunden) reaktiv auf Filter
- Aggregations-Diagramm (Jahr/Quartal/Monat × Anzahl/km/Stunden)
- Filter: Aktivitätstyp · Zeitraum · Bewertung
- Sortierung nach Datum/Distanz/Dauer
- Sterne-Bewertungen pro Tour sichtbar in der Liste

**Activity Deep-Dive**
- Detail-Sheet beim Klick auf eine Tour
- Sterne-Bewertung 1–5 (Klick auf gesetzten Stern entfernt die Bewertung)
- Datum, Start/Ende, Dauer, Distanz, Startkoordinaten
- "Auf Karte zeigen" (von Liste zur Karte)

**Datenverwaltung**
- Automatischer Garmin-Sync (Cronjob, täglich 03:00)
- Manueller CSV-Import (Metadaten + GPS-Punkte) bei Bedarf
- Drei Reset-Optionen:
  - "Nur GPS-Punkte löschen" – behält Activities, löscht Tracks
  - "Alle Garmin-Daten zurücksetzen" – Activities+GPS weg, **Bewertungen bleiben**
  - "Alle Daten zurücksetzen" – inkl. Bewertungen, kompletter Reset
- Datenbank-Statistiken + Sync-Status im Import-Tab

**Multi-User + Auth**
- Session-basiertes Login (bcrypt + signierte Cookies)
- Invite-Code-System mit Garmin-Credentials beim Signup (optional)
- Selbstregistrierung über `?invite=CODE` URL
- Garmin-Account-Eindeutigkeit: jeder Garmin-Account kann nur einmal verknüpft werden
- Pro-User-Datenisolation: jeder User sieht nur seine eigenen Touren, GPS-Punkte, Bewertungen, Sync-Logs

**Admin Nutzerverwaltung (nur für Admin-User sichtbar)**
- Vierter Tab mit Übersicht aller User: Garmin-Status, Touren-/GPS-Anzahl, letzter Sync
- Garmin-Credentials nachträglich setzen, ändern, oder entfernen
- User komplett löschen (kaskadiert Activities, GPS, Credentials, Sync-Logs)
- Self-Protection (Admin kann sich nicht selbst löschen) und Last-Admin-Protection

**Garmin-Sync Robustheit**
- Multi-User: ein Cron-Lauf synct alle User mit hinterlegten Credentials
- Login-Timeout (30s) verhindert Endlos-Blockaden bei falschen Credentials
- Fail-Tracking: nach 2 fehlgeschlagenen Logins wird der User übersprungen
  → schützt vor Garmin-Account-Sperrung
- Automatischer Reset des Fail-Counters wenn Credentials in der App aktualisiert werden
- Best-effort GPS-Fetch: Activities werden auch importiert wenn GPS-Punkte fehlen oder
  GPS-Fetch rate-limited wird

**Sicherheit**
- HTTPS mit Let's Encrypt (Auto-Renewal)
- Session-Auth mit signierten Cookies (httponly, secure, samesite)
- Garmin-Credentials Fernet-verschlüsselt (Key aus SECRET_KEY abgeleitet)
- Email-Hash (one-way) für Duplikat-Erkennung ohne Klartext-Vergleich
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
3. **Konfiguration:** `garmin.env` mit `SECRET_KEY`, `ADMIN_USER`/`ADMIN_PASS`, `SYNC_API_KEY` (Garmin-Credentials optional – können auch über die App registriert werden)
4. **Garmin-Sync:** Cronjob in `/etc/cron.d/trail-atlas-garmin-sync`
5. **CI/CD:** GitHub Actions mit SSH Deploy-Key

---

## Workflow für neue Versionen

```bash
# 1. Neue HTML-Datei in src/ ablegen (Versionsnummer hochzählen!)
cp garmin_trail_atlas_v3.7_api_local.html src/

# 2. Committen + pushen
git add src/
git commit -m "feat: v3.7 - Aktivitäts-Bewertungen"
git push origin main

# 3. GitHub Actions deployt automatisch nur das Geänderte
#    (path-basierte Detection: src/, backend/, garmin-sync/, scripts/)
# 4. Browser öffnen: https://trail-atlas.duckdns.org
```

Die GitHub Action ermittelt automatisch die zuletzt geänderte HTML-Datei aus der Git-History und deployt diese. Versionsregeln siehe [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md).

---

## Diagnose und Wartung

Alle über den `trail-atlas` CLI-Helper (siehe [`docs/OPERATIONS.md`](docs/OPERATIONS.md)):

```bash
trail-atlas status        # Systemstatus auf einen Blick
trail-atlas health        # API + HTTPS Health-Check
trail-atlas logs          # Backend-Logs (live)
trail-atlas sync          # Garmin-Sync manuell starten
trail-atlas sync 1        # Test-Sync, max. 1 Tour pro User
trail-atlas db stats      # DB-Statistik
trail-atlas db backup     # Manuelles Backup
```

---

## Tech Stack

**Frontend:** HTML/CSS/JavaScript (vanilla), Leaflet 1.9.4, PapaParse 5.4.1

**Backend:** Python 3.10+, FastAPI 0.115, uvicorn, SQLite (WAL), bcrypt, itsdangerous, cryptography (Fernet)

**Garmin-Sync:** Python, garminconnect Library, threading-basierter Login-Timeout

**Infrastruktur:** Nginx, Let's Encrypt, DuckDNS, systemd, GitHub Actions

---

## Dokumentation

| Dokument | Inhalt |
|----------|--------|
| [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Systemdiagramm, DB-Schema (v4), Data Flows, Architekturentscheidungen |
| [`SETUP.md`](docs/SETUP.md) | Komplette Erstinstallation in 4 Phasen |
| [`OPERATIONS.md`](docs/OPERATIONS.md) | Tägliche Befehle, Wartung, Automatische Prozesse |
| [`DEVELOPMENT.md`](docs/DEVELOPMENT.md) | CI/CD Pipeline, Versionsschema, Debugging |
| [`SECURITY.md`](docs/SECURITY.md) | Auth-System, Garmin-Credential-Verschlüsselung, TLS, CSP, SRI |
| [`HISTORY.md`](docs/HISTORY.md) | Versionshistorie, Architektur-Evolution, Roadmap |
| [`TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | Bekannte Probleme + Lösungen |

---

## Roadmap

**Umgesetzt seit der letzten README-Aktualisierung:**
- ✅ Garmin-Sync pro User (jeder User hinterlegt eigene Garmin-Credentials)
- ✅ Touren-Bewertung (Sterne-System, DB-Reset-resistent)
- ✅ Admin Nutzerverwaltung (vierter Tab, Garmin-Verknüpfung)
- ✅ Activities ohne GPS-Track werden importiert (Indoor, Yoga, Strength)
- ✅ Garmin-Account-Eindeutigkeit verhindert Sync-Kollisionen

**Mittelfristig:**
- Höhenprofil pro Tour
- Jahres-Heatmap (Aktivitätsgrid)
- GPX-Export
- PWA (Homescreen-Icon, Offline-Fähigkeit)

**Langfristig:**
- Persönliche Rekorde (schnellste/längste Tour)
- Streaming-Endpoint für GPS-Punkte (lazy loading)

---

## Lizenz

Privates Projekt für persönliche Nutzung.

Externe Bibliotheken unter ihren jeweiligen Lizenzen:
- Leaflet (BSD-2-Clause)
- PapaParse (MIT)
- FastAPI (MIT)
- garminconnect (MIT)
