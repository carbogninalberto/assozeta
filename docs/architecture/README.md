# Architecture documentation index

Use this directory to understand system behavior from a technical and operations perspective.

- [architecture-overview.md](./architecture-overview.md)  
  End-to-end architecture shape and module responsibilities.
- [functionality-map.md](./functionality-map.md)  
  Domain-by-domain feature mapping.
- [permissions-and-access.md](./permissions-and-access.md)  
  Authentication, permission middleware, and route-level auth behavior.
- [deployment-runtime.md](./deployment-runtime.md)  
  Service/runtime topology and operations.
- [coverage-gaps.md](./coverage-gaps.md)  
  Route/permission mismatches and review risk points.
- [coverage-metrics.md](./coverage-metrics.md)  
  Current route and permission coverage numbers.

Run order for architecture docs:

1. `analyze_architecture.mjs`
2. `generate_docs_summary.mjs`
3. `generate_coverage_gaps.mjs`
4. Review `coverage-gaps.md` before changes to auth, routing, or permissions.
