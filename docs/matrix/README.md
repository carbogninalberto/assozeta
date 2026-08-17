# Matrix data index

Machine-generated documentation inputs that drive most of the coverage docs:

- [`architecture-inventory.json`](./architecture-inventory.json): canonical structured inventory with routes, permissions, and front-end checks.
- [`architecture-inventory.md`](./architecture-inventory.md): human-readable extracted inventory.

Regeneration command:

```bash
node docs/scripts/analyze_architecture.mjs
```

When this output changes, regenerate:

- `docs/architecture/coverage-metrics.md`
- `docs/architecture/coverage-gaps.md`
