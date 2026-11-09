"""Restore of a server backup and uploaded archives, against disposable directories."""
import importlib.util
import io
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

UPDATER = Path(__file__).resolve().parents[1] / 'updater'
sys.path[:0] = [str(UPDATER), str(UPDATER.parents[1] / 'BE/instance')]
spec = importlib.util.spec_from_file_location('restore_jobs', UPDATER / 'restore_jobs.py')
jobs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jobs)
from backup_jobs import BackupRejected  # noqa: E402
from journal import Journal  # noqa: E402


def make_archive(version='1.0.33', created='20261101T020000Z', manifest=True, dump=True, objects=True, extra=()):
    """A small gzip tar shaped like the CLI's backups."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        def add(name, data=b'', kind=tarfile.REGTYPE, link=''):
            info = tarfile.TarInfo(name)
            info.type, info.size, info.linkname = kind, len(data) if kind == tarfile.REGTYPE else 0, link
            archive.addfile(info, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
        add('.', kind=tarfile.DIRTYPE)
        if dump:
            add('./postgres.dump', b'dump' * 100)
        if objects:
            add('./objects', kind=tarfile.DIRTYPE)
            add('./objects/storage/a.pdf', os.urandom(4096))
        add('./config.env', b'SECRET=x\n')
        if manifest:
            add('./manifest.txt', f'created_at={created}\nversion={version}\ndatabase=d\n'.encode())
        for item in extra:
            add(*item)
    return buffer.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'backups').mkdir()
        (self.root / '.updater').mkdir()
        self.env = self.root / '.env'
        self.env.write_text('ASSOZETA_VERSION=1.0.33\nASSOZETA_BACKUP_SPACE_MARGIN_MB=1\n')
        self.journal = Journal(self.root / '.updater/operations.sqlite3')

    def archive(self, day=1, data=None, **meta):
        name = f'assozeta-202611{day:02d}T020000Z.tar.gz'
        (self.root / 'backups' / name).write_bytes(make_archive() if data is None else data)
        if meta:
            (self.root / 'backups' / (name + '.meta')).write_text(''.join(f'{k}={v}\n' for k, v in meta.items()))
        return name

    def body(self, **extra):
        return {'request_id': str(uuid4()), 'actor_id': str(uuid4()), 'confirm': 'RIPRISTINA', **extra}


class CheckArchiveTests(unittest.TestCase):
    def check(self, data):
        return jobs.check_archive(io.BytesIO(data))

    def test_a_cli_shaped_archive_returns_its_manifest(self):
        self.assertEqual(self.check(make_archive(version='1.0.32'))['version'], '1.0.32')

    def test_rejects_missing_parts_unsafe_paths_links_and_non_archives(self):
        for data in (make_archive(manifest=False), make_archive(dump=False), make_archive(objects=False),
                     make_archive(extra=[('../escape', b'x')]), make_archive(extra=[('/etc/x', b'x')]),
                     make_archive(extra=[('objects/../../x', b'x')]),
                     make_archive(extra=[('objects/link', b'', tarfile.SYMTYPE, '/etc/passwd')]),
                     make_archive(extra=[('objects/hard', b'', tarfile.LNKTYPE, 'config.env')]),
                     b'not a gzip file', make_archive()[:200]):
            with self.assertRaises(jobs.ArchiveInvalid):
                self.check(data)


class StartRestoreTests(Base):
    def test_queues_a_restore_of_a_standard_archive(self):
        name = self.archive(origin='manual', version='1.0.32')
        body = self.body()
        operation = jobs.start_restore(self.root, self.env, self.journal, name, body)
        self.assertEqual((operation['kind'], operation['archive'], operation['status']), ('restore', name, 'queued'))
        self.assertEqual(operation['target_version'], '1.0.33')
        self.assertNotIn('confirm', operation)

    def test_requires_the_typed_word_and_valid_fields(self):
        name = self.archive()
        with self.assertRaises(BackupRejected) as raised:
            jobs.start_restore(self.root, self.env, self.journal, name, self.body(confirm='ripristina'))
        self.assertEqual(raised.exception.status, 400)
        for bad in ({'request_id': 'x'}, {'extra': 1}):
            with self.assertRaises(ValueError):
                jobs.start_restore(self.root, self.env, self.journal, name, self.body(**bad))
        self.assertEqual(self.journal.records(), [])

    def test_refuses_links_unknown_names_and_newer_versions(self):
        outside = self.root / 'outside.tar.gz'
        outside.write_bytes(make_archive())
        os.symlink(outside, self.root / 'backups/assozeta-20261105T020000Z.tar.gz')
        for name in ('../outside.tar.gz', 'assozeta-20261105T020000Z.tar.gz', 'assozeta-20261106T020000Z.tar.gz'):
            with self.assertRaises(BackupRejected) as raised:
                jobs.start_restore(self.root, self.env, self.journal, name, self.body())
            self.assertEqual(raised.exception.status, 404)
        newer = self.archive(2, origin='manual', version='1.0.40')
        with self.assertRaises(BackupRejected) as raised:
            jobs.start_restore(self.root, self.env, self.journal, newer, self.body())
        self.assertIn('più recente', str(raised.exception))

    def test_refuses_while_busy_or_with_a_pending_upgrade(self):
        name = self.archive(origin='manual')
        busy = self.journal.create({'kind': 'backup', 'origin': 'manual', 'request_id': str(uuid4()), 'actor_id': str(uuid4()),
                                    'release_id': None, 'tag': '1.0.33', 'source_version': '1.0.33'})
        with self.assertRaises(BackupRejected):
            jobs.start_restore(self.root, self.env, self.journal, name, self.body())
        self.journal.update(busy['id'], status='succeeded', stage='completed')
        (self.root / '.updater/pending-distribution').mkdir()
        (self.root / '.updater/pending-distribution/transaction.json').write_text('{}')
        with self.assertRaises(BackupRejected) as raised:
            jobs.start_restore(self.root, self.env, self.journal, name, self.body())
        self.assertIn('in sospeso', str(raised.exception))


class RunRestoreTests(Base):
    def cli(self, script):
        (self.root / 'bin').mkdir(exist_ok=True)
        path = self.root / 'bin/assozeta'
        path.write_text('#!/bin/sh\n' + script)
        path.chmod(0o755)

    def queue(self, name):
        return jobs.start_restore(self.root, self.env, self.journal, name, self.body())

    def test_success_mirrors_stages_and_records_the_safety_backup(self):
        name = self.archive(origin='manual')
        self.cli('echo "args=$*"; for s in checking backup; do echo "@@ASSOZETA_STAGE $s"; done\n'
                 'echo "@@ASSOZETA_BACKUP assozeta-20261109T100000Z.tar.gz"; echo "@@ASSOZETA_STAGE backup"\n'
                 'for s in restoring restarting health_check; do echo "@@ASSOZETA_STAGE $s"; done\n')
        operation = self.queue(name)
        stages = []
        update = self.journal.update
        with patch.object(self.journal, 'update', side_effect=lambda id, **kw: (stages.append(kw.get('stage')), update(id, **kw))[1]):
            jobs.run_restore(self.root, self.env, self.journal, operation)
        record = self.journal.records()[0]
        self.assertEqual((record['status'], record['stage']), ('succeeded', 'completed'))
        self.assertEqual(record['safety_backup'], 'assozeta-20261109T100000Z.tar.gz')
        self.assertEqual([stage for stage in stages if stage], ['checking', 'backup', 'restoring', 'restarting', 'health_check', 'completed'])
        log = (self.root / '.updater/logs' / f'{operation["id"]}.log').read_text()
        self.assertIn(f'args=restore {(self.root / "backups" / name).resolve()} --yes', log)

    def test_stages_are_monotonic_through_the_whole_restore(self):
        name = self.archive(origin='manual')
        self.cli('for s in checking backup restoring restarting health_check; do echo "@@ASSOZETA_STAGE $s"; done\n'
                 'echo "@@ASSOZETA_BACKUP ../evil"; exit 1\n')
        jobs.run_restore(self.root, self.env, self.journal, self.queue(name))
        record = self.journal.records()[0]
        self.assertEqual((record['status'], record['failed_stage']), ('failed', 'health_check'))
        self.assertNotIn('safety_backup', record)
        self.assertIn('rimessi i dati precedenti', record['error'])

    def test_an_invalid_or_newer_archive_fails_before_the_cli_runs(self):
        marker = self.root / 'ran'
        self.cli(f'touch "{marker}"\n')
        for data in (b'broken', make_archive(version='1.0.40')):
            name = self.archive(data=data)
            jobs.run_restore(self.root, self.env, self.journal, self.queue(name))
            record = self.journal.records()[0]
            self.assertEqual(record['status'], 'failed')
            self.assertFalse(marker.exists())

    def test_failures_before_the_restore_leave_the_data_untouched(self):
        name = self.archive(origin='manual')
        self.cli('echo "@@ASSOZETA_STAGE backup"; echo "@@ASSOZETA_ERROR Spazio su disco insufficiente per il backup."; exit 1\n')
        jobs.run_restore(self.root, self.env, self.journal, self.queue(name))
        record = self.journal.records()[0]
        self.assertEqual((record['status'], record['error']), ('failed', 'Spazio su disco insufficiente per il backup.'))

    def test_a_failed_rollback_requires_recovery_from_the_safety_backup(self):
        name = self.archive(origin='manual')
        self.cli('echo "@@ASSOZETA_STAGE backup"; echo "@@ASSOZETA_BACKUP assozeta-20261109T100000Z.tar.gz"\n'
                 'echo "@@ASSOZETA_STAGE restoring"; echo "[assozeta] restore rollback failed; manual recovery may be required"; exit 1\n')
        jobs.run_restore(self.root, self.env, self.journal, self.queue(name))
        record = self.journal.records()[0]
        self.assertEqual(record['status'], 'recovery_required')
        self.assertIn('restore backups/assozeta-20261109T100000Z.tar.gz --yes --no-backup', record['recovery'])

    def test_interrupted_restores_require_recovery_only_after_the_data_change(self):
        early = self.queue(self.archive(1, origin='manual'))
        self.journal.update(early['id'], status='running', stage='backup')
        self.journal.interrupt_running()
        self.assertEqual(self.journal.records()[0]['status'], 'failed')
        late = self.queue(self.archive(2, origin='manual'))
        self.journal.update(late['id'], status='running', stage='restoring', safety_backup='assozeta-20261109T100000Z.tar.gz')
        self.journal.interrupt_running()
        record = self.journal.records()[0]
        self.assertEqual(record['status'], 'recovery_required')
        self.assertIn('backups/assozeta-20261109T100000Z.tar.gz', record['recovery'])


class UploadTests(Base):
    def upload(self, data, length=None):
        return jobs.receive_upload(self.root, self.env, self.journal, io.BytesIO(data), len(data) if length is None else length)

    def files(self):
        return sorted(path.name for path in (self.root / 'backups').iterdir())

    def test_a_valid_archive_gets_a_standard_name_and_metadata(self):
        data = make_archive(version='1.0.31', created='20260101T020000Z')
        entry = self.upload(data)
        self.assertRegex(entry['name'], r'^assozeta-\d{8}T\d{6}Z\.tar\.gz$')
        self.assertEqual((entry['origin'], entry['version'], entry['size']), ('uploaded', '1.0.31', len(data)))
        self.assertEqual(entry['created_at'], '2026-01-01T02:00:00+00:00')
        path = self.root / 'backups' / entry['name']
        self.assertEqual(path.read_bytes(), data)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(self.files(), [entry['name'], entry['name'] + '.meta'])
        # A second upload in the same second never replaces the first.
        second = self.upload(data)
        self.assertNotEqual(second['name'], entry['name'])

    def test_invalid_or_short_uploads_leave_nothing_behind(self):
        for data, length in ((b'not an archive' * 100, None), (make_archive(manifest=False), None),
                             (make_archive(extra=[('objects/link', b'', tarfile.SYMTYPE, '/etc/passwd')]), None),
                             (make_archive(), len(make_archive()) + 10)):
            with self.assertRaises((jobs.ArchiveInvalid, jobs.UploadIncomplete)):
                self.upload(data, length)
        self.assertEqual(self.files(), [])

    def test_refuses_without_space_or_while_another_operation_runs(self):
        data = make_archive()
        with patch.object(jobs.shutil, 'disk_usage', return_value=type('Usage', (), {'free': len(data)})()):
            with self.assertRaises(BackupRejected) as raised:
                self.upload(data)
        self.assertIn('Spazio su disco insufficiente', str(raised.exception))
        self.journal.create({'kind': 'backup', 'origin': 'manual', 'request_id': str(uuid4()), 'actor_id': str(uuid4()),
                             'release_id': None, 'tag': '1.0.33', 'source_version': '1.0.33'})
        with self.assertRaises(BackupRejected):
            self.upload(data)
        with self.assertRaises(ValueError):
            jobs.receive_upload(self.root, self.env, self.journal, io.BytesIO(b''), 0)
        self.assertEqual(self.files(), [])


if __name__ == '__main__':
    unittest.main()
