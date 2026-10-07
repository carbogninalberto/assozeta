# Goal: finish the Assozeta manual in a runtime-capable session

Continue the implementation on `add/manuale` and complete **all requirements**
in [the original goal](MANUALE_AUTOMATION_AND_MCP_RAG_GOAL.md). This is a
continuation of existing work. Preserve its scope, source changes, documentation
structure and evidence; do not restart the architecture or declare the prepared
draft a finished manual.

## Where to resume

Workspace: `/Users/alberto/Desktop/Bakney/Workdir/assozeta`.

Paths below are relative to that workspace:

- Application source: the current checkout, branch `add/manuale`. Its HEAD is
  `e5628830421303ffe567c4274d98f3e3d6cbbc33`, with substantial staged and unstaged
  implementation changes. **HEAD alone does not identify the implemented code.**
- Owned application checkout:
  `quality-reports/manuale-development/dee70f925f6b/application`.
- Latest companion manual:
  `quality-reports/manuale-development/dee70f925f6b/manual-authoring`, branch
  `add/manuale`, HEAD `dd25488e2ca9af428af2c472ba07457cdc89759c`.
- Original manual reference: `../manuale` (`Bakney/manuale`). Use the authored
  companion as the generation input; the original is a structure/style reference.
- Handoff evidence: `quality-reports/manuale-development/dee70f925f6b/`.

Read the original goal, `docs/manuale/README.md`,
`docs/manuale/DELIVERY_CHECKLIST.md`, and `skills/assozeta-manuale/SKILL.md`.
Inspect current files and permissions before relying on these recorded revisions.
Preserve unrelated changes. If source Git metadata remains read-only, use the
existing standalone-checkout option; do not stage or commit that source checkout.

## Implemented versus unfinished

The source contains the isolated runner and seed, capture scenarios, generation,
evidence/indexing, existing MCP and agent integration, reader/chat components,
portable skill and CI configuration. The reader opens in a new tab with its own
navigation and no application sidebar/header.

Recorded preparation covers 43 MDX pages, 507 section identities, 60 workflows,
700 planned checkpoints and 317 complete procedure bindings. There are 883 SVG
placeholders at 1920 × 1080. **These counts describe preparation, not verified
workflow coverage.**

The latest UI build and offline content/source checks passed. Thirty-two distinct
host regression cases passed, including real MCP stdio against a synthetic test
corpus. These do not establish current-manual browser or corpus acceptance.
Two automatic-attendance regressions and the dashboard PostgreSQL JSON branch
still need PostgreSQL execution. Consult `remaining-gaps-review/` for results.

The latest draft has no new verified captures or installed compatible corpus.
The localhost guides, full browser execution, visual review, repeatability and
current-corpus MCP/assistant evaluation remain unfinished.

## Start with runtime, then deliver

The previous session denied Docker daemon access, loopback listeners, Chromium
startup and PostgreSQL shared memory. This was an execution restriction, not a
reason to fabricate screenshots or loosen evidence validation. The evidence is
in `completion-audit/blocked-runtime-audit.json` and `postgres-runtime-review/`.

1. Run `python3 docs/scripts/manuale.py doctor`. The new session must permit
   Docker, local application servers and Chromium. Exit code 2 means the runtime
   is still unavailable. Report the specific restriction; do not repeatedly run
   expensive checks against an unchanged blocked environment.
2. Inspect the target development API configuration and establish its trusted
   `RUNNING_VERSION`. Ensure the existing development services are available;
   seed and migrate only the runner's owned disposable instance.
3. Run the existing full orchestration, using the authored manual and the exact
   target version. Set `manual_target_version` from that inspected configuration:

   ```sh
   python3 docs/scripts/manuale.py run \
     --manual-repo quality-reports/manuale-development/dee70f925f6b/manual-authoring \
     --allow-dirty --standalone-checkouts \
     --release "$manual_target_version" --install-dev
   ```

   The default output is an owned run under `quality-reports/manuale/`. Keep its
   manifests, worktrees and diagnostics. Installing the verified package into
   the development reader is authorized; do not seed its existing database.
4. Resolve actual scenario or application failures in substantial implementation
   chunks. Resume an owned run only after confirming its live services and input
   hashes. Retest affected failures; perform one final verification batch after
   each chunk. Use the documented incremental/reuse options only when their
   compatibility requirements hold. Complete full coverage and the identical-input
   repeatability run before final acceptance.
5. Replace procedural SVGs with real screenshots. Keep every original at
   **1920 × 1080, scale 1**; crops must retain the original, coordinates and hashes.
   Verify every new screenshot layout and representative rendered chapters.
6. Make the manual useful and polished: preserve original Italian chapters and
   headings, explain complete actions and saved outcomes, and visually inspect
   desktop/mobile navigation, search, deep links, images and standalone layout.
   Chat must preserve Markdown/HTML formatting, ordered steps, matching images
   and citations without duplicating the answer. Improve its initial help message
   if the rendered result needs it.
7. Verify the **same current compatible corpus** through the localhost reader,
   existing MCP stdio and assistant dispatch. Test supported answers, abstention,
   version/configuration filtering and tenant/role restrictions. Confirm that
   actual guides appear at `http://localhost:5001/#/manuale`.
8. Complete the original automation/publication acceptance audit. Demonstrate
   local orchestration, optional editorial automation when applicable, recovery
   and CI wiring. Keep unsupported/external claims as explicit gaps. External
   pushes, PRs, merges, credential installation and publication need separate
   authorization; preparing reviewable artifacts does not.

Use parallel agents for independent substantial tasks when useful, within the
session's actual concurrency limit. Do not invent a CLI model/profile. When the
user says **report**, state active agent counts and profiles, current-repository
update status, and what remains blocked or needs their action.

Finish with actual coverage and screenshots, both checkout revisions and dirty
hashes, corpus identity, localhost behavior, checks performed, publication status
and remaining external gaps. Mark the goal complete only when the original
requirements are met and verified.
