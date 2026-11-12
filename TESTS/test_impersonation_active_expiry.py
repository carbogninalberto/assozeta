"""Protocol/static controls for active impersonation-expiry evidence only.

These tests do not execute Django, Go, PostgreSQL, Redis or HTTP fixtures.
Actual expiry success remains a parent-owned native runtime check.
"""
from pathlib import Path
import tempfile
import unittest

from TESTS.adapters.django.impersonation_concurrency import (
    BEHAVIOR_SOURCES,
    EXPIRY_BARRIER_EVENTS,
    EXPIRY_SCENARIOS,
    behavior_source_hashes,
    validate_default_ttl,
    validate_expiry_case,
    validate_expiry_evidence,
)


def evidence(scenario=EXPIRY_SCENARIOS[0]):
    replacement = scenario == EXPIRY_SCENARIOS[1]
    return {
        'scenario': scenario,
        'method': 'POST' if replacement else 'DELETE',
        'request_cid': 'ic-expiry-control-a',
        'old_session': '00000000-0000-4000-8000-000000000001',
        'new_session': '00000000-0000-4000-8000-000000000002' if replacement else None,
        'events': list(EXPIRY_BARRIER_EVENTS),
        'old_resolved_before_expiry': True,
        'request_in_flight_when_key_absent': True,
        'sql_blocked_before_expiry': True,
        'sql_blocked_when_key_absent': True,
        'old_key_absent_before_release': True,
        'unrelated_cache_preserved': True,
        'expiry_request_accepted': True,
        'old_ttl_before_expiry_ms': 3_590_000,
        'ttl_after_expiry_request_ms': 249,
        'expiry_window_ms': 250,
        'new_session_ttl_before_ms': 3_590_000 if replacement else None,
        'new_session_ttl_while_old_absent_ms': 3_580_000 if replacement else None,
        'new_session_elapsed_before_ms': 10_000 if replacement else None,
        'new_session_elapsed_while_old_absent_ms': 20_000 if replacement else None,
        'new_session_default_ttl_preserved': replacement,
    }


def completed_case(scenario=EXPIRY_SCENARIOS[0]):
    replacement = scenario == EXPIRY_SCENARIOS[1]
    return {
        'expiry_evidence': evidence(scenario),
        'success': True, 'barrier_cleanup': True,
        'workers_stopped': True, 'cleanup_verified': True,
        'expired_old_denial_state_checked': True,
        'default_ttl_read_state_checked': replacement,
        'default_ttl_reads': [{'before_ms': 3_579_900, 'after_ms': 3_579_800,
            'before_elapsed_ms': 20_100, 'after_elapsed_ms': 20_200}] if replacement else [],
    }


