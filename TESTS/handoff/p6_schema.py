"""Phase 6 schema driver: build databases through each shipped migration path and compare them.

Runs in the test process (stdlib only) except `django-migrate`, which runs under the production
Python with the BE requirements (TESTS/handoff/p3_suite.production_python, ASSOZETA_HANDOFF_PYTHON):

    python -B -m TESTS.handoff.p6_schema django-migrate DATABASE [LEAVES_JSON]

Paths compared (all in one private PostgreSQL cluster, databases named assozeta_contract_p6_*):
  reference   what an installation gets today: Django `manage.py migrate` (with its post_migrate
              content types and permissions) followed by the Go operational migrations
              (BE_GO/migrations, applied as golang-migrate records them)
  django      Django `manage.py migrate` alone (milestone B: it must also prepare the shared
              operational tables, without a Go command)
  go          the Go migrator alone (`go build ./cmd/migrate`, run with --env-file; milestone B: it
              must build the business schema, its history and content types/permissions too)
Upgrade baselines start from the schema of an older revision R: the Django migrations present at R
(the files are append-only and are verified identical to the current ones) applied with
`migrate <app> <leaf>`, third-party apps fully migrated, the Go operational SQL files present at R,
and sanitized legacy rows (the Phase 0 legacy-mixed fixture, plus a group holding a permission).

`snapshot()` captures tables, columns, constraints (by name), indexes, sequences (with their
values), functions, triggers, the Django and Go migration histories, content types and permissions
by natural key, and every table's rows (timestamps reduced to null/not-null, content-type ids
replaced by natural keys).
"""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PREFIX = 'assozeta_contract_p6_'
FIXTURE = ROOT / 'TESTS/handoff/fixtures/legacy-mixed/database.json'
# Supported upgrade baselines (handoff-inventory.json baseline.upgrade_from, real releases) and the plan base.
BASELINES = ('v1.0.2', 'v1.0.5', 'v1.0.17', 'v1.0.18', '2284ed46')
# Values for NOT NULL columns a historical schema requires and the fixture row does not carry.
SYNTHETIC = {'boolean': False, 'integer': 0, 'bigint': 0, 'smallint': 0, 'numeric': 0, 'double precision': 0,
             'jsonb': {}, 'json': {}, 'uuid': '00000000-0000-4000-8000-000000000061',
             'timestamp with time zone': '2026-01-01T00:00:00+00:00', 'date': '2026-01-01',
             'timestamp without time zone': '2026-01-01T00:00:00', 'ARRAY': [], 'character varying': 'p6',
             'text': 'p6'}
# dispatch_uid of the Django post_migrate receiver that prepares the operational tables (B1).
OPERATIONAL_RECEIVER = 'assozeta_operational_schema'
# Where the Django image carries its copy of the Go operational migrations (B1), byte for byte.
VENDORED_OPERATIONAL_SQL = ROOT / 'BE/instance/operational_sql'
NATURAL = {'django_content_type', 'auth_permission', 'auth_group_permissions', 'django_migrations',
           'assozeta_go_schema_migrations'}


class SchemaHarnessError(RuntimeError):
    """The driver failed (never a valid red)."""


def git(*arguments):
    return subprocess.run(['git', '-C', str(ROOT), *arguments], check=True, capture_output=True, text=True).stdout


# -- databases -----------------------------------------------------------------------------------------------------
class Cluster:
    """Thin wrapper over TESTS/handoff/p5_harness.Services (one private cluster per test process)."""

    def __init__(self, services):
        self.services = services

    @property
    def host(self):
        return str(self.services.directory)

    def create(self, name, template=None):
        command = [self.services.bin / 'createdb', '-h', self.services.directory, '-U', 'contracts']
        if template:
            command += ['-T', template]
        self.services._run(*command, name)
        return name

    def drop(self, name):
        subprocess.run([str(self.services.bin / 'dropdb'), '-h', self.host, '-U', 'contracts', '--if-exists',
                        '--force', name], capture_output=True, timeout=60)

    def sql(self, database, statement):
        return self.services.query(database, statement)

    def rows(self, database, statement):
        return self.services.rows(database, statement)


