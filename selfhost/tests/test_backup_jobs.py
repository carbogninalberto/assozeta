"""Owner backup actions and the automatic schedule, against disposable directories."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

UPDATER = Path(__file__).resolve().parents[1] / 'updater'
sys.path[:0] = [str(UPDATER), str(UPDATER.parents[1] / 'BE/instance')]
spec = importlib.util.spec_from_file_location('backup_jobs', UPDATER / 'backup_jobs.py')
jobs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jobs)
from journal import Journal  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reclaim_fixture import install_cli, runner_request, with_runner  # noqa: E402

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


class ReclaimSpaceTests(Base):
    def setUp(self):
        super().setUp()
        snapshots = tempfile.TemporaryDirectory()
        self.addCleanup(snapshots.cleanup)
        self.snapshot = Path(snapshots.name) / 'installation'
        self.cli = install_cli(self.root)
        self.events = self.root / 'commands'
        self.events.touch()
        self.env.write_text(self.env.read_text() + 'DBUSER=test\nDBNAME=test\nASSOZETA_BACKUP_SPACE_MARGIN_MB=1\n')
        self.enable()
        jobs.save_schedule(self.root, {**jobs.load_schedule(self.root), 'anchor': '2026-11-01T12:00:00+00:00'})
        self.next_backup = at('2026-11-02T12:00:00')
        env = patch.dict(os.environ, ASSOZETA_INSTALL_ROOT=str(self.root), ASSOZETA_ENV_FILE=str(self.env),
                         PATH=str(self.cli.parent) + os.pathsep + os.environ['PATH'], TEST_EVENTS=str(self.events),
                         TEST_FREE_KIB='5120', TEST_VACUUM_FAILURE='0', TEST_BACKUP_SNAPSHOT=str(self.snapshot))
        env.start()
        self.addCleanup(env.stop)

    def request(self, method, body=None):
        return runner_request(self.root, self.env, method, body=body)

    def state(self, root=None):
        status, inventory = runner_request(root or self.root, (root or self.root) / '.env', 'GET', '/backups')
        self.assertEqual(status, 200)
        self.assertIn('reclaim_space', inventory)
        return inventory['reclaim_space']

    def queue_reclaim(self):
        status, reply = self.request('POST', {'confirm': True})
        self.assertEqual(status, 202, reply)
        self.assertTrue(reply['reclaim_space']['pending'])
        self.assertEqual(self.events.read_text(), '')

    def run_backup(self, scheduled):
        if scheduled:
            operation = jobs.schedule_tick(self.root, self.env, self.journal, self.next_backup)
            self.next_backup += timedelta(days=1)
        else:
            operation = jobs.create_backup(self.root, self.env, self.journal,
                                           {'request_id': str(uuid4()), 'actor_id': str(uuid4())})
        jobs.run_backup(self.root, self.env, self.journal, operation)
        record = next(row for row in self.journal.records() if row['id'] == operation['id'])
        self.assertEqual(record['status'], 'succeeded', record.get('error'))

    def assert_maintenance_cycle(self, *, backup):
        trace = self.events.read_text().splitlines()
        vacuums = [i for i, line in enumerate(trace) if re.search(r'\bVACUUM\b', line, re.I)]
        self.assertEqual(len(vacuums), 2, trace)
        tables = set()
        for index in vacuums:
            sql = trace[index]
            match = re.search(r'\bVACUUM\s*\(\s*(?:FULL\s*,\s*ANALYZE|ANALYZE\s*,\s*FULL)\s*\)\s+'
                              r'(?:public\.)?(auditlog_logentry|application_auditlogindex)\b', sql, re.I)
            self.assertIsNotNone(match, sql)
            tables.add(match[1].lower())
        self.assertEqual(tables, {'auditlog_logentry', 'application_auditlogindex'})
        django_services = {'api', 'worker', 'beat', 'realtime-relay'}
        stopped = set()
        started = set()
        for index, line in enumerate(trace):
            words = line.split()
            for action in ('stop', 'start', 'restart', 'up'):
                if action not in words:
                    continue
                services = {word for word in words[words.index(action) + 1:] if not word.startswith('-')}
                self.assertNotIn('web', services, f'Caddy must keep serving maintenance: {line}')
                if action == 'stop':
                    stopped.update(services)
                else:
                    stopped.difference_update(services)
                    if index > vacuums[-1]:
                        started.update(services)
            if index in vacuums:
                self.assertEqual(stopped, django_services | {'api-go'}, trace)
        self.assertEqual(started, django_services, trace)
        if backup:
            self.assertEqual(trace[-1], 'backup --scheduled', trace)
        self.assertFalse((self.root / '.operations/maintenance.flag').exists())

    def result(self, ok):
        state = self.state()
        self.assertEqual(self.state(self.snapshot), state, 'reclaim result must be durable before backup')
        self.assertIs(state['pending'], False)
        run = state['last_run']
        self.assertIs(run['ok'], ok, run)
        self.assertLessEqual(at(run['started_at']), at(run['finished_at']))
        return run

    def test_backup_without_reclaim_only_runs_the_scheduled_backup(self):
        self.run_backup(scheduled=True)
        self.assertEqual(self.events.read_text().splitlines(), ['backup --scheduled'])

    def test_cli_reclaims_immediately(self):
        command = subprocess.run([self.cli, 'reclaim-space'], capture_output=True, text=True, timeout=20)
        self.assertEqual(command.returncode, 0, command.stderr)
        self.assert_maintenance_cycle(backup=False)
        self.assertFalse(any(line.startswith('backup') for line in self.events.read_text().splitlines()))

    def test_each_vacuum_has_60_second_lock_timeout(self):
        subprocess.run([self.cli, 'reclaim-space'], check=True, capture_output=True, timeout=20)
        calls = (self.root / 'vacuum-commands.jsonl').read_text().splitlines()
        self.assertEqual(len(calls), 2)
        minute = r"(?:60s|60000(?:ms)?|1min)"
        for line in calls:
            pgoptions, *args = json.loads(line)
            with self.subTest(pgoptions=pgoptions, command=args):
                sql = [args[i + 1] for i, arg in enumerate(args) if arg in ('-c', '--command')]
                vacuum, = [i for i, statement in enumerate(sql) if re.search(r'\bVACUUM\b', statement, re.I)]
                # A single -c containing SET; VACUUM would run inside a transaction.
                self.assertRegex(sql[vacuum], r'(?i)^\s*VACUUM\b[^;]*;?\s*$')
                via_env = re.search(r'(?:^|\s)-c\s*lock_timeout=' + minute + r'(?:$|\s)', pgoptions)
                via_sql = any(re.fullmatch(r"\s*SET\s+(?:SESSION\s+)?lock_timeout\s*(?:=|TO)\s*'?"
                                           + minute + r"'?\s*;?\s*", stmt, re.I) for stmt in sql[:vacuum])
                self.assertTrue(via_env or via_sql, 'missing 60-second lock_timeout for VACUUM')

    def test_cli_succeeds_when_only_the_after_measurement_fails(self):
        with patch.dict(os.environ, TEST_AFTER_SIZE_FAILURE='1', ASSOZETA_BACKUP_STAGES='1'):
            command = subprocess.run([self.cli, 'reclaim-space'], capture_output=True, text=True, timeout=20)
        self.assertTrue((self.root / 'vacuum-finished').exists())
        self.assertEqual(command.returncode, 0, command.stderr)
        self.assertIn('@@ASSOZETA_DATABASE_BYTES_BEFORE 7340032000', command.stdout)
        self.assertNotRegex(command.stdout, r'@@ASSOZETA_DATABASE_BYTES_AFTER [0-9]+')

    def test_scheduled_backup_records_success_with_unknown_after_size(self):
        self.queue_reclaim()
        with patch.dict(os.environ, TEST_AFTER_SIZE_FAILURE='1'):
            self.run_backup(scheduled=True)
        last = self.result(True)
        self.assertEqual(last['database_bytes_before'], 7340032000)
        self.assertIsNone(last['database_bytes_after'])
        self.assertFalse(last['error'])

    def test_updater_gives_reclaim_a_finite_maintenance_window(self):
        self.queue_reclaim()
        with patch.object(jobs.subprocess, 'run', wraps=subprocess.run) as reclaim:
            self.run_backup(scheduled=True)
        reclaim.assert_called_once()
        timeout = reclaim.call_args.kwargs.get('timeout')
        self.assertIsNotNone(timeout, 'the reclaim subprocess needs a deadline')
        self.assertTrue(math.isfinite(timeout))
        self.assertGreaterEqual(timeout, 3600)

    def test_reclaim_timeout_is_recorded_before_scheduled_backup(self):
        self.queue_reclaim()
        with patch.object(jobs.subprocess, 'run', side_effect=subprocess.TimeoutExpired('reclaim-space', 7200)):
            self.run_backup(scheduled=True)
        self.assertTrue(self.result(False)['error'])
        self.assertEqual(self.events.read_text().splitlines(), ['backup --scheduled'])

    def test_request_round_trip_and_cancel_survive_runner_restart(self):
        idle = {'pending': False, 'last_run': None}
        self.assertEqual(self.state(), idle)
        for body in ({}, {'confirm': False}):
            status, reply = self.request('POST', body)
            self.assertEqual(status, 400, reply)
            self.assertEqual(self.state(), idle)
        self.queue_reclaim()
        self.assertEqual(self.state(), {**idle, 'pending': True})
        self.assertEqual(self.request('DELETE', {}), (200, {'reclaim_space': idle}))
        self.run_backup(scheduled=True)
        self.assertEqual(self.events.read_text().splitlines(), ['backup --scheduled'])
        self.assertEqual(self.state(), idle)

    def test_next_scheduled_backup_consumes_reclaim_once(self):
        self.queue_reclaim()
        self.run_backup(scheduled=False)
        self.assertEqual(self.events.read_text().splitlines(), ['backup'])
        self.assertTrue(self.state()['pending'])
        self.events.write_text('')
        self.run_backup(scheduled=True)
        self.assert_maintenance_cycle(backup=True)
        last = self.result(True)
        self.assertEqual((last['database_bytes_before'], last['database_bytes_after']), (7340032000, 3145728000))
        self.assertFalse(last.get('error'))
        self.events.write_text('')
        self.run_backup(scheduled=True)
        self.assertEqual(self.events.read_text().splitlines(), ['backup --scheduled'])
        self.assertEqual(self.state()['last_run'], last)

    def test_cancel_during_vacuum_is_rejected(self):
        self.queue_reclaim()
        gate = self.root / 'vacuum'

        def cancel_while_running(request):
            with patch.dict(os.environ, TEST_VACUUM_GATE=str(gate)), ThreadPoolExecutor(max_workers=1) as pool:
                backup = pool.submit(self.run_backup, scheduled=True)
                try:
                    deadline = time.monotonic() + 10
                    while not gate.with_suffix('.started').exists():
                        if backup.done():
                            backup.result()
                            self.fail('backup finished before the first VACUUM')
                        self.assertLess(time.monotonic(), deadline, 'timed out waiting for VACUUM')
                        time.sleep(0.02)
                    status, reply = request('DELETE', '/backups/reclaim-space', {})
                    self.assertEqual(status, 409, reply)
                finally:
                    gate.with_suffix('.done').touch()
                backup.result(timeout=10)

        with_runner(self.root, self.env, cancel_while_running)
        self.assert_maintenance_cycle(backup=True)
        self.result(True)

    def test_vacuum_failure_restarts_services_and_backup_succeeds(self):
        self.queue_reclaim()
        with patch.dict(os.environ, TEST_VACUUM_FAILURE='1'):
            self.run_backup(scheduled=True)
        self.assert_maintenance_cycle(backup=True)
        self.assertTrue(self.result(False)['error'])

    def test_free_space_must_exceed_both_tables_plus_margin(self):
        self.queue_reclaim()
        # 2 MiB of log entries + 1 MiB of index + 1 MiB of margin. Equality is insufficient.
        with patch.dict(os.environ, TEST_FREE_KIB='4096'):
            self.run_backup(scheduled=True)
        trace = self.events.read_text()
        self.assertNotRegex(trace, r'(?i)\bvacuum\b')
        self.assertIn('backup --scheduled\n', trace)
        self.assertRegex(self.result(False)['error'], r'(?i)spazio|disco')


if __name__ == '__main__':
    unittest.main()
