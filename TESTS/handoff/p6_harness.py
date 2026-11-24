"""Phase 6 harness: mode-aware installation, maintenance and recovery on a simulated installation.

Builds on t-p5's p5_harness.py (imported, not copied): the distribution's own CLI runs
unmodified against simulated containers (real `docker compose config`, simulated
up/run/stop/exec), a private PostgreSQL cluster with every operational migration and a
private Redis. TESTS/handoff/p6_docker.py adds what lifecycle commands need on top: the
real PostgreSQL client tools against the private cluster (so `backup` dumps and `restore`
really swaps databases), `redis-cli` against the private Redis (so `restore` really
flushes it), Compose's refusal to exec into a stopped service, and kill injection.

Vocabulary used by the Phase 6 tests:
  - a *Go-mode installation* is a fresh installation (Django by default) whose authority was
    moved to Go with the Phase 1 SQL function `assozeta_commit_runtime_authority` (no work in
    flight, so no transfer is needed) and then restarted by its own CLI. This keeps Phase 6
    independent of the Phase 5 controller; tests that exercise the controller say so.
  - `executables(since)` lists every container the lifecycle asked Docker for: resident
    starts (`up`), one-shot containers (`compose run`, including the entrypoint override),
    execs into running services, and updater helper runs. Each carries the runtime of its
    image/entrypoint (topology.runtime_of): 'django', 'go' or 'shared'.
  - Milestone A (resident exclusivity) forbids starting an opposite-runtime *resident*
    service and every opposite-runtime one-shot except the documented maintenance-only
    migration tool (`migrate` in Go mode, `migrate-go` in Django mode). Milestone B (full
    independence) forbids every opposite-runtime container, one-shots included.
"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import sys
import subprocess
import tarfile
import tempfile
import time
import uuid

from TESTS.handoff import p5_harness as h5
from TESTS.handoff.p2_harness import ACTIVE_API, APP, DJANGO_APP, GO_APP, SHARED
from TESTS.handoff.topology import HERE, ROOT, runtime_of

QUEUE = h5.QUEUE
MAIL_TASK = h5.MAIL_TASK
DB_TASK = h5.DB_TASK
OPPOSITE = {'django': 'go', 'go': 'django'}
# The maintenance-only migration tool milestone A still tolerates in the opposite mode.
MAINTENANCE_MIGRATOR = {'go': 'migrate', 'django': 'migrate-go'}
LIVE_DATABASE = 'assozeta'  # DBNAME written by bin/assozeta configure
ROLES = {'django': ('broker', 'scheduler', 'relay'), 'go': ('broker', 'durable', 'scheduler', 'relay')}


# Compose `exec`/`run` options that take a value (as TESTS/handoff/fake_docker.py records them).
EXEC_VALUE_OPTIONS = {'--env', '-e', '--user', '-u', '--workdir', '-w', '--index', '--entrypoint', '--name', '-v',
                      '--volume', '--label', '-l', '--publish', '-p'}


def exec_values(arguments):
    """Positional values (service, command...) of recorded `exec` sub-arguments."""
    index = 0
    while index < len(arguments) and arguments[index].startswith('-'):
        index += 2 if arguments[index] in EXEC_VALUE_OPTIONS else 1
    return arguments[index:]


class P6Installation(h5.P5Installation):
    """A P5 installation whose data tools are real (p6_docker.py)."""

    def __init__(self, directory, revision=None, legacy_database=False):
        super().__init__(directory, revision=revision, legacy_database=legacy_database)
        self._tool('docker', f'exec "{sys.executable}" -I "{HERE / "p6_docker.py"}" "$@"\n')
        self._definitions = None

    updater_path = ()  # extra PYTHONPATH entries for updater tools run by p5_docker (e.g. a release transport)

    def environment(self, **extra):
        services = self.services
        environment = self._environment(**extra)
        if self.updater_path:
            environment['HANDOFF_P5_PYTHONPATH'] = os.pathsep.join(
                [environment['HANDOFF_P5_PYTHONPATH'], *map(str, self.updater_path)])
        return environment

    def _environment(self, **extra):
        services = self.services
        return super().environment(
            HANDOFF_P6_PGBIN=str(services.bin), HANDOFF_P6_PGHOST=str(services.directory),
            HANDOFF_P6_PGUSER=h5.USER, HANDOFF_P6_DBMAP=json.dumps({LIVE_DATABASE: self.database}),
            HANDOFF_P6_REDIS_PASSWORD=services.redis_password, **extra)

    # -- CLI ------------------------------------------------------------------------------------------------------
    def cli(self, *arguments, check=True, timeout=300, env=None):
        """bin/assozeta ARGS with no terminal, in its own session (kill injection kills only it)."""
        result = subprocess.run([str(self.root / 'bin/assozeta'), *arguments], cwd=self.root,
                                env=self.environment(**(env or {})), capture_output=True, text=True,
                                timeout=timeout, stdin=subprocess.DEVNULL, start_new_session=True)
        if check and result.returncode != 0:
            raise h5.HarnessError(f'bin/assozeta {" ".join(arguments)} exited {result.returncode}\n'
                                  f'stdout:\n{result.stdout[-3000:]}\nstderr:\n{result.stderr[-3000:]}')
        return result

    def go_mode(self):
        """Move the authority of this fresh installation to Go (Phase 1 SQL) and restart it in Go mode."""
        runtime, generation, _ = self.authority()
        if runtime == 'go':
            return
        outcome = self.sql(f"SELECT outcome FROM public.assozeta_commit_runtime_authority('{uuid.uuid4()}', "
                           f"'django', {generation}, 'go');")
        if outcome != 'committed':
            raise h5.HarnessError(f'fixture authority commit answered {outcome}')
        self.cli('restart')

    def mode(self, runtime):
        """Bring a fresh installation to `runtime` (fixture, see module docstring)."""
        if runtime == 'go':
            self.go_mode()
        return self

    def set_state(self, **values):
        state = self.observed()
        state.update(values)
        self.state.write_text(json.dumps(state, indent=1, sort_keys=True))

    # -- container evidence ---------------------------------------------------------------------------------------
    def definitions(self):
        if self._definitions is None:
            self._definitions = self.rendered('*')
        return self._definitions

    def executables(self, since=0):
        """Every container the lifecycle asked for since event index `since` (see module docstring)."""
        found = []
        definitions = self.definitions()
        for event in self.events()[since:]:
            call = event.get('call', [])
            if call[:2] == ['compose', 'up']:
                for service in event.get('started', []):
                    definition = self.observed()['definitions'].get(service) or definitions.get(service, {})
                    found.append({'kind': 'resident', 'service': service, 'runtime': runtime_of(definition)})
            elif call[:2] == ['compose', 'run'] and 'oneoff' in event:
                # The recorded call is ['compose', 'run', *options, SERVICE, *ARGS] (fake_docker).
                values = exec_values(call[2:])
                service = values[0] if values else event['oneoff'].get('service')
                definition = dict(definitions.get(service, {}))
                entrypoint = call[call.index('--entrypoint') + 1] if '--entrypoint' in call[2:] else None
                if entrypoint:
                    definition['entrypoint'] = [entrypoint]
                arguments = values[1:] or list(definition.get('command') or [])
                argv = ([entrypoint] if entrypoint else list(definition.get('entrypoint') or [])) + list(arguments)
                found.append({'kind': 'oneshot', 'service': service, 'runtime': runtime_of(definition),
                              'argv': [str(a) for a in argv], 'exit': event.get('exit', 0)})
                for service_ in event.get('started', []):
                    found.append({'kind': 'resident', 'service': service_,
                                  'runtime': runtime_of(definitions.get(service_, {}))})
            elif call[:2] == ['compose', 'exec'] and len(call) > 2:
                values = exec_values(call[2:])
                if not values or values[0] in ('postgres', 'redis'):
                    continue
                service, command = values[0], values[1:]
                found.append({'kind': 'exec', 'service': service,
                              'runtime': runtime_of(definitions.get(service, {})), 'argv': command,
                              'exit': event.get('exit', 0)})
            elif call[:1] == ['run'] and event.get('tool'):
                found.append({'kind': 'controller', 'service': event['tool'], 'runtime': 'shared'})
        return found

    def opposite(self, runtime, since=0, milestone='B'):
        """Containers of the opposite runtime that the milestone forbids."""
        other = OPPOSITE[runtime]
        out = []
        for item in self.executables(since):
            if item['runtime'] != other:
                continue
            if milestone == 'A' and item['kind'] == 'oneshot' and item['service'] == MAINTENANCE_MIGRATOR[runtime]:
                continue
            out.append(item)
        return out

    # -- heartbeats and health fixtures ---------------------------------------------------------------------------
    def heartbeat(self, runtime, role, age_seconds=0):
        consumer = f'p6-{runtime}-{role}-{uuid.uuid4().hex[:6]}'
        self.sql(f"SELECT * FROM public.assozeta_handoff_heartbeat('{consumer}', '{runtime}', '{role}');")
        if age_seconds:
            self.sql(f"UPDATE public.assozeta_handoff_consumers SET heartbeat_at = clock_timestamp() - "
                     f"interval '{int(age_seconds)} seconds' WHERE consumer_id = '{consumer}';")
        return consumer

    def scheduler_lease(self, runtime, age_seconds=0):
        holder = f'p6-scheduler-{runtime}'
        outcome = self.sql(f"SELECT outcome FROM public.assozeta_scheduler_acquire('{runtime}', '{holder}', 120);")
        if outcome not in ('acquired', 'renewed'):
            raise h5.HarnessError(f'fixture scheduler lease answered {outcome}')
        if age_seconds:
            self.sql(f"UPDATE public.assozeta_scheduler_lease SET heartbeat_at = clock_timestamp() - interval "
                     f"'{int(age_seconds)} seconds', lease_until = clock_timestamp() - interval '1 second';")
        return holder

    def reset_health(self):
        """Fixture: no heartbeat, no scheduler lease, clean schema history, no due work, readiness answering."""
        self.sql("DELETE FROM public.assozeta_handoff_consumers; DELETE FROM public.assozeta_scheduler_lease; "
                 "UPDATE public.assozeta_go_schema_migrations SET dirty = false; "
                 "DELETE FROM public.assozeta_go_realtime_events; "
                 "UPDATE public.assozeta_handoff_work SET state = 'completed' WHERE state IN ('pending', 'held', 'staged');")
        self.failing_urls.write_text('')

    def healthy_runtime(self, runtime):
        """Fresh heartbeats of every resident role of `runtime` and its scheduler lease."""
        for role in ROLES[runtime]:
            if role != 'scheduler':
                self.heartbeat(runtime, role)
        self.heartbeat(runtime, 'scheduler')
        self.scheduler_lease(runtime)

    # -- archives -------------------------------------------------------------------------------------------------
    def backups(self):
        return sorted((self.root / 'backups').glob('assozeta-*.tar.gz'))

    def archive_member(self, archive, name):
        with tarfile.open(archive) as handle:
            for member in handle.getmembers():
                if member.name.lstrip('./') == name:
                    return handle.extractfile(member).read()
        return None

    def manifest(self, archive):
        text = (self.archive_member(archive, 'manifest.txt') or b'').decode()
        return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)

    def rewrite_manifest(self, archive, values):
        """Replace manifest.txt inside the archive (e.g. to the historical five-key format)."""
        with tempfile.TemporaryDirectory(dir=self.root.parent) as directory:
            work = Path(directory)
            with tarfile.open(archive) as handle:
                handle.extractall(work, filter='data')
            (work / 'manifest.txt').write_text(''.join(f'{k}={v}\n' for k, v in values.items()))
            with tarfile.open(archive, 'w:gz') as handle:
                for name in ('postgres.dump', 'objects', 'config.env', 'manifest.txt'):
                    handle.add(work / name, arcname='./' + name)

    def dumped_rows(self, archive, sql):
        """Rows of `sql` in the archive's PostgreSQL dump, restored into a scratch database."""
        scratch = f'p6_dump_{uuid.uuid4().hex[:8]}'
        services = self.services
        services._run(services.bin / 'createdb', '-h', services.directory, '-U', h5.USER, scratch)
        try:
            with tempfile.NamedTemporaryFile(dir=self.root.parent, suffix='.dump') as dump:
                dump.write(self.archive_member(archive, 'postgres.dump') or b'')
                dump.flush()
                subprocess.run([str(services.bin / 'pg_restore'), '-h', str(services.directory), '-U', h5.USER,
                                '-d', scratch, '--no-owner', '--no-privileges', dump.name], capture_output=True,
                               timeout=120)
            return services.rows(scratch, sql)
        finally:
            subprocess.run([str(services.bin / 'dropdb'), '-h', str(services.directory), '-U', h5.USER,
                            '--if-exists', scratch], capture_output=True, timeout=60)

    def database_exists(self, name):
        return self.services.query('postgres', f"SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname='{name}');") == 't'

    def restore_databases(self):
        return self.services.query('postgres', "SELECT string_agg(datname, ',') FROM pg_database WHERE datname LIKE "
                                               "'assozeta_restore_%';").split(',') if self.services.query(
            'postgres', "SELECT count(*) FROM pg_database WHERE datname LIKE 'assozeta_restore_%';") != '0' else []

    # -- work -----------------------------------------------------------------------------------------------------
    def seed_work(self, runtime=None):
        """One admitted broker task due now and one due next month on the effective runtime's queue."""
        runtime = runtime or self.authority()[0]
        queue = QUEUE[runtime]
        return {'due now': self.push(queue, MAIL_TASK, args=h5.mail_args(1)),
                'next month': self.push(queue, MAIL_TASK, eta=h5.future(30), args=h5.mail_args(2))}

    def ledger_rows(self, task_ids):
        if not task_ids:
            return {}
        listed = ','.join(f"'{task}'" for task in task_ids)
        return {row['task_id']: row for row in self.rows(
            f"SELECT task_id, state, owner_runtime, owner_generation FROM public.assozeta_handoff_work "
            f"WHERE task_id IN ({listed})")}


