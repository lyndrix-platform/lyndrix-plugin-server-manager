# Example Catalog

This folder contains a compact, annotated reference version of the server-manager catalog.

Use it when you want to see which YAML keys are consumed by the loader and UI:

- `hardware.yml` for selectable hardware items.
- `environments.yml` for provider/stage routing.
- `profiles.yml` for profile-level limits and rules.
- `products.yml` for product constraints and resource matrices.
- `settings.yml` for global status and OS options.

The example files are intentionally small, but they follow the same schema as the live catalog under `../catalog/`.

## Built-in fallback

The live `catalog/` directory is site-specific and not part of the repository. When the configured
catalog directory is missing or holds no catalog files, the loader serves this folder instead
(`bundled_fallback: true` in `GET /catalog` and in the plugin health details), so a fresh install
has a working wizard. Copy these files to `catalog/` (or another directory inside the plugin root,
set under Settings) and adapt them; from then on your directory is used as a whole.
