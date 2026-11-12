# AI agent: `ws/agent/` and `run_agent` — implementing

Django: `BE/application/chat/` (`routing.py`, `consumers.py` AgentConsumer,
`throttling.py`), `BE/application/agent/` (`core.py`, `routing.py`,
`prompts.py`, `callbacks.py`, `providers/ai_provider.py`),
`BE/application/manuale/answers.py`, the business and manual tool functions
(`mcp_server/server.py`, `manuale/tools.py`) and
`BE/application/management/commands/run_agent.py`. Django stays the execution
owner: Caddy keeps `/ws/*` on Django; Go serves `/api/v2/ws/agent/` and the
`/app/run-agent` command.

## Behaviour

**Connect** (after `JWTAuthMiddleware`, see [websockets](websockets.md)):
anonymous closes 4001; `effective_integration('ai')` (decrypted; an
unreadable credential or a database error) closes 4004; AI disabled with no
manual available closes 4004; the concurrency slot
(`ws_concurrent:<user>` in Django's cache, `INCRBY` only on an existing key,
created with 300 s, max one) closes 4002; `User.sport_association` failing
(athletes, no or several non-deleted associations; collaborators use their
owner's) releases the slot and closes 4003; an `AIProvider` without a key
releases and closes 4004. Every close before accept is HTTP 403. Then accept
and one of three welcome texts (AI only, manual only, both).

**Messages.** Non-object JSON, binary frames, an unhashable `type` and
non-string `message`/`name`/`description`/`saved_report_id` values raise
(the connection ends without a close frame, and without `disconnect`: the
concurrency slot stays until its TTL). `save_report`, `delete_report` and
`list_reports` re-read the configuration first (`Questa funzione richiede la
configurazione AI.`). `ping`, `cancel` (`cancelling` then the task's
`cancelled` + `done`, or `idle`), `clear_history`, unknown types
(`Tipo di messaggio sconosciuto: <str(type)>`).
`user_message`: empty after strip is ignored; a running request answers
`Sto ancora elaborando…`; the configuration is re-read and, when it changed,
the provider, iteration limit, history cap and rate limit are replaced (an
error answers `Configurazione AI non disponibile…`); the throttle
(`ws_throttle:<user>`, 60 s window, `count <= ws_rate_limit`); then the
request task under the restore lease (`coordinated_async`: Busy answers
error + done) with `ws_timeout`. Saved reports: list (by association and
user, `-updated_at`), delete (stripped id, `UUIDField` parsing, not found,
generic failure), save (name, the in-memory export or the cached
`agent_save_info:<user>` pickle, the description fallback chain,
`tool_save_report`), each under the restore lease.

**Agent.** `_bound_input` (strip, 2000 characters), `html.escape`, history
cap (system prompt plus the most recent messages), `route_message`
(capabilities, conversation, manual-only action answer, manual FAQ through
`search_manual` with `grounded_answer` and the section attachment,
diagnostics through `get_manual_gaps`, ambiguous messages without AI),
pending actions and `_answer_requires_follow_up`. The loop streams chunks,
records tool calls in the history (`json.dumps(arguments,
ensure_ascii=False)`), runs each tool (unknown tools, the full-schema cache,
`None` arguments stripped, manual tools limited to their parameters with the
agent's `user_id`, `sport_association_id` and the agent user injected,
`coordinated_sync`, binding `TypeError`s, raised exceptions as
`json.dumps({'error': …})` with the field hint, results as
`json.dumps(result, ensure_ascii=False)`, 15000-character truncation),
loop detection (three identical calls: one forced request without tools),
the iteration limit, deferred `export_ready` frames (and the cached
`save_info`) and `report_saved` frames, then `done`.

**Provider** (AsyncOpenAI 2.54.0 over httpx 0.28.1): `POST
<base_url>/chat/completions` with the SDK headers (`AsyncOpenAI/Python
2.54.0`, the `X-Stainless-*` set, retry count, read timeout, bearer key) and
httpx's compact UTF-8 JSON body (`messages, model, max_tokens 4096, stream,
temperature 0.1[, tool_choice auto, tools]`); SDK retries (two; 408, 409,
429, 5xx, `x-should-retry`, `retry-after-ms`/`retry-after` up to 120 s,
otherwise 0.5·2ⁿ s capped at 8 with jitter; connection errors); the agent's
own two retries when `str(error)` mentions `tool_use_failed` or `tool call
validation failed` (temperature 0.3, 0.4); the SSE decoder (comments,
`event:`, CR/LF variants, `[DONE]`, error events) and the chunk reading of
`_consume_stream` (choices, deltas, tool call accumulation by index sorted,
invalid argument JSON as `{}`); any failure becomes the generic error frame.

**run_agent**: the association (`Sport association <id> not found` on
stderr), the disabled message, the banner, `input('\n> ')`, `exit`/`quit`/
`esci`, `clear`/`reset`/`pulisci`, the configuration re-read and provider
rebuilt per message, `CLICallback`'s ANSI output (tool arguments
`indent=2`, 500 characters), errors on stderr. The CLI agent has no user:
`save_report`/`list_reports` use the model's `user_id` argument as given.

## Go

- `internal/agentllm`: the SDK client (URL, headers, body, retries, SSE).
- `internal/service/agent*.go`: `Agent` (core loop), `AgentAIProvider`
  (`chat_stream`, `_consume_stream`), routing (Python `re` on regexp2 with
  Unicode `\b`/`\s` rewritten, `casefold`, NFKC), manual answers and the
  manual tool results in Python's key order (`_public` order; sealed index
  values keep sorted keys), `AgentTools` (business tools, manual tools,
  `coordinated_sync`), `AgentRuntime` (configuration, manual availability,
  association, provider), the embedded system prompt
  (`agent_system_prompt.txt`, extracted from `prompts.py`).
- `business_mcp_raised.go`: the business tools mark results that stand for
  a raised exception (`WithMCPRaised`); MCP output is unchanged.
- `internal/repository/agent_cache.go` (django-redis `_incr` script, integer
  values, the `save_info` pickle byte for byte with `json.loads`' shared key
  objects), `agent_store.go`.
- `internal/handler/websocket_agent.go`: the consumer; `websocket.go`
  routes `/ws/agent/` and serializes sends (the request task sends while the
  receive loop runs). `cmd/run-agent` (`/app/run-agent` in the image).
- Dependency: `github.com/dlclark/regexp2` (Python's backtracking `re`
  semantics; RE2's `\b` is ASCII-only).

## Not ported or not reproduced

Refused explicitly (the connection ends, or run-agent exits 1):
- Stream chunks with a non-string content, non-string tool call ids or
  names, tool call indexes that are floats, booleans or containers, and a
  forced loop-detection answer that fails (its error text would become an
  assistant message).
- Base URLs httpx would rewrite (non-ASCII, upper-case hosts, user info,
  query, fragment, percent escapes).
- Configuration values outside the integer/number forms (`max_iterations`,
  `history_cap`, `ws_rate_limit`, `ws_timeout`), missing configuration keys.
- Business tool inputs the [mcp-server](mcp-server.md) family refuses (XLSX/PDF
  files and the listed ORM inputs), `save_report` with a non-string
  `tool_name` or a non-object `params`/`ui_config` (Django stores or fails
  with a database error), cached `agent_save_info` values that are not the
  pickle of a JSON-like dict, user messages with lone surrogates.

Not reproduced: request header order and name case (compared as a set);
the jitter values of SDK retries; Django's log records; `run_agent`'s
tracebacks (exit 1), argparse texts and Django's global options and system
checks; Unicode case-insensitive equivalences beyond simple lowercasing
(`ſ`, `K`) in the patterns compiled with `re.I`; a reader block equal to the
default `{'title': '', 'text': …, 'screenshots': []}` is ordered like the
default; a screenshot whose index entry already has a `url` key followed by
later keys.

## Evidence (TDD)

`TESTS/fixtures/ws_agent_http.json` (generator alongside, 54 cases: 48
WebSocket sessions and 6 run_agent transcripts) runs through
`TESTS/contracts/test_ws_agent_http.py` (sessions with the RFC 6455 client,
`<<uuid:N>>` captures and run values, request comparison, CLI transcripts)
and `TESTS/adapters/django/ws_agent_http.py`: a per-run stub endpoint
scripted per case (streams, tool calls, status errors with
`retry-after-ms`, delays, an error event, a hook that disables AI mid-CLI),
a sealed manual runtime index, the instance configuration with the
encrypted key, seeded saved reports and tags; after every case the exact
outbound requests, the agent cache keys (values and TTL bounds, the pickle
bytes), the saved report rows and the complete model state.
`go test ./internal/service -run TestAgentRouting` compares 76 routes and the
follow-up pattern with CPython's results (`testdata/agent_routes.json`).

The first Django runs corrected the authored expectations for the fixture
administrator (it owns an association), `sanitize_text` stripping the
trailing replacement, and crashed sessions keeping the concurrency slot.
CLI stderr is compared without Django's log records; Django runs with
`--skip-checks` and warnings silenced (host-dependent output).

- RED (`ws-agent-http-red-r1`, the family's Go changes reversed on the merged
  base): Django 54/54; Go answers the first handshake with the unrouted 500.
- GREEN (`ws-agent-http-versioned-r1`): 54/54 on native Django and native Go
  (48 sessions and 6 transcripts), 48/48 sessions on Caddy `/api/v1` and
  `/api/v2` (run_agent is not an HTTP transport).
- Mutation (`ws-agent-http-mutation-r1`): not retrying 409 in the SDK's
  `_should_retry` (`agentllm`) fails Go on `ws-agent-provider-errors` (CAUGHT,
  restored).
- Re-run on the shared code: `websockets` (75/75, `websocket.go`), `templates-reports`
  (84/84, the business tool engine) and the MCP server tool adapter
  (`TESTS/adapters/go/mcp_server_tools.py`, all calls and stdio sessions identical).
