"""Owner backup actions and the automatic schedule, against disposable directories."""
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from uuid import uuid4

UPDATER = Path(__file__).resolve().parents[1] / 'updater'
sys.path[:0] = [str(UPDATER), str(UPDATER.parents[1] / 'BE/instance')]
spec = importlib.util.spec_from_file_location('backup_jobs', UPDATER / 'backup_jobs.py')
jobs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jobs)
from journal import Journal  # noqa: E402

UTC = timezone.utc


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=UTC) if 'T' in text and '+' not in text else datetime.fromisoformat(text)


class Base(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'backups').mkdir()
        (self.root / '.updater').mkdir()
        self.env = self.root / '.env'
        self.env.write_text('ASSOZETA_VERSION=1.0.33\nASSOZETA_TIMEZONE=Europe/Rome\n')
        self.journal = Journal(self.root / '.updater/operations.sqlite3')

    def archive(self, day, **meta):
        name = f'assozeta-202611{day:02d}T020000Z.tar.gz'
        (self.root / 'backups' / name).write_bytes(b'x')
        if meta:
            (self.root / 'backups' / (name + '.meta')).write_text(''.join(f'{k}={v}\n' for k, v in meta.items()))
        return name

    def enable(self, **values):
        return jobs.update_schedule(self.root, self.env, {'enabled': True, 'frequency': 'daily', 'time': '03:00', 'keep': 3, **values})


class ScheduleTests(Base):
    def test_disabled_by_default_with_the_installation_timezone(self):
        payload = jobs.schedule_payload(self.root, self.env)
        self.assertFalse(payload['enabled'])
        self.assertIsNone(payload['next_run'])
        self.assertEqual(payload['timezone'], 'Europe/Rome')

    def test_daily_and_weekly_times_follow_local_time_across_daylight_saving(self):
        rome = jobs.timezone_of(self.env)
        daily = {'enabled': True, 'frequency': 'daily', 'time': '03:00', 'weekday': 0}
        # 03:00 in Rome is 02:00 UTC in winter and 01:00 UTC in summer.
        self.assertEqual(jobs.next_run(daily, at('2026-11-09T12:00:00'), rome), at('2026-11-10T02:00:00'))
        self.assertEqual(jobs.next_run(daily, at('2026-03-28T12:00:00'), rome), at('2026-03-29T01:00:00'))
        weekly = {**daily, 'frequency': 'weekly', 'weekday': 6}
        # 9 Nov 2026 is a Monday; the next Sunday is the 15th.
        self.assertEqual(jobs.next_run(weekly, at('2026-11-09T12:00:00'), rome), at('2026-11-15T02:00:00'))
        self.assertEqual(jobs.next_run(weekly, at('2026-11-15T02:00:00'), rome), at('2026-11-22T02:00:00'))
        self.assertIsNone(jobs.next_run({**daily, 'enabled': False}, at('2026-11-09T12:00:00'), rome))

    def test_saving_validates_and_writes_retention_to_the_environment(self):
        payload = self.enable(frequency='weekly', weekday=2, time='22:30', keep=5)
        self.assertEqual((payload['frequency'], payload['weekday'], payload['time']), ('weekly', 2, '22:30'))
        self.assertIn('ASSOZETA_BACKUP_KEEP=5\n', self.env.read_text())
        for bad in ({'time': '24:00'}, {'frequency': 'hourly'}, {'keep': 0}, {'keep': 31}, {'weekday': 7}, {'enabled': 'yes'}, {'extra': 1}):
            with self.assertRaises(ValueError, msg=bad):
                self.enable(**bad)

    def test_a_missed_run_happens_once_after_downtime_never_a_burst(self):
        self.enable()
        schedule = jobs.load_schedule(self.root)
        schedule['anchor'] = '2026-11-01T12:00:00+00:00'
        jobs.save_schedule(self.root, schedule)
        first = jobs.schedule_tick(self.root, self.env, self.journal, at('2026-11-09T12:00:00'))
        self.assertEqual((first['kind'], first['origin'], first['status']), ('backup', 'scheduled', 'queued'))
        self.journal.update(first['id'], status='succeeded', stage='completed')
        self.assertIsNone(jobs.schedule_tick(self.root, self.env, self.journal, at('2026-11-09T12:05:00')))
        self.assertIsNotNone(jobs.schedule_tick(self.root, self.env, self.journal, at('2026-11-10T02:00:01')))

    def test_a_due_run_waits_for_another_operation(self):
        self.enable()
        schedule = jobs.load_schedule(self.root)
        schedule['anchor'] = '2026-11-08T12:00:00+00:00'
        jobs.save_schedule(self.root, schedule)
        busy = self.journal.create({'request_id': str(uuid4()), 'actor_id': str(uuid4()), 'release_id': 1, 'tag': '1.0.34', 'source_version': '1.0.33'})
        self.assertIsNone(jobs.schedule_tick(self.root, self.env, self.journal, at('2026-11-09T12:00:00')))
        self.journal.update(busy['id'], status='succeeded', stage='completed')
        self.assertIsNotNone(jobs.schedule_tick(self.root, self.env, self.journal, at('2026-11-09T12:01:00')))

    def test_a_blocked_run_is_recorded_and_not_retried_until_the_next_slot(self):
        self.enable()
        schedule = jobs.load_schedule(self.root)
        schedule['anchor'] = '2026-11-08T12:00:00+00:00'
        jobs.save_schedule(self.root, schedule)
        failed = self.journal.create({'request_id': str(uuid4()), 'actor_id': str(uuid4()), 'release_id': 1, 'tag': '1.0.34', 'source_version': '1.0.33'})
        self.journal.update(failed['id'], status='recovery_required', stage='recovery_required')
        self.assertIsNone(jobs.schedule_tick(self.root, self.env, self.journal, at('2026-11-09T12:00:00')))
        last = jobs.load_schedule(self.root)['last_run']
        self.assertEqual(last['status'], 'failed')
        self.assertIn('Backup automatico non eseguito', last['error'])
        self.assertIsNone(jobs.due_backup(self.root, self.env, at('2026-11-09T12:00:30')))

    def test_enabling_never_triggers_a_backlog(self):
        self.enable()
        self.assertIsNone(jobs.schedule_tick(self.root, self.env, self.journal))


