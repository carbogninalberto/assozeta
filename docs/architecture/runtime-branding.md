# Runtime instance branding

`UI/src/utils/BrandTheme.js` is the palette source. `instanceStore.applyRuntimeConfig` applies it for fetched, cached, refreshed and saved configuration. Startup also supports compile-time OEM colors. A single observer responds to changes of the root `data-theme` attribute.

The configured brand remains unchanged in light mode and browser metadata. Dark mode adapts it for readability. Hover, active, subtle backgrounds, borders, links and focus colors are derived; each solid primary state has a black/white foreground selected for contrast. `--text-primary` still means normal text, not branding. `--main-color` and `--primary-light` are compatibility aliases.

Application CSS consumes palette variables. `app-bundle.css` is the maintained application stylesheet, while the upstream `bootstrap.min.css` stays vendor-owned. `brand.css` contains startup defaults and the final shared overrides. A serialized, validated palette snapshot restores branding before app startup and on error/offline pages; API configuration remains authoritative.

Canvas charts and Stripe Elements subscribe to `brandPalette` because CSS cannot update their rendered content. The email editor rebuilds its UI theme and re-renders its provider without remounting the document. Its mount API and bundle replacement requirements are documented alongside the editor.

Setup uses a scoped draft palette and does not modify the saved instance. Membership card colors use `null`/missing to mean inherited; explicit saved colors, including old purple values, remain custom. The settings action “Usa il colore dell’istanza” restores inheritance. Transactional email templates resolve validated literal colors once per render through the `instance_branding` template tag, including background worker renders.

Semantic success, warning, danger and info colors, categorical chart series, branding presets, logos/artwork and authored email colors are not instance-primary colors.

## Verification

- `npm --prefix UI test`: palette contrast/validation plus the existing unit suite.
- `npm --prefix UI run theme:verify`: targeted protection against hardcoded brand colors in primary rules and application components.
- From `selfhost/tests/browser`, `npx playwright test --config brand-theme.config.js`: actual branding save, fresh/cached/background-refreshed configuration, light/dark styles, hover/active/disabled states, charts, both mounted payment forms, card inheritance, editor content preservation, setup draft and standalone pages. Backend and Stripe calls use local fixtures; no live payments or instance configuration are changed.
- From `BE`, `../.venv/bin/python manage.py test instance.tests.test_branding --noinput`: rendering checks across all ten transactional email templates, light/dark brand choices, safe fallback.
- `npm --prefix UI run build:vite:production`: production compilation.
