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
│  ├── Per-User Datenisolation              │
│  ├── Garmin-Credentials Fernet-encrypted │
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

┌───────────────────────────────────────────┐
│  Garmin Sync (Cronjob)                    │
│  ├── Token-Cache pro User (600)           │
│  ├── Login-Timeout 30s                    │
│  └── Fail-Tracking → Account-Schutz       │
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

**Invite-Codes:** Neue User registrieren sich über Einladungscodes. Codes sind einmalig verwendbar, laufen nach 7 Tagen ab, und können nur von Admins generiert werden (`POST /auth/invite`). Beim Signup können optional Garmin-Credentials direkt angegeben werden.

### Sync-API-Key (Cronjob)

Der Garmin-Sync-Cronjob authentifiziert sich über den HTTP-Header `X-Sync-Key` mit einem geteilten Secret (`SYNC_API_KEY` in `garmin.env`). Der Cronjob läuft lokal auf der VM und greift über `127.0.0.1:8000` zu – der Key ist trotzdem nötig, da die App-Auth sonst alle unauthentifizierten Requests blockiert.

Sync-Key gewährt Zugriff auf Service-Endpoints (Activity-Import, Sync-Log, User-Liste), **aber nicht auf Admin-Endpoints** (`/admin/users/*`). Diese erfordern einen echten Admin-User-Login.

### Per-User Datenisolation

Seit v1.7.0 sind alle Datenzugriffe pro User gefiltert. Konkret:

- `GET /activities` liefert nur die Activities des eingeloggten Users
- `GET /activities/gps/all` liefert nur die GPS-Punkte der eigenen Activities
- `GET /sync/status` zeigt nur eigene Sync-Logs
- `DELETE /db/reset` / `/db/garmin` / `/db/gps` löscht nur eigene Daten
- `PUT /activities/{id}/rating` schreibt nur das eigene Rating
- `GET /activities/{id}` liefert 404 wenn die Activity einem anderen User gehört

Der Cronjob umgeht das per `?user_id=X` Query-Parameter, autorisiert durch den Sync-API-Key.

### Übergangslogik

Die Auth-Dependency `get_current_user` prüft in dieser Reihenfolge:
1. `X-Sync-Key` Header → Cronjob-Zugriff (kein User-Kontext, `?user_id=X` muss übergeben werden für User-Filter)
2. Keine Users in DB → Auth nicht aktiv (Erstinstallation)
3. Session-Cookie → normaler User-Login

### Historisch: Nginx Basic Auth (entfernt in v1.6.0)

Bis v1.4.0 schützte Nginx Basic Auth die gesamte App. Ab v1.5.0 wurde App-eigene Auth eingeführt, ab v1.6.0 ist Basic Auth vollständig entfernt.

---

## Garmin-Credential-Verschlüsselung (Fernet, seit v1.7.0)

Garmin-Credentials müssen im Klartext für den Login verfügbar sein – also können sie nicht wie User-Passwörter mit bcrypt gehashed werden. Stattdessen werden sie symmetrisch verschlüsselt:

```
User gibt Garmin-Email+Passwort ein
  → save_garmin_credentials()
    → encrypt_garmin_credentials(): json.dumps({"email":..., "password":...})
                                    → Fernet.encrypt(plaintext)
    → speichern in garmin_credentials.token_json
  → email_hash = SHA-256(normalisierte Email) → garmin_credentials.email_hash (UNIQUE)

Sync-Script:
  → GET /sync/users → liefert token_json + updated_at
  → decrypt_garmin_credentials() → {"email":..., "password":...}
  → Login bei Garmin
```

**Key-Herleitung:** Der Fernet-Schlüssel wird deterministisch aus `SECRET_KEY` abgeleitet:

```python
SHA-256(SECRET_KEY.encode()) → base64-urlsafe → Fernet-Key
```

Damit braucht das Sync-Script nur den `SECRET_KEY` aus `garmin.env`, und kann selbst entschlüsseln. Kein Klartext-Passwort wird je über die API übertragen (außer beim initialen Signup/Admin-Update).

**Konsequenzen:**
- Wenn `SECRET_KEY` verloren geht, sind alle verschlüsselten Garmin-Credentials unwiederbringlich verloren.
- Wenn `SECRET_KEY` rotiert wird, müssen alle User ihre Garmin-Credentials neu eingeben.
- Die DB-Datei ohne `SECRET_KEY` ist nutzlos für Angreifer (Garmin-Credentials bleiben verschlüsselt).

---

## Garmin-Account-Eindeutigkeit (email_hash)

Ein Garmin-Account darf nur einmal in Trail Atlas registriert werden – sonst würden zwei User beim Sync dieselben Activities laden und sich gegenseitig überschreiben (gleiche `activity_id`).

**Implementierung:** Beim Speichern wird zusätzlich ein deterministischer SHA-256 der normalisierten Email gespeichert:

```python
normalized = email.strip().lower()
email_hash = hashlib.sha256(normalized.encode()).hexdigest()
```

