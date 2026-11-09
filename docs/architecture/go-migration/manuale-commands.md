# Manual management commands

Four Django commands in `BE/application/management/commands/` build, check and
install the manual corpus. Two are ported to the Go binary `BE_GO/cmd/manuale`;
one is proposed deprecated and one stays open.

| Django command | Callers | Go |
| --- | --- | --- |
| `sync_manuale_corpus` | `selfhost/bin/assozeta` start and `sync-manual` | `manuale sync-corpus` |
| `compare_manuale_captures` | `docs/scripts/manuale.py` capture step (CI `manuale.yml`) | `manuale compare-captures` |
| `build_manuale_index` | none | proposed deprecated |
| `evaluate_manuale` | `docs/scripts/manuale.py` evaluate step (CI `manuale.yml`) | not ported |

## sync-corpus

`manuale sync-corpus [--env-file PATH]` reads the same settings as the Go server
(`config.Load`: `MANUAL_CORPUS_BASE_URL`, `MANUAL_APPLICATION_REVISION` or the
image `CODE_REVISION`, `RUNNING_VERSION` or the image `VERSION`,
`MANUAL_INDEX_PATH`, `MANUAL_SOURCE_ROOT`, `ASSOZETA_MANUAL_RUN_ID`) and calls
`manual.Loader.Synchronize`, the existing refresh synchronizer
(`pkg/manual/refresh.go`, see manual-reader.md) now returning the promoted
identity. It holds the shared `.sync.lock`, downloads
`BASE/REVISION/quote(RELEASE).json`, validates seal, format, publication,
revision, release and stale evidence, installs deduplicated screenshots in
batches of eight, retires and prunes assets, and promotes the index. Success
prints `Published manual corpus updated: <identity>`; any failure prints
`CommandError: Published corpus unavailable or incompatible; previous index
retained.` and exits 1.

The differential found one difference in the shared synchronizer: Django's
`promote` writes the canonical JSON followed by a newline and Go wrote it
without one. Go now appends the newline, so both runtimes leave byte-identical
index files. Readers parse either form.

## compare-captures

`manuale compare-captures --run RUN --output OUTPUT` (`pkg/manual/captures.go`)
follows the Django loop step by step: reports in `RUN/capture-baseline/*.json`
in name order (hidden files, directories and dangling links included), the
`passed` checks, Python equality over the capture inputs (int/float/bool
equality, the shared json NaN), capture keys normalized like `pathlib`, master
keys as written, `owned_path` with non-strict `realpath`, hash checks in
Django's order, RGBA comparison, NumPy's exact means and `round(x, 8)`. The
report is written with Python's `json.dumps(indent=2)` (ensure_ascii, float
repr, NaN/Infinity, first-key order). `pkg/manual/pyjson.go` holds the
JSON reader/writer. CommandError messages are identical; paths where Django
ends with a traceback exit 1 with one fixed line and no report.

Not ported, refused before the report is written (exit 1,
`compare-captures failed: capture image format is not supported by the Go
comparison`): images other than PNG (Pillow also decodes JPEG, WebP and
others), 16-bit PNG samples, grayscale below 8 bits and palette indices beyond
the PLTE entries. Strings with lone UTF-16 surrogates and JSON nested deeper
than 900 levels are refused. Pillow's decompression-bomb limit is kept.

## Not ported or proposed deprecated

- `build_manuale_index`: no caller. `docs/scripts/manuale.py` imports
  `application/manuale/index.py` and calls `build_index`/`promote` directly
  (`index()` step); no workflow, selfhost script or entrypoint runs the
  command. Its output also embeds an SVD from NumPy/LAPACK whose rounded
  values differ in the last digit between BLAS platforms (the reason
  `reproduces()` compares semantics with a tolerance), so a byte-identical Go
  port is not possible. Proposed deprecated.
- `evaluate_manuale`: CI runs it through `docs/scripts/manuale.py evaluate`
  inside the Django container, so it is not deprecated. Its subject is the
  Python stack itself: it starts `manage.py run_mcp_server` over stdio and
  drives `application.agent.core.Agent` (with `NoModel`) and
  `application.manuale.answers`. Go has its own MCP server but no agent, so a
  Go command would evaluate a different system. Left open (`needs_review`).
- Common to both ported commands: Django global options (`--settings`,
  `--verbosity`, ...), argparse prefix abbreviations and `-h`; traceback text;
  the publisher user agent (Python-urllib versus Go-http-client). The Go
  binary is not yet in the Go image; selfhost still runs the Django command.

## Evidence

`TESTS/adapters/go/manuale_commands.py` runs every case on a private fixture
directory: the actual `manage.py` command (production settings with
placeholder credentials; the commands do not open the database) and the
race-built Go binary, each on a fresh copy at the same path with the same
arguments and environment. Exit status, stdout, the CommandError or argparse
message, every file left (bytes, mode, link target, seeded versus run mtime)
and, for sync, the requests seen by a per-run local publisher are compared.
Django must meet each authored expectation first.

- 39 sync cases: fresh install, nested index path, asset reuse and
  replacement, retire/prune/keep with a recent and an old asset, unreadable
  previous index, every URL/revision/release refusal, release quoting,
  publisher errors, tampered seal, format, publication, code/manual state,
  manual revision, conflicting and stale evidence with and without a source
  root, screenshot digest/URL/path/bytes/header failures, redirects inside and
  out of the publisher, the eight-image batches (second-batch failure and
  success), and argparse.
- 42 compare cases (40 compared, 2 Go refusals): stable, changed, Full HD crop and one-level drift,
  dimensions and checkpoint changes with identical and different inputs, no
  baseline, ordering and hidden reports, every CommandError, traceback paths
  (missing report, escapes, non-image, bad shapes, missing output directory),
  symlinks, pathlib normalization, image modes (RGB, RGBA, L, LA, palette with
  transparency, 1-bit palette, colour key, Adam7), Python equality edge cases,
  Python JSON output, argparse, and the two Go refusals.

Receipts: `quality-reports/go-migration/manuale-commands-red/` (RED: Go command
absent, Django 81/81), `manuale-commands/` (GREEN: 81/81, two of them the Go
refusals) and the mutation controls `manuale-commands-mutation-sync` (index
written without the trailing newline: 13 cases fail) and
`manuale-commands-mutation-compare` (mean channel difference divided by pixels
instead of channels: 13 cases fail). Go unit tests: `pkg/manual/captures_test.go`;
the refresh tests and `TESTS/adapters/django/manual_refresh_lock.py` pass with
the shared synchronizer change.
