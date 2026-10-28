# Manual CI performance

## Baseline

Measured on 2026-10-28 from the GitHub Actions history of
`carbogninalberto/assozeta` (read-only `gh run list/view` and the jobs/logs
API). All runs use `ubuntu-24.04` GitHub-hosted runners. Wall time runs from the
attempt's `run_started_at` to `updated_at`. Phase times inside a step come from
the timestamps of the runner's own progress lines in the job log. No numbers
here are estimates.

`manuale-verify.yml` is a reusable workflow, so it never shows up as a run of
its own. Its two jobs appear inside `manuale.yml` ("Verify manual workflows and
MCP knowledge") and `manuale-release.yml` ("Build and rehearse the release
manual"). The jobs run one after the other: `verify` (select, verify and
package) first, then `appliance` (package startup checks, or the historical
rehearsal when the mode is full).

### Run totals

Fully cached runs (`Mode: reuse`, 0 fresh / 60 reused workflows, 42 retained render pages):

| Run | Workflow / event | Date | Head | Wall | verify job | appliance job |
| --- | --- | --- | --- | --- | --- | --- |
| 40444819255 | manuale.yml / push main | 2026-10-27 | 136a26b4 | 28.1 min | 19.1 min | 8.9 min |
| 40667149133 | manuale.yml / push main | 2026-10-28 | 58d8af88 | 25.6 min | 18.2 min | 7.2 min |
| 40689858334 (attempt 2) | manuale.yml / push main | 2026-10-28 | c2f4006b | 26.5 min | 18.6 min | 7.8 min |
| 40444843488 | manuale-release.yml / dispatch | 2026-10-27 | 136a26b4 | 27.5 min | 20.9 min | 6.5 min |

So the claim of about 28 minutes for a fully cached verification holds: these
runs took 25.6 to 28.1 minutes with nothing to capture or render.

Full runs (`Mode: full`, 60 fresh workflows, 42 fresh render pages, historical appliance rehearsal):

| Run | Workflow / event | Date | Head | Wall | verify job | appliance job |
| --- | --- | --- | --- | --- | --- | --- |
| 40688878441 | manuale.yml / PR chore/vendor-manual | 2026-10-28 | 699c0aa8 | 71.6 min | 50.8 min | 20.7 min |
| 40609557271 | manuale.yml / PR | 2026-10-28 | 4b744397 | 69.7 min | 49.1 min | 20.5 min |
| 40570118972 | manuale.yml / PR | 2026-10-27 | a1c514db | 85.4 min | 65.8 min | 19.4 min |
| 40486911604 | manuale.yml / PR | 2026-10-27 | 70eb1410 | 76.0 min | 56.8 min | 19.1 min |
| 40471688683 | manuale.yml / PR | 2026-10-27 | 61f2eaac | 75.7 min | 56.1 min | 19.5 min |
| 40428481241 | manuale-release.yml / dispatch | 2026-10-27 | 347d61cf | 73.5 min | 56.9 min | 16.5 min |

`pull-request-quality.yml` has no manual-specific job. For the same vendoring
PR it took 0.8 min (run 40688878438), while `manuale.yml` took 71.6 min. In
other PR quality runs the longest job was the backend test job, at 14.2 to
21.5 min (runs 40609557251, 40570118973 and 40437337729). That job is
unrelated to the manual.

### Per-step breakdown: fully cached run 40444819255

`verify` job (19.1 min):

| Step | Seconds | What runs |
| --- | ---: | --- |
| checkout, setup-node, setup-python, pin, prepare-storage | 37 | prepare-storage runs `storage-smoke.sh` |
| Install verifier dependencies | 29 | pip, `npm ci`, `playwright install --with-deps chromium` |
| Check verifier and runtime contracts | 75 | `manuale.py doctor` (Chromium probe), Python and Node unit tests |
| Admit a successful trusted baseline | 52 | downloads the `manuale-verified-baseline` artifact (about 619 MB) |
| Explain fresh, reused and unaffected work | 99 | `prepare`: worktrees and authored-procedure validation (98 s of it) |
| Capture only workflows requiring fresh evidence | 15 | nothing selected, no services started |
| Compile complete content and verify source evidence | 30 | MDX compile and source catalog, no services |
| Verify changed pages and retain compatible render evidence | 16 | 0 fresh pages, so Mintlify is not started |
| Check current reader, access control, MCP and assistant | 594 | see the runtime breakdown below |
| Bind package to the exact application revision and release | 56 | `manuale-release.py --bundle` |
| Remove only this run's fixture services | 5 | `docker compose down --volumes` |
| Seal verified reusable baseline | 71 | builds `manuale-baseline.tar.gz` |
| Upload baseline / runtime / verification artifacts | 22 + 4 + 35 | about 619 MB + 48 MB + 666 MB |

The runtime step is a fixed cost even when nothing changed. It always starts the
full Docker Compose stack:

| Phase (log line) | 40444819255 | 40667149133 | 40689858334 |
| --- | ---: | ---: | ---: |
| Build the retrieval corpus on the host | 19 s | 12 s | 21 s |
| Build run-specific application images (`api`, `ui`, `renderer`) | 120 s | 104 s | 111 s |
| Start run-owned dependencies (Postgres, Redis, MinIO, PDF renderer) | 12 s | 11 s | 10 s |
| Migrate isolated database | 54 s | 38 s | 57 s |
| Seed the fictional association and browser session | 23 s | 20 s | 22 s |
| MCP and agent evaluation (`evaluate_manuale`) | 85 s | 47 s | 87 s |
| In-app reader in Chromium (`embedded.mjs`) | 280 s | 515 s | 279 s |
| Step total | 594 s | 747 s | 587 s |