def environment_file(cluster, database, directory):
    """The installation-shaped environment the Go migrator reads (--env-file)."""
    path = Path(directory) / f'{database}.env'
    values = {'DBTYPE': 'POSTGRES', 'DBNAME': database, 'DBUSER': 'contracts', 'DBPASSWORD': '',
              'DBHOST': cluster.host, 'DBPORT': '5432', 'DEBUG': 'False', 'SECRET_KEY': secrets.token_hex(16),
              'JWT_SECRET_KEY': 'fixture', 'JWT_PUBLIC_KEY': 'fixture', 'SOCIAL_AUTH_GOOGLE_OAUTH2_KEY': '',
              'SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET': '', 'AWS_ACCESS_KEY_ID': 'fixture', 'AWS_SECRET_ACCESS_KEY': 'fixture',
              'AWS_STORAGE_BUCKET_NAME': 'fixture', 'AWS_S3_REGION_NAME': 'us-east-1',
              'AWS_S3_ENDPOINT_URL': 'http://127.0.0.1:9', 'AWS_LOCATION': 'private', 'REDIS_HOST': '127.0.0.1',
              'REDIS_PORT': '9', 'REDIS_USERNAME': 'default', 'REDIS_PASSWORD': 'fixture', 'GO_TASK_QUEUE_TASKS': '',
              'DATA_RESTORE_LOCK_PATH': str(Path(directory) / 'lifecycle.lock')}
    path.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
    path.chmod(0o600)
    return path


