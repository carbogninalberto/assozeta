# Goal prompt for simplifying the Assozeta manual and appliance delivery

Simplify the existing Assozeta manual implementation, make routine updates substantially faster, and ensure the manual is included automatically in every installable release. Existing appliances must receive it through the ordinary application update, without editing `.env`, configuring a manual URL, running a synchronization command, copying files, or enabling a branding setting.

Implement and verify the changes end to end. Deliver working code, a simpler maintenance workflow, measured results, and a tested upgrade path. Preserve the Italian manual, real screenshots, embedded reader, existing MCP integration, and grounded assistant answers.

## Starting point

Work from the current checkout and applicable repository instructions. Preserve unrelated work and follow the configured Git identity and standing commit/push policy. Do not switch to the historical `add/manuale` branch just because an older goal mentions it.

The following findings were established on October 22, 2026. Recheck them against the execution checkout and current release state:

- The latest published release inspected was `v1.0.17`, published October 9. The release bundling implementation, commit `41c05ff`, was added October 21 and is not included in that tag. Old appliances cannot acquire those files merely by changing configuration.
- Commit `65f0535`, pushed to `main`, removes the `showManual` branding condition from the header and sidebar, removes its defaults, and adds a legacy-setting case to the release browser test. Preserve this fix; it is not evidence that an appliance update has shipped. The server still checks whether compatible manual content exists.
- The ordinary manual CI workflow records a previous full run of roughly 70 minutes, including approximately 39 minutes for 60 sequential capture workflows. This is a historical comment, not a new benchmark or a promise about the current workflow count.
- The runner already supports affected-workflow selection, evidence reuse, frontend reuse, and individual phases. Ordinary CI still invokes the full run without incremental selection. Even a run that only reuses captures currently starts the application stack.
- Release CI builds and verifies the target manual, runs another version using reused captures for a synthetic previous release, and then rehearses installation and rollback. Identify duplicate work before changing this arrangement.

Inspect these implementation entry points:

- [Runner](../scripts/manuale.py), [generator](../scripts/manuale-generate.mjs), and the other `docs/scripts/manuale*` commands.
- [Workflow registry](../manuale/recipes.json), [authored workflow definitions](../manuale/authored-workflows.mjs), and associated recipe modules.
- [Browser scenarios](../../selfhost/tests/browser/manuale), especially `scenario.mjs`, `frame.mjs`, `verify.mjs`, and `embedded.mjs`.
- [Manual backend](../../BE/application/manuale), especially bundle discovery, availability, indexing, retrieval, development, preview, and synchronization.
- [Manual CI](../../.github/workflows/manuale.yml), [release manual CI](../../.github/workflows/manuale-release.yml), and [image publishing](../../.github/workflows/publish-images.yml).
- [Backend image](../../BE/Dockerfile), [entrypoint](../../BE/entrypoint.sh), [appliance CLI](../../selfhost/bin/assozeta), and [release rehearsal](../../selfhost/tests/manual-release-smoke.sh).
- [Installed browser checks](../../TESTS/browser/manual-release.mjs), [HTTP contracts](../../TESTS/contracts/test_manual.py), and [MCP contracts](../../TESTS/mcp/manual.py).
- [Maintainer documentation](../manuale/README.md) and [manual skill](../../skills/assozeta-manuale/SKILL.md).

Keep authored content in the existing `Bakney/manuale` repository. Select and record its actual revision; do not assume an old local draft is current. Earlier manual goals remain useful background for content and correctness, but this goal replaces their requirement to use the original orchestration shape for routine work.

## Simplify the maintenance workflow

Provide three clear operations through the existing CLI. Exact command names may follow repository conventions; avoid a second orchestration framework.

