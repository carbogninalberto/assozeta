# Documentation scripts

| Script | Purpose | Primary command |
| --- | --- | --- |
| `analyze_architecture.mjs` | Rebuild machine-readable inventory | `node docs/scripts/analyze_architecture.mjs` |
| `generate_docs_summary.mjs` | Produce coverage metrics | `node docs/scripts/generate_docs_summary.mjs` |
| `generate_coverage_gaps.mjs` | Produce risk/mismatch register | `node docs/scripts/generate_coverage_gaps.mjs` |
| `render_mermaid_diagrams.mjs` | Render `.mmd` files to SVG | `node docs/scripts/render_mermaid_diagrams.mjs` |
| `capture_system_screenshots.mjs` | Capture UI route screenshots | `node docs/scripts/capture_system_screenshots.mjs --base-url=http://localhost:5001` |

Execution order:

```bash
node docs/scripts/analyze_architecture.mjs \
  && node docs/scripts/generate_docs_summary.mjs \
  && node docs/scripts/generate_coverage_gaps.mjs \
  && node docs/scripts/render_mermaid_diagrams.mjs
```

Playwright is optional and only required by the screenshot script.