def release_ref(name, number):
    """A canonical-looking immutable reference for fixture release `2.0.<number>` (simulated images)."""
    digest = hashlib.sha256(f'p6-{name}-{number}'.encode()).hexdigest()
    return f'ghcr.io/carbogninalberto/assozeta-{name}@sha256:{digest}'


RELEASE_IMAGES = ('backend', 'web', 'renderer', 'updater')
MANAGED_TOP = ('compose.yml', 'compose.updater.yml', '.env.example', '.env.dev.example')


def managed_installation(inst, runtime):
    """Install fixture release 2.0.1 of this distribution as a self-updating installation in `runtime` mode.

    Release 2.0.1 and 2.0.2 carry this checkout's managed files; their images are distinct simulated
    references (release_ref). The upstream release service is the test transport
    selfhost/tests/fixtures/sitecustomize.py (ASSOZETA_TEST_FIXTURES), loaded only into updater
    tools. Returns the environment the CLI needs for upgrades (the transport location).
    """
    fixtures = inst.root.parent / 'release-fixtures'
    fixtures.mkdir()
    inst.updater_path = (ROOT / 'selfhost/tests/fixtures',)
    files = {}
    for path in sorted(inst.root.rglob('*')):
        relative = str(path.relative_to(inst.root))
        if path.is_file() and '__pycache__' not in relative and (
                relative in MANAGED_TOP or relative.split('/')[0] in ('bin', 'config', 'caddy', 'updater')):
            files[relative] = (path.read_bytes(), path.stat().st_mode & 0o777)
    catalog, references = [], []
    for number in (1, 2):
        tag = f'v2.0.{number}'
        destination = fixtures / tag
        destination.mkdir()
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz') as archive:
            for name, (data, mode) in files.items():
                member = tarfile.TarInfo('selfhost/' + name)
                member.size, member.mode = len(data), mode
                archive.addfile(member, io.BytesIO(data))
        bundle = stream.getvalue()
        (destination / f'assozeta-selfhost-{tag}.tar.gz').write_bytes(bundle)
        manifest = {'schema_version': 1, 'version': f'2.0.{number}', 'bundle_sha256': hashlib.sha256(bundle).hexdigest(),
                    'images': {name: release_ref(name, number) for name in RELEASE_IMAGES},
                    'extensions': {'go_backend': {'schema_version': 1, 'image': release_ref('backend-go', number)}},
                    'files': {name: hashlib.sha256(data).hexdigest() for name, (data, _) in files.items()}}
        references += [*manifest['images'].values(), release_ref('backend-go', number)]
        (destination / 'assozeta-update.json').write_text(json.dumps(manifest))
        catalog.append({'id': number, 'tag_name': tag, 'name': tag, 'published_at': '2026-09-01T00:00:00Z',
                        'body': 'Fixture release', 'draft': False, 'prerelease': False,
                        'assets': [{'name': item, 'state': 'uploaded', 'size': (destination / item).stat().st_size}
                                   for item in (f'assozeta-selfhost-{tag}.tar.gz', 'assozeta-update.json')]})
    (fixtures / 'releases.json').write_text(json.dumps(catalog))
    (fixtures / 'images.json').write_text(json.dumps({reference: reference for reference in references}))
    environment = {'ASSOZETA_TEST_FIXTURES': str(fixtures)}
    inst.cli('install', '--domain', 'localhost', '--version', '2.0.1', env=environment)
    inst.set_env(REDIS_USERNAME='default', REDIS_PASSWORD=inst.services.redis_password,
                 ASSOZETA_BACKEND_GO_REF=release_ref('backend-go', 1),
                 **{f'ASSOZETA_{name.upper()}_REF': release_ref(name, 1) for name in RELEASE_IMAGES})
    inst.cli('restart', env=environment)
    inst.mode(runtime)
    return environment