def go_migrator(directory):
    binary = Path(directory) / 'migrate'
    if not binary.exists():
        if not shutil.which('go'):
            raise SchemaHarnessError('the Go toolchain is required; never skipped')
        environment = {key: value for key, value in os.environ.items()
                       if key in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN', 'GOFLAGS', 'GOPATH')}
        result = subprocess.run(['go', 'build', '-o', str(binary), './cmd/migrate'], cwd=ROOT / 'BE_GO',
                                env=environment, capture_output=True, text=True, timeout=900)
        if result.returncode:
            raise SchemaHarnessError(f'go build ./cmd/migrate failed: {result.stderr[-800:]}')
    return binary


def go_migrate(cluster, database, directory):
    """Run the Go migrator; returns (exit code, output)."""
    result = subprocess.run([str(go_migrator(directory)), '--env-file', str(environment_file(cluster, database, directory))],
                            capture_output=True, text=True, timeout=900, env={'PATH': os.environ['PATH'], 'LC_ALL': 'C'})
    return result.returncode, (result.stdout + result.stderr)[-1500:]


def operational_sql(cluster, database, revision=None):
    """Apply BE_GO/migrations/*.up.sql (of `revision`, default the checkout) as golang-migrate records them."""
    if revision is None:
        files = sorted((ROOT / 'BE_GO/migrations').glob('0*_*.up.sql'))
        contents = [(path.name, path.read_text()) for path in files]
    else:
        names = sorted(name for name in git('ls-tree', '--name-only', f'{revision}:BE_GO/migrations').split()
                       if re.fullmatch(r'0\d+_.*\.up\.sql', name)) if git_has(revision, 'BE_GO/migrations') else []
        contents = [(name, git('show', f'{revision}:BE_GO/migrations/{name}')) for name in names]
    applied = 0
    if cluster.sql(database, "SELECT to_regclass('public.assozeta_go_schema_migrations') IS NOT NULL;") == 't':
        applied = int(cluster.sql(database, 'SELECT coalesce(max(version), 0) FROM public.assozeta_go_schema_migrations;'))
    contents = [(name, text) for name, text in contents if int(name.split('_', 1)[0]) > applied]
    if not contents:
        return applied
    for name, text in contents:
        with tempfile.NamedTemporaryFile('w', suffix='.sql', delete=False) as handle:
            handle.write(text)
        try:
            cluster.services._run(cluster.services.psql, '-h', cluster.host, '-U', 'contracts', '-d', database, '-X',
                                  '-q', '-v', 'ON_ERROR_STOP=1', '-f', handle.name)
        finally:
            os.unlink(handle.name)
    version = max(int(name.split('_', 1)[0]) for name, _ in contents)
    cluster.sql(database, 'CREATE TABLE IF NOT EXISTS public.assozeta_go_schema_migrations '
                          '(version bigint NOT NULL PRIMARY KEY, dirty boolean NOT NULL);'
                          'DELETE FROM public.assozeta_go_schema_migrations;'
                          f'INSERT INTO public.assozeta_go_schema_migrations VALUES ({version}, false);')
    return version


def git_has(revision, path):
    return subprocess.run(['git', '-C', str(ROOT), 'cat-file', '-e', f'{revision}:{path}'],
                          capture_output=True).returncode == 0


def baseline_leaves(revision):
    """[(app directory, leaf migration name)] of the BE migrations present at `revision`."""
    files = [line for line in git('ls-tree', '-r', '--name-only', revision, '--', 'BE').splitlines()
             if re.fullmatch(r'BE/[^/]+/migrations/0\d+_[^/]+\.py', line)]
    leaves = {}
    for path in files:
        if git('rev-parse', f'{revision}:{path}').strip() != git('rev-parse', f'HEAD:{path}').strip():
            raise SchemaHarnessError(f'{path} changed since {revision}; the baseline cannot be rebuilt from the checkout')
        directory, name = path.split('/')[1], Path(path).stem
        leaves[directory] = max(leaves.get(directory, name), name)
    return sorted(leaves.items())


def django_migrate(cluster, database, leaves=None, python=None):
    """Run the shipped Django migrations under the production Python; returns (exit code, output)."""
    from TESTS.handoff.p3_suite import production_python
    environment = {'PATH': os.environ['PATH'], 'HOME': os.environ.get('HOME', '/tmp'), 'LC_ALL': 'C',
                   'P6_DBHOST': cluster.host, 'PYTHONPATH': f'{ROOT / "BE"}:{ROOT}',
                   'OBJC_DISABLE_INITIALIZE_FORK_SAFETY': 'YES'}
    command = [python or production_python(), '-B', '-m', 'TESTS.handoff.p6_schema', 'django-migrate', database]
    if leaves is not None:
        command.append(json.dumps(leaves))
    result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=1800)
    if result.returncode == 3:
        raise SchemaHarnessError(f'Django schema driver setup failed: {result.stderr[-1500:]}')
    return result.returncode, (result.stdout + result.stderr)[-1500:]


def seed(cluster, database):
    """Sanitized legacy rows (Phase 0 legacy-mixed) for the tables and columns present, plus a group permission."""
    fixture = json.loads(FIXTURE.read_text())
    present = {row['table_name']: set(row['columns']) for row in cluster.rows(
        database, "SELECT table_name, array_agg(column_name::text) AS columns FROM information_schema.columns "
                  "WHERE table_schema = 'public' GROUP BY table_name")}
    order = ['django_celery_beat_crontabschedule', 'django_celery_beat_intervalschedule',
             'django_celery_beat_periodictask', 'instance_instanceconfiguration', 'communications_whatsappdelivery',
             'assozeta_go_jobs']
    required = {}
    for row in cluster.rows(database, "SELECT table_name, column_name, data_type FROM information_schema.columns WHERE "
                                      "table_schema = 'public' AND is_nullable = 'NO' AND column_default IS NULL "
                                      "AND is_identity = 'NO'"):
        required.setdefault(row['table_name'], {})[row['column_name']] = row['data_type']
    seeded = []
    for table in order:
        if table not in present:
            continue
        for row in fixture.get(table, []):
            values = {column: value for column, value in row.items() if column in present[table]}
            for column, kind in required.get(table, {}).items():
                if values.get(column) is None:
                    values[column] = SYNTHETIC.get(kind, '')
            columns = list(values)
            listed = ', '.join(f'"{column}"' for column in columns)
            document = json.dumps(values).replace("'", "''")
            cluster.sql(database, f"INSERT INTO public.{table} ({listed}) SELECT {listed} FROM "
                                  f"json_populate_record(NULL::public.{table}, '{document}'::json);")
        seeded.append(table)
    if 'auth_group' in present:
        cluster.sql(database, "INSERT INTO public.auth_group (id, name) VALUES (61, 'P6 legacy group');"
                              "INSERT INTO public.auth_group_permissions (group_id, permission_id) "
                              "SELECT 61, min(id) FROM public.auth_permission;")
        seeded.append('auth_group')
    return seeded


