# Setup Guide

Komplette Erstinstallation von Trail Atlas auf einer frischen Ubuntu VM (22.04/24.04).

Voraussetzungen: VM mit 1+ GB RAM, öffentliche IP, Port 80+443 offen, SSH-Zugang.

---

## Phase 1 – Domain und HTTPS

### 1.1 DuckDNS Domain registrieren

1. https://www.duckdns.org → Login mit GitHub/Google
2. Subdomain wählen z.B. `trail-atlas` → ergibt `trail-atlas.duckdns.org`
3. Öffentliche IP eintragen, **Token notieren**

### 1.2 DuckDNS Auto-Update

```bash
mkdir -p ~/duckdns
cat > ~/duckdns/duck.sh << 'EOF'
#!/bin/bash
echo url="https://www.duckdns.org/update?domains=SUBDOMAIN&token=TOKEN&ip=" | curl -k -o ~/duckdns/duck.log -K -
EOF
# SUBDOMAIN und TOKEN ersetzen
nano ~/duckdns/duck.sh
chmod 700 ~/duckdns/duck.sh
~/duckdns/duck.sh && cat ~/duckdns/duck.log   # → "OK"
(crontab -l 2>/dev/null; echo "*/5 * * * * ~/duckdns/duck.sh >/dev/null 2>&1") | crontab -
```

### 1.3 Nginx + Certbot

```bash
sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx

# Initiale Nginx Config
sudo nano /etc/nginx/sites-available/trail-atlas
```

```nginx
server {
    listen 80;
    server_name trail-atlas.duckdns.org;
    root /var/www/trail-atlas;
    index index.html;
    location / { try_files $uri $uri/ =404; }
}
```

```bash
sudo ln -sf /etc/nginx/sites-available/trail-atlas /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo mkdir -p /var/www/trail-atlas
echo "<h1>Trail Atlas Setup</h1>" | sudo tee /var/www/trail-atlas/index.html
sudo nginx -t && sudo systemctl reload nginx

# Zertifikat
sudo certbot --nginx -d trail-atlas.duckdns.org --email deine@email.com --agree-tos --no-eff-email
# → "2" wählen für HTTP→HTTPS Redirect
```

### 1.4 Vollständige Nginx Config

Ersetze `/etc/nginx/sites-available/trail-atlas` mit der Production-Config (siehe `trail-atlas-v2.nginx.conf` im Repo). Wichtige Bestandteile: HTTPS, Security-Header, Gzip, `/api/` Proxy, `/libs/` Caching.

> **Hinweis:** Basic Auth (`auth_basic`) wird nicht mehr benötigt. Die App nutzt eigene Session-Auth. Falls noch vorhanden, auskommentieren.

```bash
sudo nginx -t && sudo systemctl reload nginx
```

---

## Phase 2 – Backend (FastAPI + SQLite)

### 2.1 System-User + Verzeichnisse

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin trail-atlas
sudo mkdir -p /opt/trail-atlas/backend /var/lib/trail-atlas /var/log/trail-atlas
sudo mkdir -p /var/lib/trail-atlas/garmin_tokens
sudo chown -R trail-atlas:trail-atlas /var/lib/trail-atlas /var/log/trail-atlas
sudo chmod 750 /var/lib/trail-atlas
sudo chmod 700 /var/lib/trail-atlas/garmin_tokens
```

### 2.2 Python Environment

```bash
sudo apt install -y python3-venv python3-pip sqlite3
python3 -m venv /opt/trail-atlas/venv
sudo /opt/trail-atlas/venv/bin/pip install -r /path/to/repo/backend/requirements.txt
# requirements.txt enthält:
#   fastapi, uvicorn, python-multipart, bcrypt, itsdangerous, cryptography
```

### 2.3 Backend-Code deployen

```bash
# Dateien kopieren (initial manuell, danach via GitHub Actions)
sudo cp main.py auth.py database.py /opt/trail-atlas/backend/
sudo chown -R trail-atlas:trail-atlas /opt/trail-atlas
```

### 2.4 Konfiguration (garmin.env)

```bash
sudo mkdir -p /etc/trail-atlas
sudo cp garmin.env.example /etc/trail-atlas/garmin.env
sudo chown -R trail-atlas:trail-atlas /etc/trail-atlas
sudo chmod 750 /etc/trail-atlas
sudo chmod 600 /etc/trail-atlas/garmin.env
sudo nano /etc/trail-atlas/garmin.env
```

Inhalt der `garmin.env`:

```env
# ─── Pflicht ────────────────────────────────────────────────

