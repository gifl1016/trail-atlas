# History

Versionshistorie und Evolution des Projekts.

---

## Frontend-Versionen

| Version | Highlights |
|---------|-----------|
| **v1.0** | Karte mit Leaflet, Track-Rendering via SVG, CSV-Import (PapaParse), IndexedDB (Dexie), Detail-Sheet bei Track-Klick, Splash-Screen |
| **v1.2** | Stats-Banner (Touren/km/Stunden), Aggregations-Diagramm (Canvas), Zeitfilter (Jahr/Quartal/Monat), Distanzberechnung (Haversine) |
| **v2.0** | Performance-Optimierungen (adaptive Punkt-Reduktion, progressive Batch-Rendering), Start-Marker ab Zoom 11, drei Kartenstile (Dark/Topo/Satellit), Dropdown-Filter statt Chips |
| **v2.2** | fitBounds-Control (manuell statt automatisch), Dim-Overlay für helle Kartenstile, diverse Bugfixes |
| **v2.3** | Datenverwaltung-Tab (DB-Info, GPS löschen, Reset), Import-Validierung (Pflichtfelder, Koordinaten-Range, Duplikate), Tour einzeln löschen |
| **v2.4c** | Track-Selektion mit Highlight (gewählt: voll sichtbar, Rest ausgegraut), "Auf Karte zeigen"-Button, Selection-Badge, **Canvas-Renderer** (ersetzt SVG) |
| **v2.5** | Empty States (n/a-Platzhalter, leere Tourenliste, Diagramm-Platzhalter), Loading-Screen mit Fortschrittsbalken, Titel-Versionierung |
| **v2.6** | XSS-Schutz (escHtml auf alle DB-Felder), SRI-Hashes auf CDN-Libraries, Content-Security-Policy Meta-Tag, lokales Library-Hosting |
| **v3.0** | **Architektur-Wechsel:** IndexedDB entfernt, alle Daten von REST-API (FastAPI/SQLite). Dexie-Library entfernt. `connect-src 'self'` in CSP |
| **v3.2** | CSP-Fix (`connect-src 'none'`→`'self'`), Versionsnummer sichtbar (Title, Splash, Import-Tab, Console), `redirect_slashes=False` für DELETE-Endpoints |
| **v3.3** | Garmin Sync Status-Box im Import-Tab (grün/rot Indikator, letzte Sync-Zeit, importierte Touren, Fehlermeldung) |
| **v3.4** | **N+1-Bug behoben:** Bulk-GPS-Endpoint `/activities/gps/all` ersetzt N einzelne GPS-Requests. Ladezeit von 60s auf 2-3s reduziert. `gpsCache` im Frontend. |
| **v3.5** | **Login-Screen:** App-eigene Authentifizierung (Session-Cookie). Login-Formular, User-Info-Badge (oben rechts), Logout. Boot prüft `/auth/me` vor App-Start. |
| **v3.6** | **Signup + Invite:** Registrierung über Einladungscodes. `?invite=CODE` URL-Parameter. Admin-Box im Import-Tab zum Generieren von Invite-Codes. |
| **v3.7** | **Multi-User Komplettausbau:** Garmin-Credentials beim Signup, Admin-Tab mit Nutzerverwaltung, Sterne-Bewertung pro Aktivität, "Alle Garmin-Daten zurücksetzen" Button, Activity-Typ-Harmonisierung (Wandern&Gehen, Schwimmen&Wasser, Kraft&Cardio in Legende/Filter), Tab "Touren"→"Aktivitäten", Map-Style-Button nach links unten |

---

## Backend-Versionen

| Version | Änderungen |
|---------|-----------|
| **1.0.0** | Initiale FastAPI-API mit 10 Endpoints, SQLite mit WAL, Connection-per-Request |
| **1.1.0** | Persistente Single-Connection (behebt WAL-Race-Condition), `redirect_slashes=False` (behebt DELETE 405), Auto-Rollback im Deploy-Script |
| **1.2.0** | GPS-Import idempotent (DELETE alte Punkte vor INSERT), Response enthält `replaced` + `activities_touched` |
| **1.3.0** | `sync_log`-Tabelle + Endpoints (`GET /sync/status`, `POST /sync/log`), Pydantic Model für Sync-Einträge |
| **1.4.0** | **Bulk-GPS-Endpoint** `GET /activities/gps/all` – alle GPS-Punkte in einem Response. Behebt N+1-Performance-Problem. |
| **1.5.0** | **Multi-User Auth:** Schema v2 (users, invite_codes, garmin_credentials), Session-Auth (`auth.py`), Login/Logout/Signup/Invite-Endpoints, `Depends(get_current_user)` auf allen geschützten Endpoints. Admin-Bootstrap aus `ADMIN_USER`/`ADMIN_PASS` env vars. |
| **1.6.0** | **Sync-API-Key:** `X-Sync-Key` Header als Alternative zu Session-Cookie für Cronjob-Zugriff. Basic Auth vollständig entfernt. |
| **1.7.0** | **Pro-User-Datenisolation:** `activities.user_id` Migration, alle Endpoints filtern nach User. Garmin-Credentials im Signup (Fernet-verschlüsselt). `GET /sync/users` für Multi-User-Sync. **Schema v3:** `start_lat`/`start_lng` nullable für Activities ohne GPS (Yoga, Strength, Indoor). `sync_log.user_id` für Per-User-Sync-Status. `garmin_credentials.email_hash` (SHA-256, UNIQUE) verhindert doppelte Garmin-Account-Verknüpfung. **Admin Nutzerverwaltung:** `/admin/users` Endpoints (Liste, Löschen, Garmin setzen/entfernen) mit Self- und Last-Admin-Protection. |
| **1.8.0** | **Schema v4 – Activity-Bewertungen:** `activity_ratings`-Tabelle mit PK `(user_id, activity_id)` und **keinem FK auf activities**. `PUT/DELETE /activities/{id}/rating` Endpoints. Neuer `DELETE /db/garmin` Endpoint löscht Activities+GPS aber behält Bewertungen. `/activities` und `/activities/{id}` liefern jetzt `rating`-Feld mit. |

