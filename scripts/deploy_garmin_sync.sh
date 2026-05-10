#!/usr/bin/env bash
# =============================================================================
# deploy_garmin_sync.sh – Sync-Script Deploy
# =============================================================================
# Einfach: kopiert garmin_sync.py nach /opt/trail-atlas/garmin-sync/.
# Cronjob lädt das Script bei jedem Lauf neu, also kein Restart nötig.
# =============================================================================

set -euo pipefail

green()  { echo -e "\033[0;32m$*\033[0m"; }
red()    { echo -e "\033[0;31m$*\033[0m"; }

STAGING_DIR="$HOME/trail-atlas/garmin-sync"
INSTALL_DIR="/opt/trail-atlas/garmin-sync"

echo ""
echo "⛰  Trail Atlas – Garmin Sync Deploy"
echo "══════════════════════════════════════════"

# ── Validierung ──────────────────────────────────────────────────────────────
if [ ! -f "$STAGING_DIR/garmin_sync.py" ]; then
    red "❌  $STAGING_DIR/garmin_sync.py fehlt"
    exit 1
fi

# Syntax-Check
if ! /opt/trail-atlas/venv/bin/python3 -c "
import ast
with open('$STAGING_DIR/garmin_sync.py') as f:
    ast.parse(f.read())
"; then
    red "❌  Syntax-Fehler in garmin_sync.py"
    exit 1
fi
green "✓  Syntax OK"

# ── Deploy ───────────────────────────────────────────────────────────────────
echo ""
echo "📋  Files installieren…"
sudo mkdir -p "$INSTALL_DIR"
sudo cp "$STAGING_DIR/garmin_sync.py" "$INSTALL_DIR/"

# garmin.env.example nur kopieren wenn noch keine echte env existiert
if [ -f "$STAGING_DIR/garmin.env.example" ]; then
    sudo cp "$STAGING_DIR/garmin.env.example" "$INSTALL_DIR/"
fi

sudo chown -R trail-atlas:trail-atlas "$INSTALL_DIR"
sudo chmod 750 "$INSTALL_DIR/garmin_sync.py"
green "  ✓  $INSTALL_DIR/garmin_sync.py"

# ── Smoke-Test (Dry-Run) ─────────────────────────────────────────────────────
echo ""
echo "🔍  Dry-Run Test…"
if sudo -u trail-atlas /opt/trail-atlas/venv/bin/python3 \
    "$INSTALL_DIR/garmin_sync.py" --dry-run --limit 1 2>&1 | tail -5; then
    green "  ✓  Dry-Run erfolgreich"
else
    red "  ⚠  Dry-Run hatte Probleme – Logs prüfen"
fi

LOG="$HOME/trail-atlas/deploy.log"
echo "$(date '+%Y-%m-%d %H:%M:%S') | garmin-sync | OK" >> "$LOG"

echo ""
echo "══════════════════════════════════════════"
green "✅  Garmin Sync Deploy erfolgreich"
echo ""
