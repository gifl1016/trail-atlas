#!/usr/bin/env bash
# =============================================================================
# ops.sh – Trail Atlas Tägliche Operations Helper
# =============================================================================
# Eine zentrale Stelle für die häufigsten Befehle.
#
# Installation auf VM:
#   sudo cp ops.sh /usr/local/bin/trail-atlas
#   sudo chmod +x /usr/local/bin/trail-atlas
#
# Verwendung:
#   trail-atlas status         # Übersicht aller Komponenten
#   trail-atlas logs           # Backend-Logs (live)
#   trail-atlas logs sync      # Garmin-Sync Logs (live)
#   trail-atlas logs nginx     # Nginx Logs (live)
#   trail-atlas sync           # Manueller Sync jetzt
#   trail-atlas sync 1         # Test-Sync mit nur 1 Tour
#   trail-atlas sync dry       # Dry-Run
#   trail-atlas restart        # Backend-Service neustart
#   trail-atlas db stats       # DB-Statistik
#   trail-atlas db backup      # DB-Backup mit Timestamp
#   trail-atlas db shell       # Interaktive SQLite-Shell
#   trail-atlas health         # Health-Check API + Frontend
#   trail-atlas help           # Diese Hilfe
# =============================================================================

set -euo pipefail

# ── Konfiguration ─────────────────────────────────────────────────────────────
BACKEND_DIR="/opt/trail-atlas/backend"
GARMIN_SYNC="/opt/trail-atlas/garmin-sync/garmin_sync.py"
VENV_PY="/opt/trail-atlas/venv/bin/python3"
DB_FILE="/var/lib/trail-atlas/trail_atlas.db"
BACKUP_DIR="/var/lib/trail-atlas/backups"
LOG_DIR="/var/log/trail-atlas"
SERVICE="trail-atlas"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
ENV_FILE="/etc/trail-atlas/garmin.env"