# Session-Secret + Fernet-Key-Source (python3 -c "import secrets; print(secrets.token_hex(32))")
# WICHTIG: Wenn dieser Key verloren geht, sind alle verschlüsselten
# Garmin-Credentials unwiederbringlich weg!
SECRET_KEY=dein-langer-zufaelliger-hex-string

# Sync-API-Key für den Cronjob (python3 -c "import secrets; print(secrets.token_hex(32))")
SYNC_API_KEY=dein-sync-api-key

# Admin-Account (wird beim ersten Start automatisch angelegt)
ADMIN_USER=dein-admin-username
ADMIN_PASS=dein-sicheres-passwort

# Trail Atlas API (lokal)
API_BASE=http://127.0.0.1:8000

# ─── Optional ───────────────────────────────────────────────

# Garmin-Credentials für den Admin (nur beim ersten Start verwendet, dann
# verschlüsselt in der DB gespeichert). Weitere User registrieren ihre Garmin-
# Credentials über die App.
GARMIN_EMAIL=deine@email.com
GARMIN_PASSWORD=dein-garmin-passwort
```

> **Hinweis:** `GARMIN_EMAIL`/`GARMIN_PASSWORD` sind seit Backend 1.7.0 optional. Sie werden nur beim ersten Backend-Start ausgewertet: falls der Admin-Account noch keine Garmin-Credentials in der DB hat, werden sie aus diesen Env-Vars Fernet-verschlüsselt in die DB geschrieben. Danach kann der Admin sie über die App ändern. Weitere User hinterlegen ihre eigenen Garmin-Credentials beim Signup.

### 2.5 Systemd Service

```bash
sudo cp trail-atlas.service /etc/systemd/system/
```

**Wichtig:** Der Service muss die `garmin.env` als Umgebungsvariablen laden:

```bash
sudo systemctl edit trail-atlas
```

Folgenden Inhalt einfügen:

```ini
[Service]
EnvironmentFile=/etc/trail-atlas/garmin.env
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable trail-atlas
sudo systemctl start trail-atlas
curl http://127.0.0.1:8000/health   # → {"status":"ok","version":"1.8.0"}
```

Prüfe im Log ob der Admin-Account erstellt wurde:

```bash
sudo journalctl -u trail-atlas -n 30
# → "Database initialized (schema v4)"
# → "User created: dein-username (id=1, admin=True)"
# → "Garmin credentials from garmin.env migrated to DB for admin (user_id=1)"
#   (nur wenn GARMIN_EMAIL/_PASSWORD gesetzt waren)
```

### 2.6 Nginx API-Proxy

Die `/api/` Location muss im HTTPS server{} Block stehen (siehe `nginx_api_snippet.conf`).

```bash
sudo nginx -t && sudo systemctl reload nginx
```

---

## Phase 3 – Garmin Sync

### 3.1 Dependencies + Script installieren

```bash
sudo /opt/trail-atlas/venv/bin/pip install garminconnect requests
sudo mkdir -p /opt/trail-atlas/garmin-sync
sudo cp garmin_sync.py /opt/trail-atlas/garmin-sync/
sudo chown -R trail-atlas:trail-atlas /opt/trail-atlas/garmin-sync
sudo chmod 750 /opt/trail-atlas/garmin-sync/garmin_sync.py
```

### 3.2 Initial-Test

Schneller Config-Check (kein Garmin-Login, nur Decryption-Test):

```bash
sudo -u trail-atlas /opt/trail-atlas/venv/bin/python3 \
  /opt/trail-atlas/garmin-sync/garmin_sync.py --check
```

Output:
```
⛰  Trail Atlas Garmin Sync (Multi-User)
   API: http://127.0.0.1:8000
   ✓ Config OK, API erreichbar
   ✓ 1 User mit Garmin-Credentials:
     · admin (id=1, email=deine@email.com)
   ✓ Alle Credentials entschlüsselbar
   Check abgeschlossen in 0.4s