---

## Sync-Script Evolution

| Phase | Highlights |
|-------|-----------|
| **Single-User** | `garmin_sync.py` mit Garmin-Credentials aus `garmin.env`, ein Token global |
| **Multi-User** | Iteriert über alle User mit hinterlegten Credentials, Token pro User unter `/var/lib/trail-atlas/garmin_tokens/{uid}.json`. `--user`-Flag für gezielte User-Syncs. |
| **Robustheit** | Login-Timeout 30s via Threading (verhindert Cloudflare-Endlosretries), persistentes Fail-Tracking (`_login_failures.json`), nach 2 Fehlversuchen wird User übersprungen → schützt vor Garmin-Account-Sperre. Automatischer Reset wenn Credentials aktualisiert werden (`updated_at`-Vergleich). |
| **Performance** | Best-effort GPS-Fetch: Activities werden importiert auch wenn GPS fehlt oder fehlschlägt. `--check` Modus für Deploy-Smoke-Tests (kein Garmin-Login, nur Config + API). |
| **Mapping** | `ACTIVITY_TYPE_MAP` harmonisiert Garmin-Varianten (`treadmill_running` → `running`, `indoor_cardio` → `cardio`, …) |

---

## Architektur-Evolution

### Phase 1: Standalone HTML (v1.0 – v2.6)

```
Browser ←→ IndexedDB (lokal)
         ←→ CDN Libraries
         ←  CSV Upload (PapaParse im Browser)
```

Alles lief im Browser. Daten in IndexedDB, Libraries von CDN. Keine Server-Abhängigkeit außer für das Hosting der HTML-Datei.

**Grenzen:** Daten nur auf einem Gerät, Browser-Cache-Löschung verliert alles, keine automatische Sync-Möglichkeit.

### Phase 2: API-basiert (v3.0 – v3.3)

```
Browser ←→ Nginx (Basic Auth) ←→ FastAPI ←→ SQLite (VM)
                                           ←  Garmin Sync (Cronjob, Basic Auth)
```

Daten zentral auf der VM. IndexedDB komplett entfernt. Browser ist nur noch Rendering-Layer.

**Vorteile:** Daten von jedem Gerät zugänglich, automatischer Garmin-Sync, kein Datenverlust bei Cache-Löschung, konsistenter Zustand.

### Phase 3: Multi-User + App-Auth (v3.4 – v3.6, Backend 1.5/1.6)

```
Browser ←→ Nginx (kein Basic Auth mehr) ←→ FastAPI (Session-Auth) ←→ SQLite (VM)
                                                                    ←  Garmin Sync (API-Key Auth)
```

Eigenes Login-System mit bcrypt, signierten Cookies, Invite-Codes. Mehrere User möglich, aber Daten noch geteilt.

### Phase 4: Datenisolation + Admin + Bewertungen (v3.7, Backend 1.7/1.8, aktuell)

```
Browser ←→ Nginx ←→ FastAPI ←→ SQLite (Schema v4, Per-User Filter überall)
                              ↑
                              ratings überleben Garmin-Reset (kein FK)

Multi-User Sync ←→ FastAPI (X-Sync-Key)
   ↓
   /var/lib/trail-atlas/garmin_tokens/{uid}.json  (pro User)
   _login_failures.json  (Account-Schutz)
```

Jeder User hat seine eigenen Touren, GPS-Punkte, Bewertungen, Sync-Logs. Admin verwaltet alle User über einen vierten Tab. Garmin-Accounts sind UNIQUE über `email_hash` – kein Konflikt durch doppelte Verknüpfung.

### Phase 5: CI/CD (parallel)

