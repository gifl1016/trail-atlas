# Trail Atlas

Mobile Web-App zur Visualisierung von Garmin GPS-Aktivitätsdaten.  
Gebaut mit Leaflet, PapaParse, Dexie (IndexedDB) – alles in einer einzelnen HTML-Datei.

---

## Repository-Struktur

```
trail-atlas/
├── .github/
│   └── workflows/
│       └── deploy.yml          ← GitHub Actions CI/CD Pipeline
├── scripts/
│   ├── deploy.sh               ← Server-seitiges Deploy-Script
│   └── sudoers_trail_atlas.txt ← Sudoers-Konfiguration für den Deploy-User
├── src/
│   └── garmin_trail_atlas_*.html  ← App-Versionen (local-Variante mit SRI-Platzhaltern)
└── README.md
```

---

## Einmalige Einrichtung

### 1. VM vorbereiten

```bash
# Ordner erstellen
mkdir -p ~/trail-atlas/src ~/trail-atlas/scripts

# Deploy-Script kopieren (oder via git clone)
scp scripts/deploy.sh user@trail-atlas.duckdns.org:~/trail-atlas/scripts/

# Sudoers einrichten (DEIN_USER ersetzen)
sudo visudo -f /etc/sudoers.d/trail-atlas
# → Inhalt aus scripts/sudoers_trail_atlas.txt einfügen, DEIN_USER ersetzen

# Sudoers testen
sudo nginx -t
sudo systemctl reload nginx
```

### 2. Deploy-Key für GitHub Actions erstellen

```bash
# Neues Key-Paar generieren (kein Passphrase!)
ssh-keygen -t ed25519 -C "github-actions-trail-atlas" -f ~/.ssh/github_deploy -N ""

# Public Key auf VM autorisieren
cat ~/.ssh/github_deploy.pub >> ~/.ssh/authorized_keys

# Private Key anzeigen → als GitHub Secret speichern
cat ~/.ssh/github_deploy
```

### 3. GitHub Secrets einrichten

Im GitHub Repository → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret Name | Wert | Beschreibung |
|---|---|---|
| `SSH_PRIVATE_KEY` | Inhalt von `~/.ssh/github_deploy` | Deploy-Key (Private) |
| `VM_HOST` | `trail-atlas.duckdns.org` | Hostname oder IP der VM |
| `VM_USER` | `dein-username` | SSH-Login-User auf der VM |
| `BASIC_AUTH_USER` | `dein-nginx-user` | Nginx Basic Auth Benutzername |
| `BASIC_AUTH_PASS` | `dein-nginx-passwort` | Nginx Basic Auth Passwort |

### 4. Ersten Deploy testen

```bash
# Repository klonen
git clone https://github.com/DEIN_USERNAME/trail-atlas.git
cd trail-atlas

# HTML-Datei hinzufügen
cp /pfad/zu/garmin_trail_atlas_v2.6_local.html src/

# Pushen → GitHub Actions startet automatisch
git add .
git commit -m "feat: initial deploy v2.6"
git push origin main
```

Deploy-Status unter: `https://github.com/DEIN_USERNAME/trail-atlas/actions`

---

## Workflow – Neue Version deployen

```bash
# 1. Neue HTML von Claude herunterladen
# 2. In src/ ablegen
cp garmin_trail_atlas_v2.7_local.html src/

# 3. Pushen
git add src/garmin_trail_atlas_v2.7_local.html
git commit -m "feat: v2.7 - neue features"
git push origin main

# → GitHub Actions deployed automatisch
# → Browser refreshen: https://trail-atlas.duckdns.org
```

**Das ist der komplette Workflow.** Kein SSH, kein manuelles Deploy mehr.

---

## Manuelles Deploy (Fallback)

Falls GitHub Actions nicht verfügbar:

```bash
ssh user@trail-atlas.duckdns.org
cd ~/trail-atlas
git pull
bash scripts/deploy.sh
```

---

## HTML-Datei Konventionen

Die App-Dateien in `src/` müssen:
- Den Namen `garmin_trail_atlas_*.html` haben
- Die `_local`-Variante sein (mit SRI-Platzhaltern statt CDN-URLs)
- Folgende Platzhalter enthalten: `LEAFLET_CSS_SRI`, `LEAFLET_JS_SRI`, `PAPAPARSE_SRI`, `DEXIE_SRI`

Das Deploy-Script wählt automatisch die **neueste Datei** (nach Änderungsdatum) aus `src/`.

---

## Deploy-Log einsehen

```bash
ssh user@trail-atlas.duckdns.org "cat ~/trail-atlas/deploy.log"
```

Beispiel-Output:
```
2026-01-15 14:23:01 | garmin_trail_atlas_v2.6_local.html | OK
2026-01-16 09:11:44 | garmin_trail_atlas_v2.7_local.html | OK
```

---

## Libraries updaten

Die Libraries (Leaflet, PapaParse, Dexie) werden gecacht und nur beim ersten Deploy heruntergeladen.  
Um sie neu zu laden (z.B. nach Versionsupdate in `deploy.sh`):

```bash
ssh user@trail-atlas.duckdns.org "bash ~/trail-atlas/scripts/deploy.sh --force"
```

---

## Versionshistorie

| Version | Datum | Highlights |
|---|---|---|
| v1.0 | 2025-01 | Karte, Tracks, Import, IndexedDB |
| v1.2 | 2025-02 | Stats, Diagramm, Zeitfilter, Distanz |
| v2.0 | 2025-03 | Performance, Marker, Kartenstile, Dropdowns |
| v2.2 | 2025-04 | fitBounds-Control, Dim-Overlay, Bugfixes |
| v2.3 | 2025-05 | Datenverwaltung, Import-Validierung |
| v2.4c | 2025-06 | Track-Selektion, Canvas-Renderer |
| v2.5 | 2025-07 | Empty States, Loading Screen |
| v2.6 | 2025-08 | XSS-Escaping, SRI-Hashes, CSP |