Auf dieser Spalte liegt ein `UNIQUE`-Index. Beim Signup oder Admin-Garmin-Update wird vorher geprüft, ob der Hash bereits existiert → bei Duplikat 409 ohne den User anzulegen.

Der Hash ist one-way – er verrät die Email nicht (kein Klartext-Vergleich nötig), erlaubt aber den Duplikat-Check.

---

## Garmin-Account-Sperr-Schutz

Garmin sperrt Accounts temporär (24h) nach mehreren fehlgeschlagenen Login-Versuchen. Das Sync-Script schützt davor:

1. **Login-Timeout (30s):** Garmin's Cloudflare-Schutz kann das `garminconnect`-Login in endlose Retry-Schleifen ziehen. Wir kappen das per Threading.
2. **Fail-Tracking** in `/var/lib/trail-atlas/garmin_tokens/_login_failures.json`. Nach 2 fehlgeschlagenen Logins wird der User komplett übersprungen, bis seine Credentials in der App aktualisiert werden.
3. **Rate-Limit-Awareness:** `GarminConnectTooManyRequestsError` zählt nicht als Auth-Fail – das ist ein Garmin-seitiges Rate-Limit, nicht ein falsches Passwort.

Ergebnis: maximal 2 echte Login-Versuche pro falscher Credential-Eingabe. Bleibt weit unter dem Garmin-Lockout-Schwellenwert.

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

Betrifft: `activity_id`, `activity_type`, `start_date`, Koordinaten, Distanz, Dauer, Username – überall wo Daten in HTML gerendert werden.

---

## Input Validation (Backend)

Alle CSV-Felder werden serverseitig validiert:
- `activity_id`: nicht leer
- `start_date`: nicht leer
- Koordinaten: `lat ∈ [-90, 90]`, `lng ∈ [-180, 180]`, nicht NaN. **Seit v1.7.0 nullable** für Activities ohne GPS (Yoga, Kraft, Indoor).
- Duplikate: `ON CONFLICT(activity_id)` für Metadaten, DELETE+INSERT für GPS
- Rating: `CHECK (rating BETWEEN 1 AND 5)` auf DB-Ebene + Pydantic `Field(ge=1, le=5)`

Ungültige Zeilen werden übersprungen und in der Response als `skipped` gezählt.

---

## Credential-Speicherung

| Credential | Ort | Schutz |
|-----------|-----|--------|
| User-Passwörter | SQLite `users.password_hash` | bcrypt-Hash. DB-Datei: `trail-atlas:trail-atlas 600` |
| Session-Secret | `SECRET_KEY` in `/etc/trail-atlas/garmin.env` | `chmod 600`, `trail-atlas:trail-atlas` |
| Sync-API-Key | `SYNC_API_KEY` in `garmin.env` | `chmod 600` |
| Admin-Bootstrap | `ADMIN_USER`/`ADMIN_PASS` in `garmin.env` | nur für initiale Erstellung des Admin-Users beim ersten Backend-Start |
| Garmin-Credentials | SQLite `garmin_credentials.token_json` | Fernet-verschlüsselt mit Key aus `SECRET_KEY` |
| Garmin-Email-Hash | `garmin_credentials.email_hash` | One-way SHA-256, verrät keine Klartext-Email |
| Garmin Session-Tokens | `/var/lib/trail-atlas/garmin_tokens/{uid}.json` | `chmod 600`, `trail-atlas:trail-atlas`, pro User |
| SSH Deploy-Key | GitHub Secrets (verschlüsselt) | nie auf Disk |

**Keine Credentials liegen im Git-Repository.** `garmin.env.example` enthält nur Platzhalter.

`GARMIN_EMAIL` und `GARMIN_PASSWORD` in `garmin.env` sind seit v1.7.0 optional – sie werden nur beim initialen Backend-Start verwendet, um den Admin-Account mit diesen Garmin-Credentials zu hinterlegen. Danach werden sie nicht mehr gelesen. Weitere User registrieren ihre Garmin-Credentials über die App.

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
- **Symmetrische Garmin-Verschlüsselung:** Wer Zugriff auf DB **und** `SECRET_KEY` hat, kann alle Garmin-Passwörter entschlüsseln. Beide Komponenten sind aber `chmod 600` und gehören dem `trail-atlas`-User – ein Angreifer müsste root sein. Auf einer Single-User-VM ist das akzeptabel.
- **Kein Rate-Limiting:** Die API hat kein eigenes Rate-Limit. Session-Auth und die geringe Nutzerzahl machen das akzeptabel.
- **Session-Token-Signierung, nicht Verschlüsselung:** Der Cookie-Inhalt (User-ID, Username) ist base64-lesbar aber manipulationssicher (HMAC-signiert). Kein sensibles Datum im Payload.
- **`activity_ratings` keine FK-Garantie auf `activities`:** Bewertungen können auf nicht-existente Activity-IDs zeigen (gewollt, damit Reset funktioniert). Bei aktivem Angreifer mit DB-Schreibzugriff könnten Ratings auf fremde Activity-IDs gesetzt werden. Da der User-Filter beim Lesen greift, ist das kein Datenleck – nur eine kosmetische Datenanomalie.
