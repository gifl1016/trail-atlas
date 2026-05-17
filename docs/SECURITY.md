# Security

Sicherheitskonzept für Trail Atlas.

---

## Übersicht

```
Internet
  │
  ▼ Port 443 (HTTPS only, HTTP→301)
┌───────────────────────────────────────────┐
│  Nginx                                    │
│  ├── TLS 1.2/1.3 (Let's Encrypt)         │
│  ├── HSTS (max-age=63072000)              │
│  ├── Security Headers (CSP, X-Frame, ...) │
│  └── Reverse Proxy → localhost:8000       │
└───────────────────────┬───────────────────┘
                        │ (localhost only, nicht von außen erreichbar)
                        ▼
┌───────────────────────────────────────────┐
│  FastAPI                                  │
│  ├── Session-Auth (signierte Cookies)     │
│  ├── Sync-API-Key (Cronjob-Zugriff)      │
│  ├── Input Validation (Koordinaten, CSV)  │
│  ├── CORS: nur localhost                  │
│  └── redirect_slashes=False               │
└───────────────────────┬───────────────────┘
                        ▼
┌───────────────────────────────────────────┐
│  SQLite                                   │
│  ├── Datei-Berechtigungen: 600            │
│  ├── Owner: trail-atlas (kein Login)      │
│  └── Nur über Backend erreichbar          │
└───────────────────────────────────────────┘
```

---

## Transport

- **HTTPS only:** HTTP → 301 Redirect auf HTTPS. Kein unverschlüsselter Verkehr.
- **TLS 1.2+:** ältere Protokolle deaktiviert.
- **HSTS:** Browser merkt sich HTTPS-Pflicht für 2 Jahre.
- **Let's Encrypt:** automatische Zertifikatserneuerung alle 60-90 Tage.

---

## Authentifizierung

### App-Auth (Session-basiert, seit v1.5.0)

Trail Atlas nutzt ein eigenes Auth-System mit drei Komponenten:

**Login:** User gibt Username + Passwort ein → `POST /auth/login` → Backend prüft bcrypt-Hash → signierter Session-Cookie wird gesetzt (`httponly`, `secure`, `samesite=strict`).

**Sessions:** Der Cookie enthält einen mit `itsdangerous` signierten Token (Payload: User-ID + Username). Max-Age: 30 Tage. Der Server kann Sessions durch Ändern des `SECRET_KEY` sofort invalidieren.

**Invite-Codes:** Neue User registrieren sich über Einladungscodes. Codes sind einmalig verwendbar, laufen nach 7 Tagen ab, und können nur von Admins generiert werden (`POST /auth/invite`).

### Sync-API-Key (Cronjob)

Der Garmin-Sync-Cronjob authentifiziert sich über den HTTP-Header `X-Sync-Key` mit einem geteilten Secret (`SYNC_API_KEY` in `garmin.env`). Der Cronjob läuft lokal auf der VM und greift über `127.0.0.1:8000` zu – der Key ist trotzdem nötig, da die App-Auth sonst alle unauthentifizierten Requests blockiert.

### Übergangslogik

Die Auth-Dependency `get_current_user` prüft in dieser Reihenfolge:
1. `X-Sync-Key` Header → Cronjob-Zugriff
2. Keine Users in DB → Auth nicht aktiv (Erstinstallation)
3. Session-Cookie → normaler User-Login

### Historisch: Nginx Basic Auth (entfernt in Schritt 5)

Bis v1.4.0 schützte Nginx Basic Auth die gesamte App. Ab v1.5.0 wurde App-eigene Auth eingeführt, ab v1.6.0 ist Basic Auth vollständig entfernt.

---

## Content Security Policy

```
default-src 'self';
script-src 'self' 'unsafe-inline';
style-src 'self' 'unsafe-inline';
img-src data: blob:
    https://*.basemaps.cartocdn.com
    https://*.tile.opentopomap.org
    https://server.arcgisonline.com;
connect-src 'self';
object-src 'none';
frame-ancestors 'none';
```

