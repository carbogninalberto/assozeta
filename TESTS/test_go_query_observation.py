"""Authored v1 JSONL fixtures; entirely offline, using real temporary files."""

import copy
import json
import os
from pathlib import Path
import tempfile
import traceback
import unittest

from TESTS.adapters.go import query_observation as reader


RUN = "12345678-1234-4234-8234-123456789abc"
OTHER_RUN = "abcdefab-1234-4234-8234-123456789abc"
UINT64_MAX = (1 << 64) - 1
# Authored from the two Go record declarations, independently of reader constants.
COUNTERS = (
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
ZERO_ONLY = (
    "driver_errors", "driver_unbound_starts", "driver_unbound_ends",
    "connect_errors", "reset_errors", "ping_errors", "driver_violations",
    "unknown_driver_kinds", "invalid_reset_probes", "query_errors",
    "unbound_starts", "unbound_ends", "violations", "late_starts", "late_ends",
    "duplicate_ends", "duplicate_finishes", "reused_contexts", "missing_contexts",
    "foreign_contexts", "context_mismatches", "active_requests", "active_queries",
    "active_driver_operations", "post_close_events",
)


def record(kind):
    return dict(type=kind, version=1, run_id=RUN, complete=kind != "header",
                active_requests_at_start=0,
                **dict.fromkeys(COUNTERS, 0))


def request(sequence=1, *, queries=3, connects=1, resets=2, probes=1, pings=0):
    row = record("request")
    row.update(request_id=f"{RUN}-{sequence}", sequence=sequence,
               active_requests_at_start=sequence,
               request_starts=1, request_finishes=1, finish_calls=1, summaries=1,
               query_starts=queries, query_ends=queries,
               connect_starts=connects, connect_ends=connects,
               reset_starts=resets, reset_ends=resets, reset_probes=probes,
               ping_starts=pings, ping_ends=pings,
               driver_starts=connects + resets + pings,
               driver_ends=connects + resets + pings)
    return row


def stream(requests=None):
    requests = [request()] if requests is None else requests
    footer = record("footer")
    for key in COUNTERS:
        footer[key] = sum(row[key] for row in requests)
    return [record("header"), *copy.deepcopy(requests), footer]


def encode(rows):
    return b"".join(json.dumps(row, separators=(",", ":")).encode() + b"\n"
                    for row in rows)


def numeric(row):
    return {key: row[key] for key in (
        "version", "sequence", "active_requests_at_start", *COUNTERS
    )}


class ReaderControls(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "private-token-and-SQL-path"

    def read(self, rows, *, finalized=True):
        self.path.write_bytes(encode(rows) if isinstance(rows, list) else rows)
        return reader.read_query_observations(self.path, finalized=finalized)

    def reject(self, rows, *, finalized=True):
        with self.assertRaises(AssertionError) as caught:
            self.read(rows, finalized=finalized)
        self.assertEqual(str(caught.exception), "invalid query observation stream")

    def test_valid_permuted_eight_requests_have_separate_numeric_records(self):
        requests = [request(i, connects=i % 4, resets=3 - i % 4,
                            probes=(3 - i % 4) // 2) for i in range(1, 9)]
        order = [7, 2, 5, 0, 6, 3, 1, 4]
        rows = stream([requests[i] for i in order])
        result = self.read(rows)
        self.assertEqual(set(result), {"run_id", "requests", "footer"})
        self.assertEqual(result["run_id"], RUN)
        self.assertEqual(result["footer"], rows[-1])
        self.assertEqual(result["footer"]["query_starts"], 24)
        self.assertEqual(result["requests"], {
            row["request_id"]: numeric(row) for row in requests
        })
        for counts in result["requests"].values():
            self.assertTrue(all(type(value) is int for value in counts.values()))
            reader.assert_message_request_budget(counts)

    def test_live_complete_header_and_partial_tail_only(self):
        rows = stream()
        for partial in (b"", b"{", b'{"type":"request"', b"\xff"):
            with self.subTest(partial=partial):
                result = self.read(encode(rows[:-1]) + partial, finalized=False)
                self.assertIsNone(result["footer"])
                self.assertEqual(len(result["requests"]), 1)
        self.assertEqual(self.read(rows[:1], finalized=False)["requests"], {})
        self.assertEqual(self.read(rows, finalized=False)["footer"], rows[-1])

    def test_live_unterminated_valid_json_is_still_not_a_record(self):
        prefix = encode(stream()[:-1])
        for tail in (encode([request(2)])[:-1], encode(stream()[-1:])[:-1]):
            result = self.read(prefix + tail, finalized=False)
            self.assertEqual(set(result["requests"]), {RUN + "-1"})
            self.assertIsNone(result["footer"])

    def test_no_complete_header_is_never_a_live_snapshot(self):
        for data in (b"", b"{", encode(stream()[:1]).rstrip(b"\n")):
            for finalized in (True, False):
                with self.subTest(data=data, finalized=finalized):
                    self.reject(data, finalized=finalized)

    def test_final_requires_complete_last_footer_and_newline(self):
        rows = stream()
        for data in (encode(rows[:-1]), encode(rows)[:-1],
                     encode(rows)[:-30], encode(rows[:-1]) + b"{",
                     encode(rows[:-1]) + b"\n"):
            with self.subTest(data=data[-20:]):
                self.reject(data)

    def test_footer_rejects_every_later_record_or_partial_byte_even_live(self):
        rows = stream()
        for tail in (encode([rows[-1]]), encode([request(2)]),
                     encode([record("invalidated")]), b"{", b"\n", b" "):
            for finalized in (True, False):
                with self.subTest(tail=tail[:20], finalized=finalized):
                    self.reject(encode(rows) + tail, finalized=finalized)

    def test_complete_records_must_be_json_objects_with_exact_schema(self):
        for value in (None, True, [], 3, "private-sql", {"type": "request"}):
            self.reject(encode([record("header"), value]), finalized=False)
        for index in range(3):
            for key in stream()[index]:
                rows = stream()
                del rows[index][key]
                with self.subTest(index=index, missing=key):
                    self.reject(rows)
            for key in ("sql", "args", "token", "error", "owner", "unexpected"):
                rows = stream()
                rows[index][key] = "private-content"
                with self.subTest(index=index, extra=key):
                    self.reject(rows)
        for index in (0, 2):
            for key, value in (("sequence", 0), ("request_id", "")):
                rows = stream()
                rows[index][key] = value
                self.reject(rows)

    def test_duplicate_json_keys_are_rejected_including_identical_values(self):
        data = encode(stream())
        for fragment, replacement in (
            (b'"version":1', b'"version":1,"version":1'),
            (b'"query_starts":3', b'"query_starts":3,"query_starts":3'),
            (b'"complete":true', b'"complete":true,"complete":true'),
        ):
            self.reject(data.replace(fragment, replacement, 1))

    def test_all_numeric_fields_reject_bool_float_negative_and_uint64_overflow(self):
        for index in range(3):
            keys = ["version", "active_requests_at_start", *COUNTERS]
            keys += ["sequence"] if index == 1 else []
            for key in keys:
                for bad in (True, False, 1.0, -1, UINT64_MAX + 1, "1", None, [], {}):
                    rows = stream()
                    rows[index][key] = bad
                    with self.subTest(index=index, key=key, bad=bad):
                        self.reject(rows)
        for bad in (b"NaN", b"Infinity", b"-Infinity", b"1e999", b"9" * 5000):
            self.reject(encode(stream()).replace(b'"query_starts":3',
                                                 b'"query_starts":' + bad, 1))

    def test_negative_zero_integer_tokens_are_not_uint64_encoding(self):
        for index in range(3):
            rows = stream()
            data = encode(rows[:index])
            data += encode([rows[index]]).replace(b'"query_errors":0',
                                                  b'"query_errors":-0', 1)
            data += encode(rows[index + 1:])
            with self.subTest(index=index):
                self.reject(data)

    def test_version_and_complete_have_exact_types_and_values(self):
        for index in range(3):
            for bad in (0, 2, UINT64_MAX):
                rows = stream()
                rows[index]["version"] = bad
                self.reject(rows)
            for bad in (None, 0, 1, "true", [], {}):
                rows = stream()
                rows[index]["complete"] = bad
                self.reject(rows)
            rows = stream()
            rows[index]["complete"] = index == 0
            self.reject(rows, finalized=False)

    def test_header_all_counters_are_zero(self):
        for key in (*COUNTERS, "active_requests_at_start"):
            rows = stream()
            rows[0][key] = 1
            with self.subTest(key=key):
                self.reject(rows)

    def test_active_requests_at_start_is_a_bounded_snapshot_not_a_sum(self):
        rows = stream([request(i) for i in range(1, 9)])
        # All eight Begin calls precede finishes, so snapshots are 1..8.
        values = self.read(rows)["requests"].values()
        self.assertEqual(max(row["active_requests_at_start"] for row in values), 8)
        self.assertEqual(rows[-1]["active_requests_at_start"], 0)
        for index, value in ((0, 1), (1, 0), (1, 2), (8, 9), (9, 1)):
            bad = copy.deepcopy(rows)
            bad[index]["active_requests_at_start"] = value
            with self.subTest(index=index, value=value):
                self.reject(bad)
        # Serial requests have the same query budget; parser reports them as
        # serial, leaving the parent's overlap assertion to reject that run.
        for row in rows[1:-1]:
            row["active_requests_at_start"] = 1
        serial = self.read(rows)
        self.assertEqual(max(row["active_requests_at_start"]
                             for row in serial["requests"].values()), 1)

    def test_bad_or_conflicting_run_ids(self):
        for bad in ("", "00000000-0000-0000-0000-000000000000", RUN.upper(),
                    RUN.replace("-", ""), "{" + RUN + "}", "urn:uuid:" + RUN,
                    RUN + "\n", "private-sql", 1, None):
            rows = stream()
            for row in rows:
                row["run_id"] = bad
            self.reject(rows)
        for index in (1, 2):
            rows = stream()
            rows[index]["run_id"] = OTHER_RUN
            self.reject(rows)

    def test_request_ids_and_sequences_cannot_be_forged_or_reused(self):
        for bad in ("", RUN, OTHER_RUN + "-1", RUN + "-01", RUN + "-0",
                    RUN + "-+1", RUN + "-1\n", RUN + "-2", 1, None):
            rows = stream()
            rows[1]["request_id"] = bad
            self.reject(rows)
        rows = stream()
        rows[1]["sequence"] = 0
        self.reject(rows)
        for requests in ([request(), request()], [request(2)],
                         [request(1), request(3)], [request(UINT64_MAX)]):
            self.reject(stream(requests))
        rows = stream([request(), request(2)])
        rows[2]["sequence"] = 1
        self.reject(rows, finalized=False)
        self.assertEqual(len(self.read(stream([request(2)])[:-1], finalized=False)
                             ["requests"]), 1)

    def test_invalid_types_order_and_invalidated_records_fail(self):
        for kind in ("invalidated", "unknown", "Header", None, 1, []):
            rows = stream()
            rows[1]["type"] = kind
            self.reject(rows)
        rows = stream()
        for malformed in ([rows[1], rows[0], rows[2]],
                          [rows[0], rows[0], rows[1], rows[2]],
                          [rows[2]], [rows[0], record("invalidated")]):
            self.reject(malformed, finalized=False)

    def test_all_error_unbound_violation_active_postclose_counters_fail(self):
        for key in ZERO_ONLY:
            for index in (1, 2):
                rows = stream()
                rows[index][key] = 1
                with self.subTest(index=index, key=key):
                    self.reject(rows, finalized=False)

    def test_true_complete_cannot_bypass_balanced_counter_identities(self):
        for key in ("query_ends", "driver_starts", "driver_ends", "connect_ends",
                    "reset_ends", "ping_ends", "reset_probes", "request_starts",
                    "request_finishes", "finish_calls", "summaries"):
            rows = stream()
            rows[1][key] += 1 if key != "reset_probes" else 3
            # Reconcile footer sums so the local invariant is what must fail.
            rows[-1][key] = rows[1][key]
            with self.subTest(key=key):
                self.reject(rows, finalized=False)

    def test_per_request_budget_rejects_four_two_despite_clean_aggregate_24(self):
        requests = [request(i) for i in range(1, 9)]
        requests[2].update(query_starts=4, query_ends=4)
        requests[5].update(query_starts=2, query_ends=2)
        observations = self.read(stream(requests))
        self.assertEqual(observations["footer"]["query_starts"], 24)
        for sequence in (3, 6):
            with self.assertRaisesRegex(AssertionError, "^invalid message request budget$"):
                reader.assert_message_request_budget(
                    observations["requests"][f"{RUN}-{sequence}"]
                )

    def test_account_query_offset_by_fewer_probes_still_fails_after_reading(self):
        for queries, probes in ((3, 2), (4, 1)):
            observations = self.read(stream([request(queries=queries, probes=probes)]))
            self.assertEqual(observations["footer"]["query_starts"] +
                             observations["footer"]["reset_probes"], 5)
            counts = observations["requests"][RUN + "-1"]
            if queries == 3:
                self.assertEqual(reader.assert_message_request_budget(counts)
                                 ["raw_expected"], 5)
            else:
                with self.assertRaises(AssertionError):
                    reader.assert_message_request_budget(counts)

    def test_footer_every_counter_sum_and_request_count_is_reconciled_live(self):
        for key in COUNTERS:
            rows = stream()
            rows[-1][key] += 1
            with self.subTest(key=key):
                self.reject(rows, finalized=False)
        rows = stream()
        # Footer remains balanced and clean but disagrees with request totals.
        for key in ("query_starts", "query_ends", "driver_starts", "driver_ends",
                    "connect_starts", "connect_ends"):
            rows[-1][key] += 1
        self.reject(rows, finalized=False)

    def test_empty_final_run_and_uint64_max_balanced_query_counters(self):
        self.assertEqual(self.read(stream([]))["requests"], {})
        counts = self.read(stream([request(queries=UINT64_MAX)]))["requests"]
        self.assertEqual(next(iter(counts.values()))["query_starts"], UINT64_MAX)
        self.reject(stream([request(1, queries=UINT64_MAX), request(2)]))

    def test_bad_json_utf8_blank_lines_and_deep_nesting_fail_safely(self):
        for bad in (b"\xff\n", b"\n", b"{\n", b"{}{}\n", b"\xef\xbb\xbf{}\n",
                    b"[" * 1200 + b"]" * 1200 + b"\n"):
            self.reject(encode(stream()[:1]) + bad, finalized=False)

    def test_private_input_never_appears_in_error_or_formatted_traceback(self):
        secret = "SELECT private_token_abc FROM private_table"
        bad_inputs = [encode(stream()).replace(b'"version":1', secret.encode(), 1),
                      encode([record("header"), {"sql": secret}]),
                      encode(stream()).replace(b'"query_starts":3',
                                               b'"query_starts":"' + secret.encode() + b'"', 1)]
        for data in bad_inputs:
            self.path.write_bytes(data)
            try:
                reader.read_query_observations(self.path, finalized=True)
            except AssertionError as exc:
                output = "".join(traceback.format_exception(exc))
                for private in (secret, str(self.path), self.path.name):
                    self.assertNotIn(private, output)
            else:
                self.fail("malformed private input was accepted")

    def test_missing_directory_and_fifo_are_fixed_failures_without_blocking(self):
        for path in (self.path, Path(self.directory.name)):
            with self.assertRaisesRegex(AssertionError, "^invalid query observation stream$"):
                reader.read_query_observations(path, finalized=True)
        os.mkfifo(self.path)
        with self.assertRaisesRegex(AssertionError, "^invalid query observation stream$"):
            reader.read_query_observations(self.path, finalized=False)

    def test_64mib_file_cap_and_bounded_line_cap_including_live_partial(self):
        self.path.touch()
        with self.path.open("r+b") as output:
            output.truncate((64 << 20) + 1)
        with self.assertRaises(AssertionError):
            reader.read_query_observations(self.path, finalized=False)
        for tail in (b" " * (16 << 10) + b"\n", b" " * ((16 << 10) + 1)):
            self.reject(encode(stream()[:1]) + tail, finalized=False)

    def test_exact_64mib_and_exact_line_cap_are_permitted(self):
        # 4096 lines, each 16 KiB including newline: actual 64 MiB tempfile.
        rows = stream([request(i) for i in range(1, 4095)])
        with self.path.open("wb") as output:
            for row in rows:
                line = encode([row])
                self.assertLess(len(line), 16 << 10)
                output.write(line[:-1] + b" " * ((16 << 10) - len(line)) + b"\n")
        self.assertEqual(self.path.stat().st_size, 64 << 20)
        result = reader.read_query_observations(self.path, finalized=True)
        self.assertEqual(len(result["requests"]), 4094)

    def test_finalized_argument_is_an_actual_bool(self):
        for value in (0, 1, None, "false"):
            self.reject(stream(), finalized=value)


class BudgetControls(unittest.TestCase):
    def reject(self, counts):
        with self.assertRaisesRegex(AssertionError, "^invalid message request budget$"):
            reader.assert_message_request_budget(counts)

    def test_three_acquisitions_all_accepted_connect_reset_probe_combinations(self):
        for connects in range(4):
            resets = 3 - connects
            for probes in range(resets + 1):
                counts = numeric(request(connects=connects, resets=resets, probes=probes))
                before = copy.deepcopy(counts)
                summary = reader.assert_message_request_budget(counts)
                self.assertEqual(summary, {
                    "query_starts": 3, "query_ends": 3,
                    "connect_starts": connects, "connect_ends": connects,
                    "reset_starts": resets, "reset_ends": resets,
                    "ping_starts": 0, "ping_ends": 0,
                    "reset_probes": probes, "driver_starts": 3, "driver_ends": 3,
                    "raw_expected": 3 + probes,
                })
                self.assertEqual(counts, before)

    def test_same_owner_four_and_two_queries_cannot_hide_in_aggregate_24(self):
        rows = [request(i) for i in range(1, 9)]
        rows[2].update(query_starts=4, query_ends=4)
        rows[5].update(query_starts=2, query_ends=2)
        self.assertEqual(sum(row["query_starts"] for row in rows), 24)
        self.reject(numeric(rows[2]))
        self.reject(numeric(rows[5]))

    def test_extra_account_query_offset_by_fewer_probes_is_rejected(self):
        baseline = request(queries=3, probes=2)
        extra = request(queries=4, probes=1)
        self.assertEqual(baseline["query_starts"] + baseline["reset_probes"],
                         extra["query_starts"] + extra["reset_probes"])
        self.reject(numeric(extra))

    def test_fourth_acquisition_or_probe_and_readiness_ping_are_rejected(self):
        for row in (request(connects=1, resets=3, probes=3),
                    request(connects=0, resets=4, probes=4),
                    request(connects=0, resets=3, probes=4),
                    request(pings=1), request(connects=0, resets=2, pings=1)):
            self.reject(numeric(row))

    def test_errors_before_sql_all_bad_counters_and_unbalanced_work_fail(self):
        for key in ZERO_ONLY:
            row = numeric(request())
            row[key] = 1
            self.reject(row)
        for operation in ("connect", "reset"):
            row = numeric(request(queries=0))
            row["driver_errors"] = row[operation + "_errors"] = 1
            self.reject(row)
        for key in ("query_starts", "query_ends", "driver_starts", "driver_ends",
                    "connect_ends", "reset_ends", "request_starts", "summaries"):
            row = numeric(request())
            row[key] += 1
            self.reject(row)

    def test_budget_is_strict_even_when_called_without_reader(self):
        counts = numeric(request())
        for key in counts:
            for bad in (True, 1.0, -1, UINT64_MAX + 1, "3", None):
                row = dict(counts, **{key: bad})
                self.reject(row)
            row = dict(counts)
            del row[key]
            self.reject(row)
        self.reject(dict(counts, sql="SELECT private token"))
        self.reject(dict(counts, active_requests_at_start=0))
        self.reject(dict(counts, active_requests_at_start=2))
        self.reject(dict(counts, sequence=0))
        self.reject(dict(counts, version=2))
        for bad in (None, [], 3, request()):
            self.reject(bad)

    def test_middle_unknown_sql_and_process_exit_are_external_checks(self):
        # A three-query record is identical whether the middle query has the
        # expected scoped SQL or an unknown statement. This API cannot decide.
        summary = reader.assert_message_request_budget(numeric(request()))
        self.assertEqual(summary["raw_expected"], 4)
        self.assertNotIn("accepted", summary)
        self.assertNotIn("process_exit", summary)


if __name__ == "__main__":
    unittest.main()
