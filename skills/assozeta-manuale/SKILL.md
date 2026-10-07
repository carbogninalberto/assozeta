---
name: assozeta-manuale
description: Maintain the Italian Assozeta user manual in Bakney/manuale, capture real seeded application workflows, validate implementation evidence, and update the versioned RAG corpus in the existing MCP and agent. Use for manual updates, screenshot refreshes, manual coverage audits, and documentation retrieval maintenance.
---

Use the Assozeta repository's `docs/scripts/manuale.py` and `docs/scripts/manuale-generate.mjs`. Read `docs/goal-prompts/MANUALE_AUTOMATION_AND_MCP_RAG_GOAL.md` for the complete objective and `docs/manuale/README.md` for executable commands and current coverage.

Resolve those files from the selected Assozeta checkout, not the installed skill directory. The portable source is `skills/assozeta-manuale`; installation and discovery commands are in `docs/manuale/README.md`. Invoke explicitly with `$assozeta-manuale`.

The source pages belong to `Bakney/manuale`; application tooling and retrieval belong to Assozeta. Extend `BE/application/mcp_server/server.py` and `BE/application/agent/core.py`. Keep one MCP connection and preserve the existing data tools.

Use `python3 docs/scripts/manuale.py doctor` to diagnose runtime prerequisites
without preparing worktrees or running a seed. The full `run` command checks
these capabilities first and exits with code 2 when unavailable. If Docker,
local listeners or Chromium are denied by the execution environment, report
the required environment change; preserve the draft and last verified corpus.
Do not retry the same denied runtime indefinitely or substitute placeholders
for captures. A successful doctor check proves only prerequisite availability.

## Evidence rules

- Write user content in Italian, using the manual's MDX components and existing image conventions.
- For authoring, use `docs/scripts/manuale-scaffold.py create` with the local original repository. The runner also initializes a clean selected manual snapshot automatically, recording generated draft provenance separately; existing reviewed MDX must be preserved. It preserves the complete navigation and MDX content and prepares SVG placeholders at 1920 × 1080. They belong only to the draft: replace them with real captures before final verification, and never index them as verified screenshots. Track completion in `.manuale-authoring.json` and `docs/manuale/DELIVERY_CHECKLIST.md`.
- Follow `Bakney/manuale/CLAUDE.md`, `mint.json`, and the existing page headings. Keep its `docs/`, `faq/`, and `tutorials/` organization and use `<Steps>`, `<Step>`, `<Frame>`, and notes as appropriate. Preserve the original order of introductions, steps, and behavioral limits when projecting sections into the reader and chat.
- Preserve Markdown formatting and associate screenshots with their actual step. Use the shared reader/chat renderer and authenticated manual assets; an ambiguous or unsupported lookup must not display a procedural guide.
- Inspect the relevant UI, backend services, models, and permission rules. Existing manual prose and requested screenshots are claims to verify.
- Keep page/section hashes, source symbols/ranges/hashes, both repository revisions, and current passed scenario reports linked in the manifest.
- Screenshots that illustrate application procedures must come from the isolated running application and real backend. Record external sandbox/simulated evidence separately.
- Use explicit working-tree provenance for uncommitted sources. Never provide a committed source citation for different local contents.
- Maintain `docs/manuale/page-map.json` for the whole navigation/backlog. The runner validates route/symbol targets before generation and seals `source-catalog.json` afterward. A source map remains a review target; use reviewed workflow or text-only recipes to verify claims. Git links require a byte match with the actual committed blob.
- Text-only recipes bind reviewed implementation hashes. A matching symbol alone cannot reverify changed behavior; update the reviewed recipe only after source inspection. Newline encoding is normalized for the review guard, while capture manifests retain exact source bytes.
- Index only verified sections. Keep unsupported, stale, external-only, and unreviewed material visible in the coverage report.
- Preserve ordinary Italian explanations in pages; keep source snippets and diagnostics in maintainer evidence.

## Optional editorial adapter

