# Goal prompt: automatic, code-grounded Assozeta manual with MCP and RAG

Implement a reproducible system that maintains Assozeta's Italian user manual, generates real screenshots and verified steps, and makes the same verified knowledge available through MCP with retrieval-augmented generation (RAG).

Use `https://github.com/Bakney/manuale` as the documentation source repository and `https://manuale.bakney.com/docs/introduzione` as the existing presentation reference. Work on the Assozeta branch `add/manuale`. Keep the published MDX pages in the manual repository; keep application fixtures, browser scenarios, evidence generation, and application integration in Assozeta.

The application's **Manuale d'uso** sidebar entry and header help action must open an embedded reader in a new tab with a standalone layout. That reader must use the same compatible, verified corpus as MCP, with real steps and screenshots, and must not redirect users to the public manual when local knowledge is missing. Verify navigation, section deep links, search, screenshots, and authenticated access in the real browser.

Implement manual retrieval in Assozeta's existing MCP implementation under `BE/application/mcp_server/`, and expose it through the existing agent integration. Do not create a parallel MCP server or require a second MCP connection for the manual. Retrieval storage/indexing may have internal components as needed, but the tool interface belongs to the current MCP.

Every factual statement about application behavior must be grounded in the target Assozeta code. Existing manual prose, generated inventories, screenshots, and model output are discovery inputs, not independent proof of backend behavior. A successful first screenshot or an MCP tool that merely returns search results is not completion.

## Verified starting points

These references were inspected when preparing this prompt. Reinspect them against the execution checkout before making changes.