class ArchiveActionTests(Base):
    def test_keep_toggles_without_losing_other_metadata(self):
        name = self.archive(1, origin='scheduled', version='1.0.32')
        entry = jobs.set_keep(self.root, self.env, self.journal, name, {'keep': True})
        self.assertTrue(entry['keep'])
        self.assertEqual(entry['origin'], 'scheduled')
        entry = jobs.set_keep(self.root, self.env, self.journal, name, {'keep': False})
        self.assertFalse(entry['keep'])
        meta = self.root / 'backups' / (name + '.meta')
        self.assertEqual(meta.read_text(), 'origin=scheduled\nversion=1.0.32\n')
        self.assertEqual(stat.S_IMODE(meta.stat().st_mode), 0o600)
        legacy = self.archive(2)
        self.assertTrue(jobs.set_keep(self.root, self.env, self.journal, legacy, {'keep': True})['keep'])

    def test_delete_removes_the_archive_and_sidecar_with_protections(self):
        plain = self.archive(1, origin='manual')
        kept = self.archive(2, origin='scheduled', keep='true')
        upgrade = self.archive(3, origin='pre-upgrade', upgrade='completed')
        pending = self.archive(4, origin='pre-upgrade')
        (self.root / '.updater/pending-distribution').mkdir()
        (self.root / '.updater/pending-distribution/transaction.json').write_text(json.dumps({'backup': {'path': pending}}))
        self.assertEqual(jobs.delete_archive(self.root, self.env, self.journal, plain, {}), {'deleted': plain})
        self.assertFalse((self.root / 'backups' / plain).exists())
        self.assertFalse((self.root / 'backups' / (plain + '.meta')).exists())
        for protected in (kept, upgrade):
            with self.assertRaises(jobs.BackupRejected):
                jobs.delete_archive(self.root, self.env, self.journal, protected, {'confirm': False})
            jobs.delete_archive(self.root, self.env, self.journal, protected, {'confirm': True})
        with self.assertRaises(jobs.BackupRejected):
            jobs.delete_archive(self.root, self.env, self.journal, pending, {'confirm': True})

    def test_actions_refuse_unknown_names_links_and_running_operations(self):
        target = self.root / 'outside.tar.gz'
        target.write_bytes(b'x')
        os.symlink(target, self.root / 'backups' / 'assozeta-20261105T020000Z.tar.gz')
        for name in ('../outside.tar.gz', 'assozeta-20261105T020000Z.tar.gz', 'assozeta-20261106T020000Z.tar.gz'):
            with self.assertRaises(jobs.BackupRejected) as raised:
                jobs.delete_archive(self.root, self.env, self.journal, name, {})
            self.assertEqual(raised.exception.status, 404)
        self.assertTrue(target.exists())
        name = self.archive(1, origin='manual')
        self.journal.create({'kind': 'backup', 'origin': 'manual', 'request_id': str(uuid4()), 'actor_id': str(uuid4()), 'release_id': None, 'tag': '1.0.33', 'source_version': '1.0.33'})
        with self.assertRaises(jobs.BackupRejected):
            jobs.delete_archive(self.root, self.env, self.journal, name, {})
        with self.assertRaises(jobs.BackupRejected):
            jobs.create_backup(self.root, self.env, self.journal, {'request_id': str(uuid4()), 'actor_id': str(uuid4())})

    def test_create_validates_and_queues_a_manual_backup(self):
        with self.assertRaises(ValueError):
            jobs.create_backup(self.root, self.env, self.journal, {'request_id': 'x', 'actor_id': str(uuid4())})
        operation = jobs.create_backup(self.root, self.env, self.journal, {'request_id': str(uuid4()), 'actor_id': str(uuid4())})
        self.assertEqual((operation['kind'], operation['origin'], operation['target_version']), ('backup', 'manual', '1.0.33'))