# Sync-API-Key aus garmin.env laden (für authentifizierte API-Calls)
SYNC_API_KEY=""
if [ -r "$ENV_FILE" ]; then
    SYNC_API_KEY=$(grep '^SYNC_API_KEY=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
fi

# Authentifizierter curl-Wrapper
api_curl() {
    if [ -n "$SYNC_API_KEY" ]; then
        curl -sf -H "X-Sync-Key: $SYNC_API_KEY" "$@" 2>/dev/null
    else
        curl -sf "$@" 2>/dev/null
    fi
}

# ── Farben ────────────────────────────────────────────────────────────────────
green()  { echo -e "\033[0;32m$*\033[0m"; }
yellow() { echo -e "\033[0;33m$*\033[0m"; }
red()    { echo -e "\033[0;31m$*\033[0m"; }
blue()   { echo -e "\033[0;34m$*\033[0m"; }
bold()   { echo -e "\033[1m$*\033[0m"; }

# ── Sub-Commands ─────────────────────────────────────────────────────────────

cmd_status() {
    bold "⛰  Trail Atlas – System Status"
    echo "══════════════════════════════════════════"

    # Backend Service
    if systemctl is-active --quiet "$SERVICE"; then
        green "✓  Backend:    läuft"
        UPTIME=$(systemctl show "$SERVICE" --property=ActiveEnterTimestamp --value)
        echo "   Seit:       $UPTIME"
    else
        red "✗  Backend:    NICHT aktiv"
    fi

    # Nginx
    if systemctl is-active --quiet nginx; then
        green "✓  Nginx:      läuft"
    else
        red "✗  Nginx:      NICHT aktiv"
    fi

    # Cron
    if systemctl is-active --quiet cron 2>/dev/null || systemctl is-active --quiet crond 2>/dev/null; then
        green "✓  Cron:       läuft"
    else
        red "✗  Cron:       NICHT aktiv"
    fi

    # API Health
    HEALTH=$(curl -sf http://127.0.0.1:8000/health 2>/dev/null || echo "fail")
    if echo "$HEALTH" | grep -q '"ok"'; then
        VERSION=$(echo "$HEALTH" | grep -oP '"version":"\K[^"]+')
        green "✓  API:        antwortet  (v$VERSION)"
    else
        red "✗  API:        antwortet nicht"
    fi

    echo ""
    bold "📊 Datenbank"
    if [ -f "$DB_FILE" ]; then
        DB_SIZE=$(sudo du -sh "$DB_FILE" | cut -f1)
        echo "   Datei:      $DB_FILE  ($DB_SIZE)"

        STATS=$(api_curl http://127.0.0.1:8000/db/stats || echo "{}")
        if [ "$STATS" != "{}" ]; then
            ACTS=$(echo "$STATS" | grep -oP '"activities":\K\d+')
            GPS=$(echo  "$STATS" | grep -oP '"gps_points":\K\d+')
            NOGPS=$(echo "$STATS" | grep -oP '"activities_no_gps":\K\d+')
            echo "   Activities: $ACTS  (davon ohne GPS: $NOGPS)"
            echo "   GPS Points: $(printf "%'d" $GPS 2>/dev/null || echo $GPS)"
        fi
    else
        red "   DB-Datei nicht gefunden"
    fi

    echo ""
    bold "🔄 Letzter Garmin Sync"
    SYNC=$(api_curl http://127.0.0.1:8000/sync/status || echo "{}")
    if [ "$SYNC" != "{}" ]; then
        LAST=$(echo "$SYNC" | grep -oP '"started_at":"\K[^"]+' | head -1)
        STATUS=$(echo "$SYNC" | grep -oP '"status":"\K[^"]+' | head -1)
        IMP=$(echo "$SYNC" | grep -oP '"activities_imported":\K\d+' | head -1)
        if [ -n "$LAST" ]; then
            if [ "$STATUS" = "ok" ]; then
                green "   ✓  $LAST  ($IMP neue Touren)"
            else
                red   "   ✗  $LAST  (Status: $STATUS)"
            fi
        else
            yellow "   noch nie gelaufen"
        fi
    fi

    echo ""
    bold "💾 Backups"
    if [ -d "$BACKUP_DIR" ]; then
        COUNT=$(sudo ls "$BACKUP_DIR"/trail_atlas_*.db 2>/dev/null | wc -l)
        NEWEST=$(sudo ls -t "$BACKUP_DIR"/trail_atlas_*.db 2>/dev/null | head -1 | xargs -I{} basename {} 2>/dev/null || echo "keine")
        echo "   Anzahl:     $COUNT"
        echo "   Neuestes:   $NEWEST"
    else
        yellow "   Kein Backup-Verzeichnis gefunden"
    fi
    echo ""
}

cmd_logs() {
    local what="${1:-backend}"
    case "$what" in
        backend|""|api)
            blue "📋  Backend-Logs (Strg+C zum Beenden)"
            sudo journalctl -u "$SERVICE" -f
            ;;
        sync|garmin)
            blue "📋  Garmin-Sync Logs (Strg+C zum Beenden)"
            sudo tail -f "$LOG_DIR/garmin_sync.log"
            ;;
        nginx)
            blue "📋  Nginx Access Log (Strg+C zum Beenden)"
            sudo tail -f /var/log/nginx/trail-atlas.access.log
            ;;
        nginx-error)
            blue "📋  Nginx Error Log (Strg+C zum Beenden)"
            sudo tail -f /var/log/nginx/trail-atlas.error.log
            ;;
        *)
            red "Unbekannt: '$what'"
            echo "Optionen: backend | sync | nginx | nginx-error"
            exit 1
            ;;
    esac
}

cmd_sync() {
    local arg="${1:-}"
    local cmd="$VENV_PY $GARMIN_SYNC"
    case "$arg" in
        dry|dry-run)
            cmd="$cmd --dry-run"
            blue "🔬  Garmin Sync (Dry-Run)…"
            ;;
        [0-9]*)
            cmd="$cmd --limit $arg"
            blue "🔄  Garmin Sync (--limit $arg)…"
            ;;
        full)
            cmd="$cmd --full-resync"
            yellow "⚠  Full-Resync – kann lange dauern"
            ;;
        "")
            blue "🔄  Garmin Sync (alle neuen Touren)…"
            ;;
        *)
            red "Unbekannt: '$arg'"
            echo "Optionen: <Zahl> | dry | full | (leer für alles)"
            exit 1
            ;;
    esac
    sudo -u trail-atlas bash -c "$cmd 2>&1 | tee -a $LOG_DIR/garmin_sync.log"
}

