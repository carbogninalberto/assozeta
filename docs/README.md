# Assozeta documentation hub

This folder is the project-wide documentation pack for architecture and functionality.
Everything here is markdown-first and generated from repository analysis where possible.

## Quick navigation

Start here for architecture exploration:

- [Architecture overview](./architecture/architecture-overview.md)
- [Functionality map](./architecture/functionality-map.md)
- [Permissions & access control](./architecture/permissions-and-access.md)
- [Deployment/runtime model](./architecture/deployment-runtime.md)
- [Coverage gaps and risks](./architecture/coverage-gaps.md)

Artifacts and evidence:

- [Coverage metrics](./architecture/coverage-metrics.md)
- [Mermaid diagrams (sources)](./diagrams)
- [Rendered Mermaid artifacts](./diagrams/rendered)
- [Screenshot capture guide](./scripts/capture_system_screenshots.mjs)
- Rendered screenshot manifest: `docs/screenshots/index.md` (generated when capture runs)

Data and automation:

- [Inventory JSON](./matrix/architecture-inventory.json)
- [Inventory markdown](./matrix/architecture-inventory.md)
- [Architecture matrix scripts](./scripts/analyze_architecture.mjs)
- [Docs summary generator](./scripts/generate_docs_summary.mjs)
- [Gap generator](./scripts/generate_coverage_gaps.mjs)
- [Mermaid renderer](./scripts/render_mermaid_diagrams.mjs)

## Current structure (reference)

- `docs/architecture/`: human docs by domain and concern
- `docs/diagrams/`: Mermaid sources
- `docs/diagrams/rendered/`: rendered Mermaid SVG artifacts
- `docs/matrix/`: machine-readable and machine-generated route/permission inventory
- `docs/scripts/`: reproducible documentation scripts
- `docs/screenshots/`: UI route screenshots and generated manifest
- `docs/goal-prompts/`: actionable goal prompt for docs remediation

## How to keep docs in sync

1. Regenerate baseline inventory:

```bash
node docs/scripts/analyze_architecture.mjs
```

2. Refresh summary artifacts:

```bash
node docs/scripts/generate_docs_summary.mjs
```

3. Render Mermaid diagrams:

```bash
node docs/scripts/render_mermaid_diagrams.mjs
```

4. Regenerate coverage gap register:

```bash
node docs/scripts/generate_coverage_gaps.mjs
```

5. Capture UI screenshots (requires app running on `--base-url`):

```bash
node docs/scripts/capture_system_screenshots.mjs --base-url=http://localhost:5001
```

If Playwright is not already available in this checkout, install it locally before running screenshots:

```bash
npm install -D playwright
```

6. If UI routes evolve, rerun inventory/summary/gaps and review:
   - `docs/architecture/coverage-gaps.md`
   - `docs/architecture/functionality-map.md` for route mapping deltas.

## One-command refresh (quick)

```bash
node docs/scripts/analyze_architecture.mjs \
&& node docs/scripts/generate_docs_summary.mjs \
&& node docs/scripts/generate_coverage_gaps.mjs \
&& node docs/scripts/render_mermaid_diagrams.mjs
```

If you also maintain screenshots:

```bash
npm install -D playwright
node docs/scripts/capture_system_screenshots.mjs --base-url=http://localhost:5001
```

## Conventions used

- Files are split by concern (architecture, functions, permissions, deployment, gaps).
- Counts and route coverage are derived from script outputs, not guessed.
- All workflow commands are documented in the relevant files.
