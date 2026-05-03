#!/usr/bin/env bash
# =============================================================================
# setup_backend.sh  –  Trail Atlas Backend Setup
# =============================================================================
# Einmalige Installation auf der VM.
#
# Verwendung:
#   chmod +x setup_backend.sh
#   sudo bash setup_backend.sh
#
# Was dieses Script macht:
#   1. System-User 'trail-atlas' erstellen
#   2. Python venv + Dependencies installieren
#   3. Backend-Dateien nach /opt/trail-atlas/backend/ kopieren
#   4. DB-Verzeichnis erstellen
#   5. Systemd Service einrichten + starten
#   6. Nginx-Config um API-Proxy ergänzen
# =============================================================================

set -euo pipefail

BACKEND_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/trail-atlas"
BACKEND_DIR="$INSTALL_DIR/backend"
VENV_DIR="$INSTALL_DIR/venv"
DB_DIR="/var/lib/trail-atlas"
SERVICE_FILE="/etc/systemd/system/trail-atlas.service"
NGINX_SITE="/etc/nginx/sites-available/trail-atlas"

green()  { echo -e "\033[0;32m$*\033[0m"; }
yellow() { echo -e "\033[0;33m$*\033[0m"; }
red()    { echo -e "\033[0;31m$*\033[0m"; }

echo ""
echo "⛰  Trail Atlas – Backend Setup"
echo "══════════════════════════════════════════"

# ── 1. System-User ────────────────────────────────────────────────────────────
echo ""
echo "👤  System-User erstellen…"
if id "trail-atlas" &>/dev/null; then
    yellow "  ↷  User 'trail-atlas' existiert bereits"
else
    useradd --system --no-create-home --shell /usr/sbin/nologin trail-atlas
    green "  ✓  User 'trail-atlas' erstellt"
fi

# ── 2. Verzeichnisse ──────────────────────────────────────────────────────────
echo ""
echo "📁  Verzeichnisse erstellen…"
mkdir -p "$BACKEND_DIR" "$DB_DIR"
chown trail-atlas:trail-atlas "$DB_DIR"
chmod 750 "$DB_DIR"
green "  ✓  $BACKEND_DIR"
green "  ✓  $DB_DIR"

# ── 3. Python + venv ──────────────────────────────────────────────────────────
echo ""
echo "🐍  Python venv einrichten…"
apt-get install -y python3-venv python3-pip -qq

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$BACKEND_SRC/requirements.txt"
green "  ✓  venv: $VENV_DIR"

# ── 4. Backend-Dateien kopieren ───────────────────────────────────────────────
echo ""
echo "📋  Dateien installieren…"
cp "$BACKEND_SRC/main.py"     "$BACKEND_DIR/"
cp "$BACKEND_SRC/database.py" "$BACKEND_DIR/"
chown -R trail-atlas:trail-atlas "$INSTALL_DIR"
green "  ✓  main.py, database.py → $BACKEND_DIR"

# ── 5. Systemd Service ────────────────────────────────────────────────────────
echo ""
echo "⚙️   Systemd Service einrichten…"
cp "$BACKEND_SRC/trail-atlas.service" "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable trail-atlas
systemctl restart trail-atlas
sleep 2

if systemctl is-active --quiet trail-atlas; then
    green "  ✓  Service läuft"
else
    red "  ❌  Service startet nicht – Log prüfen:"
    echo "      journalctl -u trail-atlas -n 30"
    exit 1
fi

# ── 6. Nginx API-Proxy ergänzen ───────────────────────────────────────────────
echo ""
echo "🌐  Nginx konfigurieren…"

if grep -q "location /api/" "$NGINX_SITE" 2>/dev/null; then
    yellow "  ↷  /api/ Location bereits in Nginx vorhanden"
else
    # API-Snippet vor der schließenden } des server-Blocks einfügen
    API_SNIPPET=$(cat "$BACKEND_SRC/nginx_api_snippet.conf")
    # Füge den Snippet vor der letzten } ein
    python3 - << PYEOF
with open("$NGINX_SITE", "r") as f:
    content = f.read()

snippet = open("$BACKEND_SRC/nginx_api_snippet.conf").read()

# Vor der letzten schließenden Klammer einfügen
last_brace = content.rfind("}")
new_content = content[:last_brace] + "\n" + snippet + "\n" + content[last_brace:]

with open("$NGINX_SITE", "w") as f:
    f.write(new_content)
print("  ✓  Nginx API-Proxy eingetragen")
PYEOF
fi

# Nginx testen + reload
if nginx -t 2>/dev/null; then
    systemctl reload nginx
    green "  ✓  Nginx neu geladen"
else
    red "  ❌  Nginx config ungültig"
    nginx -t
    exit 1
fi

# ── 7. Health-Check ───────────────────────────────────────────────────────────
echo ""
echo "🔍  Health-Check…"
sleep 1
HEALTH=$(curl -s http://127.0.0.1:8000/health 2>/dev/null || echo "error")
if echo "$HEALTH" | grep -q '"ok"'; then
    green "  ✓  API antwortet: $HEALTH"
else
    red "  ❌  API antwortet nicht: $HEALTH"
    echo "      journalctl -u trail-atlas -n 20"
    exit 1
fi

# ── Fertig ────────────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════"
green "✅  Backend Setup abgeschlossen!"
echo ""
echo "📋  Nützliche Befehle:"
echo "    sudo systemctl status trail-atlas     # Service-Status"
echo "    sudo journalctl -u trail-atlas -f     # Live-Logs"
echo "    curl http://127.0.0.1:8000/health     # Health-Check"
echo "    curl http://127.0.0.1:8000/db/stats   # DB-Statistiken"
echo ""
echo "📖  Swagger UI (intern):"
echo "    http://127.0.0.1:8000/api/docs"
echo ""
echo "👉  Nächster Schritt: CSV-Daten importieren"
echo "    curl -X POST https://trail-atlas.duckdns.org/api/import/summary \\"
echo "      -u 'user:pass' -F 'file=@activity_summary.csv'"
echo ""