def build_baseline(cluster, revision, directory):
    name = cluster.create(f'{PREFIX}base_{re.sub(r"[^a-z0-9]", "_", revision.lower())}')
    code, output = django_migrate(cluster, name, leaves=baseline_leaves(revision))
    if code:
        raise SchemaHarnessError(f'baseline {revision}: Django migrations failed: {output}')
    operational_sql(cluster, name, revision)
    seed(cluster, name)
    return name


# -- snapshot and comparison -----------------------------------------------------------------------------------------
SNAPSHOT_SQL = {
    'tables': "SELECT table_name AS k FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'",
    'columns': "SELECT table_name||'.'||column_name AS k, data_type||'|'||udt_name||'|'||is_nullable||'|'||"
               "coalesce(column_default,'')||'|'||coalesce(character_maximum_length::text,'')||'|'||"
               "coalesce(numeric_precision::text,'')||'|'||coalesce(numeric_scale::text,'') AS v "
               "FROM information_schema.columns WHERE table_schema='public'",
    'constraints': "SELECT conrelid::regclass::text||'.'||conname AS k, pg_get_constraintdef(oid) AS v "
                   "FROM pg_constraint WHERE connamespace='public'::regnamespace",
    'indexes': "SELECT indexname AS k, indexdef AS v FROM pg_indexes WHERE schemaname='public'",
    'sequences': "SELECT sequencename AS k, coalesce(last_value::text,'unused')||'|'||increment_by AS v "
                 "FROM pg_sequences WHERE schemaname='public'",
    'functions': "SELECT p.proname||'('||pg_get_function_identity_arguments(p.oid)||')' AS k, "
                 "md5(pg_get_functiondef(p.oid)) AS v FROM pg_proc p WHERE p.pronamespace='public'::regnamespace",
    'triggers': "SELECT tgrelid::regclass::text||'.'||tgname AS k, pg_get_triggerdef(oid) AS v FROM pg_trigger "
                "WHERE NOT tgisinternal",
}


def uuid_columns(cluster, database):
    """{table: [uuid-typed columns]} of the public schema."""
    found = {}
    for row in cluster.rows(database, "SELECT table_name, column_name FROM information_schema.columns WHERE "
                                      "table_schema='public' AND data_type='uuid' ORDER BY table_name, column_name"):
        found.setdefault(row['table_name'], []).append(row['column_name'])
    return found


def existing_uuids(cluster, database):
    """Every UUID value stored in a uuid-typed column: the identifiers a migration path must preserve."""
    values = set()
    for table, columns in uuid_columns(cluster, database).items():
        for column in columns:
            values.update(r['v'] for r in cluster.rows(
                database, f'SELECT DISTINCT "{column}"::text AS v FROM public."{table}" WHERE "{column}" IS NOT NULL'))
    return frozenset(values)


