# lyndrix-server-manager

Grundbasis für ein Lyndrix-Plugin zur Server-Verwaltung.

## Enthaltene Basis

- `entrypoint.py` mit `ModuleManifest`
- `setup(ctx)` mit UI-Route `/server-manager`
- `render_dashboard_widget(ctx)` für die Lyndrix-Dashboard-Kachel
- `render_settings_ui(ctx)` für die Plugin-Einstellungen

## Einbindung in Lyndrix Core

1. Repository als Plugin in `app/plugins/server_manager` einbinden
2. Lyndrix Core starten
3. Plugin aktivieren
4. UI unter `/server-manager` aufrufen
