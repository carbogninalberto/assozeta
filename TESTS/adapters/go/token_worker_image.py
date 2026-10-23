"""Packaged expired-token cleanup on the actual current Django schema.

This rehearsal consumes an existing image. It does not build, retag, deploy or
activate a service, and the fixture's queue/ownership/restore rows are never
installed authority. Worker and migrate commands come from the image; only a
read-only Linux helper supplies the lifecycle-lock barrier.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema
from TESTS.adapters.go.token_worker_process import NIL, TokenFixture
from TESTS.adapters.go.worker_image import LABEL, Proof, wait

DATABASE = 'assozeta_contract_token_worker_image'
KIND = 'clear_expired_subscription_tokens'
EXPECTED = {
    'packaged worker and migrate run as UID10001 on read-only root with owned metadata',
    'default Django owner emits claim SQL but leaves tokens audits indexes and managed models unchanged',
    'pending restore and exclusive lifecycle lock defer packaged cleanup without attempts',
    'two packaged workers commit one exact cutoff token cleanup with audit and index readback',
    'SIGTERM cancels blocked cleanup rolls back and a replacement recovers exactly once',
    'SIGKILL lets PostgreSQL roll back and a replacement recovers the expired lease exactly once',
    'handled audit failure rolls back cleanup and completes once with a null result',
}
SOURCES = [
    'TESTS/adapters/go/token_worker_image.py',
    'TESTS/adapters/go/token_worker_process.py',
    'TESTS/adapters/go/age_worker_fixture.py',
    'TESTS/adapters/go/worker_image.py',
    'TESTS/adapters/go/testdata/worker_image_probe.go',
    'BE_GO/Dockerfile',
    'BE_GO/cmd/worker/main.go',
    'BE_GO/cmd/migrate/main.go',
    'BE_GO/internal/domain/subscription_token_cleanup.go',
    'BE_GO/internal/repository/subscription_token_cleanup.go',
    'BE_GO/internal/service/subscription_token_cleanup.go',
    'docs/architecture/go-migration/token-worker-image.md',
]


def source_hashes():
    return {
        path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in SOURCES
    }


def git_value(*arguments):
    result = subprocess.run(
        ['git', *arguments], cwd=ROOT, capture_output=True, text=True, timeout=20
    )
    return result.stdout.strip() if result.returncode == 0 else None


class TokenImage(Proof):
    def __init__(self, image, expected_revision, postgres, work, output):
        super().__init__(image, 'unused', postgres, work, output)
        self.requested_image = image
        self.expected_revision = expected_revision
        self.connections = []
        self.fixture = None
        self.image_metadata = {}
        self.observations = {}
        self.postgres_port = None
        self.fixture_network = self.identifier + "-fixture-net"
        self.fixture_network_created = False

    def connect(self, name):
        import psycopg2

        connection = psycopg2.connect(
            host='127.0.0.1',
            port=self.postgres_port,
            user='contracts',
            dbname=DATABASE,
            application_name='fixture-token-image-' + name,
        )
        self.connections.append(connection)
        return connection

    def sql(self, statement, parameters=()):
        with self.connection.cursor() as cursor:
            cursor.execute(statement, parameters or None)
            return cursor.fetchall() if cursor.description else None

    def close_connections(self):
        for connection in reversed(self.connections):
            try:
                connection.rollback()
            except Exception:
                pass
            try:
                connection.close()
            except Exception:
                pass
        self.connections = []

    def cleanup(self):
        failures = []
        try:
            super().cleanup()
        except Exception as error:
            failures.append(str(error))
        if self.fixture_network_created:
            try:
                self.docker('network', 'rm', self.fixture_network)
            except Exception:
                failures.append('private fixture bridge cleanup failed')
        if failures:
            raise AssertionError('; '.join(failures))

    def image_proof(self):
        """Resolve one immutable image and inspect its packaged command metadata."""
        image = json.loads(
            self.docker('image', 'inspect', self.requested_image).stdout
        )
        assert len(image) == 1, 'Exactly one local image is required'
        image = image[0]
        assert re.fullmatch(
            r'sha256:[0-9a-f]{64}', image['Id']
        ), 'Immutable image ID required'
        self.image = image['Id']
        config = image['Config']
        assert image['Os'] == 'linux' and image['Architecture'] in ('amd64', 'arm64')
        assert config['User'] == '10001:10001', 'Image runtime identity changed'
        assert config['Entrypoint'] == ['/app/server'], 'Default server entrypoint changed'
        assert not config.get('Volumes'), 'Unscoped image volumes are not permitted'
        assert '/readyz' in ' '.join(config.get('Healthcheck', {}).get('Test', [])), \
            'Default server readiness health check changed'
        self.image_metadata = {
            'immutable_id': self.image,
            'requested_image': self.requested_image,
            'platform': image['Os'] + '/' + image['Architecture'],
            'user': config['User'],
            'entrypoint': config['Entrypoint'],
        }

        worker_help = self.go(
            'worker-presence', '/app/worker', ['--help'], environment=False
        )
        migrate_help = self.go(
            'migrate-presence', '/app/migrate', ['--help'], environment=False
        )
        assert 'subscription token cleanup' in worker_help, \
            'Packaged worker does not register token cleanup'
        assert 'operational migrations' in migrate_help, \
            'Packaged migration command is missing'

        script = r'''set -eu
printf 'uid=%s\n' "$(id -u)"
printf 'gid=%s\n' "$(id -g)"
printf 'version=%s\n' "$(cat /app/VERSION)"
printf 'revision=%s\n' "$(cat /app/CODE_REVISION)"
printf 'worker_sha256=%s\n' "$(sha256sum /app/worker | awk '{print $1}')"
printf 'migrate_sha256=%s\n' "$(sha256sum /app/migrate | awk '{print $1}')"
test ! -w /app/worker
test ! -w /app/migrate
'''
        lines = self.go(
            'metadata', '/bin/sh', ['-ec', script], environment=False
        ).splitlines()
        metadata = dict(line.split('=', 1) for line in lines)
        assert set(metadata) == {
            'uid', 'gid', 'version', 'revision', 'worker_sha256', 'migrate_sha256'
        }, 'Unexpected packaged metadata'
        assert metadata['uid'] == metadata['gid'] == '10001'
        assert metadata['revision'] == self.expected_revision, \
            'Packaged CODE_REVISION differs from the supplied revision'
        assert all(
            re.fullmatch(r'[0-9a-f]{64}', metadata[name])
            for name in ('worker_sha256', 'migrate_sha256')
        )
        self.image_metadata.update({
            'version': metadata['version'],
            'revision': metadata['revision'],
            'worker_sha256': metadata['worker_sha256'],
            'migrate_sha256': metadata['migrate_sha256'],
        })

        architecture = image['Architecture']
        tool_env = {
            key: value for key, value in os.environ.items()
            if key in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')
        }
        try:
            subprocess.run(
                [
                    'go', 'build', '-o', str(self.probe),
                    str(ROOT / 'TESTS/adapters/go/testdata/worker_image_probe.go'),
                ],
                cwd=ROOT / 'BE_GO',
                env={**tool_env, 'CGO_ENABLED': '0', 'GOOS': 'linux', 'GOARCH': architecture},
                check=True,
                capture_output=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise AssertionError('Cannot build the read-only lifecycle lock helper') from error
        self.probe.chmod(0o755)
        keys = json.loads(
            self.go('keys', '/proof/probe', ['keys'], environment=False)
        )
        values = {
            'DBTYPE': 'POSTGRES',
            'DBHOST': 'postgres',
            'DBPORT': '5432',
            'DBNAME': DATABASE,
            'DBUSER': 'contracts',
            'DBPASSWORD': 'private-image-password-canary',
            'DEBUG': 'False',
            'SECRET_KEY': 'private-image-session-canary',
            'JWT_SECRET_KEY': 'fixture',
            'JWT_PUBLIC_KEY': 'fixture',
            'SOCIAL_AUTH_GOOGLE_OAUTH2_KEY': '',
            'SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET': '',
            'AWS_ACCESS_KEY_ID': 'fixture',
            'AWS_SECRET_ACCESS_KEY': 'fixture',
            'AWS_STORAGE_BUCKET_NAME': 'fixture',
            'AWS_S3_REGION_NAME': 'us-east-1',
            'AWS_S3_ENDPOINT_URL': 'http://127.0.0.1:9000',
            'AWS_LOCATION': 'private',
            'DATA_RESTORE_LOCK_PATH': '/operations/lifecycle.lock',
            'RUNNING_VERSION': metadata['version'],
            **keys,
        }
        self.environment.write_text(
            ''.join(f'{key}={value}\n' for key, value in values.items())
        )
        self.environment.chmod(0o600)

    def start_database(self):
        """Start private PostgreSQL; only its ephemeral loopback fixture port is exposed."""
        self.docker('image', 'inspect', self.postgres)
        self.docker(
            'network', 'create', '--internal',
            '--label', LABEL + '=' + self.identifier, self.network,
        )
        self.network_created = True
        assert self.inspect(self.network, '{{.Internal}}') == 'true', \
            'PostgreSQL network is not private'

        # Docker does not publish host ports from an internal-only network.
        # Only PostgreSQL joins this second fixture bridge; Go stays internal.
        self.docker(
            'network', 'create', '--label', LABEL + '=' + self.identifier,
            self.fixture_network,
        )
        self.fixture_network_created = True
        self.database = self.identifier + '-postgres'
        self.containers.append(self.database)
        self.docker(
            'run', '--detach', '--name', self.database,
            '--label', LABEL + '=' + self.identifier,
            '--network', self.fixture_network,
            '--publish', '127.0.0.1::5432',
            '--tmpfs', '/var/lib/postgresql/data',
            '--env', 'POSTGRES_USER=contracts',
            '--env', 'POSTGRES_DB=' + DATABASE,
            '--env', 'POSTGRES_HOST_AUTH_METHOD=trust',
            self.postgres,
            '-c', 'log_statement=all',
            '-c', 'log_line_prefix=%m [%p] app=%a ',
        )
        self.docker('network', 'connect', '--alias', 'postgres', self.network, self.database)
        assert set(json.loads(self.inspect(
            self.database, '{{json .NetworkSettings.Networks}}'
        ))) == {self.network, self.fixture_network}
        wait(
            lambda: self.docker(
                'exec', self.database, 'pg_isready',
                '-h', '127.0.0.1', '-U', 'contracts', '-d', DATABASE,
                success=False,
            ).returncode == 0,
            'private PostgreSQL startup',
        )
        binding = json.loads(
            self.inspect(self.database, '{{json .NetworkSettings.Ports}}')
        )['5432/tcp'][0]
        assert binding['HostIp'] == '127.0.0.1'
        self.observations['postgres_loopback_ephemeral_observed'] = True
        self.postgres_port = str(binding['HostPort'])
        with (self.output / 'schema.log').open('w') as log:
            initialize_current_schema(
                self.work, '127.0.0.1', self.postgres_port, DATABASE, log
            )
        self.connection = self.connect('fixture')
        self.connection.autocommit = True
        self.fixture = TokenFixture()
        assert not self.fixture.snapshot()['application.SubscriptionToken']

    def assert_execution(self, name):
        info = json.loads(self.docker('inspect', name).stdout)[0]
        assert info['Image'] == self.image
        assert info['Config']['User'] == '10001:10001'
        assert info['HostConfig']['ReadonlyRootfs'] is True
        assert info['HostConfig']['NetworkMode'] == self.network
        assert set(info['NetworkSettings']['Networks']) == {self.network}
        assert not any(info['HostConfig'].get('PortBindings', {}).values()), \
            'Worker or migration published a port'
        assert not any((info['NetworkSettings'].get('Ports') or {}).values()), \
            'Worker or migration has a published port'
        assert info['Config']['Healthcheck']['Test'] == ['NONE']
        mounts = {mount['Destination']: mount for mount in info['Mounts']}
        assert set(mounts) == {'/operations', '/proof/probe'}, \
            'Unexpected worker or migration mount'
        assert Path(mounts['/operations']['Source']).resolve() == self.operations.resolve()
        assert mounts['/operations']['RW'] is True
        assert Path(mounts['/proof/probe']['Source']).resolve() == self.probe.resolve()
        assert mounts['/proof/probe']['RW'] is False

    def migrate(self):
        # The installing lifecycle, not /app/migrate itself, owns this exclusive bit.
        migration_gate = self.barrier('migration-lifecycle', 'hold-exclusive')
        migrate = self.go(
            'migrate', '/app/migrate', detached=True, network=self.network
        )
        self.exited(migrate, expected=0)
        self.assert_execution(migrate)
        self.go('migrate-repeat', '/app/migrate', network=self.network)
        self.stop(migration_gate)
        assert tuple(self.sql(
            'SELECT runtime,generation FROM assozeta_go_job_ownership'
        )[0]) == ('django', 1)
        assert tuple(self.sql(
            'SELECT version,dirty FROM assozeta_go_schema_migrations'
        )[0]) == (3, False)
        self.case(
            'packaged worker and migrate run as UID10001 on read-only root with owned metadata'
        )

    def worker(self, suffix, drain='8s'):
        arguments = [
            '--concurrency', '1',
            '--poll-interval', '50ms',
            '--maintenance-interval', '50ms',
            '--lease', '2s',
            '--renew-interval', '200ms',
            '--store-timeout', '500ms',
            '--task-timeout', '15s',
            '--drain-timeout', drain,
        ]
        name = self.go(
            suffix, '/app/worker', arguments, detached=True, network=self.network
        )
        wait(
            lambda: 'worker ready' in self.logs(name) or not self.running(name),
            'packaged token worker readiness',
        )
        assert self.running(name), 'Packaged token worker exited during startup'
        self.assert_execution(name)
        return name

    def seed_token(self, attempts=3):
        self.sql('DELETE FROM assozeta_go_jobs; DELETE FROM instance_datarestore;')
        before = self.fixture.seed()
        identifier = str(uuid4())
        self.sql(
            '''INSERT INTO assozeta_go_jobs(
               id,association_id,kind,version,idempotency_key,payload,max_attempts,available_at)
               VALUES (%s,%s,%s,1,%s,'{}'::jsonb,%s,clock_timestamp())''',
            (identifier, NIL, KIND, identifier, attempts),
        )
        return identifier, before

    def job(self, identifier):
        return tuple(self.sql(
            '''SELECT state,attempts,lease_owner,ownership_generation
               FROM assozeta_go_jobs WHERE id=%s''',
            (identifier,),
        )[0])

    def complete(self, identifier, before, attempts=1):
        wait(
            lambda: self.job(identifier)[0] == 'completed',
            'packaged token cleanup completion',
        )
        state = self.job(identifier)
        assert state[:2] == ('completed', attempts)
        assert state[2] == ''
        assert self.sql(
            'SELECT result FROM assozeta_go_jobs WHERE id=%s', (identifier,)
        ) == [(None,)]
        self.fixture.assert_effect(before)

    def handoff(self):
        # Trusted fixture only: no Django producer, scheduler or installed transition.
        self.sql(
            "UPDATE assozeta_go_job_ownership "
            "SET runtime='go',generation=generation+1 WHERE singleton=1 AND runtime='django'"
        )
        assert tuple(self.sql(
            'SELECT runtime,generation FROM assozeta_go_job_ownership'
        )[0]) == ('go', 2)

    def statement_seen(self, fragment, offset=0):
        """Inspect only PostgreSQL log text appended after a captured offset."""
        records = re.split(
            r'(?m)^(?=\d{4}-\d{2}-\d{2} .* app=)',
            self.logs(self.database)[offset:],
        )
        return any(
            'app=assozeta-go-worker' in record.split('\n', 1)[0]
            and re.search(r'LOG:\s+(execute|statement)', record)
            and fragment in record
            for record in records
        )

    def block_token_effect(self):
        gate = self.connect('token-row-gate')
        with gate.cursor() as cursor:
            cursor.execute(
                '''SELECT subscription_token_id
                   FROM application_subscriptiontoken
                   WHERE expiration_date<%s
                   ORDER BY expiration_date LIMIT 1 FOR UPDATE''',
                (self.fixture.cutoff,),
            )
            assert cursor.fetchone(), 'No expired token available for lock barrier'
        return gate

    @staticmethod
    def release(gate):
        gate.rollback()
        gate.close()

    def effect_is_blocked(self):
        return bool(self.sql(
            """SELECT 1 FROM pg_stat_activity
               WHERE application_name='assozeta-go-worker'
               AND query LIKE '%%WITH removed AS%%'
               AND query LIKE '%%DELETE FROM application_subscriptiontoken%%'
               AND query LIKE '%%auditlog_logentry%%'
               AND wait_event_type='Lock'"""
        ))

    def blocked_backend(self):
        rows = self.sql(
            """SELECT pid FROM pg_stat_activity
               WHERE application_name='assozeta-go-worker'
               AND query LIKE '%%WITH removed AS%%'
               AND query LIKE '%%DELETE FROM application_subscriptiontoken%%'
               AND wait_event_type='Lock'"""
        )
        assert len(rows) == 1, 'Exactly one blocked cleanup backend required'
        return rows[0][0]

    def backend_exists(self, pid):
        return self.sql('SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid=%s)', (pid,))[0][0]

    def business_readback(self, before):
        from application.models import SubscriptionToken
        from application.models.audit_models import AuditLogIndex
        from auditlog.models import LogEntry

        self.fixture.assert_effect(before)
        retained = sorted(
            str(row['subscription_token_id'])
            for row in self.fixture.tokens if not row['deleted']
        )
        removed = sorted(
            str(row['subscription_token_id'])
            for row in self.fixture.tokens if row['deleted']
        )
        actual_tokens = sorted(
            str(value) for value in SubscriptionToken.objects.values_list(
                'subscription_token_id', flat=True
            )
        )
        assert actual_tokens == retained
        audits = list(
            LogEntry.objects.filter(content_type=self.fixture.ct)
            .order_by('object_pk')
            .values('object_pk', 'action', 'changes', 'timestamp')
        )
        assert sorted(entry['object_pk'] for entry in audits) == removed
        assert {entry['action'] for entry in audits} == {LogEntry.Action.DELETE}
        assert not AuditLogIndex.objects.filter(
            log_entry__content_type=self.fixture.ct
        ).exists()
        return {
            'retained_token_ids': retained,
            'removed_token_ids': removed,
            'audit_count': len(audits),
            'audit_actions': sorted({entry['action'] for entry in audits}),
            'audit_index_count': 0,
            'managed_models_exact': True,
        }

    def leave_unchanged(self, before):
        assert self.fixture.snapshot() == before
        from auditlog.models import LogEntry
        assert not LogEntry.objects.filter(content_type=self.fixture.ct).exists()

    def run(self):
        self.image_proof()
        self.start_database()
        self.migrate()

        identifier, before = self.seed_token()
        worker = self.worker('django-owner')
        wait(self.observed_claim, 'observed packaged claim SQL under Django ownership')
        self.observations['default_claim_statement_observed'] = True
        assert self.job(identifier)[:2] == ('pending', 0)
        self.leave_unchanged(before)
        self.stop(worker)
        self.case(
            'default Django owner emits claim SQL but leaves tokens audits indexes and managed models unchanged'
        )

        identifier, before = self.seed_token()
        self.handoff()
        from instance.models import DataRestore
        restore = DataRestore.objects.create(
            state='running',
            owner_id=self.fixture.owners[0],
            association_id=self.fixture.associations[0],
            sha256='0' * 64,
        )
        held = self.fixture.snapshot()
        restore_statement = 'FROM instance_datarestore'
        prior_logs = self.logs(self.database)
        assert self.statement_seen(restore_statement), \
            'Default-owner history lacks the expected restore-admission statement'
        log_offset = len(prior_logs)
        assert not self.statement_seen(restore_statement, log_offset), \
            'Restore log checkpoint did not exclude prior worker history'
        self.observations['restore_log_checkpoint'] = {
            'prior_statement_excluded': True,
            'offset': log_offset,
        }
        worker = self.worker('restore-deferred')
        wait(
            lambda: self.statement_seen(restore_statement, log_offset),
            'observed packaged restore-admission SQL',
        )
        self.observations['restore_admission_statement_observed'] = True
        assert self.running(worker)
        assert self.job(identifier)[:2] == ('pending', 0)
        assert self.fixture.snapshot() == held
        self.stop(worker)
        restore.delete()

        lifecycle_gate = self.barrier('maintenance-lifecycle', 'hold-exclusive')
        worker = self.worker('lifecycle-deferred')
        assert not self.exclusive(), 'Lifecycle gate did not hold the exclusive lock'
        # A separate helper proves the inode is exclusively held and unavailable.
        # The worker's own nonblocking flock syscall is not traced directly.
        self.observations['exclusive_lifecycle_lock_unavailable'] = True
        assert self.running(worker)
        assert self.job(identifier)[:2] == ('pending', 0)
        assert self.fixture.snapshot() == before
        self.stop(lifecycle_gate)
        self.complete(identifier, before)
        self.stop(worker)
        self.case(
            'pending restore and exclusive lifecycle lock defer packaged cleanup without attempts'
        )

        identifier, before = self.seed_token()
        gate = self.block_token_effect()
        first = self.worker('two-workers-first')
        second = self.worker('two-workers-second')
        wait(self.effect_is_blocked, 'positive token cleanup row-lock wait')
        self.observations['token_cleanup_lock_wait_observed'] = True
        assert self.job(identifier)[:2] == ('running', 1)
        assert self.fixture.snapshot() == before
        self.release(gate)
        self.complete(identifier, before)
        self.stop(first)
        self.stop(second)
        self.observations['two_worker_readback'] = self.business_readback(before)
        self.case(
            'two packaged workers commit one exact cutoff token cleanup with audit and index readback'
        )

        identifier, before = self.seed_token()
        gate = self.block_token_effect()
        worker = self.worker('term-worker', drain='250ms')
        wait(self.effect_is_blocked, 'SIGTERM token cleanup row-lock wait')
        backend_pid = self.blocked_backend()
        assert not self.exclusive(), 'Admitted worker released lifecycle lease before cancellation'
        self.docker('kill', '--signal', 'TERM', worker)
        self.exited(worker, expected=1)
        wait(
            lambda: not self.backend_exists(backend_pid),
            'PostgreSQL backend exit after SIGTERM cancellation',
        )
        assert self.job(identifier)[:2] == ('pending', 1)
        assert self.exclusive(), 'Exited SIGTERM worker retained lifecycle lease'
        self.leave_unchanged(before)
        self.release(gate)
        recovery = self.worker('term-recovery')
        self.complete(identifier, before, attempts=2)
        self.stop(recovery)
        self.case(
            'SIGTERM cancels blocked cleanup rolls back and a replacement recovers exactly once'
        )

        identifier, before = self.seed_token()
        gate = self.block_token_effect()
        worker = self.worker('kill-worker')
        wait(self.effect_is_blocked, 'SIGKILL token cleanup row-lock wait')
        backend_pid = self.blocked_backend()
        assert not self.exclusive()
        self.docker('kill', '--signal', 'KILL', worker)
        self.exited(worker, expected=137)
        assert self.exclusive(), 'SIGKILL worker retained lifecycle lease'
        assert self.job(identifier)[:2] == ('running', 1)
        # A dead client need not be detected while PostgreSQL waits on our row
        # lock. Let that statement finish, then observe its backend exit and
        # rollback before starting any replacement worker.
        self.release(gate)
        wait(
            lambda: not self.backend_exists(backend_pid),
            'captured PostgreSQL backend exit after SIGKILL',
        )
        self.leave_unchanged(before)
        recovery = self.worker('kill-recovery')
        self.complete(identifier, before, attempts=2)
        self.stop(recovery)
        self.case(
            'SIGKILL lets PostgreSQL roll back and a replacement recovers the expired lease exactly once'
        )

        identifier, before = self.seed_token()
        self.sql(
            "CREATE FUNCTION fixture_token_image_audit_failure() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
            "'private-token-image-audit-canary'; END $$"
        )
        self.sql(
            'CREATE TRIGGER fixture_token_image_audit_failure '
            'BEFORE INSERT ON auditlog_logentry FOR EACH STATEMENT '
            'EXECUTE FUNCTION fixture_token_image_audit_failure()'
        )
        try:
            worker = self.worker('handled-audit-failure')
            wait(
                lambda: self.job(identifier)[0] == 'completed',
                'handled audit failure null completion',
            )
            self.stop(worker)
            assert self.job(identifier)[:2] == ('completed', 1)
            assert self.sql(
                'SELECT result FROM assozeta_go_jobs WHERE id=%s', (identifier,)
            ) == [(None,)]
            self.leave_unchanged(before)
            logs = self.logs(worker)
            assert 'subscription_token_cleanup_failed' in logs
            assert 'private-token-image-audit-canary' not in logs
        finally:
            self.sql(
                'DROP TRIGGER fixture_token_image_audit_failure ON auditlog_logentry'
            )
            self.sql('DROP FUNCTION fixture_token_image_audit_failure()')
        self.case(
            'handled audit failure rolls back cleanup and completes once with a null result'
        )
        assert set(self.completed) == EXPECTED and len(self.completed) == len(EXPECTED)


def main():
    require_production_python()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--expected-revision', required=True)
    parser.add_argument('--postgres-image', default='postgres:16-alpine')
    parser.add_argument(
        '--output',
        type=Path,
        default=ROOT / 'quality-reports/go-migration/token-worker-image',
    )
    options = parser.parse_args()
    output = options.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / 'results.json'

    before_hashes = source_hashes()
    result = {
        'success': False,
        'scope': (
            'packaged token worker/migrate on actual current Django models under '
            'synthetic ownership and restore rows; no installed activation'
        ),
        'requested_image': options.image,
        'supplied_revision': options.expected_revision,
        'source_worktree_head': git_value('rev-parse', 'HEAD'),
        'source_main_commit': git_value('rev-parse', 'main'),
        'source_hashes': before_hashes,
        'source_provenance_limit': (
            'Hashes identify reviewed local inputs; this adapter consumes an '
            'image and does not attest a source-to-image build.'
        ),
        'expected_cases': sorted(EXPECTED),
        'cases': [],
    }
    report_path.write_text(json.dumps(result, indent=2) + '\n')
    run_error = cleanup_error = source_error = None
    proof = None
    work = None
    with tempfile.TemporaryDirectory(prefix='az-token-image-', dir='/tmp') as directory:
        work = Path(directory)
        work.chmod(0o755)
        proof = TokenImage(
            options.image,
            options.expected_revision,
            options.postgres_image,
            work,
            output,
        )
        try:
            proof.run()
        except Exception as error:
            run_error = error
        finally:
            try:
                proof.close_connections()
            except Exception as error:
                cleanup_error = error
            try:
                if 'django.db' in sys.modules:
                    from django.db import connections
                    connections.close_all()
            except Exception as error:
                cleanup_error = cleanup_error or error
            try:
                proof.cleanup()
            except Exception as error:
                cleanup_error = cleanup_error or error
            try:
                result['owned_resources'] = {
                    'containers_absent': (
                        proof.docker(
                            'ps', '-aq', '--filter',
                            'label=' + LABEL + '=' + proof.identifier,
                        ).stdout.strip() == ''
                    ),
                    'networks_absent': (
                        proof.docker(
                            'network', 'ls', '-q', '--filter',
                            'label=' + LABEL + '=' + proof.identifier,
                        ).stdout.strip() == ''
                    ),
                }
                if not all(result['owned_resources'].values()):
                    cleanup_error = cleanup_error or AssertionError(
                        'Owned Docker resources remain after cleanup'
                    )
            except Exception as error:
                cleanup_error = cleanup_error or error
            result.update(
                cases=proof.completed,
                case_count=len(proof.completed),
                image_id=proof.image_metadata.get('immutable_id'),
                image=proof.image_metadata,
                observations=proof.observations,
            )
            if run_error:
                result['failure'] = {
                    'type': type(run_error).__name__,
                    'message': str(run_error),
                }
            if cleanup_error:
                result['cleanup_failure'] = {
                    'type': type(cleanup_error).__name__,
                    'message': str(cleanup_error),
                }
            report_path.write_text(json.dumps(result, indent=2) + '\n')

    assert work is not None and not work.exists(), 'Private temporary directory was not removed'
    after_hashes = source_hashes()
    if before_hashes != after_hashes:
        source_error = AssertionError('Reviewed source changed during packaged rehearsal')
    result.update(
        private_temporary_directory_removed=not work.exists(),
        source_hashes=after_hashes,
    )
    if run_error:
        report_path.write_text(json.dumps(result, indent=2) + '\n')
        raise run_error
    if cleanup_error:
        report_path.write_text(json.dumps(result, indent=2) + '\n')
        raise cleanup_error
    if source_error:
        report_path.write_text(json.dumps(result, indent=2) + '\n')
        raise source_error
    assert set(result['cases']) == EXPECTED, 'Missing required packaged token case'
    assert len(result['cases']) == len(EXPECTED)
    result.update(
        success=True,
        scenario_count=len(EXPECTED),
        uid=10001,
        read_only_root=True,
        lifecycle_lock_helper_read_only=True,
        private_resources_removed=True,
        no_worker_or_migrate_published_ports=True,
        internal_runtime_network=True,
        postgres_loopback_ephemeral_only=True,
        installed_ownership_or_scheduler_activated=False,
    )
    report_path.write_text(json.dumps(result, indent=2) + '\n')
    print(
        json.dumps({
            'success': True,
            'image_id': result['image_id'],
            'scenario_count': result['scenario_count'],
            'private_resources_removed': True,
        })
    )


if __name__ == '__main__':
    main()