def snapshot(cluster, database, preserved=frozenset()):
    """Comparable state of `database`.

    Exact everywhere except one class of value: a UUID in a uuid-typed column that a migration minted
    (uuid4 defaults of rows created by data migrations, e.g. application 0230 add_enti_feature), i.e.
    absent from `preserved` (the UUIDs of the database the migrations started from). Each minted value
    becomes a token named after its first occurrence in a deterministic walk (tables, columns, rows
    ordered by their content without UUIDs), and every reference to it gets the same token. Preserved
    identifiers, and every non-UUID value, are compared as they are.
    """
    result = {}
    for category, statement in SNAPSHOT_SQL.items():
        rows = cluster.rows(database, statement)
        result[category] = {row['k']: row.get('v', True) for row in rows}
    tables = sorted(result['tables'])
    if 'django_migrations' in result['tables']:
        result['django_history'] = {f"{r['app']}.{r['name']}": True for r in cluster.rows(
            database, 'SELECT app, name FROM public.django_migrations')}
    if 'assozeta_go_schema_migrations' in result['tables']:
        result['go_history'] = {str(r['version']): r['dirty'] for r in cluster.rows(
            database, 'SELECT version, dirty FROM public.assozeta_go_schema_migrations')}
    if 'django_content_type' in result['tables']:
        result['content_types'] = {f"{r['app_label']}.{r['model']}": True for r in cluster.rows(
            database, 'SELECT app_label, model FROM public.django_content_type')}
        if 'auth_permission' in result['tables']:
            result['permissions'] = {f"{r['app_label']}.{r['model']}.{r['codename']}": r['name'] for r in cluster.rows(
                database, 'SELECT c.app_label, c.model, p.codename, p.name FROM public.auth_permission p '
                          'JOIN public.django_content_type c ON c.id = p.content_type_id')}
        if 'auth_group_permissions' in result['tables']:
            result['group_permissions'] = {f"{r['group_id']}:{r['app_label']}.{r['model']}.{r['codename']}": True
                                           for r in cluster.rows(
                database, 'SELECT gp.group_id, c.app_label, c.model, p.codename FROM public.auth_group_permissions gp '
                          'JOIN public.auth_permission p ON p.id = gp.permission_id '
                          'JOIN public.django_content_type c ON c.id = p.content_type_id')}
    temporal = {}
    for row in cluster.rows(database, "SELECT table_name, column_name FROM information_schema.columns WHERE "
                                      "table_schema='public' AND data_type IN ('timestamp with time zone', "
                                      "'timestamp without time zone', 'date')"):
        temporal.setdefault(row['table_name'], set()).add(row['column_name'])
    data = {}
    uuids = uuid_columns(cluster, database)
    minted, documents = {}, {}
    for table in tables:
        if table in NATURAL or table not in uuids:
            continue
        rows = [r['d'] for r in cluster.rows(database, f'SELECT to_jsonb(t) AS d FROM public."{table}" t')]
        for document in rows:
            for column in temporal.get(table, ()):
                document[column] = document.get(column) is None
        rows.sort(key=lambda d: json.dumps({k: (None if k in uuids[table] else v) for k, v in d.items()},
                                           sort_keys=True))
        documents[table] = rows
        for column in uuids[table]:
            for index, document in enumerate(rows):
                value = document.get(column)
                if value is not None and value not in preserved and value not in minted:
                    minted[value] = f'minted:{table}.{column}:{index}'
    for table, rows in documents.items():
        normalized = sorted(json.dumps({k: minted.get(v, v) if k in uuids[table] else v for k, v in d.items()},
                                       sort_keys=True) for d in rows)
        data[table] = f"{len(normalized)}:{hashlib.md5('|'.join(normalized).encode()).hexdigest() if normalized else ''}"
    for table in tables:
        if table in NATURAL or table in uuids:
            continue
        masked = ' - '.join(f"'{column}'" for column in sorted(temporal.get(table, ())))
        expression = f"to_jsonb(t){' - ' + masked if masked else ''}"
        nulls = ''.join(f"||(t.\"{column}\" IS NULL)::text" for column in sorted(temporal.get(table, ())))
        row = cluster.rows(database, f"SELECT count(*) AS n, coalesce(md5(string_agg(({expression})::text{nulls}, '|' "
                                     f"ORDER BY ({expression})::text{nulls})), '') AS h FROM public.\"{table}\" t")[0]
        data[table] = f"{row['n']}:{row['h']}"
    result['data'] = data
    return result


