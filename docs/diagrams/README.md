# Architecture diagrams

Source and rendered artifacts for architecture diagrams:

- Source:
  - [system-topology.mmd](./system-topology.mmd)
  - [request-runtime-flow.mmd](./request-runtime-flow.mmd)
  - [deployment-stack.mmd](./deployment-stack.mmd)
- Rendered:
  - [request-runtime-flow.svg](./rendered/request-runtime-flow.svg)
  - [system-topology.svg](./rendered/system-topology.svg)
  - [deployment-stack.svg](./rendered/deployment-stack.svg)

Render command:

```bash
node docs/scripts/render_mermaid_diagrams.mjs
```

The renderer validates every `.mmd` source with Mermaid CLI and writes the corresponding SVG under [`rendered/`](./rendered/). Keep labels quoted when they contain punctuation such as `/`, `(`, `)`, `:` or `-` to avoid GitHub Mermaid parser differences.
