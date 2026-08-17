# Goal prompt: fix architecture and functionality documentation for Assozeta

Create a complete markdown documentation package for Assozeta’s system architecture and features, with evidence-backed coverage of all major backend and frontend surfaces.

This request is for documentation quality only: no functional product behavior changes are requested unless a blocker is found in docs evidence.

## Objective

Produce a comprehensive, structured `docs/` set that fully describes:

- system architecture (runtime topology, communication flow, authN/Z, async jobs, websocket channels),
- functional domains (memberships, courses, accounting, communications, billing, documents, exports, imports, integrations, AI),
- route and permission surface alignment,
- deployment and operations behavior,
- and a reproducible screenshot and diagram workflow using Playwright + Mermaid.

## Scope

- **Backend:** `BE/` Django stack (projects, URL routers, services, middleware, background workers, and tasking).
- **Frontend:** `UI/` Svelte SPA routes, routing registry, permissions, and client configuration.
- **Infrastructure:** `selfhost/` compose and lifecycle scripts.
- **Documentation pipeline:** `docs/` artifacts and generation scripts.
- **Quality baseline:** currently available artifacts:
  - `docs/matrix/architecture-inventory.json`
  - `docs/matrix/architecture-inventory.md`

## Required work

1. **Establish documentation structure in Markdown**
   - Keep docs under `docs/` with clear purpose folders:
     - `docs/architecture/` (technical architecture + data/authorization flows)
     - `docs/diagrams/` (Mermaid sources)
     - `docs/scripts/` (documentation support scripts)
     - `docs/matrix/` (machine inventory outputs, already present)
   - Add an index page describing document entry points and maintenance strategy.

2. **Run and lock a complete architecture inventory baseline**
   - Run:
     - `node docs/scripts/analyze_architecture.mjs`
   - Preserve outputs in:
     - `docs/matrix/architecture-inventory.json`
     - `docs/matrix/architecture-inventory.md`
   - Keep route counts and coverage facts as baseline inputs for all docs.

3. **Generate an architecture documentation bundle**
   - Add/update markdown files that explain:
     - request entry points and runtime boundaries,
     - domain decomposition (module responsibility by folder/API prefix/UI domain),
     - authN/authZ model (JWT/permissions/collaborator logic),
     - async/queue processing (Celery + Channels + scheduled jobs),
     - deployment topology (Docker services, external integrations).
   - Include Mermaid diagrams for:
     - system topology,
     - request + websocket runtime,
     - deployment stack dependencies.
   - Render Mermaid sources and verify output (at least non-empty `.svg` files in `docs/diagrams/rendered/`).

4. **Document functional coverage by domain**
   - Build a feature matrix from observed prefixes and route groups:
     - backend top-level API groups (`subscription`, `course`, `payment`, `communication`, `invoice`, `balance-sheet`, `modules`, etc.),
     - frontend top-level route groups (`members`, `course`, `subscription`, `accounting`, `payment`, `stripe`, `communication`, etc.).
   - Map each domain to:
     - primary backend module(s),
     - front-end route family,
     - entry/API touchpoints,
     - notable integrations.

5. **Document security and access controls**
   - Capture and explain:
     - permission registry strategy and default behavior,
     - middleware stack interactions,
     - collaborator impersonation/permission fallback behavior,
     - explicit “not enforced” and “explicitly excluded” paths.

6. **Capture and link documentation evidence assets**
   - Add a Playwright script to collect screenshot evidence for UI route families.
   - Add a Mermaid rendering workflow and executable script using `@mermaid-js/mermaid-cli`.
   - Generate and version a screenshot manifest, and link visual artifacts in docs.

7. **Close the loop with a gap report**
   - Add a gap file documenting:
     - backend routes without registry entries,
     - frontend routes missing explicit permission checks,
     - permissions declared without current backend matches,
     - any known intentional exceptions.

## Acceptance criteria

- A markdown documentation tree exists under `docs/` with at least:
  - architecture overview file,
  - functional map by domain,
  - permissions/access-control explanation,
  - deployment/operations reference,
  - and route/coverage gap matrix.
- `docs/matrix/architecture-inventory.md` and `.json` are regenerated and linked from `docs/`.
- Mermaid sources are committed in `docs/diagrams/` for:
  - topology,
  - request/auth/runtime flow,
  - deployment stack.
- A Playwright capture script is present and runnable with clear auth and non-auth modes.
- Gap report is explicit about current mismatch numbers and file-level sources.

## Concrete expected outputs

1. `docs/README.md`
2. `docs/architecture/architecture-overview.md`
3. `docs/architecture/functionality-map.md`
4. `docs/architecture/permissions-and-access.md`
5. `docs/architecture/deployment-runtime.md`
6. `docs/architecture/coverage-gaps.md`
7. `docs/diagrams/system-topology.mmd`
8. `docs/diagrams/request-runtime-flow.mmd`
9. `docs/scripts/capture_system_screenshots.mjs`
10. `docs/scripts/generate_docs_summary.mjs`
11. `docs/scripts/render_mermaid_diagrams.mjs`
12. `docs/matrix/*` inventory outputs refreshed

---

## Working assumptions

- No markdown docs existed at required depth before this task; this request covers creating that structure from scratch where needed.
- Route inventory is used as the source of truth for endpoint/coverage claims unless code inspection indicates clear exceptions.
