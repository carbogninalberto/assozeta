"""Isolated updater contracts; no live installation or network is used."""
import base64
import io
import json
from pathlib import Path
import sys
import subprocess
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'selfhost/updater'), str(ROOT / 'BE/instance')]

from common import atomic_write, read_env, update_env, update_eligibility_reason
from distribution import digest, migration_started, prepare, rollback, unpack, validate_manifest
from recovery import validate as validate_recovery, restore_files, finish as finish_recovery, reconcile
from engine import Engine, VerificationError
from journal import Journal, Conflict
from release_catalog import ReleaseError
from server import validate_request, update_blocked_reason
from status_access import AccessDenied, OwnershipUnavailable, authenticate_access_token, require_current_owner


def request():
    return {'release_id': 2, 'tag': 'v1.0.2', 'source_version': 'v1.0.1',
            'request_id': str(uuid4()), 'actor_id': str(uuid4())}


def official_environment(version='1.0.1'):
    return f'ASSOZETA_VERSION={version}\n' + ''.join(
        f'ASSOZETA_{service.upper()}_IMAGE=ghcr.io/carbogninalberto/assozeta-{service}\n'
        for service in ('backend', 'web', 'renderer'))


def archive(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(f'selfhost/{name}')
            info.size = len(content)
            info.mode = 0o755 if name.startswith('bin/') else 0o644
            tar.addfile(info, io.BytesIO(content))
    return output.getvalue()


def manifest(content, files):
    return {'schema_version': 1, 'version': '1.0.2', 'bundle_sha256': digest(content),
            'files': {name: digest(value) for name, value in files.items()},
            'images': {name: f'ghcr.io/carbogninalberto/assozeta-{name}@sha256:{"a" * 64}'
                       for name in ('backend', 'web', 'renderer', 'updater')}}


class StatusAccessTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.key = self.root / 'private.pem'
        subprocess.run(['openssl', 'genpkey', '-algorithm', 'ED25519', '-out', str(self.key)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        public = subprocess.check_output(['openssl', 'pkey', '-in', str(self.key), '-pubout'], stderr=subprocess.DEVNULL)
        self.values = {'JWT_PUBLIC_KEY_B64': base64.b64encode(public).decode()}
        self.actor = str(uuid4())
        self.claims = {'user_id': self.actor, 'token_type': 'access', 'exp': 2000}

    def token(self, claims=None, header=None):
        def encoded(value):
            return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
        message = encoded(header or {'alg': 'EdDSA'}) + '.' + encoded(claims or self.claims)
        path = self.root / 'message'
        path.write_text(message)
        signature = subprocess.check_output(['openssl', 'pkeyutl', '-sign', '-inkey', str(self.key),
                                             '-rawin', '-in', str(path)], stderr=subprocess.DEVNULL)
        return message + '.' + base64.urlsafe_b64encode(signature).decode().rstrip('=')

    def test_real_signed_access_token_and_forged_signature(self):
        token = self.token()
        self.assertEqual(authenticate_access_token(token, self.values, clock=lambda: 1000), self.actor)
        header, payload, signature = token.split('.')
        forged = header + '.' + payload + '.' + ('A' if signature[0] != 'A' else 'B') + signature[1:]
        for invalid in (forged, 'not-a-token', self.token(header={'alg': 'none'})):
            with self.subTest(token=invalid[:10]), self.assertRaises(AccessDenied):
                authenticate_access_token(invalid, self.values, clock=lambda: 1000)

    def test_expired_refresh_invalid_identity_and_malformed_time_claims_are_rejected(self):
        for change in ({'exp': 999}, {'token_type': 'refresh'}, {'user_id': 'owner'},
                       {'exp': True}, {'exp': None}, {'nbf': 1001}, {'nbf': 'later'},
                       {'nbf': float('nan')}, {'exp': float('inf')}):
            with self.subTest(change=change), self.assertRaises(AccessDenied):
                authenticate_access_token(self.token({**self.claims, **change}), self.values, clock=lambda: 1000)

    def test_owner_query_reads_current_database_and_fails_closed(self):
        env = self.root / '.env'
        env.write_text('DBUSER=assozeta\nDBNAME=assozeta\n')
        for value, error in (('t', None), ('f', AccessDenied), ('', OwnershipUnavailable)):
            with patch('status_access.subprocess.run', return_value=subprocess.CompletedProcess([], 0, stdout=value)) as run:
                if error:
                    with self.assertRaises(error):
                        require_current_owner(self.root, env, self.actor)
                else:
                    require_current_owner(self.root, env, self.actor)
                args, kwargs = run.call_args
                self.assertIn(f'actor={self.actor}', args[0])
                self.assertNotIn(self.actor, kwargs['input'])
                self.assertIn('u.is_active IS TRUE', kwargs['input'])
                self.assertIn('cfg.self_hosted IS TRUE', kwargs['input'])
                self.assertIn('ORDER BY id LIMIT 1', kwargs['input'])
                self.assertIn("u.user_id = :'actor'", kwargs['input'])
        with patch('status_access.subprocess.run', side_effect=subprocess.TimeoutExpired('psql', 10)):
            with self.assertRaises(OwnershipUnavailable):
                require_current_owner(self.root, env, self.actor)


class JournalTests(unittest.TestCase):
    def test_idempotency_concurrency_durability_and_interrupted_recovery(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'operations.sqlite'
            journal = Journal(path)
            data = request()
            first = journal.create(data)
            self.assertEqual(journal.create(data)['id'], first['id'])
            with self.assertRaises(Conflict):
                journal.create(request())
            with self.assertRaises(Conflict):
                journal.create({**data, 'actor_id': str(uuid4())})
            journal.update(first['id'], status='running', stage='migrating')
            restarted = Journal(path)
            restarted.interrupt_running()
            self.assertEqual(restarted.records()[0]['status'], 'recovery_required')
            self.assertEqual(restarted.interrupted()[0]['failed_stage'], 'migrating')
            self.assertEqual(restarted.create(data)['id'], first['id'])

    def test_recovery_blocks_new_requests_but_preserves_idempotent_retries(self):
        with TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / 'operations.sqlite')
            data = request()
            operation = journal.create(data)
            journal.update(operation['id'], status='recovery_required', stage='recovery_required')
            self.assertEqual(journal.create(data, blocked_reason='Recovery required')['id'], operation['id'])
            with self.assertRaisesRegex(Conflict, 'Recovery required'):
                journal.create(request(), blocked_reason='Recovery required')

    def test_runner_accepts_only_typed_release_requests(self):
        self.assertEqual(validate_request(request())['tag'], 'v1.0.2')
        for changes in ({'tag': '1.0.2; touch /tmp/pwn'}, {'command': 'id'}, {'release_id': True}, {'actor_id': 'owner'}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, TypeError)):
                validate_request({**request(), **changes})


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.env = self.root / '.env'
        self.env.write_text('ASSOZETA_VERSION=1.0.1\nDBPASSWORD=keep-private\nCUSTOM=value\n')
        (self.root / '.updater').mkdir()
        (self.root / 'compose.yml').write_bytes(b'old-compose')
        (self.root / '.updater/managed.json').write_text(json.dumps({'compose.yml': digest(b'old-compose')}))
        self.files = {'compose.yml': b'new-compose', 'bin/assozeta': b'#!/bin/sh\nexit 0\n'}
        self.content = archive(self.files)
        self.metadata = manifest(self.content, self.files)
        self.backup = self.root / 'backup.tar.gz'
        with tarfile.open(self.backup, 'w:gz') as data:
            content = b'version=1.0.1\n'
            member = tarfile.TarInfo('./manifest.txt')
            member.size = len(content)
            data.addfile(member, io.BytesIO(content))

    def prepare(self, operation_id=None):
        with patch('distribution.resolve_tag', return_value='v1.0.2'), patch('distribution.read_json', return_value=self.metadata), \
             patch('distribution.download', return_value=self.content), patch('distribution.subprocess.run') as run:
            prepare(self.root, self.env, '1.0.2', self.backup, operation_id)
            return run

    def test_recovery_requires_matching_backup_and_keeps_gate_until_version_verified(self):
        operation_data = request()
        journal = Journal(self.root / '.updater/operations.sqlite3')
        earlier = journal.create(request())
        journal.update(earlier['id'], status='failed', stage='failed', failed_stage='backup')
        operation = journal.create(operation_data)
        journal.update(operation['id'], status='recovery_required', stage='recovery_required')
        self.prepare(operation['id'])
        migration_started(self.root)
        with self.assertRaisesRegex(ReleaseError, 'Migrations may have changed data'):
            rollback(self.root)
        unrelated = self.root / 'unrelated.tar.gz'
        unrelated.write_bytes(b'not the pre-update backup')
        with self.assertRaisesRegex(ReleaseError, 'unchanged backup'):
            restore_files(self.root, unrelated)
        self.assertEqual(read_env(self.env)['ASSOZETA_VERSION'], '1.0.2')
        restore_files(self.root, self.backup)
        self.assertEqual(read_env(self.env)['ASSOZETA_VERSION'], '1.0.1')
        self.assertTrue((self.root / '.updater/pending-distribution').exists())
        self.assertFalse((self.root / 'bin/assozeta').exists())
        cached = list((self.root / '.updater/recovery-cli').glob('*/assozeta'))
        self.assertEqual(cached[0].read_bytes(), self.files['bin/assozeta'])
        self.assertEqual((self.root / '.updater/recover-upgrade').stat().st_mode & 0o777, 0o700)
        with patch('recovery.Engine.verify_running', side_effect=RuntimeError('Wrong running version')):
            with self.assertRaises(RuntimeError):
                finish_recovery(self.root, self.env)
        self.assertEqual(journal.records()[0]['status'], 'recovery_required')
        with patch('recovery.Engine.verify_running') as verify:
            finish_recovery(self.root, self.env)
            verify.assert_called_once_with('1.0.1', allow_configured_images=True)
        self.assertFalse((self.root / '.updater/pending-distribution').exists())
        self.assertEqual(journal.records()[0]['status'], 'recovered')
        self.assertEqual(journal.records()[1]['id'], earlier['id'])
        self.assertEqual(journal.records()[1]['status'], 'failed')
        self.assertEqual(journal.records()[1]['failed_stage'], 'backup')
        self.assertEqual(len(list((self.root / '.updater/distribution-history').iterdir())), 1)

    def test_cli_recovery_without_operation_id_does_not_rewrite_an_old_attempt(self):
        journal = Journal(self.root / '.updater/operations.sqlite3')
        earlier = journal.create(request())
        journal.update(earlier['id'], status='failed', stage='failed', failed_stage='backup')
        self.prepare()
        migration_started(self.root)
        restore_files(self.root, self.backup)
        with patch('recovery.Engine.verify_running'):
            finish_recovery(self.root, self.env)
        self.assertEqual(journal.records()[0]['status'], 'failed')
        self.assertFalse((self.root / '.updater/pending-distribution').exists())

    def test_recovery_checks_backup_version_even_when_digest_matches(self):
        self.prepare()
        pending = self.root / '.updater/pending-distribution/transaction.json'
        record = json.loads(pending.read_text())
        record['source_version'] = '1.0.0'
        pending.write_text(json.dumps(record))
        with self.assertRaisesRegex(ReleaseError, 'different source versions'):
            validate_recovery(self.root, self.backup)

    def test_unknown_transaction_phase_cannot_silently_roll_back_images(self):
        self.prepare()
        pending = self.root / '.updater/pending-distribution/transaction.json'
        record = json.loads(pending.read_text())
        record.pop('phase')
        pending.write_text(json.dumps(record))
        with self.assertRaisesRegex(ReleaseError, 'Migrations may have changed data'):
            rollback(self.root)
        self.assertEqual(read_env(self.env)['ASSOZETA_VERSION'], '1.0.2')

    def test_verified_distribution_preserves_env_and_can_restore_before_migrations(self):
        run = self.prepare()
        self.assertEqual(run.call_count, 4)
        self.assertEqual((self.root / 'compose.yml').read_bytes(), b'new-compose')
        self.assertEqual(read_env(self.env)['CUSTOM'], 'value')
        self.assertEqual(read_env(self.env)['DBPASSWORD'], 'keep-private')
        self.assertEqual(read_env(self.env)['ASSOZETA_VERSION'], '1.0.2')
        rollback(self.root)
        self.assertEqual((self.root / 'compose.yml').read_bytes(), b'old-compose')
        self.assertEqual(read_env(self.env)['ASSOZETA_VERSION'], '1.0.1')
        self.assertFalse((self.root / 'bin/assozeta').exists())

    def test_customized_files_abort_without_overwriting_operator_changes(self):
        (self.root / 'compose.yml').write_bytes(b'operator-customization')
        with self.assertRaisesRegex(ReleaseError, 'Local changes'):
            self.prepare()
        self.assertEqual((self.root / 'compose.yml').read_bytes(), b'operator-customization')
        self.assertEqual(read_env(self.env)['ASSOZETA_VERSION'], '1.0.1')

    def test_bad_digest_or_untrusted_image_aborts(self):
        self.metadata['bundle_sha256'] = '0' * 64
        with self.assertRaisesRegex(ReleaseError, 'checksum'):
            self.prepare()
        self.metadata['images']['backend'] = 'evil.example/image:latest'
        with self.assertRaisesRegex(ReleaseError, 'image reference'):
            validate_manifest(self.metadata, 'v1.0.2')

    def test_manifest_rejects_extra_images_and_malformed_fields(self):
        for changes in (
            {'images': {**self.metadata['images'], 'extra': 'evil.example/command:latest'}},
            {'images': None}, {'bundle_sha256': None}, {'files': {'bin/../escape': 'a' * 64}},
            {'files': {'bin//assozeta': 'a' * 64}}, {'files': {'compose.yml': None}},
        ):
            with self.subTest(changes=changes), self.assertRaises(ReleaseError):
                validate_manifest({**self.metadata, **changes}, 'v1.0.2')

    def test_snapshot_failure_leaves_installation_unchanged_and_retryable(self):
        def fail_environment_snapshot(path, data, *args):
            if Path(path).name == 'environment':
                raise OSError('Simulated full backup filesystem')
            return atomic_write(path, data, *args)

        original_env = self.env.read_bytes()
        with patch('distribution.atomic_write', side_effect=fail_environment_snapshot), self.assertRaises(OSError):
            self.prepare()
        self.assertEqual(self.env.read_bytes(), original_env)
        self.assertEqual((self.root / 'compose.yml').read_bytes(), b'old-compose')
        self.assertFalse((self.root / '.updater/pending-distribution').exists())
        self.assertEqual(list((self.root / '.updater').glob('snapshot-*')), [])
        self.prepare()

    def test_rollback_restores_previous_metadata_and_environment_permissions(self):
        target = self.root / '.updater/target-manifest.json'
        target.write_bytes(b'{"previous": true}\n')
        self.env.chmod(0o640)
        self.prepare()
        rollback(self.root)
        self.assertEqual(target.read_bytes(), b'{"previous": true}\n')
        self.assertEqual(self.env.stat().st_mode & 0o777, 0o640)

    def test_directory_conflict_aborts_before_downloading_images(self):
        (self.root / 'bin/assozeta').mkdir(parents=True)
        with self.assertRaisesRegex(ReleaseError, 'existing directory'):
            self.prepare()
        self.assertEqual(read_env(self.env)['ASSOZETA_VERSION'], '1.0.1')

    def test_archive_paths_cannot_escape_distribution(self):
        with self.assertRaises(ReleaseError):
            unpack(archive({'../../escape': b'unsafe'}))

    def test_environment_updates_preserve_unrelated_values_and_do_not_duplicate(self):
        update_env(self.env, {'NEW_VALUE': 'generated'})
        update_env(self.env, {'NEW_VALUE': 'generated'})
        self.assertEqual(self.env.read_text().count('NEW_VALUE='), 1)
        self.assertEqual(read_env(self.env)['DBPASSWORD'], 'keep-private')


class EngineTests(unittest.TestCase):
    def test_private_log_failure_still_persists_failed_operation(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            env = root / '.env'
            env.write_text(official_environment('1.0.6'))
            journal = Journal(root / '.updater/operations.sqlite3')
            operation = journal.create(request())
            engine = Engine(root, env, journal, resolver=lambda _: {'tag': 'v1.0.2', 'artifacts_ready': True})
            with patch('engine.os.open', side_effect=PermissionError('private path')):
                engine.execute(operation)
            self.assertEqual(journal.records()[0]['status'], 'failed')
            self.assertNotIn('private path', json.dumps(journal.records()))

    def test_live_verification_rejects_unhealthy_wrong_image_and_wrong_backend_version(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            env = root / '.env'
            refs = {f'ASSOZETA_{name}_REF': f'registry/{name.lower()}@sha256:' + 'a' * 64
                    for name in ('BACKEND', 'WEB', 'RENDERER')}
            env.write_text(official_environment('1.0.5'))
            update_env(env, refs)
            engine = Engine(root, env, None)
            services = {service: refs[f'ASSOZETA_{name}_REF'] for service, name in
                        [('api', 'BACKEND'), ('worker', 'BACKEND'), ('beat', 'BACKEND'), ('web', 'WEB'), ('renderer', 'RENDERER')]}
            for fault in ('health', 'image', 'version', None):
                def docker(command, **kwargs):
                    if command[-3:] == ['config', '--format', 'json']:
                        return json.dumps({'services': {key: {'image': value} for key, value in services.items()}})
                    if command[0:2] == ['docker', 'inspect']:
                        service = command[-1]
                        return json.dumps([{'State': {'Running': True, 'Health': {'Status': 'unhealthy' if fault == 'health' else 'healthy'}},
                                            'Config': {'Image': 'wrong' if fault == 'image' else services[service]}}])
                    if command[-2:] == ['cat', '/app/VERSION']:
                        return 'v1.0.6' if fault == 'version' else 'v1.0.5'
                    return command[-1]
                with self.subTest(fault=fault), patch('engine.subprocess.check_output', side_effect=docker):
                    if fault:
                        with self.assertRaises(RuntimeError):
                            engine.verify_running('1.0.5', allow_configured_images=True)
                    else:
                        engine.verify_running('1.0.5', allow_configured_images=True)
                        engine.verify_running('1.0.5')

    def test_incident_retry_reports_version_mismatch_before_launch(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            env = root / '.env'
            env.write_text(official_environment('1.0.6'))
            journal = Journal(root / '.updater/operations.sqlite3')
            operation = journal.create({**request(), 'tag': 'v1.0.6', 'source_version': 'v1.0.5'})
            engine = Engine(root, env, journal, resolver=lambda _: {'tag': 'v1.0.6', 'artifacts_ready': True})
            with patch.object(engine, 'launch_command') as launch:
                engine.execute(operation)
                launch.assert_not_called()
            record = journal.records()[0]
            self.assertEqual(record['failed_stage'], 'checking')
            self.assertIn('richiesta 1.0.5, configurata 1.0.6', record['error'])
            self.assertEqual(read_env(env)['ASSOZETA_VERSION'], '1.0.6')

    def test_eligibility_rechecked_after_queue_and_release_errors_are_distinct(self):
        for scenario, expected in [('custom', 'ASSOZETA_WEB_IMAGE'), ('release', 'distribuzione completa'),
                                   ('older', 'successiva'), ('invalid', 'stabile valida'), ('recovery', 'ripristino')]:
            with self.subTest(scenario=scenario), TemporaryDirectory() as directory:
                root = Path(directory)
                env = root / '.env'
                env.write_text(official_environment())
                journal = Journal(root / '.updater/operations.sqlite3')
                if scenario == 'recovery':
                    old = journal.create(request())
                    journal.update(old['id'], status='failed')
                operation = journal.create(request())
                release = {'tag': 'v1.0.2', 'artifacts_ready': scenario != 'release'}
                if scenario == 'custom':
                    update_env(env, {'ASSOZETA_WEB_IMAGE': 'private-secret-image-name'})
                if scenario == 'older':
                    operation['tag'] = release['tag'] = 'v1.0.1'
                if scenario == 'invalid':
                    update_env(env, {'ASSOZETA_VERSION': 'private-secret-version'})
                if scenario == 'recovery':
                    journal.update(old['id'], status='recovery_required')
                engine = Engine(root, env, journal, resolver=lambda _: release)
                with patch.object(engine, 'launch_command') as launch:
                    engine.execute(operation)
                    launch.assert_not_called()
                record = journal.records()[0]
                self.assertIn(expected, record['error'])
                self.assertNotIn('private-secret', json.dumps(record))

    def test_missing_receipt_is_private_logged_failure_and_blocks_retry(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            env = root / '.env'
            env.write_text(official_environment())
            journal = Journal(root / '.updater/operations.sqlite3')
            operation = journal.create(request())
            engine = Engine(root, env, journal, resolver=lambda _: {'tag': 'v1.0.2', 'artifacts_ready': True})
            with patch.object(engine, 'launch_command', return_value=[sys.executable, '-c', 'print("@@ASSOZETA_STAGE health_check")']), patch.object(engine, 'refresh_runner') as refresh:
                engine.execute(operation)
                refresh.assert_not_called()
            record = journal.records()[0]
            self.assertEqual(record['status'], 'recovery_required')
            self.assertNotIn('interrupted_at', record)
            self.assertIn('manca la ricevuta', record['error'])
            log = root / '.updater/logs' / f'{operation["id"]}.log'
            self.assertEqual(log.stat().st_mode & 0o777, 0o600)
            self.assertIn('FileNotFoundError', log.read_text())
            self.assertNotIn(str(root), json.dumps(record))
            self.assertIn('ripristino', update_blocked_reason(root, env, journal))
            with self.assertRaises(Conflict):
                journal.create(request())
            self.assertEqual(journal.create({k: operation[k] for k in request()})['id'], operation['id'])

    def test_non_interrupted_recovery_requires_verified_state_and_no_silent_version_fix(self):
        for configured in ('1.0.5', '1.0.6', '1.0.9'):
            with self.subTest(configured=configured), TemporaryDirectory() as directory:
                root = Path(directory)
                env = root / '.env'
                env.write_text(official_environment(configured))
                original = env.read_bytes()
                journal = Journal(root / '.updater/operations.sqlite3')
                operation = journal.create({**request(), 'tag': 'v1.0.6', 'source_version': 'v1.0.5'})
                journal.update(operation['id'], status='recovery_required', stage='recovery_required')
                self.assertEqual(journal.interrupted(), [])
                with patch.object(Engine, 'verify_running', side_effect=RuntimeError('mismatch')) as verify:
                    with self.assertRaises((ReleaseError, RuntimeError)):
                        reconcile(root, env)
                    self.assertEqual(env.read_bytes(), original)
                    self.assertEqual(len(journal.requiring_recovery()), 1)
                    if configured == '1.0.9':
                        verify.assert_not_called()
                        continue
                    if configured == '1.0.6':
                        verify.assert_not_called()
                        Engine(root, env, None).record_verification(operation['id'], configured)
                        with self.assertRaises(RuntimeError):
                            reconcile(root, env)
                    verify.side_effect = None
                    reconcile(root, env)
                    verify.assert_called_with(configured, **({'allow_configured_images': True} if configured == '1.0.5' else {}))
                self.assertEqual(journal.requiring_recovery(), [])
                self.assertEqual(journal.records()[0]['status'], 'recovered' if configured == '1.0.5' else 'succeeded')

    def test_malformed_receipts_are_explicitly_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            engine = Engine(root, root / '.env', None)
            operation = request() | {'id': str(uuid4())}
            engine.record_verification(operation['id'], '1.0.2')
            path = engine.verification_path(operation['id'])
            valid = json.loads(path.read_text())
            for raw in ('{', '[]', 'null', json.dumps(valid | {'verified_at': 'tomorrow'}),
                        json.dumps(valid | {'verified_at': '2026-09-23T08:00:00'}),
                        json.dumps(valid | {'schema_version': True}), json.dumps(valid | {'operation_id': str(uuid4())})):
                with self.subTest(raw=raw):
                    path.write_text(raw)
                    with self.assertRaises(VerificationError):
                        engine.require_verification(operation)

    def test_interruption_reconciliation_requires_verified_source_or_receipted_target(self):
        for version in ('1.0.1', '1.0.2', '1.0.9'):
            with self.subTest(version=version), TemporaryDirectory() as directory:
                root = Path(directory)
                env = root / '.env'
                env.write_text(f'ASSOZETA_VERSION={version}\n')
                journal = Journal(root / '.updater/operations.sqlite3')
                operation = journal.create(request())
                journal.update(operation['id'], status='running', stage='backup')
                journal.interrupt_running()
                engine = Engine(root, env, journal)
                with patch.object(Engine, 'verify_running') as verify:
                    if version == '1.0.9':
                        with self.assertRaises(ReleaseError):
                            reconcile(root, env)
                        verify.assert_not_called()
                        self.assertEqual(len(journal.interrupted()), 1)
                        continue
                    if version == '1.0.2':
                        with self.assertRaises(VerificationError):
                            reconcile(root, env)
                        self.assertEqual(len(journal.interrupted()), 1)
                        engine.record_verification(operation['id'], version)
                    verify.side_effect = RuntimeError('Unhealthy running version')
                    with self.assertRaises(RuntimeError):
                        reconcile(root, env)
                    self.assertEqual(len(journal.interrupted()), 1)
                    verify.side_effect = None
                    reconcile(root, env)
                self.assertEqual(journal.interrupted(), [])
                self.assertEqual(journal.records()[0]['status'], 'recovered' if version == '1.0.1' else 'succeeded')

    def test_interruption_with_pending_distribution_requires_backup_recovery(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.updater/pending-distribution').mkdir(parents=True)
            with self.assertRaisesRegex(ReleaseError, 'recover-upgrade'), patch.object(Engine, 'verify_running') as verify:
                reconcile(root, root / '.env')
            verify.assert_not_called()

    def test_success_and_failure_are_verified_and_persisted(self):
        for exit_code in (0, 1):
            with self.subTest(exit_code=exit_code), TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'bin').mkdir()
                script = root / 'bin/assozeta'
                script.write_text(f'#!/bin/sh\nprintf "@@ASSOZETA_STAGE migrating\\n"\nexit {exit_code}\n')
                script.chmod(0o755)
                env = root / '.env'
                env.write_text(official_environment())
                journal = Journal(root / '.updater/operations.sqlite')
                operation = journal.create(request())
                engine = Engine(root, env, journal, resolver=lambda _: {'tag': 'v1.0.2', 'artifacts_ready': True})
                with patch.object(engine, 'require_verification', return_value={'verified_at': '2026-09-14T00:00:00Z'}) as verify, patch.object(engine, 'refresh_runner'), \
                     patch.object(engine, 'launch_command', return_value=[str(script), 'upgrade', '1.0.2']):
                    engine.execute(operation)
                    self.assertEqual(verify.call_count, 1 if exit_code == 0 else 0)
                record = journal.records()[0]
                self.assertEqual(record['status'], 'succeeded' if exit_code == 0 else 'recovery_required')
                self.assertNotIn('keep-private', json.dumps(record))

    def test_success_requires_a_verification_bound_to_this_operation_and_release(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            engine = Engine(root, root / '.env', None)
            operation = {'id': str(uuid4()), 'tag': 'v1.0.2'}
            with self.assertRaises(VerificationError):
                engine.require_verification(operation)
            engine.record_verification(operation['id'], '1.0.1')
            with self.assertRaises(VerificationError):
                engine.require_verification(operation)
            engine.record_verification(operation['id'], '1.0.2')
            self.assertEqual(engine.require_verification(operation)['version'], '1.0.2')
            with self.assertRaises(VerificationError):
                engine.require_verification({**operation, 'id': str(uuid4())})


if __name__ == '__main__':
    unittest.main()
