"""Strict offline consumer of the unpublished Go query-observation JSONL v1.

No SQL, arguments, tokens, or endpoint acceptance decisions belong here. A clean
footer is accounting evidence only: the caller must also observe process exit 0
and reconcile PostgreSQL raw business/probe counts and scoped SQL/arguments.
"""

import json
import os
import re
import stat


MAX_BYTES = 64 << 20
MAX_LINE_BYTES = 16 << 10  # Includes the terminating newline.
UINT64_MAX = (1 << 64) - 1
_STREAM_ERROR = "invalid query observation stream"
_BUDGET_ERROR = "invalid message request budget"
_RUN_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

# Explicit declarations from observer.go and driver_observation.go; never infer
# schemas from input and never import or execute Go implementation code.
_COUNTERS = (
    "driver_starts", "driver_ends", "driver_errors", "driver_unbound_starts",
    "driver_unbound_ends", "connect_starts", "connect_ends", "connect_errors",
    "reset_starts", "reset_ends", "reset_errors", "ping_starts", "ping_ends",
    "ping_errors", "reset_probes", "driver_violations", "unknown_driver_kinds",
    "invalid_reset_probes", "query_starts", "query_ends", "query_errors",
    "unbound_starts", "unbound_ends", "violations", "late_starts", "late_ends",
    "duplicate_ends", "duplicate_finishes", "reused_contexts", "missing_contexts",
    "foreign_contexts", "context_mismatches", "request_starts", "request_finishes",
    "finish_calls", "summaries", "active_requests", "active_queries",
    "active_driver_operations", "post_close_events",
)
_ZERO_ONLY = (
    "driver_errors", "driver_unbound_starts", "driver_unbound_ends",
    "connect_errors", "reset_errors", "ping_errors", "driver_violations",
    "unknown_driver_kinds", "invalid_reset_probes", "query_errors",
    "unbound_starts", "unbound_ends", "violations", "late_starts", "late_ends",
    "duplicate_ends", "duplicate_finishes", "reused_contexts", "missing_contexts",
    "foreign_contexts", "context_mismatches", "active_requests", "active_queries",
    "active_driver_operations", "post_close_events",
)
_REQUEST_COUNTS = ("request_starts", "request_finishes", "finish_calls", "summaries")
_BASE_FIELDS = frozenset(("type", "version", "run_id", "complete",
                          "active_requests_at_start", *_COUNTERS))
_REQUEST_FIELDS = _BASE_FIELDS | {"sequence", "request_id"}
_NUMERIC_FIELDS = ("version", "sequence", "active_requests_at_start", *_COUNTERS)
_SUMMARY_FIELDS = (
    "query_starts", "query_ends", "connect_starts", "connect_ends",
    "reset_starts", "reset_ends", "ping_starts", "ping_ends", "reset_probes",
    "driver_starts", "driver_ends",
)


def _require(condition):
    if not condition:
        raise ValueError(_STREAM_ERROR)


def _uint64(value):
    return type(value) is int and 0 <= value <= UINT64_MAX


def _integer(text):
    # Bound conversion work as well as values; bool/float never reach here.
    _require(len(text) <= 20 and not text.startswith("-"))
    value = int(text)
    _require(_uint64(value))
    return value


def _not_integer(_):
    raise ValueError(_STREAM_ERROR)


def _object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _decode(line):
    # Decode explicitly: json.loads(bytes) otherwise also accepts UTF-16/32.
    row = json.loads(line.decode("utf-8"), object_pairs_hook=_object,
                     parse_int=_integer, parse_float=_not_integer,
                     parse_constant=_not_integer)
    _require(type(row) is dict)
    kind = row.get("type")
    _require(type(kind) is str and kind in ("header", "request", "footer"))
    _require(row.keys() == (_REQUEST_FIELDS if kind == "request" else _BASE_FIELDS))
    _require(all(_uint64(row[key]) for key in _COUNTERS))
    _require(_uint64(row["active_requests_at_start"]))
    _require(type(row["version"]) is int and row["version"] == 1)
    _require(type(row["complete"]) is bool)
    run_id = row["run_id"]
    _require(type(run_id) is str and _RUN_ID.fullmatch(run_id) is not None)
    _require(run_id != "00000000-0000-0000-0000-000000000000")
    if kind == "header":
        _require(row["complete"] is False)
        _require(all(row[key] == 0 for key in _COUNTERS))
    else:
        _require(row["complete"] is True)
        _clean(row)
    if kind == "request":
        _require(_uint64(row["sequence"]) and row["sequence"] > 0)
        _require(type(row["request_id"]) is str and
                 row["request_id"] == f"{run_id}-{row['sequence']}")
        _require(all(row[key] == 1 for key in _REQUEST_COUNTS))
        _require(1 <= row["active_requests_at_start"] <= row["sequence"])
    else:
        _require(row["active_requests_at_start"] == 0)
    return row


