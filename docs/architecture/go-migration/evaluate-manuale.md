# evaluate_manuale: `manuale evaluate`

Django: `BE/application/management/commands/evaluate_manuale.py`. CI runs it
through `docs/scripts/manuale.py evaluate` (`manuale.yml`) inside the Django
container; nothing switches yet. The Go port is a subcommand of
`BE_GO/cmd/manuale` and evaluates the Go stack the same way the Django
command evaluates the Python one: the Go MCP server ([mcp-server](mcp-server.md))
and the Go agent ([ws-agent](ws-agent.md)).

## Behaviour (both runtimes)

1. Arguments: `--association-id` and `--output` required, `--query`
   optional (an empty value keeps the default cases).
2. `SportAssociation.objects.get(pk=...)` (non-deleted rows): a malformed id
   or a missing row ends with a traceback (exit 1, no report).
3. Reads `MANUAL_INDEX_PATH` (missing or invalid JSON: traceback) and builds
   the case list: scenario-gated cases (`tags-create-assign`,
   `members-search`, ...), the two section-subset gates (medical
   certificates, archive) and the seven ranking regressions when their
   section exists. `--query` replaces the list with one case without an
   expected section. No case: `CommandError: No evaluation cases for the
   verified corpus` (no report).
4. Starts the MCP server for `str(association.pk)` over stdio (Django:
   `manage.py run_mcp_server`; Go: the `mcp-server` binary next to
   `manuale`, or `--mcp-server PATH`), with the process environment; its
   stderr (the start line) goes to the command's stderr.
5. Through MCP: initialize, `tools/list` must contain the business and
   manual tools; `search_manual(query)` must be verified; the section text
   must equal the hit's; evidence and gaps must be `forbidden` for the
   stdio client; an unknown operation must abstain; `count_data(Course)`
   must be at least 1.
6. The agent with NoModel (any model call raises `Explicit manual questions
   should use verified extractive answers, not a model`): the owner's
   `Mostrami la copertura del manuale` must use only `get_manual_gaps` and,
   when the corpus has `catalog.summary.navigation_pages`, say `N pagine
   inventariate`; every case must retrieve a verified first hit (in the
   expected sections) and the agent's answer must contain its text and URL
   with only `search_manual`; the first case again without a provider must
   give the same answer; five unsupported questions must abstain in MCP and
   in the agent.
7. The deterministic answer fixtures (`evaluate_adversarial_answers`):
   conflicting section identity and English/Italian assistant directives
   through `grounded_answer` and `manual_section_attachment`.
8. The report is written in every case once the exercise started
   (`json.dumps(report, ensure_ascii=False, indent=2)` plus a newline, the
   parent directory created): `status`, `transport`, `query`, `checks`,
   then `cases`, `manual_only_without_provider`, `unsupported_cases`,
   `adversarial_cases`, `adversarial_limits`, `answer`, `citation`,
   `corpus_identity`, `content_identity`, `application_revision`,
   `version`; on failure `status: failed` and `error` (last), and the same
   text as `CommandError`. The whole exercise has `max(90, 6·cases + 60)` s
   (`Evaluation timed out after k/n supported questions`). Success prints
   `Existing MCP and agent manual evaluation passed.`

Neither command asks a model: both pass NoModel or no provider explicitly
and never read the AI configuration.

## Go

- `BE_GO/cmd/manuale/evaluate.go`: the command, a minimal MCP stdio client
  (request ids from 0, `notifications/initialized`, `McpError` texts,
  `Connection closed`; stdin closed then two seconds before SIGTERM/kill
  like `stdio_client`), `CaptureCallback` and NoModel, the report as an
  ordered object (`dto.AgentJSON{Indent: 2}`), Python `KeyError` texts for
  missing keys.
- `BE_GO/internal/service/evaluate_manuale.go`: exported
  `grounded_answer`/`manual_section_attachment` (the agent's existing ports).
- The agent runtime is the one of `cmd/run-agent` (manual tools, business
  tools, restore coordination) with `AgentRuntime.ManualAvailable`.
- Go-only options: `--env-file` (also passed to `mcp-server`) and
  `--mcp-server`.

## Not ported or not reproduced

Refused before the report is written (exit 1, `evaluate failed: not
supported by the Go command: ...`):
- tool texts that are not JSON (Django's `JSONDecodeError` text would be
  the error), non-object tool results and hits, non-string `text`/`url`,
  non-integer counts, comparisons of non-string values, report values
  outside JSON;
- agent inputs the Go agent refuses (see [ws-agent](ws-agent.md));
- corpora whose `chunks` or `scenario_ids` are not lists, chunks that are
  not objects, `catalog`/`summary` values that are not objects, lone
  surrogates.

Not reproduced: Django's tracebacks (Go prints `evaluate failed`, exit 1),
argparse usage texts and Django's global options, Django's system-check
warnings on stderr, launch errors of the MCP server process (`[Errno …]`),
several simultaneous failures flattened from an exception group (Go
reports the first). The timeout path is implemented but not exercised (it
needs at least 90 s). A provider-less agent reaching the model loop is not
reachable: the manual-only run repeats a question that NoModel already
answered extractively (the route does not depend on the provider).

The Go `manuale` binary is not yet in the Go image (the image has
`/app/mcp-server`), so `docs/scripts/manuale.py` keeps running the Django
command.

## Evidence

`TESTS/adapters/go/evaluate_manuale.py` on a private PostgreSQL with the
current schema and the `mcp_server_seed.py` rows plus one association
without courses. Each case seals a runtime index from
`TESTS/fixtures/evaluate-manuale-corpus.json` (the 65 published sections
the case list names, public projection fields only; regenerate with
`TESTS/fixtures/generate_evaluate_manuale_corpus.py --source <index.json>`),
fits its semantics with Django's `fit_semantics` and runs the actual
`manage.py evaluate_manuale` (contract settings with the manual paths from
the environment) and the race-built `manuale evaluate` with the race-built
`mcp-server`, same arguments and environment. Compared: exit status,
stdout, the CommandError/argparse message, stderr without Django's
system-check warnings, the report bytes (or absence), all managed rows
(unchanged) and the requests seen by a per-run model endpoint stub that
both runtimes are configured with and that would answer a scripted
completion (both lists empty). Django meets each authored expectation
first (status, error, the selected case queries, counts).

16 cases: full corpus with the instance maintainer (49 supported cases,
diagnostics with the catalog summary), the owner without maintainer access,
no catalog, no instance configuration (self-hosted sections hidden), one
`--query` into a nested output directory, empty `--query`, an unsupported
query, a verified query the agent routes to the model (NoModel raises), a
corpus for another release, a missing expected section, an association
without courses, no evaluation cases, unknown and malformed association
ids, a missing index, and missing arguments.

The first Django runs corrected two authored expectations: without an
instance configuration the `self_hosted` sections are not applicable, and
the tag cases precede the search cases in the report.

- RED (`evaluate-manuale-red`, the base revision's BE_GO): Django 16/16, Go
  0/16 (no `evaluate` subcommand).
- GREEN (`evaluate-manuale`): 16/16 identical.
- Mutation (`evaluate-manuale-mutation`): the medical-certificate gate
  requiring the scenario and the sections (`||` to `&&`) is CAUGHT (4 cases
  differ: every default case list).
- Re-run (`manuale-commands-evaluate-rerun`): `TESTS/adapters/go/manuale_commands.py`
  81/81 (the shared `cmd/manuale/main.go`); `go test ./internal/service -run TestAgentRouting`.
