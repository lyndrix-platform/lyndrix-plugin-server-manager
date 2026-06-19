# Lyndrix Server Manager

Central server inventory built around YAML/JSON hardware catalogs and combination rules, with event-bus hooks for downstream order workflows.

- **Repository:** [https://github.com/lyndrix-platform/lyndrix-plugin-server-manager](https://github.com/lyndrix-platform/lyndrix-plugin-server-manager)
- **Platform docs:** [Lyndrix Core](https://docs.lyndrix.eu) · [Plugin ecosystem](https://docs.lyndrix.eu/ecosystem/)

## Features

- Server inventory management with a guided configuration wizard
- Configurable hardware catalogs and combination/validation rules
- Event-driven hooks (server_created, status_changed, …)
- Dashboard widget, settings UI, and REST API

## Installation

Install **Server Manager** from the Lyndrix **Plugin Manager**, or declare it for
reconciliation on boot via `LYNDRIX_PLUGINS_DESIRED`:

```text
https://github.com/lyndrix-platform/lyndrix-plugin-server-manager
```

See the [Plugin Development Guide](https://docs.lyndrix.eu/plugins/) for the plugin model and
lifecycle, and [Usage](usage.md) / [Configuration](configuration.md) for details.
