#!/usr/bin/env bash
# =============================================================================
# deploy.sh  –  Trail Atlas Server-seitiges Deploy-Script
# =============================================================================
# Wird von GitHub Actions remote via SSH aufgerufen.
# Kann auch manuell ausgeführt werden: bash ~/trail-atlas/scripts/deploy.sh
#
# Ablauf:
#   1. Neueste HTML-Datei in src/ finden
#   2. Libraries herunterladen (nur wenn nicht vorhanden oder --force)
#   3. SRI-Hashes berechnen
#   4. Platzhalter in HTML ersetzen
#   5. Nach /var/www/trail-atlas/ deployen
#   6. Nginx neu laden
# =============================================================================

set -euo pipefail

# ── Konfiguration ─────────────────────────────────────────────────────────────
WEB_ROOT="/var/www/trail-atlas"
LIBS_DIR="$WEB_ROOT/libs"
SRC_DIR="$HOME/trail-atlas/src"
FORCE_DOWNLOAD="${1:-}"   # --force um Libraries neu zu laden

LEAFLET_CSS_URL="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
LEAFLET_JS_URL="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
PAPAPARSE_URL="https://cdnjs.cloudflare.com/ajax/libs/PapaParse/5.4.1/papaparse.min.js"
DEXIE_URL="https://unpkg.com/dexie@3.2.4/dist/dexie.js"

# ── Farben ────────────────────────────────────────────────────────────────────
green()  { echo -e "\033[0;32m$*\033[0m"; }
yellow() { echo -e "\033[0;33m$*\033[0m"; }
red()    { echo -e "\033[0;31m$*\033[0m"; }

echo ""
echo "⛰  Trail Atlas – Deploy"
echo "══════════════════════════════════════════"
echo "  Zeitstempel: $(date '+%Y-%m-%d %H:%M:%S')"

# ── Neueste HTML-Datei finden ─────────────────────────────────────────────────
HTML_FILE=$(ls -t "$SRC_DIR"/garmin_trail_atlas_*.html 2>/dev/null | head -1 || true)

if [[ -z "$HTML_FILE" ]]; then
    red "❌  Keine HTML-Datei in $SRC_DIR gefunden"
    exit 1
fi

echo "  HTML: $(basename "$HTML_FILE")"

# Platzhalter-Check
#for placeholder in LEAFLET_CSS_SRI LEAFLET_JS_SRI PAPAPARSE_SRI DEXIE_SRI; do
#    if ! grep -q "$placeholder" "$HTML_FILE"; then
#        red "❌  Platzhalter '$placeholder' nicht in HTML gefunden."
#        echo "    Stelle sicher dass du die _local.html Variante verwendest."
#        exit 1
#    fi
#done

# ── Ordner erstellen ──────────────────────────────────────────────────────────
mkdir -p "$LIBS_DIR"

sudo chown -R $USER /var/www/trail-atlas 2>/dev/null || true
sudo mkdir -p "$LIBS_DIR"

# ── Libraries herunterladen (nur wenn nötig) ──────────────────────────────────
sri_hash() {
    echo -n "sha384-$(openssl dgst -sha384 -binary "$1" | openssl base64 -A)"
}

download_lib() {
    local url="$1" dest="$2" label="$3"
    if [[ -f "$dest" && "$FORCE_DOWNLOAD" != "--force" ]]; then
        yellow "  ↷  $label (gecacht)"
        return 0
    fi
    echo -n "  ↓  $label … "
    if curl -sSL --fail --retry 3 --retry-delay 2 -o "$dest" "$url"; then
        green "✓ ($(du -sh "$dest" | cut -f1))"
    else
        red "❌  Fehlgeschlagen: $url"
        exit 1
    fi
}

echo ""
echo "📥  Libraries…"
download_lib "$LEAFLET_CSS_URL" "$LIBS_DIR/leaflet.css"       "Leaflet CSS     "
download_lib "$LEAFLET_JS_URL"  "$LIBS_DIR/leaflet.js"        "Leaflet JS      "
download_lib "$PAPAPARSE_URL"   "$LIBS_DIR/papaparse.min.js"  "PapaParse       "
download_lib "$DEXIE_URL"       "$LIBS_DIR/dexie.js"          "Dexie           "

# ── SRI-Hashes berechnen ──────────────────────────────────────────────────────
echo ""
echo "🔑  SRI-Hashes…"
LEAFLET_CSS_SRI=$(sri_hash "$LIBS_DIR/leaflet.css")
LEAFLET_JS_SRI=$(sri_hash  "$LIBS_DIR/leaflet.js")
PAPAPARSE_SRI=$(sri_hash   "$LIBS_DIR/papaparse.min.js")
DEXIE_SRI=$(sri_hash       "$LIBS_DIR/dexie.js")

# Hashes zur Referenz speichern
cat > "$LIBS_DIR/sri_hashes.txt" << EOF
# Trail Atlas SRI Hashes – $(date '+%Y-%m-%d %H:%M:%S')
LEAFLET_CSS_SRI=$LEAFLET_CSS_SRI
LEAFLET_JS_SRI=$LEAFLET_JS_SRI
PAPAPARSE_SRI=$PAPAPARSE_SRI
DEXIE_SRI=$DEXIE_SRI
EOF
green "  ✓  Hashes berechnet und gespeichert"

# ── HTML patchen und deployen ─────────────────────────────────────────────────
echo ""
echo "📄  HTML patchen…"

python3 - << PYEOF
with open("$HTML_FILE", "r", encoding="utf-8") as f:
    html = f.read()

replacements = {
    "LEAFLET_CSS_SRI": "$LEAFLET_CSS_SRI",
    "LEAFLET_JS_SRI":  "$LEAFLET_JS_SRI",
    "PAPAPARSE_SRI":   "$PAPAPARSE_SRI",
    "DEXIE_SRI":       "$DEXIE_SRI",
}

for placeholder, value in replacements.items():
    html = html.replace(placeholder, value)

with open("$WEB_ROOT/index.html", "w", encoding="utf-8") as f:
    f.write(html)

print("  ✓  index.html geschrieben")
PYEOF

# ── Berechtigungen ────────────────────────────────────────────────────────────
sudo chown -R www-data:www-data "$WEB_ROOT"
sudo chmod 644 "$WEB_ROOT/index.html"
sudo chmod 644 "$LIBS_DIR"/*.js "$LIBS_DIR"/*.css "$LIBS_DIR"/*.txt 2>/dev/null || true
sudo chmod 755 "$WEB_ROOT" "$LIBS_DIR"

# ── Nginx neu laden ───────────────────────────────────────────────────────────
echo ""
echo "🔄  Nginx reload…"
if sudo nginx -t 2>/dev/null; then
    sudo systemctl reload nginx
    green "  ✓  Nginx neu geladen"
else
    red "  ❌  Nginx config ungültig – reload übersprungen"
    sudo nginx -t
    exit 1
fi

# ── Deployment-Log ────────────────────────────────────────────────────────────
LOG_FILE="$HOME/trail-atlas/deploy.log"
echo "$(date '+%Y-%m-%d %H:%M:%S') | $(basename "$HTML_FILE") | OK" >> "$LOG_FILE"

echo ""
echo "══════════════════════════════════════════"
green "✅  Deploy abgeschlossen: $(basename "$HTML_FILE")"
echo ""