- Assozeta baseline: `e5628830421303ffe567c4274d98f3e3d6cbbc33`.
- Manual baseline: `0475c2c8b4d397982558c8453a4819587e7a9da9` on `main`.
- [`selfhost/compose.dev.yml`](../../selfhost/compose.dev.yml): development services, backend/frontend source mounts, migrations, `seed_selfhost`, and configurable API/UI ports. It has existing project and image names; a worktree alone does not isolate running services or image builds.
- [`selfhost/bin/assozeta`](../../selfhost/bin/assozeta): environment-aware Compose invocation, project configuration, and development lifecycle commands. Inspect its environment precedence before using it for concurrent manual runs.
- [`BE/application/management/commands/seed_selfhost.py`](../../BE/application/management/commands/seed_selfhost.py): creates/updates a billing plan and renews owner entitlement when configured. It does not create a populated demonstration association.
- [`selfhost/tests/browser/package.json`](../../selfhost/tests/browser/package.json) and [`playwright.config.js`](../../selfhost/tests/browser/playwright.config.js): existing Playwright tooling; the referenced configuration targets `self-instance.spec.js`.
- [`selfhost/tests/browser/self-instance.spec.js`](../../selfhost/tests/browser/self-instance.spec.js): browser actions and assertions against an application instance. Other browser tests, such as `self-instance-reload.spec.js`, capture screenshots using simulated APIs; distinguish those fixtures from real end-to-end evidence.
- [`BE/application/mcp_server/server.py`](../../BE/application/mcp_server/server.py): MCP tool definitions, application data queries/exports, and server construction. [`run_mcp_server.py`](../../BE/application/management/commands/run_mcp_server.py) implements stdio execution; its SSE option currently prints guidance rather than starting an SSE service.
- [`BE/application/agent/core.py`](../../BE/application/agent/core.py) and [`prompts.py`](../../BE/application/agent/prompts.py): existing AI agent tool dispatch and instructions. [`test_mcp_stdio.py`](../../BE/application/tests/test_mcp_stdio.py) covers actual MCP stdio behavior.
- [`BE/application/permissions.py`](../../BE/application/permissions.py) and [`permissions_registry.py`](../../BE/application/permissions_registry.py): starting points for permission evidence; inspect the relevant views and frontend gates as well.
- [`docs/README.md`](../README.md), `docs/architecture/`, and `docs/matrix/`: existing technical documentation and route inventories. These do not replace source inspection.
- Manual repository [`mint.json`](https://github.com/Bakney/manuale/blob/0475c2c8b4d397982558c8453a4819587e7a9da9/mint.json): Mintlify configuration, Venus theme, navigation for documentation, FAQs, and tutorials.
- Manual repository [`CLAUDE.md`](https://github.com/Bakney/manuale/blob/0475c2c8b4d397982558c8453a4819587e7a9da9/CLAUDE.md): Italian content, simple language for nontechnical users, MDX components, and section-based screenshot paths.
- Manual repository [`SCREENSHOTS-NEEDED.md`](https://github.com/Bakney/manuale/blob/0475c2c8b4d397982558c8453a4819587e7a9da9/SCREENSHOTS-NEEDED.md): requested captures and naming conventions. Its feature descriptions and completion counts must be verified, not assumed correct.

Everything below is required new work or an integration to verify, not a claim that it already exists.

## 1. Repository ownership and isolated execution

- Inspect both repositories' instructions, branches, and working changes. Preserve unrelated work.
- Create linked, disposable worktrees for an explicitly selected Assozeta revision and a manual update branch. Accept existing checkout paths and local-only operation; do not require a GitHub write token for generation and verification.
- Record both Git commits, relevant dirty-file hashes if allowed, fixture/scenario versions, and capture settings in each run's manifest. Reject or explicitly label uncommitted inputs; never cite a committed file as evidence for different local contents.
- Isolate Compose project, images where necessary, environment files, ports, database, object storage, and bind-mounted runtime files. Check port conflicts and verify the browser is reaching the intended instance/revision.
- Keep credentials and runtime state out of tracked files. Cleanup must remove only resources owned by that run and preserve source changes and useful diagnostics.
- Preserve the manual site's conventions. Inspect actual deployment wiring before changing branding, navigation, application links, or publishing configuration. Reuse the existing site rather than creating a second documentation system.

## 2. Deterministic demonstration data

- Add a dedicated manual seed using the actual Django models and services. Cover the data and roles required by the verified chapters: association configuration, members, activities, payments, receipts, and other domains only as supported by code.
- Use fictional Italian data, explicit reference dates/timezone, predictable relationships, and reversible scenario state. Create sufficient examples for lists, forms, details, permission differences, and documented edge cases.
- Verify associations, owner setup, entitlement, and required configuration through code; do not assume the existing seed initializes an instance.
- Make reset/reseed repeatable and restrict it to the owned disposable environment. Avoid accumulating duplicate records or changing a developer/live database.
- Use local integration substitutes or explicit sandbox modes for email/payment/external services. Label simulated integration evidence; do not present it as proof of a real external transaction.

## 3. Code grounding and coverage manifest

- Inventory every page referenced by the manual navigation and every entry in the screenshot backlog. Map each page to supported Assozeta routes, UI components, backend handlers/services, model behavior, and authorization rules.
- Create a versioned machine-readable manifest linking page sections or individual claims to source paths, symbols, commit-pinned line ranges, source-content hashes, scenario IDs, role/configuration requirements, screenshot paths, and verification status.
- Use statuses such as verified, stale, unsupported, and needs external verification. Existing prose does not start as verified. Text-only pages, including security and subscription claims, still require evidence; code alone cannot prove external operational or legal guarantees.
- Maintain explicit coverage and gaps for the whole navigation. Do not silently drop unsupported pages to make coverage appear complete. Correct unsupported wording and keep unresolved requirements visible in the report.
- Preserve a clear distinction between what the UI displays, what the backend enforces, and what a browser scenario actually exercised.
- Keep technical provenance in manifests and maintainer reports; user-facing Italian pages should explain actions in ordinary language.

## 4. Browser scenarios and manual updates

- Build a dedicated manual Playwright configuration using the existing dependencies where appropriate. Run screenshot scenarios against the seeded application and real backend, rather than replacing production endpoints with canned successful responses.
- For each documented workflow, assert relevant navigation, labels, role access, action outcomes, and persisted state where needed. Capture named checkpoints only after the screen and required assets have settled.
- Standardize viewport, locale, timezone, theme, and animation behavior. Use the manual's section-based image paths and maintain stable page-to-checkpoint mappings even if numbered filenames are retained.
- Handle scenario dependencies and reset state explicitly. A failure must produce diagnostics and a nonzero result; it must not overwrite verified output with incomplete screenshots or speculative text.
- Update the existing MDX pages, FAQ/tutorial steps, screenshot references, and navigation based on source inspection and observed workflows. Replace Bakney-specific behavior only where the target Assozeta code warrants a change.
- Validate MDX, internal links, image existence, navigation targets, and rendering. Inspect representative rendered pages and all new screenshot layouts for legibility and correct cropping.
- Add an application entry to the manual using the existing navigation/configuration conventions discovered in code. Support the appropriate manual URL for the target deployment without hardcoding a developer checkout address.

## 5. Retrieval corpus and versioned indexing

- Build the retrieval corpus from verified Italian manual sections plus their curated implementation evidence and browser-result metadata. Split MDX by meaningful headings/steps, retaining page hierarchy, links, and screenshot references.
- Store stable chunk IDs, document/section IDs, language, audience, feature/configuration requirements, source paths and hashes, both repository revisions, verification status, and evidence links.
- Exclude unverified claims from normal answers; allow maintainers to inspect gaps through an explicit diagnostic path. Index screenshots as linked capture artifacts with verified captions/step context; do not assume text retrieval understands their pixels.
- Implement retrieval combining keyword matching and semantic similarity, with version/configuration filters applied before results are used. Choose and document the smallest suitable storage/embedding implementation after inspecting deployment constraints; do not assume a vector database or embedding provider is already present.
- Separate release corpora and permit reuse of evidence across revisions only when its relevant implementation/configuration/scenario dependencies remain valid. Never silently fall back to incompatible release content.
- Make indexing repeatable, detect changed/deleted source content, remove obsolete chunks, and atomically promote a validated corpus. Partial indexing or provider failure must retain the previous valid index and report the failure.
- Pin the index format and embedding identity/version; make required configuration and credentials explicit. Retrieval tests should be runnable with deterministic fixtures without paid live model calls.

## 6. MCP tools and grounded answers

- Extend the existing MCP server and agent tool-registration paths. Register the manual tools alongside current tools in `BE/application/mcp_server/server.py`, and wire their functions and definitions into `BE/application/agent/core.py` and relevant prompts. Preserve current clients, tool names, and data-tool behavior. Inspect authentication, deployment, and dispatch behavior rather than assuming tools exposed over MCP automatically appear in the in-process agent.
- Add read-only knowledge tools, for example `search_manual`, `get_manual_section`, and `get_manual_evidence`. Final names may follow repository conventions. Return structured excerpts, canonical page links, pinned provenance, version/configuration applicability, verification status, and relevant screenshot links.
- Provide a working client configuration and invocation example for the existing MCP with the added tools, using an implemented transport. Preserve stdout as JSON-RPC in stdio mode. If a network transport is needed, extend and test the current server's serving/authentication path; do not document the existing SSE placeholder as functional or introduce a separate manual MCP service.
- Integrate retrieval into the existing assistant's response flow so documentation questions trigger retrieval and supported answers cite the relevant manual section. Maintainer tooling can expose code citations; ordinary users should not receive unnecessary implementation details.
- Select target release and user/configuration context from trusted server state where available. Optional client filters may narrow scope but must not elevate access or forge applicability. Enforce any restricted maintainer evidence/role filtering server-side.
- Keep tenant records, secrets, and credentials out of the shared documentation corpus. Retain the existing association scoping of data tools and verify that new knowledge tools do not change it.
- Treat retrieved text as evidence, not executable instructions. Ground behavioral claims in retrieved verified content; describe missing/conflicting/stale evidence explicitly and ask a concise clarification when the user's question is ambiguous.
- Retrieval scores alone are not proof. An unsupported question or incompatible version must yield a clear no-evidence result rather than an invented procedure. RAG reduces unsupported answers but does not guarantee their absence; test citation support and abstention explicitly.

## 7. Reusable skill and automatic orchestration

- Create a reusable `assozeta-manuale` skill with concise instructions and scripts for setup, seed/reset, capture, evidence validation, MDX update, indexing, and cleanup. Commit a portable skill source in the repository and document installation/discovery using the skill conventions available in the execution environment.
- Provide one documented command for a full run and an incremental mode selecting affected pages through explicit source dependencies. A full run must be available to catch dependency gaps and role/configuration changes.
- Add CI automation for documentation-affecting code changes, manual-only changes, and an optional scheduled full check. Define a cross-repository trigger or revision input so both repositories' changes can be reconciled; changes in only one repository must not go unnoticed.
- Automate generation and validation without recurring human screenshot work. If prose updates use an agent/model, specify its runtime, credentials, inputs, limits, and deterministic validation; a checklist that only tells a human to update pages is insufficient.
- Publish run reports and previewable changes, with compatible manual/corpus revision metadata. Define recovery when only one repository's change lands, and do not expose an index as current before its corresponding manual/evidence is available.
- Prepare publication integration for the existing site's actual deployment mechanism and the retrieval index consumed by the existing MCP. External pushes, PR creation, merges, credential installation, and production publication are separate actions; perform them only when authorized by the execution session. Once configured and authorized, routine updates should run without repeated approval requests.
- Preserve diagnostics for failed runs and retain the last verified manual/corpus. Do not repeatedly regenerate failed scenarios indefinitely.

## Acceptance criteria

1. A clean environment can run the documented command to create isolated worktrees/services, seed data, verify workflows, update MDX/screenshots, build a grounded retrieval index, validate outputs, and clean up owned runtime resources.
2. All existing navigation pages and screenshot backlog entries have manifest coverage. Supported pages are updated and verified; unsupported/external-only claims have explicit corrections or gaps. No silent omissions or unsupported claims are indexed as verified.
3. At least one complete workflow spanning navigation, a write operation, persisted backend state, screenshots, MDX, evidence, retrieval, and a cited assistant answer is demonstrated first. Then extend verified coverage across the supported manual; the first demonstration alone is not completion.
4. Repeating a run with identical inputs leaves semantic text, fixtures, evidence, and index identities stable. Screenshot variance is controlled and measured rather than assuming byte-for-byte identity without verification.
5. A targeted UI/backend change invalidates its dependent claims/scenarios and updates the correct chapters. Deletion, permission/configuration changes, stale evidence, and incompatible releases are handled explicitly.
6. A real MCP client connected to the existing server can discover and call both current data tools and new manual tools using the documented transport. The existing assistant can answer Italian manual questions with supporting citations through its current tool-dispatch flow; no second MCP connection is required.
7. Retrieval/answer evaluations include supported procedures, paraphrased questions, ambiguous requests, absent functionality, conflicting/stale evidence, wrong versions, removed pages, restricted evidence, and irrelevant retrieved instructions. Document success criteria and report failures honestly.
8. Regression tests preserve existing MCP data tool behavior, association scoping, agent dispatch, and stdio protocol. Seed isolation, artifact validation, index refresh/deletion, and failure recovery have meaningful tests.
9. Publication wiring, required credentials, actual automated triggers, and any unconfigured external dependencies are documented and demonstrated where authorized. Do not call a local prototype a fully automatic deployed system.

## Expected deliverables and final report

- Assozeta changes on `add/manuale`: isolated runner, seed, real browser scenarios, evidence manifest/validator, retrieval/indexing implementation, MCP/agent integration, reusable skill source, CI configuration, and concise maintainer instructions. Choose specific directories after inspecting repository conventions.
- Companion manual checkout/branch: verified Italian MDX, screenshots, corrected navigation/backlog where needed, and previewable changes. Record its local location, branch, and revision.
- A complete coverage/gap report and an end-to-end evidence bundle with pinned inputs and actual results.
- A short final report describing delivered behavior, reproducible commands, checks performed, manual/corpus compatibility, publication status, and remaining external blockers. Distinguish implemented, verified, and proposed items.

Resolve routine implementation choices autonomously within this scope. Preserve unrelated product behavior and existing documentation systems. If a real application defect blocks a workflow, report its exact code evidence and make only the narrowly necessary fix with appropriate regression coverage.