class RunBackupTests(Base):
    def cli(self, script):
        (self.root / 'bin').mkdir(exist_ok=True)
        path = self.root / 'bin/assozeta'
        path.write_text('#!/bin/sh\n' + script)
        path.chmod(0o755)

    def queue(self, origin):
        return self.journal.create({'kind': 'backup', 'origin': origin, 'request_id': str(uuid4()), 'actor_id': str(uuid4()),
                                    'release_id': None, 'tag': '1.0.33', 'source_version': '1.0.33'})

    def test_success_mirrors_stages_and_records_the_scheduled_run(self):
        self.cli('echo "args=$*"; echo "@@ASSOZETA_STAGE backup"; echo "@@ASSOZETA_STAGE restarting"; [ "$ASSOZETA_BACKUP_STAGES" = 1 ]')
        operation = self.queue('scheduled')
        jobs.run_backup(self.root, self.env, self.journal, operation)
        record = self.journal.records()[0]
        self.assertEqual((record['status'], record['stage']), ('succeeded', 'completed'))
        log = (self.root / '.updater/logs' / f'{operation["id"]}.log').read_text()
        self.assertIn('args=backup --scheduled', log)
        self.assertEqual(jobs.load_schedule(self.root)['last_run']['status'], 'succeeded')

    def test_failure_reports_the_cli_reason(self):
        self.cli('echo "@@ASSOZETA_ERROR Spazio su disco insufficiente per il backup."; exit 1')
        operation = self.queue('manual')
        jobs.run_backup(self.root, self.env, self.journal, operation)
        record = self.journal.records()[0]
        self.assertEqual((record['status'], record['error']), ('failed', 'Spazio su disco insufficiente per il backup.'))
        # A manual backup does not change the schedule's last run.
        self.assertNotIn('last_run', jobs.load_schedule(self.root))

    def test_interrupted_backups_fail_instead_of_requiring_recovery(self):
        operation = self.queue('manual')
        self.journal.update(operation['id'], status='running', stage='backup')
        self.journal.interrupt_running()
        record = self.journal.records()[0]
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(self.journal.requiring_recovery(), [])


if __name__ == '__main__':
    unittest.main()
