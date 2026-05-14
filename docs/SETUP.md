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
sudo apt install -y nginx certbot python3-certbot-nginx apache2-utils

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

### 1.4 Basic Auth

```bash
sudo htpasswd -c /etc/nginx/.htpasswd deinname
```

### 1.5 Vollständige Nginx Config

Ersetze `/etc/nginx/sites-available/trail-atlas` mit der Production-Config (siehe `trail-atlas-v2.nginx.conf` im Repo). Wichtige Bestandteile: HTTPS, Basic Auth, Security-Header, Gzip, `/api/` Proxy, `/libs/` Caching.

```bash
sudo nginx -t && sudo systemctl reload nginx
```

---

## Phase 2 – Backend (FastAPI + SQLite)

### 2.1 System-User + Verzeichnisse

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin trail-atlas
sudo mkdir -p /opt/trail-atlas/backend /var/lib/trail-atlas /var/log/trail-atlas
sudo chown trail-atlas:trail-atlas /var/lib/trail-atlas /var/log/trail-atlas
sudo chmod 750 /var/lib/trail-atlas
```

### 2.2 Python Environment

```bash
sudo apt install -y python3-venv python3-pip sqlite3
python3 -m venv /opt/trail-atlas/venv
sudo /opt/trail-atlas/venv/bin/pip install fastapi uvicorn python-multipart
```

### 2.3 Backend-Code deployen

```bash
# Dateien kopieren (initial manuell, danach via GitHub Actions)
sudo cp main.py database.py /opt/trail-atlas/backend/
sudo chown -R trail-atlas:trail-atlas /opt/trail-atlas
```

### 2.4 Systemd Service

```bash
sudo cp trail-atlas.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable trail-atlas
sudo systemctl start trail-atlas
curl http://127.0.0.1:8000/health   # → {"status":"ok",...}
```

### 2.5 Nginx API-Proxy

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

### 3.2 Credentials

```bash
sudo mkdir -p /etc/trail-atlas
sudo cp garmin.env.example /etc/trail-atlas/garmin.env
sudo chown -R trail-atlas:trail-atlas /etc/trail-atlas
sudo chmod 750 /etc/trail-atlas
sudo chmod 600 /etc/trail-atlas/garmin.env
sudo nano /etc/trail-atlas/garmin.env
# → GARMIN_EMAIL, GARMIN_PASSWORD, API_USER, API_PASS eintragen
```

### 3.3 Initial-Test

```bash
sudo -u trail-atlas /opt/trail-atlas/venv/bin/python3 \
  /opt/trail-atlas/garmin-sync/garmin_sync.py --limit 1
```

### 3.4 Cronjob + Logrotate

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
| `BASIC_AUTH_USER` | Nginx Basic Auth User |
| `BASIC_AUTH_PASS` | Nginx Basic Auth Passwort |

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

---

## Verifikation

```bash
trail-atlas status    # alle Komponenten grün?
trail-atlas health    # API + HTTPS erreichbar?
trail-atlas sync 1    # Garmin-Sync funktioniert?
trail-atlas db stats  # Daten in der DB?
```

Im Browser: `https://trail-atlas.duckdns.org` → Login → Karte mit Tracks.
