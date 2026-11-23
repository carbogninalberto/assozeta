"""Phase 6 milestone B: one shared schema, built by either runtime's migration path (CONTRACT P6-B v1).

Boundary: the shipped migrations themselves on a private PostgreSQL cluster — Django's
`manage.py migrate` under the production Python and the Go migrator built from BE_GO/cmd/migrate —
compared database to database by TESTS/handoff/p6_schema.py (tables, columns, named constraints,
indexes, sequences and their values, functions, triggers, Django and Go migration histories,
content types and permissions by natural key, and every row). Failures read 'P6B contract red: ...'.

Reference = what an installation gets today: Django migrations, then the Go operational
migrations. Milestone B requires each path alone to produce exactly that database:
  - Go mode: `/app/migrate` (cmd/migrate) creates and upgrades the business schema, runs the
    historical data migrations, records the Django migration history (so a later Django-mode
    lifecycle sees nothing to apply) and creates the same content types and permissions, besides
    the operational tables it owns today. It never needs a Django container.
  - Django mode: `manage.py migrate` also prepares the shared operational tables (and records
    them in public.assozeta_go_schema_migrations as golang-migrate does), without a Go command.
Both on an empty database and from each supported upgrade baseline (p6_schema.BASELINES: the
released v1.0.2, v1.0.5, v1.0.17, v1.0.18 and the plan base 2284ed46) with legacy rows whose
identifiers, sequences and later data migrations must come out identical.

Needs the production Python with the BE requirements (ASSOZETA_HANDOFF_PYTHON), the Go toolchain
and the PostgreSQL server binaries. Run: python3 -B -m unittest -v TESTS.handoff.test_p6_schema
"""
from pathlib import Path
import shutil
import tempfile
import unittest

from TESTS.handoff import p5_harness as h5
from TESTS.handoff import p6_harness as h
from TESTS.handoff import p6_schema as s

RED = 'P6B contract red'
STATE = {}


def setUpModule():
    STATE['work'] = Path(tempfile.mkdtemp(prefix='az-p6-schema-'))
    STATE['cluster'] = s.Cluster(h5.services())
    STATE['references'] = {}


def tearDownModule():
    try:
        h.stop_services()
    finally:
        shutil.rmtree(STATE.get('work', ''), ignore_errors=True)


def cluster():
    return STATE['cluster']


def reference(baseline=None):
    """The database an installation reaches today (from empty or from `baseline`), and its snapshot."""
    key = baseline or 'empty'
    if key not in STATE['references']:
        name = cluster().create(f'{s.PREFIX}reference_{len(STATE["references"])}', template=base(baseline))
        code, output = s.django_migrate(cluster(), name)
        if code:
            raise s.SchemaHarnessError(f'reference Django migrations failed: {output}')
        s.operational_sql(cluster(), name)
        STATE['references'][key] = (name, s.snapshot(cluster(), name, preserved(baseline)))
    return STATE['references'][key]


def preserved(baseline):
    """UUIDs of the database the migrations start from (none for an empty database)."""
    if baseline is None:
        return frozenset()
    cache = STATE.setdefault('preserved', {})
    if baseline not in cache:
        cache[baseline] = s.existing_uuids(cluster(), base(baseline))
    return cache[baseline]


def base(baseline):
    if baseline is None:
        return None
    bases = STATE.setdefault('bases', {})
    if baseline not in bases:
        bases[baseline] = s.build_baseline(cluster(), baseline, STATE['work'])
    return bases[baseline]


class SharedSchema(h.ContractB, unittest.TestCase):
    maxDiff = None

    def candidate(self, path, baseline=None):
        STATE['n'] = STATE.get('n', 0) + 1
        name = cluster().create(f'{s.PREFIX}{path}_{STATE["n"]}', template=base(baseline))
        if path == 'go':
            code, output = s.go_migrate(cluster(), name, STATE['work'])
        else:
            code, output = s.django_migrate(cluster(), name)
        return name, code, output

    def compare(self, path, baseline=None):
        _, expected = reference(baseline)
        name, code, output = self.candidate(path, baseline)
        label = f'the {path} migration path {"on an empty database" if baseline is None else "from " + baseline}'
        self.assertEqual(code, 0, f'{RED}: {label} failed: {output}')
        found = s.differences(expected, s.snapshot(cluster(), name, preserved(baseline)))
        self.assertEqual(found, [], f'{RED}: {label} does not build the shared schema:\n' + '\n'.join(found))

    def test_two_independent_reference_builds_are_identical(self):
        """Control (green guard): the comparator does not see two runs of the same path as different.

        Data migrations mint random UUIDs on every run (application 0230 add_enti_feature); everything else
        must compare exactly, so this also proves the normalization is limited to those values."""
        _, expected = reference()
        name, code, output = self.candidate('django')
        if code:
            raise s.SchemaHarnessError(f'control Django migrations failed: {output}')
        s.operational_sql(cluster(), name)
        found = s.differences(expected, s.snapshot(cluster(), name))
        self.assertEqual(found, [], 'two independent reference builds differ:\n' + '\n'.join(found))

    def test_go_migrator_builds_the_whole_schema_on_an_empty_database(self):
        self.compare('go')

    def test_django_migrations_prepare_the_operational_tables_on_an_empty_database(self):
        self.compare('django')


def _baseline_test(path, baseline):
    def test(self):
        self.compare(path, baseline)
    name = baseline.replace('.', '_')
    test.__name__ = f'test_{path}_migration_path_upgrades_from_{name}'
    test.__doc__ = f'{path} path from {baseline} with legacy data'
    return test


for _baseline in s.BASELINES:
    for _path in ('go', 'django'):
        _test = _baseline_test(_path, _baseline)
        setattr(SharedSchema, _test.__name__, _test)


if __name__ == '__main__':
    unittest.main()
