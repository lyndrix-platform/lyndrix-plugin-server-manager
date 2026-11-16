# Server Manager — Dokumentation

## Übersicht

Der Server Manager ist ein zentrales Server-Inventar mit konfigurierbaren Hardware-Katalogen und Kombinationsregeln. Er erfasst Server mitsamt ihren Hardware-Profilen, Umgebungszuordnungen und Statusänderungen. Über den globalen Event-Bus werden reichhaltige Ereignisse emittiert, die nachgelagerte Bestell- und Provisionierungs-Workflows anderer Plugins (z. B. des IaC Orchestrators) anstoßen können.

---

## Architektur

```
lyndrix-plugin-server-manager/
├── entrypoint.py               # Manifest + Lifecycle-Hooks
├── examples/
│   ├── hardware.yml            # CPU-, RAM-, Storage-, Netzwerk-Optionen + Kombinationsregeln
│   └── environments.yml        # Umgebungen und IT-Provider-Bestell-Workflows
└── app/
    ├── api.py                  # FastAPI-Router (Server-CRUD + Katalog + Wartungs-Read-Model)
    ├── model/
    │   ├── models.py           # SQLAlchemy-Modelle (server_manager_servers, server_manager_maintenance)
    │   ├── database.py         # DB-Session-Helfer, Tabelleninitialisierung
    │   └── catalog.py          # YAML/JSON-Katalog-Loader + Regel-Evaluator
    ├── controller/
    │   ├── service.py          # CRUD + Event-Emission (Singleton: server_manager_service)
    │   ├── maintenance.py      # Wartungsfenster-Read-Model (Core-API ≥ 1.13, Konsument)
    │   └── configurator/       # 3-stufiger Hinzufügen-/Bearbeiten-Dialog
    └── ui/
        ├── overview.py         # Server-Liste mit Suche / Filter (NiceGUI)
        ├── settings.py         # Plugin-Einstellungen + Katalog-Dokumentation (NiceGUI)
        ├── widget.py           # Kompaktes Dashboard-Widget (NiceGUI)
        └── page.py             # Hauptseite /server-manager (NiceGUI)
```

**`server_manager_service`** ist ein Singleton, das CRUD-Operationen an der Datenbank ausführt und bei jeder Änderung passende Events auf dem Event-Bus emittiert.

**`catalog.py`** lädt die YAML-Katalogdateien und wertet Kombinationsregeln aus, um sicherzustellen, dass nur gültige Hardware-Kombinationen ausgewählt werden können.

---

## API-Referenz

Alle Routen sind unter `/api/plugins/lyndrix.plugin.server_manager/` erreichbar und erfordern eine gültige Authentifizierung.

