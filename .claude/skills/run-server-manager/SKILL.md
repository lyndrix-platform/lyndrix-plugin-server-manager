---
name: run-server-manager
description: Run, launch, and screenshot the Server Manager (/server-manager) dashboard plugin. This plugin has NO standalone run path — it only runs mounted inside the lyndrix-core dev stack; use this to start/screenshot the Server Manager inventory UI or verify a server-manager UI change in the actually-running app.
---

# Run the Server Manager (/server-manager) plugin

This repo is a **lyndrix-core plugin**, not an app — no `main`, no server of its own.
The only way to run it is to boot the **lyndrix-core** dev stack (FastAPI + NiceGUI +
Vault + MariaDB) with this repo **volume-mounted** as `/app/plugins/server_manager`
and the plugin **enabled**, then open its UI route **`/server-manager`**. NiceGUI
renders over a websocket, so `curl` only sees an empty shell — to *see* the page you
need a real browser. `driver.py` (a copy of the core skill's Playwright/Chromium
driver, default route `/server-manager`) logs in and screenshots it.

Same harness as `run-lyndrix-core` / `run-iac-orchestrator` — same compose stack,
same shared venv, same driver. Paths below are relative to this plugin repo root
(`lyndrix-plugin-server-manager/`); the core repo is its sibling `../lyndrix-core/`.

## Prerequisites

The driver runtime (Python venv + Chromium) lives in the **core** repo and is shared.
`chromium-cli` is not available on this host; Playwright is used instead. Create it
once if missing:

```bash
python3 -m venv ../lyndrix-core/.dev/run-venv
. ../lyndrix-core/.dev/run-venv/bin/activate
pip install playwright
python -m playwright install chromium
sudo $(which python) -m playwright install-deps chromium   # system libs (libnspr4/libnss3/…)
```

## Bring up the stack (with this plugin mounted)

The plugin is already wired into `../lyndrix-core/docker/docker-compose.dev.yml`:

```
- ../../lyndrix-plugin-server-manager:/app/plugins/server_manager
```

If `docker ps` already shows `lyndrix-core-dev` on `:8081`, skip this:

```bash
docker compose -f ../lyndrix-core/docker/docker-compose.dev.yml up -d --build
```

## Ensure the plugin is enabled

The manifest sets `auto_enable_on_install=False` (`entrypoint.py`), so on a fresh DB
the plugin is discovered but starts **inactive** and `/server-manager` will not render.
Confirm it is active — it appears in `/api/health` when enabled:

```bash
curl -s http://localhost:8081/api/health | python3 -m json.tool | grep server_manager
# "lyndrix.plugin.server_manager": {
```

## Run (agent path) — screenshot the UI

The admin password comes from `../lyndrix-core/docker/.env.dev` (never hardcode it):

```bash
export LYNDRIX_ADMIN_PASSWORD=$(grep -E '^LYNDRIX_ADMIN_PASSWORD=' ../lyndrix-core/docker/.env.dev | cut -d= -f2-)
../lyndrix-core/.dev/run-venv/bin/python .claude/skills/run-server-manager/driver.py --no-mobile --outdir /tmp/sm-shots
```

Output (verified):

```
core_version=0.2.2 api_version=1.2.0 plugins=6
login: ok
shot: /tmp/sm-shots/login.desktop.png
shot: /tmp/sm-shots/server-manager.desktop.png
wrote 2 screenshots to /tmp/sm-shots/
```

`/tmp/sm-shots/server-manager.desktop.png` shows the **Server Manager** dashboard
(Total Servers / Staging KPI cards, search + filters, **Add Server**, and the server
inventory cards). Useful flags: `--health-only` (no browser), `--routes /server-manager`
(explicit), `--base/--user/--password` overrides. Drop `--no-mobile` for a 390px shot too.

## Run (React bundle) — the lyndrix-ui shell

The plugin also ships a **React bundle** (`src/ui/PluginApp.tsx` → built to
`ui_static/ui_bundle.js`) that renders inside the **lyndrix-ui** shell (Vite dev
server on `:5173`), in addition to the NiceGUI page above. Drive it with the sibling
**run-lyndrix-ui** driver. The route is `/apps/<safeId>/server-manager`, where
`safeId` is the plugin id with dots→dashes: `lyndrix.plugin.server_manager` →
**`lyndrix-plugin-server_manager`** (the underscore stays — an all-dashes spelling
bounces to the dashboard):

```bash
UI=../lyndrix-ui/.claude/skills/run-lyndrix-ui/driver.mjs
node "$UI" /apps/lyndrix-plugin-server_manager/server-manager          /tmp/sm-react.png
node "$UI" /apps/lyndrix-plugin-server_manager/server-manager/settings /tmp/sm-react-settings.png
```

Verified: the React main-view header carries **Aktualisieren · + Server · Settings**;
the **Settings** button navigates to `/server-manager/settings`, which renders
"Server Manager — Einstellungen" (Katalog-Konfiguration + CPU/RAM/Storage/Profile/
Produkte counts) with a back arrow.

## Gotchas

- **No standalone run.** There is no `python -m server_manager` / dev server. It only
  renders inside core at `/server-manager`. A direct `curl /server-manager` returns the
  empty NiceGUI shell — always use the browser driver.
- **Password, not hardcoded.** The driver reads `LYNDRIX_ADMIN_PASSWORD` from the env;
  the dev value lives in `../lyndrix-core/docker/.env.dev`. Without it the driver exits 2.
- **Plugin must be enabled.** `auto_enable_on_install=False` → enable it in the Plugin
  Manager (or it won't be in `/api/health` and `/server-manager` 302-redirects to `/login`).
- **NiceGUI WS auth timing.** Auth is hydrated over the websocket after navigation; the
  driver sleeps ~2 s post-login and retries once if a page bounces to `/login`. A
  screenshot that shows the login page = the retry still lost the race; just re-run.
- **Shared venv lives in core.** `../lyndrix-core/.dev/run-venv` — this plugin repo has
  no venv of its own. `chromium-cli` is intentionally not used (absent on this host).
- **React-route safeId keeps underscores.** The shell builds plugin routes as
  `plugin.id.replace(/\./g,'-')`, so `lyndrix.plugin.server_manager` →
  `/apps/lyndrix-plugin-server_manager/...`. Spelling it `...server-manager` (all
  dashes) is an unknown route that silently lands on the **dashboard**, not the plugin —
  the screenshot looks like a login worked but shows the wrong page.

## Test

```bash
pip install -r requirements-dev.txt && pytest    # tests/test_smoke.py
```
