"""Apply Go operational migrations to a parent-owned private database.

Builds and runs the shipped ``cmd/migrate`` with the private environment, then
requires one clean history row at the binary's CurrentVersion. Parent-only.
"""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]


def current_version():
    match = re.search(r'^const CurrentVersion = (\d+)$', (ROOT / 'BE_GO/migrations/migrations.go').read_text(), re.M)
    assert match, 'go-migrations.version-constant'
    return int(match[1])


def apply(work, environment, log):
    binary = Path(work) / 'go-migrate'
    for command in (['go', 'build', '-o', str(binary), './cmd/migrate'], [str(binary)]):
        if subprocess.run(command, cwd=ROOT / 'BE_GO', env=environment, stdout=log, stderr=log, timeout=240).returncode:
            raise AssertionError('go-migrations.' + ('build' if command[0] == 'go' else 'apply'))
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute('SELECT version, dirty FROM public.assozeta_go_schema_migrations')
        rows = cursor.fetchall()
    assert rows == [(current_version(), False)], 'go-migrations.history'
    return {'version': current_version(), 'clean': True}