def differences(expected, actual, limit=12):
    """Human-readable differences between two snapshots (expected = reference)."""
    out = []
    for category in sorted(set(expected) | set(actual)):
        left, right = expected.get(category, {}), actual.get(category, {})
        missing = sorted(set(left) - set(right))
        extra = sorted(set(right) - set(left))
        changed = sorted(key for key in set(left) & set(right) if left[key] != right[key])
        if missing or extra or changed:
            out.append(f'{category}: {len(missing)} missing {missing[:limit]}, {len(extra)} unexpected {extra[:limit]}, '
                       f'{len(changed)} different {changed[:limit]}')
    return out


# -- production-Python entry point -------------------------------------------------------------------------------------
def _django_main(database, leaves_json=None):
    workdir = tempfile.mkdtemp(prefix='az-p6-django-')
    try:
        return _django_run(database, leaves_json, workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _django_run(database, leaves_json, workdir):
    try:
        values = {}
        for line in (ROOT / 'selfhost/.env.example').read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key = Ed25519PrivateKey.generate()
        values.update(
            DJANGO_SETTINGS_MODULE='TESTS.handoff.p6_django_settings', TESTS_DATABASE=database,
            TESTS_WORKDIR=workdir, DBTYPE='POSTGRES', DBHOST=os.environ['P6_DBHOST'],
            DBPORT='5432', DBNAME=database, DBUSER='contracts', DBPASSWORD='', USE_PGBOUNCER='False',
            STORE_ON_DISK='True', JWT_SECRET_KEY_B64='', JWT_PUBLIC_KEY_B64='',
            JWT_SECRET_KEY=key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption()).decode(),
            JWT_PUBLIC_KEY=key.public_key().public_bytes(serialization.Encoding.PEM,
                                                         serialization.PublicFormat.SubjectPublicKeyInfo).decode(),
            EMAIL_HOST_USER='noreply@example.test', GO_TASK_QUEUE_TASKS='')
        os.environ.update(values)
        sys.path[:0] = [str(ROOT / 'BE'), str(ROOT)]
        from unittest.mock import patch
        import django
        with patch('environ.Env.read_env'):
            django.setup()
        from django.apps import apps
        from django.core.management import call_command
    except Exception as error:  # noqa: BLE001 - setup failures are harness errors (exit 3)
        print(f'setup: {type(error).__name__}: {error}', file=sys.stderr)
        return 3
    if leaves_json:
        # A historical release had no operational post_migrate receiver (Phase 6 B1): rebuilding its
        # schema with the current code must not run it.
        from django.db.models.signals import post_migrate
        post_migrate.disconnect(dispatch_uid=OPERATIONAL_RECEIVER)
        labels = {Path(config.path).name: config.label for config in apps.get_app_configs()
                  if Path(config.path).parent == ROOT / 'BE'}
        for directory, name in json.loads(leaves_json):
            call_command('migrate', labels[directory], name, interactive=False, verbosity=0)
        # Third-party apps at their current state (their historical versions are not pinned here).
        local = set(labels.values())
        for config in apps.get_app_configs():
            if config.label not in local and getattr(config, 'models_module', None) is not None:
                try:
                    call_command('migrate', config.label, interactive=False, verbosity=0)
                except Exception as error:  # noqa: BLE001 - apps without migrations
                    if 'does not have migrations' not in str(error):
                        raise
    else:
        call_command('migrate', interactive=False, verbosity=0)
    return 0


if __name__ == '__main__':
    if sys.argv[1:2] == ['django-migrate']:
        raise SystemExit(_django_main(*sys.argv[2:4]))
    raise SystemExit('usage: python -m TESTS.handoff.p6_schema django-migrate DATABASE [LEAVES_JSON]')