| Operation | Required behavior |
| --- | --- |
| Build | Compile reviewed MDX, existing valid images, navigation, and the search corpus. Validate links and required evidence without launching Docker, seeding data, capturing screenshots, or starting the live application. |
| Capture | Run explicitly requested or affected workflows against one owned demonstration environment. Produce screenshots and workflow results for the build operation. Reuse unchanged build inputs and compatible evidence automatically. |
| Verify release | Run the complete release acceptance checks against the target application and manual inputs, package the validated result, and exercise installation, update, and rollback. Reuse successful outputs within that verification instead of regenerating them at each boundary. |

A build must work with Docker stopped when all necessary capture evidence is already available. If evidence is missing or stale, identify the exact workflows requiring capture and fail publication clearly. An explicit preview may show draft material, but must never install or advertise it as verified release knowledge.

Distinguish reviewed editorial corrections from changes to documented behavior. A wording-only change should not require recapturing an unchanged interface. Revalidate its editorial and source bindings; never treat every MDX change as harmless or silently reuse a capture for changed behavior.

Preserve a convenient complete command for cold starts. Normal users of the tooling should not need to remember run-directory dependencies or supply a long combination of reuse flags. Prefer a small set of predictable defaults and explainable cache decisions.

## Consolidate implementation and metadata

Keep MDX as the sole maintained source of user-facing prose. Define each workflow's identity, source dependencies, fixture profile, checkpoints, image slots, and expected outcomes once. Generate or derive other inventories from those definitions instead of maintaining parallel mappings in registries, capture code, and generators.

Separate orchestration, pure content compilation, evidence validation, and runtime retrieval at their actual dependency boundaries. Remove replaced code and unnecessary state transitions. Splitting a large file into many modules without reducing duplicated responsibilities does not satisfy this goal.

Consolidate validation behind shared functions while preserving the checks needed when accepting untrusted or mutable inputs. Repeated checks across independent trust boundaries can remain; repeated reconstruction of the same already-validated output within one run should be eliminated where safe.

Use one installed-package contract for the reader, MCP, and assistant. Keep detailed build evidence in build artifacts and maintainer diagnostics. Runtime must not need Git worktrees, the authoring repository, a renderer process, or browser tooling to serve the manual. Keep development preview and legacy download support outside the normal appliance path; retire obsolete paths only after accounting for existing installations.

Preserve the current retrieval behavior and evaluation coverage. Do not introduce a vector database, another MCP server, another documentation site, a mandatory AI provider, or a new general-purpose cache service to achieve this refactor.

## Reduce runtime and CI work

Measure the current implementation before changing its execution strategy. Record elapsed time by phase and workflow, including setup, frontend build, seeding, captures, rendering, indexing, retrieval checks, and appliance rehearsal. Keep instrumentation small and useful for subsequent runs.

Wire the existing dependency selection into ordinary PR verification. Preserve unaffected content by merging compatible retained evidence; a partial run must not accidentally publish a partial manual. Use a full run when dependencies are unknown or shared authorization, fixture, retrieval, packaging, or orchestration changes require it.

Reuse compatible frontend builds and captures using explicit input fingerprints. Missing caches must lead to a correct cold execution. Changed or deleted dependencies, configuration, fixture behavior, and checkpoints must invalidate the affected evidence. Preserve original capture provenance when reusing results; package compatibility with a new release requires verification, not relabeling old evidence.

Reduce repeated process/browser startup and expensive fixture setup where measurements justify it. Preserve isolation and deterministic resets. Do not run mutating workflows concurrently against the same database. Parallel capture is optional and should be added only if it produces a clear benefit without creating another maintenance burden.

For release CI, perform one complete verification of the target manual and consume its output in packaging and appliance tests. Prefer the actual previous published package for update/rollback checks when available. Handle the first transition from an older release without a bundled manual explicitly. Keep synthetic two-version contract tests where useful, but distinguish them from a real historical upgrade and avoid using a second full generation as the default way to construct a rollback fixture.

Report comparable cold and warm timings for a text-only edit, a change affecting one workflow, and a full release verification. State hardware, inputs, cache state, and work performed. Show a real reduction in routine work and wall-clock time without claiming that skipped coverage is a speed improvement.