class ActiveExpiryProtocolControls(unittest.TestCase):
    def test_both_reviewed_expiry_evidence_orders_pass(self):
        for scenario in EXPIRY_SCENARIOS:
            with self.subTest(scenario=scenario):
                validate_expiry_evidence(evidence(scenario))
                validate_expiry_case(completed_case(scenario))

    def test_expired_before_admission_or_before_sql_barrier_fails(self):
        for index in (0, 1):
            with self.subTest(index=index):
                changed = evidence()
                changed['events'] = list(EXPIRY_BARRIER_EVENTS)
                expired = changed['events'].index('old_session_expired')
                changed['events'].insert(index, changed['events'].pop(expired))
                with self.assertRaisesRegex(AssertionError, 'admitted SQL barrier'):
                    validate_expiry_evidence(changed)

    def test_finished_request_or_lost_sql_wait_fails(self):
        for field in ('request_in_flight_when_key_absent', 'sql_blocked_when_key_absent'):
            with self.subTest(field=field):
                changed = evidence(EXPIRY_SCENARIOS[1])
                changed[field] = False
                with self.assertRaisesRegex(AssertionError, field):
                    validate_expiry_evidence(changed)

    def test_missing_real_expiry_request_fails(self):
        for field, value in (('ttl_after_expiry_request_ms', -1), ('expiry_window_ms', 0)):
            with self.subTest(field=field):
                changed = evidence()
                changed[field] = value
                with self.assertRaisesRegex(AssertionError, 'expiry'):
                    validate_expiry_evidence(changed)
        changed = evidence()
        changed['expiry_request_accepted'] = False
        with self.assertRaisesRegex(AssertionError, 'expiry_request_accepted'):
            validate_expiry_evidence(changed)

    def test_wrong_or_colliding_old_and_new_sessions_fail(self):
        changed = evidence(EXPIRY_SCENARIOS[1])
        changed['new_session'] = changed['old_session']
        with self.assertRaisesRegex(AssertionError, 'colliding replacement'):
            validate_expiry_evidence(changed)
        changed = evidence(EXPIRY_SCENARIOS[0])
        changed['old_session'] = changed['new_session']
        with self.assertRaisesRegex(AssertionError, 'old session'):
            validate_expiry_evidence(changed)

    def test_shortened_or_renewed_replacement_ttl_fails(self):
        for field, value in (('new_session_ttl_before_ms', 60_000),
                             ('new_session_ttl_while_old_absent_ms', 3_600_001)):
            with self.subTest(field=field):
                changed = evidence(EXPIRY_SCENARIOS[1])
                changed[field] = value
                with self.assertRaisesRegex(AssertionError, 'TTL'):
                    validate_expiry_evidence(changed)
        changed = evidence(EXPIRY_SCENARIOS[1])
        changed['new_session_ttl_while_old_absent_ms'] = changed['new_session_ttl_before_ms'] + 1
        with self.assertRaisesRegex(AssertionError, 'renewed'):
            validate_expiry_evidence(changed)

    def test_incomplete_cleanup_cannot_count_as_a_passing_case(self):
        case = completed_case(EXPIRY_SCENARIOS[1])
        case['cleanup_verified'] = False
        with self.assertRaisesRegex(AssertionError, 'cleanup_verified'):
            validate_expiry_case(case)

    def test_source_manifest_binds_both_behavior_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, relative in enumerate(BEHAVIOR_SOURCES):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'behavior-' + str(index).encode())
            before = behavior_source_hashes(root)
            self.assertEqual(set(before), set(BEHAVIOR_SOURCES))
            (root / BEHAVIOR_SOURCES[1]).write_bytes(b'changed')
            after = behavior_source_hashes(root)
            self.assertNotEqual(before[BEHAVIOR_SOURCES[1]], after[BEHAVIOR_SOURCES[1]])
            self.assertEqual(before[BEHAVIOR_SOURCES[0]], after[BEHAVIOR_SOURCES[0]])

    def test_missing_behavior_source_fails_instead_of_becoming_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(AssertionError, 'missing'):
                behavior_source_hashes(directory)

    def test_strict_evidence_rejects_nonboolean_success(self):
        changed = evidence()
        changed['sql_blocked_before_expiry'] = 1
        with self.assertRaisesRegex(AssertionError, 'sql_blocked_before_expiry'):
            validate_expiry_evidence(changed)

    def test_missing_post_read_state_checks_fail_before_cleanup_can_hide_effects(self):
        for scenario in EXPIRY_SCENARIOS:
            fields = ['expired_old_denial_state_checked']
            if scenario == EXPIRY_SCENARIOS[1]:
                fields.append('default_ttl_read_state_checked')
            for field in fields:
                for missing in (True, False):
                    with self.subTest(scenario=scenario, field=field, missing=missing):
                        case = completed_case(scenario)
                        if missing:
                            del case[field]
                        else:
                            case[field] = False
                        with self.assertRaisesRegex(AssertionError, 'exact state check'):
                            validate_expiry_case(case)

    def test_five_minute_shortening_cannot_hide_within_previous_fifty_minute_floor(self):
        for suffix in ('before', 'while_old_absent'):
            changed = evidence(EXPIRY_SCENARIOS[1])
            changed['new_session_ttl_' + suffix + '_ms'] = 3_300_000
            with self.subTest(suffix=suffix), self.assertRaisesRegex(AssertionError, 'elapsed time'):
                validate_expiry_evidence(changed)
        for prefix in ('before', 'after'):
            case = completed_case(EXPIRY_SCENARIOS[1])
            case['default_ttl_reads'][0][prefix + '_ms'] = 3_300_000
            with self.subTest(read=prefix), self.assertRaisesRegex(AssertionError, 'elapsed time'):
                validate_expiry_case(case)

    def test_ttl_bound_accounts_for_elapsed_time_and_exact_clock_allowance(self):
        validate_default_ttl(3_598_000, 1_000)
        with self.assertRaisesRegex(AssertionError, 'elapsed time'):
            validate_default_ttl(3_597_999, 1_000)
        validate_default_ttl(3_300_000, 299_000)
        for elapsed in (None, -1, True, 1.5):
            with self.subTest(elapsed=elapsed), self.assertRaisesRegex(AssertionError, 'elapsed time'):
                validate_default_ttl(3_599_000, elapsed)

    def test_replacement_read_samples_are_mandatory_ordered_and_cannot_renew(self):
        mutations = [
            lambda case: case.update(default_ttl_reads=[]),
            lambda case: case['default_ttl_reads'][0].pop('before_elapsed_ms'),
            lambda case: case['default_ttl_reads'][0].update(before_elapsed_ms=19_900),
            lambda case: case['default_ttl_reads'][0].update(after_ms=3_579_901),
            lambda case: case['default_ttl_reads'][0].update(before_ms=3_580_001),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                case = completed_case(EXPIRY_SCENARIOS[1])
                mutate(case)
                with self.assertRaises(AssertionError):
                    validate_expiry_case(case)


if __name__ == '__main__':
    unittest.main()