Plain verification does not invoke a model. Add `--edit-content` to `manuale.py
run` when a bounded editorial pass is wanted before preflight and real capture.
It requires an already installed Codex CLI and existing CLI sign-in or an
externally supplied `CODEX_API_KEY`. Do not install credentials or an unpinned
CLI as part of a manual update. See the [official non-interactive mode documentation](https://learn.chatgpt.com/docs/non-interactive-mode)
for saved authentication and structured `--output-schema` responses.

The runner defaults to `--editor-timeout 180` and `--editor-max-sections 12`.
Use affected recipe selection to keep batches bounded. Set `--editor-profile`
or `--editor-model` only when the execution context explicitly configures them;
never invent a model, provider or profile. Without an explicit profile, the
adapter ignores user configuration and retains CLI defaults.

Discovery includes every original manual section, including sections without a
capture recipe. A full run considers all supported sections; incremental runs
use source dependencies and changed MDX pages. The adapter shares actual code
excerpts within each batch and splits batches at the actual prompt byte limit.
Preserve existing constant MDX column attributes together with headings, steps,
links and verification notes.

The `docs/scripts/manuale-edit.py` adapter supplies structured source context to an isolated read-only CLI execution
without project hooks. The adapter validates its structured proposal before
applying permitted MDX edits in the run checkout. Those edits are drafts until
the existing source, binding, scenario, screenshot and retrieval checks pass.
Do not treat model output as verified behavior or synthesize new scenario
assertions to make prose pass. Changed source contracts or symbols can stop the
run with explicit diagnostics; retain the prior valid index with its existing
compatibility limits.

A successful partial capture cannot promote an automatically rewritten legacy
procedure: it needs an exact full procedure binding. Changed external or
unsupported claims remain explicit gaps and are excluded from model editing;
they do not prevent independently supported sections from being drafted.

The CI workflow defaults to verification. Editorial opt-in requires the default
application branch and the configured trusted manual revision. Pull requests
and repository dispatches never enable the editor or its credentials.
`MANUAL_AUTO_EDIT=true` applies only to trusted default-branch push/scheduled
runs; `workflow_dispatch.edit_content` must be explicit and cannot select an
untrusted manual ref. An unavailable CLI is an explicit failure. Scope a CI key
to the editorial step; the runner removes it after that phase, including failure
paths, before builds or browser/backend subprocesses. Keep authentication and
private editorial diagnostics out of public exports. This mode does not publish.
Do not retain CLI sign-in or reusable keys on a runner that also executes pull
request code; supply authentication only to its trusted editorial step.

## Execution

1. Inspect both working trees. Select the application/manual revisions and preserve unrelated changes.
2. Run `python3 docs/scripts/manuale.py run --manual-repo /path/to/manuale`. Add `--allow-dirty` to deliberately document and hash uncommitted application and manual snapshots. Use `--keep-services` when inspecting a live failure or extending scenarios.
3. Follow the emitted run directory. For an existing run, use `start`, `capture`, `generate`, `verify`, `index`, `evaluate`, `export`, or `cleanup` with `--run /path/to/run`. Revalidate live process/container state before resuming; a state file alone does not prove a process is running. Run tooling is snapshotted and hashed: changing its retained files requires new evidence.
4. Maintain prose directly in the manual MDX checkout. After inspecting application sources for an intentional edit, use `manuale-scaffold.py review-content --output CHECKOUT --section path#id` to bind it; this is editorial provenance, not workflow verification. After reviewing a changed recipe, add `--refresh-recipe-bindings` for the explicitly selected sections; this retains the previous hash and removes any old capture seal. Publishing reads that MDX and rejects stale prose/module bindings. `apply-drafts` only bootstraps templates and must preserve edited MDX. For new chapters, register dependencies/pages in `docs/manuale/recipes.json`, implement real scenarios and reviewed generation recipes, including persisted-state assertions and relevant roles. `--changed-since` and `--manual-changed-since` select affected recipes; use a full run to catch missing dependencies. Do not mark a whole page verified based on one unrelated capture.
5. Verify MDX/rendered pages, every new screenshot layout, retrieval results, and actual MCP/agent answers. Also verify the sidebar and header open the embedded `/#/manuale` reader in a new tab with a standalone layout, with matching steps, images, search and section deep links. Crops must contain the controls described by their step. Repeat `capture` on unchanged inputs when establishing screenshot repeatability or investigating a visual regression; ordinary text changes do not require repeating already successful scenarios. Identical inputs must stay within the documented threshold. Unsupported questions must yield missing-evidence feedback, not an invented procedure.
6. Retain reports and both worktrees for review. Cleanup removes only the recorded run's services/data; preserve manual changes.

## Retrieval and publication

The runtime index is selected by trusted application revision/release and server configuration. Callers cannot supply a different identity, evidence root, or elevated role. Public tools return verified manual text and citations; maintainer source evidence requires the actual instance owner.

Use owner-only `get_manual_gaps` for whole-manual diagnostics or a specific MDX `page`. Stale snapshots and external constraints are diagnostic evidence, never current procedural instructions. Reverify runtime MCP/agent behavior after adding tools; offline corpus tests do not establish live transport or authorization behavior.

Build a complete index before atomic promotion. Changed/deleted claims must disappear from the next corpus. Retain the last valid artifact on generation/index failure; never silently serve it as compatible with a different application version.

Optional `MANUAL_CORPUS_BASE_URL` synchronization accepts only published, committed evidence matching the running image's code revision/release. Keep it disabled until compatible release assets have been published; local preview corpora are for owned fixture instances.

Use `docs/scripts/manuale-release.py` to prepare a release package only after the original run is evaluated and its exact generated manual content is committed separately. It preserves the original proof and emits `prepared` metadata; it does not publish or make a package production-compatible. Never change that status manually to bypass missing site/evidence publication.

Local generation and verification require no GitHub write credentials. External pushes, PR creation, merges, credential installation, and production publication require authorization in the execution session. After that configuration is authorized, recurring runs should not request approval for routine updates.

Finish with verified coverage, commands/checks run, the manual checkout/branch, compatible corpus metadata, publication status, and explicit gaps. A passing first workflow is progress, not completion of the full manual.

Per installare un risultato completo nel lettore dell’istanza di sviluppo esistente, usare `manuale.py install-dev --run DIRECTORY` oppure aggiungere `--install-dev` all’esecuzione completa, con `--release` uguale alla versione dell’API di destinazione. Questa installazione modifica soltanto il pacchetto locale e la configurazione dell’API; non migrare o eseguire seed nel database di sviluppo esistente. La provenienza resta `development-local`, con gli hash del codice ricontrollati a ogni lettura.

Use one final verification batch per implementation chunk. Select affected workflows through registered recipe/script/import dependencies; test-only changes do not recapture. Full retrieval/permission suites are appropriate when those implementations change. Report unavailable Docker/browser execution as an unexecuted check, without fabricating screenshots or current-package compatibility.