| Methode | Pfad | Beschreibung |
|---|---|---|
| `GET` | `/servers` | Vollständige Server-Liste; jeder Eintrag mit `ci_id` trägt das Feld `maintenance` (siehe [Wartungsfenster](#wartungsfenster)). `?in_maintenance=true\|false` filtert auf den aktuellen Wartungszustand |
| `GET` | `/servers/{id}` | Ein Server (inkl. `maintenance`) |
| `POST` | `/servers` | Server hinzufügen (Hardware-Profil + Umgebung) |
| `PATCH` | `/servers/{id}` | Server aktualisieren |
| `DELETE` | `/servers/{id}` | Server entfernen |
| `GET` | `/maintenance/{ci_id}` | Vollständiges Wartungs-Read-Model eines Configuration Items (Fenster je Quelle, laufende Overrides, `server_ids`) |
| `GET` | `/catalog` | Verfügbarer Hardware-Katalog + Kombinationsregeln |
| `PUT` | `/servers/external/{source}/{external_id}` | Server aus einem Fremdsystem anlegen oder aktualisieren (Upsert) |
| `DELETE` | `/servers/external/{source}/{external_id}` | Von einem Fremdsystem gelieferten Server entfernen |
| `POST` | `/servers/import` | Batch-Upsert `{servers: [...]}`, Ergebnis pro Eintrag |

### Server aus Fremdsystemen (Ingest)

Andere Systeme (IaC-Orchestrator, CMDB, Skripte) können Server per API oder Event-Bus einspielen. Schlüssel ist das Paar **`source` + `external_id`** (z. B. `iac-orchestrator` / `docker-atlas`):

- **Idempotent:** Wiederholtes Senden desselben Servers erzeugt keine Duplikate — ein Sync-Job kann sein komplettes Inventar bei jedem Lauf erneut schicken.
- **Anlegen** verlangt `name`, `environment_id` und `server_type`; bei späteren Pushes werden nur die mitgeschickten Felder übernommen (PATCH-Semantik).
- **Manuell angelegte Server** (`source` leer) werden nie getroffen und können von einem Fremdsystem nicht überschrieben werden.
- `source` und `external_id` sind nach dem Anlegen unveränderlich.
- Es werden die normalen Events (`server_created`, `server_updated`, …) emittiert.
- **Schreibschutz vs. Rückschreiben (`writeback`):** Das liefernde System entscheidet pro Server, ob es Änderungen selbst persistieren kann.
  - `writeback: false` (Standard): Der Server ist in beiden UIs und über die normale API **schreibgeschützt** (🔒, `PATCH`/`DELETE` → `409`). Nur die Quelle selbst ändert ihn über den Ingest-Pfad.
  - `writeback: true`: Der Server ist editierbar. Eine Änderung wird lokal sofort übernommen, als `sync_state = pending` markiert und als `server_manager:writeback_requested` an die Quelle gemeldet (z. B. Commit ins IaC-Repository durch das IaC-Plugin). Der nächste Push der Quelle setzt den Server wieder auf „synchron“; meldet die Quelle `server_manager:writeback_failed`, zeigt die UI den Fehler an.
  - Die Quelle bleibt die Wahrheit: Was sie nicht übernimmt, überschreibt ihr nächster Push.
- **Berechtigung (REST):** Die Ingest-Routen verlangen die eigene Berechtigung `plugin:lyndrix.plugin.server_manager:server_manager:ingest` (Rolle „Server Manager Source System“ bzw. Administrator) — `api:write` genügt nicht, weil der Core dieses Recht jedem normalen Benutzer gibt. Die `source` ist nicht frei wählbar: ein Aufrufer darf nur als die Quelle liefern, die so heißt wie sein Konto (Service-Account/API-Key des Sync-Jobs, z. B. Benutzer `cmdb` → `source` `cmdb`), sonst `403`. Nur der System-API-Key und Inhaber von `…:server_manager:admin` dürfen für beliebige Quellen handeln. Damit kann niemand über REST einen fremden, schreibgeschützten Datensatz per `writeback: true` editierbar machen, seine `ci_id` umhängen oder ihn löschen. Der Event-Bus-Pfad (`register_server`) ist unverändert.
- Katalog neu laden / Katalogpfad setzen (`POST /catalog/reload`, `POST /catalog/path`) verlangt `…:server_manager:admin`; `GET /catalog` liefert dazu `can_manage`.
- Es gibt kein automatisches Aufräumen: Server, die ein Fremdsystem nicht mehr liefert, müssen explizit per `DELETE` bzw. `deregister_server` entfernt werden.

```bash
curl -X PUT -H "Authorization: Bearer <api-key>" -H "Content-Type: application/json" \
  https://<lyndrix>/api/plugins/lyndrix.plugin.server_manager/servers/external/iac-orchestrator/docker-atlas \
  -d '{"name": "docker-atlas", "environment_id": "onprem:prod", "server_type": "container", "status": "active"}'
```

---

## Events

### Emittierte Events

| Topic | Payload | Beschreibung |
|---|---|---|
| `server_manager:server_created` | `{server}` | Neuer Server wurde angelegt |
| `server_manager:server_updated` | `{server, changes: {field: {old, new}}}` | Server-Daten haben sich geändert |
| `server_manager:server_deleted` | `{server}` | Server wurde entfernt (letzter bekannter Zustand) |
| `server_manager:hardware_changed` | `{server_id, server_name, action, old_profile, new_profile, environment_id, server_type}` | Hardware-Profil geändert |
| `server_manager:status_changed` | `{server_id, server_name, old_status, new_status}` | Status hat sich geändert |

| `server_manager:ingest_failed` | `{topic, source, external_id, error}` | Ein per Event-Bus eingespielter Server wurde abgewiesen |
| `server_manager:writeback_requested` | `{action: update\|delete, source, external_id, server, changes}` | Ein Benutzer hat einen Server mit `writeback: true` geändert — die Quelle soll persistieren |

### Abonnierte Events (Ingest)

| Topic | Payload | Beschreibung |
|---|---|---|
| `server_manager:register_server` | `{source, external_id, name, environment_id, server_type, …}` | Server anlegen oder aktualisieren (gleiche Regeln wie der REST-Upsert) |
| `server_manager:deregister_server` | `{source, external_id}` | Server entfernen |
| `server_manager:writeback_failed` | `{source, external_id, error}` | Die Quelle konnte eine angeforderte Änderung nicht persistieren |

Das sendende Plugin muss das Topic in seinem eigenen `permissions.emit` deklarieren. Da der Event-Bus keine Antwort liefert, werden Fehler über `server_manager:ingest_failed` zurückgemeldet.

```python
ctx.emit("server_manager:register_server", {
    "source": "iac-orchestrator",
    "external_id": "docker-atlas",
    "name": "docker-atlas",
    "environment_id": "onprem:prod",
    "server_type": "container",
})
```

---

## Wartungsfenster

Der Server Manager ist **Konsument** des Wartungsfenster-Vertrags der Core-API ≥ 1.13
(`core.api.maintenance`, siehe `lyndrix-core/docs/plugins.md` → *Maintenance windows*). Er legt
selbst keine Fenster an und emittiert keine `maintenance:*`-Events — er zeigt am Host, ob gerade
ein Wartungsfenster oder ein Override (z. B. ein laufender Deploy) läuft (Entscheidung E: Badge,
Tooltip und Filter; eine Detailansicht ist ein Folgevorhaben).

### Abonnierte Events

| Topic | Payload | Verarbeitung |
|---|---|---|
| `maintenance:windows_published` | `{source, published_at, replaces, items: [{ci_id, kind, name, windows: [Normalform…], inherited_from, …}]}` | `replaces: true` ersetzt **alle** Fenster-Zeilen dieser `source` in einer Transaktion; `kind` ist offen (jeder Registry-Kind); ungültige Items werden protokolliert und übersprungen, der Rest wird gespeichert |
| `maintenance:override_started` | `{override_id, ci_ids, until, reason, source, started_at, linger_minutes}` | Upsert je `(ci_id, override_id)`; aktiv von `started_at` bis `until` |
| `maintenance:override_ended` | `{override_id, source, ended_at, linger_minutes}` | setzt `ended_at`; das Override läuft noch `linger_minutes` (Default 15) nach und wird beim Lesen danach ignoriert, später gelöscht |

Regeln:

- **Eigene Tabelle, Auswertung beim Lesen.** Gespeichert wird nur, was die Emitter senden. Ob ein
  Fenster gerade läuft, berechnet `core.api.maintenance.effective_state` beim Lesen der Liste —
  kein Timer, kein Lease.
- **Unbekannte `ci_id`s werden gespeichert.** Die Reihenfolge `windows_published` vor
  `register_server` ist normal; die Zuordnung zum Server-Record passiert beim Lesen über
  `server_manager_servers.ci_id`. Manuelle Server ohne `ci_id` haben kein Wartungs-Read-Model.
- **Cluster:** Die Handler laufen auf jeder Instanz und schreiben idempotent in die gemeinsame
  Tabelle (Unique-Key, Delete+Insert je Quelle). `payload["_origin"]` wird bewusst nicht gefiltert.
- **Ohne Core-Bibliothek** (`min_core_version` bleibt `0.0.6`): Events werden gespeichert und roh
  ausgeliefert, `active` ist `null`, das Badge bleibt aus.

### Read-Model (`maintenance` an jedem Server mit `ci_id`)

```json
{
  "active": true,
  "until": "2026-10-18T11:00:00Z",
  "source": "override",
  "next_start": "2026-10-25T10:00:00Z",
  "note": "Deploy aac-grafana",
  "windows": [
    {"source": "iac-orchestrator", "inherited_from": "stage_default",
     "windows": [{"type": "weekly", "days": ["sun"], "start": "11:00", "end": "13:00", "tz": "Europe/Berlin"}],
     "updated_at": "2026-10-18T09:58:12Z"}
  ],
  "overrides": [
    {"override_id": "deploy-42", "source": "iac-orchestrator", "reason": "Deploy aac-grafana",
     "until": "2026-10-18T14:00:00Z", "started_at": "2026-10-18T09:50:00Z", "ended_at": null,
     "linger_minutes": 15, "effective_until": "2026-10-18T14:00:00Z", "active": true}
  ]
}
```

`source` ist `window` oder `override` (ein aktives Override gewinnt, `until` ist das spätere Ende),
`next_start` der Beginn des nächsten noch nicht laufenden Fensters (Horizont 30 Tage). Abgelaufene
Overrides erscheinen nicht. `GET /maintenance/{ci_id}` liefert dasselbe plus `ci_id` und
`server_ids`; für ein unbekanntes CI kommt `200` mit leeren Listen („nicht in Wartung“).

### Anzeige (React)

In der Status-Spalte erscheint neben dem Status-Badge ein Wartungs-Badge: „Wartung bis hh:mm“,
solange ein Fenster oder Override läuft, „Wartung ab …“, wenn das nächste Fenster innerhalb von
24 h beginnt. Der Tooltip nennt Quelle (Emitter, ggf. „geerbt von host/stage_default/…“), Grund
und Ende. Die Filterleiste hat einen Filter „Wartung: alle / In Wartung / Nicht in Wartung“.
Das Badge nutzt die Shell-Klasse `lx-badge--maintenance` (Token `--lx-state-maintenance`) mit
`lx-badge--paused` als Fallback auf älteren Shells. Die NiceGUI-Seiten sind unverändert.

---

## Konfiguration & Katalog

Die Hardware- und Umgebungskataloge werden aus YAML-Dateien im Katalogverzeichnis geladen (Standard `catalog/` im Plugin-Verzeichnis, umstellbar in den Einstellungen). Das Verzeichnis `catalog/` ist standortspezifisch und nicht Teil des Repositories: fehlt es oder enthält es keine Katalogdateien, lädt das Plugin als Ganzes den mitgelieferten Beispielkatalog aus `examples/` (`bundled_fallback: true` in `GET /catalog` und in den Health-Details). Enthält das Verzeichnis Dateien, aber keinen nutzbaren Katalog (keine Servertypen/Umgebungen), meldet `health()` `degraded` mit `reason: catalog_not_loaded`. Die Kataloge definieren:

- **`hardware.yml`:** Verfügbare CPU-, RAM-, Storage- und Netzwerk-Optionen sowie Kombinationsregeln, die ungültige Konfigurationen ausschließen.
- **`environments.yml`:** Umgebungsdefinitionen (z. B. `prod`, `staging`, `dev`) und die zugehörigen IT-Provider-Workflows für Bestellprozesse.

Eigene Katalogdateien können über die Plugin-Einstellungen hochgeladen werden.

**`auto_enable_on_install=False`** — vor der ersten Nutzung müssen die Katalogdateien geprüft und ggf. angepasst werden.

---

## Datenbankschema

Tabelle: `server_manager_servers`

| Spalte | Typ | Beschreibung |
|---|---|---|
| `id` | UUID / int | Primärschlüssel |
| `name` | string | Server-Bezeichnung |
| `hardware_profile` | JSON | Ausgewähltes Hardware-Profil (aus Katalog) |
| `environment_id` | string | Zugewiesene Umgebung |
| `server_type` | string | Typ-Klassifikation |
| `status` | string | Aktueller Status (`active`, `decommissioned`, …) |
| `source` | string, nullable | Herkunftssystem; leer = manuell angelegt |
| `external_id` | string, nullable | ID im Herkunftssystem; eindeutig zusammen mit `source` |
| `writeback` | bool | Quelle kann Änderungen persistieren (sonst schreibgeschützt) |
| `sync_state` / `sync_error` | string, nullable | Rückschreib-Status: leer = synchron, `pending`, `failed` (+ Fehlertext) |
| `ci_id` | string(36), nullable, Index | Entity-Registry-UUID des Configuration Items; Join-Schlüssel für das Wartungs-Read-Model |
| `created_at` | datetime | Anlagezeitpunkt |
| `updated_at` | datetime | Letzter Änderungszeitpunkt |

Tabelle: `server_manager_maintenance` (Read-Model der Wartungsfenster, seit 1.4.0; wird vom
bestehenden `create_all` unter dem Bootstrap-Lock angelegt)

| Spalte | Typ | Beschreibung |
|---|---|---|
| `id` | int | Primärschlüssel |
| `ci_id` | string(36), Index | Configuration Item, auch ohne Server-Record |
| `kind` | string(16) | `window` (publizierter Fenstersatz) oder `override` |
| `source` | string(64), Index | Emitter (`iac-orchestrator`, `monitoring`, …) |
| `override_id` | string(128), Index | Override-ID; bei `window`-Zeilen leer (`""`, damit der Unique-Key greift) |
| `windows` | JSON, nullable | Fenster in der Bus-Normalform (`{"type": …}`) |
| `inherited_from` | string(32), nullable | `host`, `stage_default`, `site_default` oder leer |
| `until` / `started_at` / `ended_at` | datetime (UTC, naiv), nullable | Override-Zeiten |
| `linger_minutes` | int, nullable | Nachlauf nach `ended_at` (Default 15) |
| `reason` | text, nullable | Grund des Overrides |
| `updated_at` | datetime | Letzter Schreibzeitpunkt |

Unique-Key: `(ci_id, source, kind, override_id)`. Abgelaufene Overrides werden beim Lesen ignoriert
und ab einem Tag nach ihrem effektiven Ende gelegentlich gelöscht (bei Schreibvorgängen, höchstens
alle 10 Minuten je Prozess).

---

## Entwicklung & Tests

```bash
# Aus dem Plugin-Verzeichnis (lyndrix-plugin-server-manager/)
pip install -r requirements-dev.txt

# Tests ausführen
pytest

# Typprüfung
mypy .

# Linter
ruff check .

# Formatter prüfen
black --check .
```

Model- und Controller-Schicht sind ohne laufenden Core testbar. `ModuleContext` kann für Lifecycle-Tests gemockt werden.
