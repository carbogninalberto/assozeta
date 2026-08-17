# Assozeta documentation hub

This folder is the project-wide documentation pack for architecture and functionality.
Everything here is markdown-first and generated from repository analysis where possible.

## Current structure

- `docs/matrix/`
  - Baseline machine-readable inventory:
    - `architecture-inventory.json`
    - `architecture-inventory.md`
- `docs/architecture/`
  - Human-readable architecture, feature, and security documentation:
    - `architecture-overview.md`
    - `functionality-map.md`
    - `permissions-and-access.md`
    - `deployment-runtime.md`
    - `coverage-gaps.md`
- `docs/diagrams/`
  - Mermaid source files for architecture visualization:
    - `system-topology.mmd`
    - `request-runtime-flow.mmd`
- `docs/scripts/`
  - Documentation and validation helpers:
    - `analyze_architecture.mjs` (existing baseline extractor)
    - `generate_docs_summary.mjs` (new)
    - `capture_system_screenshots.mjs` (new)
    - `render_mermaid_diagrams.mjs` (new)

- `docs/diagrams/`
  - Mermaid source files for architecture visualization:
    - `system-topology.mmd`
    - `request-runtime-flow.mmd`

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

## Conventions used

- Files are split by concern (architecture, functions, permissions, deployment, gaps).
- Counts and route coverage are derived from script outputs, not guessed.
- All workflow commands are documented in the relevant files.
