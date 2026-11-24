"""Apply the shared operational SQL after Django has migrated the business schema."""
from contextlib import closing
from pathlib import Path
from zlib import crc32

from django.conf import settings
from django.db import connections, router


SQL_DIRECTORY = Path(__file__).with_name('operational_sql')


def instance_migrated(connection, label):
    from django.db.migrations.loader import MigrationLoader
    loader = MigrationLoader(connection, ignore_no_migrations=True)
    leaves = loader.graph.leaf_nodes(label)
    return bool(leaves) and all(leaf in loader.applied_migrations for leaf in leaves)


def apply_operational_schema(sender, using, plan=None, **kwargs):
    connection = connections[using]
    # flush also sends post_migrate, without a migration plan.
    if plan is None or connection.vendor != 'postgresql' or not router.allow_migrate(using, sender.label):
        return
    # Respect deployments using syncdb in place of this app's migration path.
    if settings.MIGRATION_MODULES.get(sender.label, True) is None:
        return
    # Only a migrate that leaves this app fully migrated prepares the operational tables;
    # a partial run (e.g. 'migrate django_celery_beat' in a fixture) leaves them alone.
    if not instance_migrated(connection, sender.label):
        return
    versions = {int(path.name.split('_', 1)[0]): path for path in sorted(SQL_DIRECTORY.glob('*.up.sql'))}
    if not versions:
        raise RuntimeError('Missing packaged operational schema migrations')

    # A session advisory lock must never cross a transaction-mode pooler.
    params = connection.get_connection_params()
    if getattr(settings, 'USE_PGBOUNCER', False):
        params.update(host=settings.DBHOST, port=settings.DBPORT)
    with closing(connection.Database.connect(**params)) as db, db.cursor() as cursor:
        cursor.execute("SET lock_timeout = '10s'")
        cursor.execute("SET statement_timeout = '30s'")
        cursor.execute('SELECT current_database()')
        database, = cursor.fetchone()
        # golang-migrate database.GenerateAdvisoryLockId(database, schema, table).
        name = f'public\0assozeta_go_schema_migrations\0{database}'
        lock_id = (crc32(name.encode()) * 1486364155) & 0xffffffff
        cursor.execute('SELECT pg_advisory_lock(%s)', (lock_id,))
        cursor.execute("SELECT to_regclass('public.assozeta_go_schema_migrations')")
        if cursor.fetchone()[0] is None:
            cursor.execute('CREATE TABLE public.assozeta_go_schema_migrations '
                           '(version bigint NOT NULL PRIMARY KEY, dirty boolean NOT NULL)')
        cursor.execute('SELECT version, dirty FROM public.assozeta_go_schema_migrations')
        history = cursor.fetchall()
        if len(history) > 1 or (history and (history[0][1] or history[0][0] not in versions)):
            raise RuntimeError('Dirty or incompatible operational schema migration history')
        applied = history[0][0] if history else 0
        db.commit()

        for version, path in versions.items():
            if version <= applied:
                continue
            # Persist the dirty fence before executing any DDL, as golang-migrate does.
            cursor.execute('TRUNCATE public.assozeta_go_schema_migrations')
            cursor.execute('INSERT INTO public.assozeta_go_schema_migrations VALUES (%s, true)', (version,))
            db.commit()
            cursor.execute(path.read_text(encoding='utf-8'))
            cursor.execute('UPDATE public.assozeta_go_schema_migrations SET dirty = false WHERE version = %s', (version,))
            db.commit()