Was das bedeutet:
- Scripts nur von eigener Domain (lokal gehostete Libraries)
- `unsafe-inline` nötig weil der gesamte JS-Code in der HTML-Datei liegt
- Kartenkacheln nur von den drei erlaubten Tile-Providern
- API-Calls nur an eigene Domain (`connect-src 'self'`)
- Kein Embedding in iframes möglich (`frame-ancestors 'none'`)

---

## Subresource Integrity (SRI)

Alle JavaScript-Libraries werden mit SHA384-Hashes geladen:

```html
<script src="/libs/leaflet.js" integrity="sha384-..." crossorigin="anonymous"></script>
```

Die Hashes werden beim Deploy automatisch aus den tatsächlich heruntergeladenen Dateien berechnet (`deploy.sh`). Eine manipulierte Library-Datei würde vom Browser blockiert.

---

## XSS-Schutz

Alle CSV-Felder die in `innerHTML`-Templates eingebettet werden, durchlaufen `escHtml()`:

```javascript
function escHtml(s) {
    return String(s).replace(/[&<>"']/g, c => _esc[c]);
}
```

Betrifft: `activity_id`, `activity_type`, `start_date`, Koordinaten, Distanz, Dauer – überall wo Daten in HTML gerendert werden.

---

## Input Validation (Backend)

Alle CSV-Felder werden serverseitig validiert:
- `activity_id`: nicht leer
- `start_date`: nicht leer
- Koordinaten: `lat ∈ [-90, 90]`, `lng ∈ [-180, 180]`, nicht NaN
- Duplikate: `ON CONFLICT` für Metadaten, DELETE+INSERT für GPS

Ungültige Zeilen werden übersprungen und in der Response als `skipped` gezählt.

---

## Credential-Speicherung

| Credential | Ort | Berechtigungen |
|-----------|-----|---------------|
| User-Passwörter | SQLite `users.password_hash` (bcrypt) | DB-Datei: `trail-atlas:trail-atlas 600` |
| Session-Secret | `SECRET_KEY` in `/etc/trail-atlas/garmin.env` | `trail-atlas:trail-atlas 600` |
| Sync-API-Key | `SYNC_API_KEY` in `/etc/trail-atlas/garmin.env` | `trail-atlas:trail-atlas 600` |
| Admin-Credentials | `ADMIN_USER`/`ADMIN_PASS` in `garmin.env` (nur für initialen Bootstrap) | `trail-atlas:trail-atlas 600` |
| Garmin Email+Passwort | `/etc/trail-atlas/garmin.env` | `trail-atlas:trail-atlas 600` |
| Garmin Session-Token | `/var/lib/trail-atlas/garmin_token.json` | `trail-atlas:trail-atlas 600` |
| SSH Deploy-Key | GitHub Secrets (verschlüsselt) | nie auf Disk |

**Keine Credentials liegen im Git-Repository.** `garmin.env.example` enthält nur Platzhalter.

---

## systemd Hardening

Der Backend-Service läuft als unprivilegierter User mit Sicherheitsrestriktionen:

```ini
User=trail-atlas
EnvironmentFile=/etc/trail-atlas/garmin.env
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/var/lib/trail-atlas
```

Der `trail-atlas` User hat keine Login-Shell (`/usr/sbin/nologin`), kann sich nicht interaktiv einloggen.

---

## Netzwerk-Isolation

- **uvicorn** lauscht nur auf `127.0.0.1:8000` – nicht von außen erreichbar
- **SQLite** hat keinen Netzwerk-Listener (Dateisystem-basiert)
- **Garmin-Sync** kommuniziert mit API über localhost

Einziger öffentlicher Endpunkt: Nginx auf Port 443.

---

## Bekannte Limitierungen

- **`unsafe-inline` in CSP:** nötig weil JS/CSS in der HTML-Datei liegen. Ein separates JS-Bundle würde das eliminieren.
- **Garmin-Passwort auf Disk:** Steht in `garmin.env`. Risiko minimiert durch chmod 600 + systemd-User.
- **Kein Rate-Limiting:** Die API hat kein eigenes Rate-Limit. Session-Auth und die geringe Nutzerzahl machen das akzeptabel.
- **Session-Token-Signierung, nicht Verschlüsselung:** Der Cookie-Inhalt (User-ID, Username) ist base64-lesbar aber manipulationssicher (HMAC-signiert). Kein sensibles Datum im Payload.