cmd_restart() {
    blue "🔄  Backend neustart…"
    sudo systemctl restart "$SERVICE"
    sleep 2
    if systemctl is-active --quiet "$SERVICE"; then
        green "✓  Service läuft"
        cmd_health
    else
        red "✗  Service startet nicht – Logs prüfen:"
        echo "   trail-atlas logs"
        exit 1
    fi
}

cmd_db() {
    local sub="${1:-stats}"
    case "$sub" in
        stats)
            api_curl http://127.0.0.1:8000/db/stats | python3 -m json.tool
            ;;
        backup)
            sudo -u trail-atlas mkdir -p "$BACKUP_DIR"
            BACKUP="$BACKUP_DIR/trail_atlas_${TIMESTAMP}_manual.db"
            blue "💾  Erstelle Backup…"
            sudo -u trail-atlas sqlite3 "$DB_FILE" ".backup '$BACKUP'"
            SIZE=$(sudo du -sh "$BACKUP" | cut -f1)
            green "✓  $BACKUP  ($SIZE)"
            ;;
        backups)
            blue "💾  Vorhandene Backups:"
            sudo ls -lh "$BACKUP_DIR"/trail_atlas_*.db 2>/dev/null | awk '{print "   "$9"  ("$5")"}'
            ;;
        shell)
            blue "🐚  Öffne SQLite Shell (.quit zum Beenden)"
            sudo -u trail-atlas sqlite3 "$DB_FILE"
            ;;
        *)
            red "Unbekannt: '$sub'"
            echo "Optionen: stats | backup | backups | shell"
            exit 1
            ;;
    esac
}

cmd_health() {
    bold "🩺  Health Check"
    echo "──────────────────────────────────────"
    HEALTH=$(curl -sf http://127.0.0.1:8000/health 2>/dev/null || echo "fail")
    if echo "$HEALTH" | grep -q '"ok"'; then
        green "✓  API:      $HEALTH"
    else
        red "✗  API:      Antwortet nicht"
    fi

    DOMAIN=$(grep -E "server_name" /etc/nginx/sites-available/trail-atlas 2>/dev/null | grep -v "#" | head -1 | awk '{print $2}' | tr -d ';' || echo "localhost")
    if curl -sf -o /dev/null "https://$DOMAIN" 2>/dev/null; then
        green "✓  HTTPS:    $DOMAIN antwortet"
    else
        # 401 ist OK – heißt Auth läuft
        CODE=$(curl -s -o /dev/null -w "%{http_code}" "https://$DOMAIN" 2>/dev/null)
        if [ "$CODE" = "401" ]; then
            green "✓  HTTPS:    $DOMAIN antwortet (401 = Auth aktiv ✓)"
        else
            red "✗  HTTPS:    $DOMAIN antwortet nicht (HTTP $CODE)"
        fi
    fi
    echo ""
}

cmd_help() {
    cat <<EOF

⛰  Trail Atlas – Operations Helper

VERWENDUNG:
    trail-atlas <command> [args]

COMMANDS:
    status              System-Übersicht (Services, DB, Sync)
    health              Quick Health-Check

    logs [target]       Live-Logs anzeigen
                        Targets: backend (default), sync, nginx, nginx-error

    sync [arg]          Garmin Sync starten
                        arg: leer (alle neuen) | <Zahl> | dry | full

    restart             Backend-Service neustart

    db <sub>            Datenbank-Operationen
                        Subs: stats | backup | backups | shell

    help                Diese Hilfe

BEISPIELE:
    trail-atlas status
    trail-atlas logs sync
    trail-atlas sync 1
    trail-atlas db backup
    trail-atlas db shell

EOF
}

# ── Main ─────────────────────────────────────────────────────────────────────
CMD="${1:-help}"
shift || true

case "$CMD" in
    status)         cmd_status         ;;
    logs)           cmd_logs   "$@"    ;;
    sync)           cmd_sync   "$@"    ;;
    restart)        cmd_restart        ;;
    db)             cmd_db     "$@"    ;;
    health)         cmd_health         ;;
    help|--help|-h) cmd_help           ;;
    *)
        red "Unbekanntes Kommando: $CMD"
        echo ""
        cmd_help
        exit 1
        ;;
esac