```

Echter Sync-Test (loggt sich bei Garmin ein):

```bash
sudo -u trail-atlas /opt/trail-atlas/venv/bin/python3 \
  /opt/trail-atlas/garmin-sync/garmin_sync.py --limit 1
```

### 3.3 Cronjob + Logrotate

```bash
sudo tee /etc/cron.d/trail-atlas-garmin-sync << 'EOF'
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 3 * * * trail-atlas /opt/trail-atlas/venv/bin/python3 /opt/trail-atlas/garmin-sync/garmin_sync.py >> /var/log/trail-atlas/garmin_sync.log 2>&1
EOF

sudo tee /etc/logrotate.d/trail-atlas << 'EOF'
/var/log/trail-atlas/*.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
    su trail-atlas trail-atlas
}
EOF
```

---

## Phase 4 – CI/CD (GitHub Actions)

### 4.1 Repository erstellen

Neues GitHub Repo `trail-atlas` (Public oder Private) mit Ordnerstruktur:

```
.github/workflows/deploy.yml
backend/
garmin-sync/
scripts/
src/
docs/
```

### 4.2 SSH Deploy-Key

```bash
ssh-keygen -t ed25519 -C "github-actions" -f ~/.ssh/github_deploy -N ""
cat ~/.ssh/github_deploy.pub >> ~/.ssh/authorized_keys
cat ~/.ssh/github_deploy   # → als Secret speichern
```

### 4.3 GitHub Secrets

Repository → Settings → Secrets → Actions:

| Secret | Wert |
|--------|------|
| `SSH_PRIVATE_KEY` | Inhalt von `~/.ssh/github_deploy` |
| `VM_HOST` | `trail-atlas.duckdns.org` |
| `VM_USER` | `ubuntu` (oder dein SSH-User) |

> **Hinweis:** `BASIC_AUTH_USER` und `BASIC_AUTH_PASS` werden nicht mehr benötigt (Basic Auth entfernt seit v1.6.0).

### 4.4 Sudoers

```bash
sudo visudo -f /etc/sudoers.d/trail-atlas
# → Inhalt aus scripts/sudoers_trail_atlas.txt einfügen, DEIN_USER ersetzen
```

### 4.5 VM-Verzeichnisse für Staging

```bash
mkdir -p ~/trail-atlas/{src,backend,garmin-sync,scripts}
```

### 4.6 Operations Helper installieren

```bash
sudo cp scripts/ops.sh /usr/local/bin/trail-atlas
sudo chmod +x /usr/local/bin/trail-atlas
trail-atlas status
```

Ab dem ersten Push aktualisiert der `deploy-ops-cli` Job das CLI automatisch.

---

## Verifikation

```bash
trail-atlas status        # alle Komponenten grün?
trail-atlas health        # API + HTTPS erreichbar?

# Garmin-Sync prüfen:
sudo -u trail-atlas /opt/trail-atlas/venv/bin/python3 \
  /opt/trail-atlas/garmin-sync/garmin_sync.py --check    # schneller Config-Check
sudo -u trail-atlas /opt/trail-atlas/venv/bin/python3 \
  /opt/trail-atlas/garmin-sync/garmin_sync.py --limit 1  # 1 Tour real importieren

trail-atlas db stats      # Daten in der DB?
```

Im Browser: `https://trail-atlas.duckdns.org` → Login mit `ADMIN_USER`/`ADMIN_PASS` → Karte mit Tracks.

### Erste Schritte als Admin

1. Im Admin-Tab (vierter Reiter, nur für is_admin sichtbar) sind alle User aufgelistet
2. Über "Einladungscode generieren" (Import-Tab) Freunde einladen
3. Beim Signup können neue User ihre Garmin-Credentials direkt angeben
4. Per Admin-Tab kann der Garmin-Account später geändert/entfernt werden

---

## Häufige Setup-Probleme

Siehe [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md), insbesondere:
- "SECRET_KEY / ADMIN_USER nicht gesetzt" → EnvironmentFile nicht geladen
- "Permission denied: garmin.env" → falsche Owner/Mode
- "Login schlägt fehl / MFA-Problem" → Garmin-spezifische Sperren
