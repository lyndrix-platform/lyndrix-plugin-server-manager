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

Das Plugin abonniert keine externen Events.

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