def failed_upgrade(case, inst, runtime):
    """Upgrade 2.0.1 -> 2.0.2 whose target image of `runtime` cannot start, then recover-upgrade.

    Returns {'upgrade': result, 'recover': result or None, 'marks': (before upgrade, before recovery),
    'generation': generation before the upgrade}. The target CLI is bootstrapped from the release
    bundle by the installed (source) controller, as an owner or operator update does.
    """
    environment = managed_installation(inst, runtime)
    inst.set_state(crashing_images=[release_ref('backend-go' if runtime == 'go' else 'backend', 2)])
    outcome = {'generation': inst.authority()[1], 'marks': [inst.mark()]}
    outcome['upgrade'] = inst.cli('upgrade', '2.0.2', check=False, env=environment, timeout=900)
    transaction = inst.root / '.updater/pending-distribution/transaction.json'
    outcome['transaction'] = json.loads(transaction.read_text()) if transaction.exists() else None
    outcome['recover'] = None
    outcome['marks'].append(inst.mark())
    if outcome['transaction'] and outcome['transaction'].get('backup'):
        inst.set_state(crashing_images=[])
        outcome['recover'] = inst.cli('recover-upgrade', outcome['transaction']['backup']['path'], '--yes', check=False,
                                      env=environment, timeout=900)
    return outcome


