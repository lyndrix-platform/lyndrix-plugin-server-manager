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
    ├── api.py                  # FastAPI-Router (Server-CRUD + Katalog)
    ├── model/
    │   ├── models.py           # SQLAlchemy-Modell (Tabelle: server_manager_servers)
    │   ├── database.py         # DB-Session-Helfer, Tabelleninitialisierung
    │   └── catalog.py          # YAML/JSON-Katalog-Loader + Regel-Evaluator
    ├── controller/
    │   ├── service.py          # CRUD + Event-Emission (Singleton: server_manager_service)
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
| `GET` | `/servers` | Vollständige Server-Liste |
| `POST` | `/servers` | Server hinzufügen (Hardware-Profil + Umgebung) |
| `PUT` | `/servers/{id}` | Server aktualisieren |
| `DELETE` | `/servers/{id}` | Server entfernen |
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

## Konfiguration & Katalog

Die Hardware- und Umgebungskataloge werden aus YAML-Dateien im `examples/`-Verzeichnis geladen. Sie definieren:

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
| `created_at` | datetime | Anlagezeitpunkt |
| `updated_at` | datetime | Letzter Änderungszeitpunkt |

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