## Deliver the manual automatically to appliances

Use the image-owned release bundle as the normal production source. Every release advertised as installable must contain the compatible manual and required images. Preserve the distinction between installable releases and development/edge images in publishing and updater manifests.

Installation, upgrade, restart, and rollback must select the manual belonging to the application image. No manual-specific `.env` values, provider credentials, external documentation connection, persistent-volume copy, or post-install synchronization command may be required. Existing empty download volumes and saved `showManual: false` settings must not suppress a valid bundled manual.

Keep the header and sidebar links available to the existing staff audience whenever the bundled manual is available. Preserve authentication and existing role behavior. Open the embedded reader in a new tab with its standalone layout. Navigation, search, deep links, screenshots, MCP, and manual-only assistance must work without external network access or an AI key.

Validate package integrity and application compatibility at build/install/startup boundaries, and cache validated immutable runtime state where appropriate. Preserve integrity checks for mutable optional sources. A required missing or corrupt package must be rejected before a new release replaces a working installation; retain startup validation as a final safeguard. Do not make deployment appear successful by hiding the manual or redirecting to unrelated public documentation.

Prove recovery after interrupted or failed updates and after container recreation. Preserve business data and the previously compatible application/manual pair. Rollback to a historical release that never contained a manual must honestly restore that historical behavior; do not serve a newer incompatible manual under its version.

## Acceptance and verification

Completion requires all of the following:

1. A reviewed text-only manual edit builds and validates with Docker stopped and performs no captures. Missing required evidence produces an actionable result without silently starting services or promoting incomplete content.
2. A change affecting one workflow selects the correct capture work, retains compatible unrelated sections, and produces a complete usable package. Shared and unknown dependencies trigger conservative verification.
3. One canonical workflow definition supplies the capture and build mappings. Superseded duplicate definitions and orchestration paths are removed, with compatibility handled where needed.
4. The target release passes content, image, navigation, source/evidence, reader, MCP, assistant, and authorization checks. Unsupported questions still abstain; stale or incompatible content is not returned as verified guidance.
5. An isolated fresh install and an ordinary upgrade from a real published appliance image both expose the manual without manual-specific configuration. Include an empty legacy volume and the old branding flag. Use fictional data, record the actual image versions/digests, and verify business data survives the update.
6. With external requests blocked and AI disabled, a real browser can open the manual from both application links, search it, reload a section deep link, and load its images. The existing MCP transport and manual-only assistant return compatible instructions from the same package.
7. Restart, container recreation, compatible-version rollback, and failed/interrupted update recovery preserve the correct application/manual pairing. Missing or corrupt packages cannot become successful installable releases.
8. Before/after measurements demonstrate the reduced routine work. Full release coverage remains intact. Document remaining expensive stages and the reasons they remain necessary.
9. Maintainer instructions, CLI help, CI workflows, and the reusable manual skill describe the new operations consistently. Update any older instructions that would send users back through the expensive path unnecessarily.

Use focused tests during implementation and one complete final verification after the affected code stabilizes. Rerun successful checks only when later changes or failures make their results insufficient. Unit tests with synthetic corpora and browser tests with intercepted configuration are useful regressions, but do not substitute for the real installed-appliance acceptance above.

## Delivery and execution boundaries

Commit and push finished, checked changes under the repository's standing policy. Record any companion manual changes separately. Preserve private credentials, databases, runtime files, and generated diagnostics outside Git.

Prepare and test the release integration completely. Publishing a production release or operating a live appliance is a separate action unless the user explicitly authorizes it. Do not stop implementation merely because publication will be a later step, and do not claim that installed appliances have changed because code was pushed to `main`.

The final handoff must state the simpler architecture and commands, deleted or consolidated mechanisms, measured timings, checks actually run, application/manual revisions, and the appliance versions exercised. Include the commit hashes and exact remaining publication/update step, if any. Distinguish implemented and locally verified behavior from a published release and an upgraded live appliance.