`appliance` job (8.9 min, `manual-release-smoke.py --scope compatibility`,
469.7 s elapsed): production images built and historical digests resolved
291.2 s, fresh installation with reader, assets, search and the browser
assistant 120.5 s, container recreation 54.5 s. The job also installs
Chromium again (30 s) and downloads the 48 MB runtime package (5 s).

### Per-step breakdown: full runs

| Step | 40688878441 | 40609557271 | 40570118972 | 40486911604 | 40471688683 | 40428481241 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Install verifier dependencies | 25 s | below 60 s | 322 s | below 60 s | below 60 s | 463 s |
| Admit trusted baseline (download) | 56 s | 67 s | 202 s | 68 s | 111 s | below 60 s |
| Capture (Docker stack and 60 workflows in Chromium) | 2076 s | 1945 s | 2593 s | 2528 s | 2438 s | 2015 s |
| Render (Mintlify) | 183 s | 161 s | 235 s | 236 s | 230 s | 165 s |
| Runtime (reader, MCP, assistant) | 503 s | 591 s | 350 s | 329 s | 330 s | 577 s |
| Historical appliance rehearsal | 1154 s | 1178 s | 1105 s | 1087 s | 1120 s | 925 s |

The "below 60 s" cells come from a listing that only printed steps longer than
60 s. Install times of 322 s and 463 s were slow apt and Playwright downloads.

Capture phase of run 40609557271: image build 100 s, dependency start 11 s,
migration 35 s, then seeding and the 60 workflow captures (00:39:06 to
01:09:06, about 30 minutes). The runtime phase of the same run spent 542 s in
`embedded.mjs`.

Historical rehearsal of run 40688878441 (1152.9 s): production images and
historical digests 467.2 s, fresh install 131.3 s, recreation 57.6 s,
interrupted-start recovery 46.3 s, failed-download recovery 36.2 s,
corrupt-candidate rejection 17.6 s + 40.0 s + 186.9 s, 1.0.17 upgrade 78.4 s,
backup rollback 87.1 s.

### Mintlify cost (render phase with fresh pages)

`docs/scripts/manuale.py verify` started `npx --yes mint@4.2.955 dev` on a
copy of the manual and then drove it with `verify.mjs` in Chromium. Mintlify is
not installed by any workflow step: `npx` downloads it while the render step
runs.

| Run | `mint` download and start (until ready) | `verify.mjs`, 42 pages | Render step |
| --- | ---: | ---: | ---: |
| 40688878441 | 7.9 s | 174.1 s | 183 s |
| 40609557271 | 6.5 s | 154.7 s | 161 s |
| 40570118972 | 9.4 s | 224.4 s | 235 s |
| 40486911604 | 9.7 s | 225.3 s | 236 s |
| 40471688683 | 9.5 s | 219.0 s | 230 s |
| 40428481241 | 6.5 s | 157.4 s | 165 s |

With 0 fresh pages (reuse mode) the render step took 10 to 16 s and did not
start Mintlify.

### Evidence artifacts

| Artifact | Size | Transfer |
| --- | --- | --- |
| `manuale-verified-baseline` (downloaded by "Admit", uploaded after sealing) | 515 to 720 MB | download inside "Admit" (25 to 202 s); upload 18 to 22 s |
| `manuale-verification` (decisions, renders, export) | 546 to 666 MB | upload 21 to 35 s |
| `manuale-release-runtime` | 45.5 to 47.5 MB | download 1 to 5 s in the appliance job |
| `manuale-release-evidence` | 2.0 to 4.6 MB | upload about 1 s |

### Bottlenecks identified

1. The runtime phase always starts Docker Compose: an image build (104 to
   120 s), Postgres, Redis, MinIO and the PDF renderer (10 to 12 s), migrations
   (38 to 57 s) and seeding (20 to 23 s). It then runs `embedded.mjs` in
   Chromium (279 to 515 s), even when every workflow and page is reused. In the
   cached runs this phase alone is 587 to 747 s.
2. The appliance job always builds production images (291 s compatibility,
   467 s full) and boots the appliance. With the job's own setup this adds 6.5
   to 8.9 minutes to every cached run.
3. Fresh captures take 1945 to 2593 s in full mode. Each one goes through a
   Docker Compose start (image build, dependencies, migration, seed) followed
   by 60 workflows in Chromium.
4. Mintlify renders take 155 to 225 s per full run (42 pages in Chromium plus
   a 6.5 to 9.7 s `npx mint` download and start).
5. The fixed overhead of a cached `verify` job is about 7 minutes outside the
   runtime phase: the baseline download (about 619 MB, 25 to 202 s), `prepare`
   (59 to 103 s), unit tests in CI (50 to 77 s), sealing (44 to 79 s) and
   artifact uploads (about 1.3 GB in total).
6. Chromium is installed twice, once per job, inside each job's dependency
   install step (`playwright install --with-deps chromium`). That step usually
   takes 20 to 49 s, with outliers of 322 s and 463 s. Chromium is
   launched by the doctor probe, by the captures, by Mintlify `verify.mjs`, by
   `embedded.mjs` and by the appliance smoke.

The local unit suite (`python3 -m unittest discover -s docs/scripts/tests -p
'test_manuale*.py'`, 207 tests before this change) took 126.5 s on a developer
laptop on 2026-10-28.