```
GitHub Push → Actions → SSH → VM → Path-basierte Deploys
                              ↓     ├ src/         → Frontend
                              ↓     ├ backend/     → Backend (Backup + Rollback)
                              ↓     ├ garmin-sync/ → Sync-Script
                              ↓     └ scripts/     → CLI (ops.sh) + ggf. andere Scripts
                              ↓
                              Backend-Deploy mit Health-Check
```

Keine manuellen Deployment-Schritte mehr. Push auf `main` → automatisch live. Backend-Deploy mit DB-Backup und Auto-Rollback. Eigener `deploy-ops-cli` Job installiert das CLI bei Änderungen unter `scripts/`.

---

## Wichtige Entscheidungen

| Wann | Entscheidung | Begründung |
|------|-------------|-----------|
| v1.0 | Leaflet statt Google Maps | Open-Source, kostenlos, kein API-Key |
| v1.0 | Vanilla JS statt React | Kein Build-Prozess nötig, eine Datei |
| v1.0 | IndexedDB (Dexie) | Offline-Fähigkeit, große Datenmengen im Browser |
| v2.0 | PapaParse chunked parsing | 50+ MB CSV-Dateien ohne Browser-Freeze |
| v2.4c | Canvas-Renderer | Performance bei 400+ Tracks auf Mobile |
| v2.6 | Libraries lokal hosten | SRI-Hash-Stabilität, CDN-Unabhängigkeit |
| v2.6 | CSP + SRI + XSS-Escaping | Defense-in-Depth Sicherheitskonzept |
| v3.0 | FastAPI + SQLite Backend | Zentrale Daten, Garmin-Sync möglich |
| v3.0 | Single-Worker uvicorn | SQLite-Kompatibilität, ausreichend für wenige User |
| v3.0 | Persistente DB-Connection | WAL-Race-Condition vermeiden |
| v3.4 | Bulk-GPS-Endpoint | N+1-Bug behoben, 60s→2s Ladezeit |
| v3.5 | Session-Auth statt JWT | Einfacher, sofort invalidierbar, ausreichend für 1-5 User |
| v3.5 | Invite-Code-System | Kontrollierte Registrierung, kein offenes Signup |
| v3.6 | Sync-API-Key statt Basic Auth | Cronjob braucht eigenen Auth-Mechanismus |
| 1.7.0 | Pro-User Datenisolation | Jeder User sieht nur eigene Touren, Multi-User echt funktionsfähig |
| 1.7.0 | Fernet für Garmin-Credentials | Symmetrisch (Login braucht Klartext), Key aus SECRET_KEY |
| 1.7.0 | `email_hash` UNIQUE | Verhindert doppelte Garmin-Verknüpfung → keine Activity-ID-Kollisionen |
| 1.7.0 | nullable Koordinaten | Indoor-Activities (Yoga, Kraft) werden importiert |
| 1.7.0 | Sync best-effort | Activities bleiben in DB auch bei GPS-Fetch-Fehler (Rate-Limit) |
| 1.7.0 | Login-Timeout + Fail-Tracking | Schutz vor Garmin-Account-Sperre bei falschen Credentials |
| 1.8.0 | `activity_ratings` ohne FK auf activities | Bewertungen überleben DB-Reset |
| v3.7 | Activity-Mapping zweistufig | Sync vereinheitlicht (`treadmill_running` → `running`), Frontend gruppiert nur Legende/Filter |
| CI/CD | Path-basierte GitHub Actions | Nur geänderte Komponenten deployen |
| CI/CD | Backend-Deploy mit Rollback | DB-Backup + Auto-Restore bei Health-Check-Fehler |
| CI/CD | Eigener `deploy-ops-cli` Job | CLI wird bei jeder `scripts/`-Änderung aktualisiert |

---

## Roadmap

**Umgesetzt seit der letzten Roadmap-Aktualisierung:**
- ✅ Garmin-Sync pro User mit eigenen Credentials je Account
- ✅ Touren-Bewertung (1–5 Sterne, DB-Reset-resistent)
- ✅ Touren nach User filtern (komplette Datenisolation)
- ✅ Activities ohne GPS-Track werden importiert
- ✅ Admin-Nutzerverwaltung (vierter Tab)
- ✅ Robuster Sync mit Account-Sperr-Schutz

**Mittelfristig:**
- Höhenprofil pro Tour (im Detail-Sheet)
- Jahres-Heatmap (GitHub-Style Aktivitätsgrid)
- GPX-Export einzelner Touren
- PWA / Service Worker (Offline-Fähigkeit, Homescreen-Icon)
- Notizen pro Tour (Freitext im Deep-Dive)

**Langfristig:**
- Persönliche Rekorde (schnellste/längste Tour pro Typ)
- Streaming-Endpoint für GPS-Punkte (lazy loading bei vielen Touren)
- Vergleich mehrerer Touren überlagert auf der Karte
