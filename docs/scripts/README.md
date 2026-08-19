# Documentation scripts

| Script | Purpose | Primary command |
| --- | --- | --- |
| `analyze_architecture.mjs` | Rebuild machine-readable inventory | `node docs/scripts/analyze_architecture.mjs` |
| `generate_docs_summary.mjs` | Produce coverage metrics | `node docs/scripts/generate_docs_summary.mjs` |
| `generate_coverage_gaps.mjs` | Produce risk/mismatch register | `node docs/scripts/generate_coverage_gaps.mjs` |
| `render_mermaid_diagrams.mjs` | Render `.mmd` files to SVG | `node docs/scripts/render_mermaid_diagrams.mjs` |

Execution order:

```bash
node docs/scripts/analyze_architecture.mjs \
  && node docs/scripts/generate_docs_summary.mjs \
  && node docs/scripts/generate_coverage_gaps.mjs \
  && node docs/scripts/render_mermaid_diagrams.mjs
```