@contextlib.contextmanager
def installation(case, runtime='django', install=True, revision=None):
    directory = tempfile.TemporaryDirectory(prefix='assozeta-p6-')
    case.addCleanup(directory.cleanup)
    # Resolved: the CLI compares archive paths with the installation root (macOS /var -> /private/var).
    item = P6Installation(Path(directory.name).resolve(), revision=revision)
    if install:
        item.install()
        item.mode(runtime)
    yield item


class ContractA:
    """Milestone A (resident-service exclusivity): failures read 'P6A contract red: ...'."""
    PREFIX = 'P6A contract red'

    def _formatMessage(self, msg, standardMsg):
        text = super()._formatMessage(msg, standardMsg)
        return text if text.startswith(self.PREFIX) else f'{self.PREFIX}: {text}'


class ContractB(ContractA):
    """Milestone B (full Go-mode independence): failures read 'P6B contract red: ...'."""
    PREFIX = 'P6B contract red'


def health(installation, *arguments, env=None):
    """`bin/assozeta health` -> (exit code, JSON document or None, result)."""
    result = installation.cli('health', *arguments, check=False, env=env)
    document = None
    for line in reversed(result.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith('{'):
            try:
                document = json.loads(line)
            except ValueError:
                document = None
            break
    return result.returncode, document, result


def wait_for(predicate, description, timeout=60.0, interval=0.1):
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f'P6 contract red: timed out waiting for {description}')
        time.sleep(interval)


stop_services = h5.stop_services
__all__ = ['ACTIVE_API', 'APP', 'DJANGO_APP', 'GO_APP', 'SHARED', 'ROOT', 'signal']
