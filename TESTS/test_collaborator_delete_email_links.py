"""Portable controls for the collaborator-delete email-link helper."""
from copy import deepcopy
import unittest
from uuid import UUID

from TESTS.adapters.django.collaborator_delete_email_links import (
    uid,
    authored_snapshot,
)


class AuthoredSnapshotControls(unittest.TestCase):
    def setUp(self):
        self.user_one = UUID(uid(764050))
        self.user_two = UUID(uid(764051))
        self.invite = UUID(uid(765050))
        self.audit_two = 2
        self.index_two = UUID(uid(766052))
        self.before = {
            'application.User': [
                {'user_id': self.user_one, 'email': 'one@example.test'},
                {'user_id': self.user_two, 'email': 'two@example.test'},
            ],
            'application.CollaborationInvites': [
                {
                    'collaboration_invite_id': self.invite,
                    'email': 'one@example.test',
                },
            ],
            'auditlog.LogEntry': [
                {'id': 1, 'cid': 'retained'},
                {'id': self.audit_two, 'cid': 'retained-two'},
            ],
            'application.AuditLogIndex': [
                {'audit_log_index_id': self.index_two, 'log_entry_id': 1},
            ],
            'other.Model': [
                {'other_id': UUID(uid(767050)), 'value': 'must survive'},
            ],
        }
        self.primary_keys = {
            'application.User': 'user_id',
            'application.CollaborationInvites': 'collaboration_invite_id',
            'auditlog.LogEntry': 'id',
            'application.AuditLogIndex': 'audit_log_index_id',
            'other.Model': 'other_id',
        }

    def expected(self, removals, *, new_logs=(), new_indexes=()):
        return authored_snapshot(
            self.before,
            removals,
            primary_keys=self.primary_keys,
            log_label='auditlog.LogEntry',
            new_logs=new_logs,
            index_label='application.AuditLogIndex',
            new_indexes=new_indexes,
        )

    def test_explicit_removals_append_exact_audits_and_preserve_every_other_row(self):
        before_copy = deepcopy(self.before)
        new_log = {'id': 3, 'cid': 'authored'}
        new_index = {
            'audit_log_index_id': UUID(uid(766053)),
            'log_entry_id': 3,
        }

        result = self.expected(
            {
                'application.User': {self.user_one},
                'application.CollaborationInvites': {self.invite},
            },
            new_logs=(new_log,),
            new_indexes=(new_index,),
        )

        self.assertEqual(
            result['application.User'],
            [{'user_id': self.user_two, 'email': 'two@example.test'}],
        )
        self.assertEqual(result['application.CollaborationInvites'], [])
        self.assertEqual(
            result['auditlog.LogEntry'],
            [
                {'id': 1, 'cid': 'retained'},
                {'id': 2, 'cid': 'retained-two'},
                new_log,
            ],
        )
        self.assertEqual(
            result['application.AuditLogIndex'],
            [
                {'audit_log_index_id': self.index_two, 'log_entry_id': 1},
                new_index,
            ],
        )
        self.assertEqual(result['other.Model'], before_copy['other.Model'])
        self.assertEqual(self.before, before_copy, 'Helper mutated its input')

    def test_unrequested_deletion_or_mutation_does_not_match_expected(self):
        expected = self.expected(
            {'application.CollaborationInvites': {self.invite}},
        )
        unexpected = deepcopy(expected)
        unexpected['application.User'].pop()
        self.assertNotEqual(unexpected, expected)

        unexpected = deepcopy(expected)
        unexpected['other.Model'][0]['value'] = 'collector side effect'
        self.assertNotEqual(unexpected, expected)

class FixtureRangeControls(unittest.TestCase):
    def test_assigned_uuid_range_is_inclusive_and_formatted(self):
        self.assertEqual(
            uid(760000),
            'a2000000-0000-4000-8000-000000760000',
        )
        self.assertEqual(
            uid(769999),
            'a2000000-0000-4000-8000-000000769999',
        )
        for invalid in (759999, 770000):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(
                    AssertionError,
                    'Fixture ID outside assigned range',
                ):
                    uid(invalid)


if __name__ == '__main__':
    unittest.main()