def _clean(row):
    """Validate identities independently of the writer's Complete boolean."""
    _require(all(row[key] == 0 for key in _ZERO_ONLY))
    _require(row["query_starts"] == row["query_ends"])
    _require(row["driver_starts"] == row["driver_ends"])
    for operation in ("connect", "reset", "ping"):
        _require(row[operation + "_starts"] == row[operation + "_ends"])
    _require(row["driver_starts"] ==
             row["connect_starts"] + row["reset_starts"] + row["ping_starts"])
    _require(row["driver_ends"] ==
             row["connect_ends"] + row["reset_ends"] + row["ping_ends"])
    _require(row["reset_probes"] <= row["reset_starts"])


def _read(source, *, finalized):
    run_id = None
    requests = {}
    sequences = set()
    totals = dict.fromkeys(_COUNTERS, 0)
    footer = None
    consumed = 0
    while True:
        # A fixed line bound prevents an unterminated line from allocating the
        # whole file. The remaining-byte bound also covers files growing while
        # read, without trusting only the size from fstat at open time.
        line = source.readline(min(MAX_LINE_BYTES, MAX_BYTES - consumed) + 1)
        if not line:
            break
        consumed += len(line)
        _require(consumed <= MAX_BYTES and len(line) <= MAX_LINE_BYTES)
        _require(footer is None)  # Even a partial byte after footer is invalid.
        if not line.endswith(b"\n"):
            _require(not finalized and run_id is not None)
            break  # Writer may still be appending; never accept a partial row.
        row = _decode(line)
        kind = row["type"]
        if run_id is None:
            _require(kind == "header")
            run_id = row["run_id"]
            continue
        _require(row["run_id"] == run_id and kind != "header")
        if kind == "request":
            identifier, sequence = row["request_id"], row["sequence"]
            _require(identifier not in requests and sequence not in sequences)
            sequences.add(sequence)
            requests[identifier] = {key: row[key] for key in _NUMERIC_FIELDS}
            for key in _COUNTERS:
                totals[key] += row[key]
                _require(totals[key] <= UINT64_MAX)
        else:
            count = len(requests)
            # Positive unique sequences with maximum N are exactly 1..N.
            _require(max(sequences, default=0) == count)
            _require(all(row[key] == count for key in _REQUEST_COUNTS))
            _require(all(row[key] == totals[key] for key in _COUNTERS))
            footer = {key: row[key] for key in ("type", "version", "run_id",
                                              "complete", "active_requests_at_start",
                                              *_COUNTERS)}
    _require(run_id is not None and (not finalized or footer is not None))
    return {"run_id": run_id, "requests": requests, "footer": footer}


def read_query_observations(path, *, finalized: bool):
    """Read a regular file, bounded to 64 MiB and 16 KiB per JSONL line.

    Return ``{run_id, requests, footer}``. Each request value contains only
    integer ``version``, ``sequence``, ``active_requests_at_start``, and the 40
    declared counters. The active-at-start snapshot is not summed. Footer is
    None until present, then a validated fixed-schema footer including metadata.
    Live mode permits only an incomplete final line after a complete header;
    requests may finish out of order. Final mode requires sequences 1..N and a
    clean, fully reconciled final footer ending in a newline. Any footer present
    in live mode must already satisfy the same checks.

    Failures raise AssertionError with a fixed message; input content, file
    paths, JSON diagnostics, and OS error details are never included.
    """
    try:
        _require(type(finalized) is bool)
        # O_NONBLOCK avoids hanging on FIFOs before the regular-file check.
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        try:
            info = os.fstat(descriptor)
            _require(stat.S_ISREG(info.st_mode) and info.st_size <= MAX_BYTES)
            source = os.fdopen(descriptor, "rb")
        except BaseException:
            os.close(descriptor)
            raise
        with source:
            return _read(source, finalized=finalized)
    except (OSError, ValueError, TypeError, RecursionError, OverflowError):
        raise AssertionError(_STREAM_ERROR) from None


def assert_message_request_budget(record):
    """Validate one numeric request record; return fixed counts + raw_expected.

    Exactly three query callbacks and three connect/reset acquisitions are
    allowed, with no Ping operations and at most one probe per reset. The raw
    expected count is 3 + reset_probes. Caller must independently check actual
    PostgreSQL raw counts, scoped SQL/arguments, and successful process exit.
    """
    try:
        _require(type(record) is dict and record.keys() == frozenset(_NUMERIC_FIELDS))
        _require(all(_uint64(record[key]) for key in _NUMERIC_FIELDS))
        _require(record["version"] == 1 and record["sequence"] > 0)
        _require(1 <= record["active_requests_at_start"] <= record["sequence"])
        _require(all(record[key] == 1 for key in _REQUEST_COUNTS))
        _clean(record)
        _require(record["query_starts"] == record["query_ends"] == 3)
        _require(record["connect_starts"] + record["reset_starts"] == 3)
        _require(record["ping_starts"] == record["ping_ends"] == 0)
        _require(record["reset_probes"] <= record["reset_starts"] <= 3)
        _require(record["driver_starts"] == record["driver_ends"] == 3)
        summary = {key: record[key] for key in _SUMMARY_FIELDS}
        summary["raw_expected"] = 3 + record["reset_probes"]
        return summary
    except (ValueError, TypeError, KeyError):
        raise AssertionError(_BUDGET_ERROR) from None
