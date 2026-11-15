# Assozeta Marketing Site (WEB)

Static marketing site for [Assozeta](https://assozeta.it), the open source management software (gestionale) for
Italian amateur sports associations (ASD/SSD). Built with SvelteKit and the static adapter.

## Canonical origin

`https://assozeta.it` — configured centrally in `src/lib/config/site.ts`.

## Stack

- **SvelteKit** (prerendered static, `@sveltejs/adapter-static`, `strict: true`)
- **Svelte 5** (runes mode)
- **Tailwind CSS v4** (no `tailwind.config`; tokens live in `src/app.css`)
- **shadcn-svelte** primitives (`src/lib/components/ui/*`)
- **Lucide** icons (`@lucide/svelte/icons/<kebab-name>`)

All copy is Italian (`<html lang="it">`). The code and comments are English.

## Scripts

| Command                   | What it does                                                        |
| ------------------------- | ------------------------------------------------------------------- |
| `npm run dev`             | Start the dev server                                                |
| `npm run build`           | Prerender the static site to `build/`                               |
| `npm run preview`         | Preview the production build                                        |
| `npm run check`           | Run `svelte-check` for type errors                                  |
| `npm run test:unit`       | Node unit tests (`tests/unit/*.test.mjs`)                            |
| `npm run validate-seo`    | Run the SEO/link validation on `build/`                             |
| `npm run validate`        | Unit tests + build + SEO validation (CI and image build gate)       |
| `npm run test:responsive` | Playwright end-to-end checks (`tests/e2e/*.test.ts`)                |

## Theme

Light is the default and the server-rendered theme. A visitor can switch to dark with the toggle in the navbar; the
choice is stored in `localStorage` under the key `assozeta-theme` (`light` or `dark`) and is never sent to a server.
The OS preference is intentionally ignored, so the toggle is the only thing that changes the theme.

A small inline script in `src/app.html` reads the stored value before first paint and adds the `dark` class to
`<html>`, so there is no flash of the wrong theme. `src/lib/theme.svelte.ts` owns the state (`sync`, `set`, `toggle`)
and keeps `<meta name="theme-color">` in sync. `ThemeToggle.svelte` renders the moon/sun button with the Italian
labels `Attiva il tema scuro` / `Attiva il tema chiaro`.

`Logo.svelte` renders both `static/logo.svg` (brand purple, `dark:hidden`) and `static/logo-white.svg`
(`hidden dark:block`), so the logo follows the theme with CSS only.

## Design tokens

`src/app.css` defines the light theme on `:root` and the dark theme on `.dark`, with the `dark:` variant scoped to
`.dark`. Components must use the semantic utilities (`bg-background`, `text-foreground`, `text-muted-foreground`,
`bg-card`, `bg-subtle`, `bg-surface`, `bg-surface-bar`, `border-border`, `text-brand`, `bg-primary`,
`text-primary-foreground`, `bg-brand-soft`, `border-primary/30`, `shadow-mock`, `bg-success` …). Never hardcode
`white`, `black`, hex colours or the fixed `brand-50…brand-900` scale in component classes; the fixed scale exists
only for rare accents.

Shared utilities: `.gradient-text`, `.hero-grid`, `.noise-overlay`, `.ring-gradient`, `.beam`, `.shadow-mock`,
`.animate-fade-up`, `.animate-glow`, `.animate-float`, `.animate-pulse-dot`, `kbd.key`, `.log-line`.

## Content registries

- `src/lib/data/routes.ts` — single source of truth for hubs, child slugs, standalone pages, breadcrumbs,
  `allRoutes()` (sitemap input), the JSON-LD builders (`ORGANIZATION_LD`, `WEBSITE_LD`, `SOFTWARE_APP_LD`,
  `webPageLd`, `faqPageLd`) and the `ContentPage`/`ContentSection` shapes.
- `src/lib/data/features.ts` — content for the eight `/funzionalita/<slug>` pages.
- `src/lib/data/self-hosting.ts` — content for the six `/self-hosting/<slug>` guides.
- `src/lib/data/assozeta-facts.ts` — the verifiable facts (release version, stack, limits) reused by the homepage,
  the feature pages and the structured data.
- `src/lib/data/faq.ts` — homepage FAQ items; they feed `faqPageLd()` and must also be visible as text.
- `src/lib/data/navigation.ts` — navbar dropdowns, mobile sheet and footer columns, read from `routes.ts`.
- `src/lib/config/site.ts` — `ORIGIN`, `SITE`, `LINKS`, `url()`, `resolveMeta()`.

## Facts policy

Every claim in the copy must be verifiable in the Assozeta repository documentation (`README.md`, `selfhost/README.md`,
`selfhost/UPDATES.md`, `selfhost/OPERATIONS.md`, `selfhost/DATA_RESTORE.md`, `manuale/`). Do not invent numbers,
customers, testimonials, ratings, certifications, integrations or compliance statements. Product mocks show sample
data (fictional Italian names), sit inside a `<figure>` with `aria-hidden` content and an sr-only `<figcaption>`, and
are labelled as illustrations with sample data. Feature availability may depend on the instance configuration and on
the optional external services enabled.

## Route map

```
/                                      Homepage
/funzionalita                          Feature hub
/funzionalita/soci-e-tesserati         Feature detail (8 pages)
/funzionalita/iscrizioni-online
/funzionalita/corsi-e-presenze
/funzionalita/certificati-e-documenti
/funzionalita/pagamenti-e-ricevute
/funzionalita/contabilita
/funzionalita/comunicazioni-e-automazioni
/funzionalita/bacheca-e-report
/self-hosting                          Self-hosting hub
/self-hosting/requisiti                Guide (6 pages)
/self-hosting/installazione
/self-hosting/configurazione
/self-hosting/aggiornamenti
/self-hosting/backup-e-ripristino
/self-hosting/manutenzione
/open-source                           Why the project is open source
/licenza                               GNU AGPL v3.0 summary
/sicurezza                             Security measures and operator duties
/chi-siamo                             Project, Bakney and contacts
/privacy                               Privacy policy for this website
/404                                   Not found (noindex, not in the sitemap)
/sitemap.xml                           XML sitemap generated from routes.ts
```

Every non-home page uses `<Breadcrumbs>` and ends with `<CtaSection>`. The 404 page is served by Caddy with HTTP 404
for unknown paths and is excluded from the sitemap.

## Deployment

`deploy/Dockerfile` builds the site (`npm run validate`, so unit tests and SEO checks gate the image) and serves
`build/` with Caddy using `deploy/Caddyfile`. Build from this directory:

```bash
docker build -f deploy/Dockerfile -t assozeta-web .
```

The Caddyfile uses `try_files {path} {path}.html {path}/index.html`, returns the prerendered `/404` page with HTTP 404
for unknown paths, caches the hashed `/_app/immutable/*` assets for a year, and sets `X-Content-Type-Options`,
`X-Frame-Options`, `Referrer-Policy` and a restrictive `Permissions-Policy`. TLS and HSTS are handled by the edge proxy in front of the container.

The image is fully static: rebuild and redeploy it after a content change. There is no runtime API, no database and no
server-side rendering.

## SEO validation

`scripts/validate-seo.mjs` reads the indexable routes from `build/sitemap.xml`, adds `/404`, and checks:

1. Every route produces an HTML file, and every prerendered page is listed in the sitemap
2. `<html lang="it">`, a `<title>`, a meta description and a canonical URL starting with `https://assozeta.it`
3. Exactly one `<h1>` per page; no duplicate titles or descriptions
4. Robots directives (`index` on indexable pages, `noindex` on `/404`) and the full Open Graph/Twitter tags
5. JSON-LD blocks parse; no `aggregateRating` is published anywhere
6. Images carry `alt` plus intrinsic `width`/`height`
7. Internal links resolve to built files; fragments (`/path#id`, `/#id`) resolve to an existing `id`
8. Pages publishing `FAQPage` JSON-LD show every question as visible text
9. `sitemap.xml` never contains `/404`; `robots.txt` declares `https://assozeta.it/sitemap.xml`
10. `social-card.png` exists and is a 1200 × 630 PNG (a warning above 200 KB)

The script prints a summary and exits with status 1 on errors.

Playwright (`tests/e2e/`) then checks that every sitemap route has no horizontal overflow at 320 px in both themes and
that the theme toggle, logo swap, `theme-color` meta, persistence, no-flash dark load, skip link and mobile menu work.

The social card is generated once with Playwright Chromium from a throwaway HTML file; regenerate it when the
headline or the brand assets change.
