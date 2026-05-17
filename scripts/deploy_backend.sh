#!/usr/bin/env bash
# =============================================================================
# deploy_backend.sh – Backend Deploy mit DB-Backup und Auto-Rollback
# =============================================================================
# Wird von GitHub Actions remote ausgeführt.
#
# Ablauf:
#   1. DB-Backup erstellen
#   2. Aktuelle Backend-Files sichern (für Rollback)
#   3. Neue Files kopieren
#   4. Service neustart
#   5. Health-Check
#   6. Bei Fehler: alte Files restoren + restart
# =============================================================================

set -euo pipefail

green()  { echo -e "\033[0;32m$*\033[0m"; }
yellow() { echo -e "\033[0;33m$*\033[0m"; }
red()    { echo -e "\033[0;31m$*\033[0m"; }

STAGING_DIR="$HOME/trail-atlas/backend"
BACKEND_DIR="/opt/trail-atlas/backend"
BACKUP_DIR="/var/lib/trail-atlas/backups"
DB_FILE="/var/lib/trail-atlas/trail_atlas.db"
SERVICE="trail-atlas"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')

echo ""
echo "⛰  Trail Atlas – Backend Deploy"
echo "══════════════════════════════════════════"

# ── 1. Validierung der Staging-Files ─────────────────────────────────────────
for required in main.py database.py auth.py; do
    if [ ! -f "$STAGING_DIR/$required" ]; then
        red "❌  $STAGING_DIR/$required fehlt"
        exit 1
    fi
done
green "✓  Staging-Files vorhanden"

# Python-Syntax prüfen bevor wir live deployen
for f in "$STAGING_DIR"/*.py; do
    if ! /opt/trail-atlas/venv/bin/python3 -c "
import ast, sys
with open('$f') as fh:
    try:
        ast.parse(fh.read())
    except SyntaxError as e:
        print(f'Syntax error in {fh.name}: {e}', file=sys.stderr)
        sys.exit(1)
" 2>&1; then
        red "❌  Syntax-Fehler in $f"
        exit 1
    fi
done
green "✓  Syntax-Check passed"

# ── 2. DB-Backup ──────────────────────────────────────────────────────────────
echo ""
echo "💾  DB-Backup…"
sudo -u trail-atlas mkdir -p "$BACKUP_DIR"
DB_BACKUP="$BACKUP_DIR/trail_atlas_${TIMESTAMP}.db"
sudo -u trail-atlas sqlite3 "$DB_FILE" ".backup '$DB_BACKUP'" 2>/dev/null || {
    # Fallback wenn sqlite3 CLI nicht verfügbar
    sudo cp "$DB_FILE" "$DB_BACKUP"
    sudo chown trail-atlas:trail-atlas "$DB_BACKUP"
}
DB_SIZE=$(sudo du -sh "$DB_BACKUP" 2>/dev/null | cut -f1 || echo "?")
green "  ✓  $DB_BACKUP  ($DB_SIZE)"

# Alte Backups löschen (behalte letzte 10)
sudo -u trail-atlas bash -c "ls -t $BACKUP_DIR/trail_atlas_*.db 2>/dev/null | tail -n +11 | xargs -r rm -f"

# ── 3. Code-Backup für Rollback ──────────────────────────────────────────────
ROLLBACK_DIR="$BACKUP_DIR/code_${TIMESTAMP}"
sudo mkdir -p "$ROLLBACK_DIR"
sudo cp -r "$BACKEND_DIR"/*.py "$ROLLBACK_DIR/" 2>/dev/null || true
green "  ✓  Code-Backup für Rollback: $ROLLBACK_DIR"

# ── 4. Neue Files deployen ───────────────────────────────────────────────────
echo ""
echo "📋  Neue Files installieren…"
sudo cp "$STAGING_DIR"/*.py "$BACKEND_DIR/"
if [ -f "$STAGING_DIR/requirements.txt" ]; then
    sudo cp "$STAGING_DIR/requirements.txt" "$BACKEND_DIR/"
    # Falls requirements geändert wurden, neu installieren
    sudo /opt/trail-atlas/venv/bin/pip install --quiet -r "$BACKEND_DIR/requirements.txt" || \
        yellow "  ⚠  pip install hatte Probleme – läuft trotzdem weiter"
fi
sudo chown -R trail-atlas:trail-atlas "$BACKEND_DIR"
sudo chmod 644 "$BACKEND_DIR"/*.py "$BACKEND_DIR"/*.txt 2>/dev/null || true
green "  ✓  Files kopiert"

# ── 5. Service neustarten ────────────────────────────────────────────────────
echo ""
echo "🔄  Service-Restart…"
sudo systemctl restart "$SERVICE"
sleep 3

# ── 6. Health-Check mit Rollback bei Fehlschlag ──────────────────────────────
echo ""
echo "🔍  Health-Check…"
HEALTH=$(curl -sf http://127.0.0.1:8000/health 2>/dev/null || echo "fail")

if echo "$HEALTH" | grep -q '"ok"'; then
    green "  ✓  API antwortet: $HEALTH"
    echo ""
    echo "══════════════════════════════════════════"
    green "✅  Deploy erfolgreich"

    # Log
    LOG="$HOME/trail-atlas/deploy.log"
    echo "$(date '+%Y-%m-%d %H:%M:%S') | backend | OK | $TIMESTAMP" >> "$LOG"

else
    red "  ❌  Health-Check fehlgeschlagen: $HEALTH"
    echo ""
    yellow "🔙  ROLLBACK wird ausgeführt…"

    sudo cp "$ROLLBACK_DIR"/*.py "$BACKEND_DIR/" 2>/dev/null || true
    sudo chown -R trail-atlas:trail-atlas "$BACKEND_DIR"
    sudo systemctl restart "$SERVICE"
    sleep 3

    HEALTH2=$(curl -sf http://127.0.0.1:8000/health 2>/dev/null || echo "fail")
    if echo "$HEALTH2" | grep -q '"ok"'; then
        yellow "  ✓  Rollback erfolgreich – alter Stand wiederhergestellt"
    else
        red "  ❌  Auch Rollback fehlgeschlagen – manuelle Intervention nötig!"
        red "      Logs: sudo journalctl -u trail-atlas -n 50"
    fi

    echo "$(date '+%Y-%m-%d %H:%M:%S') | backend | FAIL_ROLLBACK | $TIMESTAMP" >> "$HOME/trail-atlas/deploy.log"
    exit 1
fi
echo ""
