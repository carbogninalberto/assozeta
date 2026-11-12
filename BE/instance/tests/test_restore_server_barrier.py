"""Unit routing/compatibility checks; real PostgreSQL proof lives in its adapter."""
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.db import DatabaseError
from django.test import SimpleTestCase, override_settings

from instance.restore.locking import Busy, operation_lock
from instance.restore.server_barrier import drain_go_transactions


class RestoreServerBarrierTests(SimpleTestCase):
    def postgres(self):
        connection = MagicMock()
        connection.vendor = 'postgresql'
        connection.in_atomic_block = False
        connection.get_autocommit.return_value = True
        return connection

    def test_sqlite_preserves_legacy_restore_without_claiming_a_server_fence(self):
        connection = self.postgres()
        connection.vendor = 'sqlite'
        with patch('instance.restore.server_barrier.connection', connection), patch('instance.restore.server_barrier.transaction.atomic') as atomic:
            drain_go_transactions()
        atomic.assert_not_called()
        connection.cursor.assert_not_called()

    def test_unknown_backend_refuses_without_database_work(self):
        connection = self.postgres()
        connection.vendor = 'unsupported-fixture'
        with patch('instance.restore.server_barrier.connection', connection), self.assertRaises(Busy):
            drain_go_transactions()
        connection.cursor.assert_not_called()

    def test_enclosing_transaction_refuses_to_extend_the_exclusive_lock(self):
        for nested, autocommit in [(True, True), (False, False)]:
            with self.subTest(nested=nested, autocommit=autocommit):
                connection = self.postgres()
                connection.in_atomic_block = nested
                connection.get_autocommit.return_value = autocommit
                with patch('instance.restore.server_barrier.connection', connection), self.assertRaises(Busy):
                    drain_go_transactions()
                connection.cursor.assert_not_called()

    def test_postgres_uses_short_transaction_timeout_and_shared_protocol_key(self):
        connection = self.postgres()
        with patch('instance.restore.server_barrier.connection', connection), patch('instance.restore.server_barrier.transaction.atomic') as atomic:
            drain_go_transactions()
        atomic.assert_called_once_with(durable=True)
        calls = connection.cursor.return_value.__enter__.return_value.execute.call_args_list
        self.assertEqual([(call.args[0], call.args[1]) for call in calls], [
            ("SELECT set_config('statement_timeout', %s, true)", ['5000ms']),
            ('SELECT pg_advisory_xact_lock(%s, %s)', (1095981639, 1381192786)),
        ])

    def test_database_and_commit_failure_are_sanitized_busy(self):
        for stage in ('connect', 'query', 'commit'):
            with self.subTest(stage=stage):
                connection = self.postgres()
                failure = DatabaseError('private-database-canary SELECT sensitive')
                with patch('instance.restore.server_barrier.connection', connection), patch('instance.restore.server_barrier.transaction.atomic') as atomic:
                    if stage == 'connect':
                        connection.get_autocommit.side_effect = failure
                    elif stage == 'query':
                        connection.cursor.return_value.__enter__.return_value.execute.side_effect = failure
                    else:
                        atomic.return_value.__exit__.side_effect = failure
                    with self.assertRaises(Busy) as caught:
                        drain_go_transactions()
                self.assertNotIn('private-database-canary', str(caught.exception))
                self.assertIs(caught.exception.__cause__, failure)

    def test_busy_precedes_operation_attempt_export_and_releases_file_lease(self):
        from instance.restore.execution import execute
        with tempfile.TemporaryDirectory() as directory, override_settings(DATA_RESTORE_LOCK_PATH=str(Path(directory) / 'lifecycle.lock')):
            with patch('instance.restore.execution.drain_go_transactions', side_effect=Busy('fixture busy')), \
                    patch('instance.restore.execution.DataRestore.objects.get') as read, \
                    patch('instance.restore.execution.save_operation') as save, \
                    patch('instance.restore.execution.RecoveryExport') as export, \
                    patch('instance.restore.execution.replace') as replace:
                with self.assertRaises(Busy):
                    execute('not-read')
            read.assert_not_called()
            save.assert_not_called()
            export.assert_not_called()
            replace.assert_not_called()
            with operation_lock(exclusive=True, required=True):
                pass

    def test_busy_uses_existing_maintenance_deferral(self):
        from instance.tasks import restore_instance_data
        with patch('instance.restore.execution.execute', side_effect=Busy('fixture busy')), \
                patch('instance.restore.task_base.defer_for_maintenance') as defer:
            restore_instance_data.run('not-read')
        self.assertEqual(defer.call_args.kwargs, {'countdown': 10})
        self.assertIsInstance(defer.call_args.args[1], Busy)
