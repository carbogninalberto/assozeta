"""Native auto_mark_attendance worker on actual Django-created current schema.

Private PostgreSQL. Each seeded fixture is first processed by the actual Django
``auto_mark_attendance`` task; the native Go worker must then leave the
identical complete managed-model state on the same reseeded rows (audit ids
and timestamps normalized). Seeds cover each filter of the task, carnet
selection and decrement (int, float, bool lessons, unpaid and finished
carnets, carnets without a course link or subscription), expected absences,
and stored data that makes the task stop midway with earlier saves kept.
Explicit fixture producers and ownership are not installed activation.
"""
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from uuid import UUID, uuid4

import psycopg2

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.worker_process import Rehearsal, await_condition, NIL
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema

DATABASE = 'assozeta_contract_auto_mark_worker'
KIND = 'auto_mark_attendance'
VARIANTS = ('main', 'meta', 'absences', 'registry')
EXPECTED = {
    'default Django owner does not claim or mutate attendance',
    'native worker reproduces the Django task state',
    'unsupported version payload and tenant fail before business effects',
    'completed job replay and a later run leave marked days untouched',
    'malformed carnet meta stops after the earlier saves',
    'malformed expected absences stop before the day is saved',
    'missing lessons registry stops after the decrement is computed',
}


def U(n):
    return UUID(int=0xA7000000 + n)


class AttendanceFixture:
    """Owners, courses, registries, days, course subscriptions and carnets.

    Dates are relative to one base instant shared by every seed; column
    defaults written while seeding (``timezone.now``) are normalized.
    """

    def __init__(self):
        self.base = datetime.now(timezone.utc).replace(microsecond=0)
        self.seeding = (self.base, self.base)

    def snapshot(self):
        from django.apps import apps
        since, until = self.seeding
        def value(item):
            return 'seed-default' if isinstance(item, datetime) and since <= item <= until else item
        return {m._meta.label: [{k: value(v) for k, v in row.items()} for row in m._base_manager.order_by('pk').values()]
                for m in apps.get_models() if m._meta.managed and not m._meta.proxy}

    def seed(self, variant='main'):
        from django.db import connection
        from application.models import User, SportAssociation, AttendanceRegistry, AttendanceDay, Payment
        from application.models.courses_models import Course, CourseSubscription
        from application.models.subscriptions_models import Subscription
        from application.models.carnet_models import CarnetSubscription
        now = self.base
        started = datetime.now(timezone.utc)
        with connection.cursor() as cursor:
            # TRUNCATE resets the heap, so unordered scans return insertion order.
            cursor.execute('''TRUNCATE auditlog_logentry, application_auditlogindex, application_attendanceday,
              application_attendanceregistry, application_carnetsubscription_course_subscription,
              application_carnetsubscription, application_coursesubscription, application_course,
              application_payment, application_subscription, application_sportassociation, bakney_user CASCADE''')
        users = [  # n, role, auto_mark_attendance, deleted
            (1, 1, True, False), (2, 1, False, False), (3, 1, True, True), (4, 1, True, False), (5, 2, True, False),
            (6, 1, True, False)]
        User.original_objects.bulk_create([User(user_id=U(n), username=f'auto-mark-{n}', role=role, auto_mark_attendance=auto,
            deleted=deleted, password='!private-fixture', date_joined=now, email=f'owner{n}@example.test')
            for n, role, auto, deleted in users])
        # Owner 6 has only a deleted association.
        associations = [(10, 6, True), (11, 1, False), (12, 2, False), (13, 3, False), (15, 5, False)]
        SportAssociation.original_objects.bulk_create([SportAssociation(sport_association_id=U(n), user_id=U(owner),
            denomination=f'Auto mark {n}', tax_code='00000000000', deleted=deleted) for n, owner, deleted in associations])
        courses = [  # n, association, status, deleted, title
            (25, 11, 2, False, None), (21, 11, 2, False, 'Corso "Uno" è'), (22, 11, 1, False, 'Archived'),
            (23, 11, 2, True, 'Deleted'), (24, 11, 2, False, 'No registry'), (26, 12, 2, False, 'Not enabled'),
            (27, 13, 2, False, 'Deleted owner'), (28, 15, 2, False, 'Athlete'),
            (29, 10, 2, False, 'Deleted association')]
        Course.objects.bulk_create([Course(course_id=U(n), sport_association_id=U(a), status_flag=s, deleted=d, title=t,
                                           google_background_color='#123456')
                                    for n, a, s, d, t in courses])
        registries = [(31, 21), (32, 21), (35, 25), (36, 22), (37, 23), (38, 26), (39, 27), (40, 28), (34, 29)]
        AttendanceRegistry.objects.bulk_create([AttendanceRegistry(attendance_registry_id=U(n), course_id=U(c))
                                                for n, c in registries])
        hour = timedelta(hours=1)
        days = [  # n, registry, date, title, attendees, expected_absences, auto_marked
            (41, 31, now - 2 * hour, 'Lezione 1', None, None, False),
            (42, 31, now - 3 * hour + timedelta(microseconds=123456), None, [{'course_subscription_id': 'old'}],
             [{'course_subscription_id': str(U(53))}], False),
            (43, 31, now - 30 * hour, 'Outside', None, None, False),
            (44, 31, now + 2 * hour, 'Future', None, None, False),
            (45, 31, now - hour, 'Marked', None, None, True),
            (46, 32, now - hour, 'Second registry', None, None, False),
            (47, 35, now - hour, 'No subscriptions', [{'course_subscription_id': 'x'}], [], False),
            (48, 36, now - hour, 'Archived', None, None, False), (49, 37, now - hour, 'Deleted', None, None, False),
            (50, 38, now - hour, 'Not enabled', None, None, False), (51, 39, now - hour, 'Owner', None, None, False),
            (52, 40, now - hour, 'Athlete', None, None, False), (90, 34, now - hour, 'Deleted association', None, None, False)]
        if variant != 'main':
            days = [days[0], days[6]]
        if variant == 'absences':
            days[0] = days[0][:5] + ([{'course_subscription_id': 'none'}, {'other': 1}],) + days[0][6:]
        AttendanceDay.objects.bulk_create([AttendanceDay(attendance_day_id=U(n), attendance_registry_id=U(r), date=d, title=t,
            attendees=a, expected_absences=e, auto_marked=m) for n, r, d, t, a, e, m in days])
        Payment.objects.bulk_create([Payment(payment_id=U(n), sport_association_id=U(11), paid=paid)
                                     for n, paid in ((95, False), (97, True))])
        subscriptions = [60 + n for n in range(1, 12) if n != 9]
        Subscription.objects.bulk_create([Subscription(subscription_id=U(n), sport_association_id=U(11), user_id=U(1))
                                          for n in subscriptions])
        enrolments = [  # course subscription n, subscription n (None), deleted
            (52, 62, False), (53, 63, False), (51, 61, False), (54, 64, False), (55, 65, False), (56, 66, False),
            (57, 67, False), (58, 68, True), (59, None, False), (60, 70, False)]
        if variant == 'meta':
            enrolments = [(52, 62, False), (61, 71, False), (51, 61, False)]
        if variant in ('absences', 'registry'):
            enrolments = [(51, 61, False), (52, 62, False)]
        CourseSubscription.objects.bulk_create([CourseSubscription(course_subscription_id=U(n), course_id=U(21),
            subscription_id=U(s) if s else None, deleted=d) for n, s, d in enrolments])
        registry = [{'date': 'earlier', 'title': None}]
        carnets = [  # n, subscription, payment, disabled, meta, links, created hours ago
            (72, 62, None, False, {'lessons_left': 3, 'lessons_counter': 10, 'lessons_registry': registry}, [52], 5),
            (73, 63, None, False, {'lessons_registry': [], 'lessons_left': 2.5}, [], 5),
            (74, 64, None, False, {'lessons_left': 5, 'lessons_registry': []}, [54], 9),
            (75, 64, None, False, {'lessons_left': 2, 'lessons_registry': []}, [54], 8),
            (76, 64, None, True, {'lessons_left': 1, 'lessons_registry': []}, [54], 7),
            (77, 65, 95, False, {'lessons_left': 4, 'lessons_registry': []}, [55], 5),
            (78, 66, None, False, {'lessons_left': 0, 'lessons_registry': []}, [56], 5),
            (79, 67, 97, False, {'lessons_left': True, 'lessons_registry': []}, [57], 5),
            (80, None, None, False, {'lessons_left': 1, 'lessons_registry': []}, [], 5),
            (81, 70, None, False, {'lessons_left': 9, 'lessons_registry': []}, [52], 5),
        ]
        if variant == 'meta':
            carnets = carnets[:1] + [(82, 71, None, False, {'lessons_left': '3', 'lessons_registry': []}, [61], 5)]
        if variant == 'absences':
            carnets = carnets[:1]
        if variant == 'registry':
            carnets = [carnets[0], (83, 61, None, False, {'lessons_left': 4, 'lessons_counter': 4}, [51], 5)]
        CarnetSubscription.objects.bulk_create([CarnetSubscription(carnet_subscription_id=U(n), subscription_id=U(s) if s else None,
            payment_id=U(p) if p else None, disabled=d, meta=m) for n, s, p, d, m, _, _ in carnets])
        links = CarnetSubscription.course_subscription.through
        links.objects.bulk_create([links(carnetsubscription_id=U(n), coursesubscription_id=U(cs))
                                   for n, *_, linked, _ in carnets for cs in linked])
        for n, *_, ago in carnets:
            CarnetSubscription.objects.filter(pk=U(n)).update(creation_date=now - ago * hour)
        self.seeding = (started, datetime.now(timezone.utc))
        return self.snapshot()

    @staticmethod
    def normalized(state, since, until):
        """Audit ids, index ids and timestamps vary by run; everything else must match."""
        state = dict(state)
        keys, rows = {}, []
        for entry in state['auditlog.LogEntry']:
            entry = dict(entry)
            assert since <= entry['timestamp'] <= until, 'audit timestamp outside the run window'
            keys[entry['id']] = (entry['object_pk'], json.dumps(entry['changes'], sort_keys=True))
            entry.pop('id'); entry['timestamp'] = 'run'
            rows.append(entry)
        state['auditlog.LogEntry'] = sorted(rows, key=lambda row: (row['object_pk'], json.dumps(row['changes'], sort_keys=True)))
        indexes = []
        for index in state['application.AuditLogIndex']:
            index = dict(index)
            assert since <= index['created_at'] <= until, 'index timestamp outside the run window'
            index.pop('audit_log_index_id'); index['created_at'] = 'run'
            index['log_entry_id'] = keys[index['log_entry_id']]
            indexes.append(index)
        state['application.AuditLogIndex'] = sorted(indexes, key=lambda row: row['log_entry_id'])
        return state


class AutoMarkRehearsal(Rehearsal):
    def __init__(self, work, output, environment, binaries):
        self.work, self.output, self.env, self.binaries = work, output, environment, binaries
        self.children, self.connections, self.completed = [], [], []
        self.connection = self.connect('fixture')
        self.connection.autocommit = True
        self.env_file = work / 'existing.env'
        values = dict(DBTYPE='POSTGRES', DBNAME=DATABASE, DBUSER='contracts', DBHOST=str(work), DBPORT='5432',
            DBPASSWORD='private-worker-password-canary', SECRET_KEY='private-worker-secret-canary', DEBUG='False',
            JWT_SECRET_KEY='fixture', JWT_PUBLIC_KEY='fixture', SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='',
            SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET='', AWS_ACCESS_KEY_ID='fixture', AWS_SECRET_ACCESS_KEY='fixture',
            AWS_STORAGE_BUCKET_NAME='fixture', AWS_S3_REGION_NAME='us-east-1',
            AWS_S3_ENDPOINT_URL='http://127.0.0.1:9000', AWS_LOCATION='private',
            REDIS_HOST='127.0.0.1', REDIS_PORT='1', REDIS_USERNAME='default', REDIS_PASSWORD='unused',
            DATA_RESTORE_LOCK_PATH=str(work / 'operations.lock'))
        self.env_file.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
        self.env_file.chmod(0o600)
        self.fixture = AttendanceFixture()
        self.django = {variant: self.django_expected(variant) for variant in VARIANTS}

    def django_expected(self, variant):
        """Run the actual Django task on a fresh seed."""
        from contextlib import redirect_stdout
        import io
        from application import tasks
        before = self.fixture.seed(variant)
        since = datetime.now(timezone.utc)
        with redirect_stdout(io.StringIO()) as printed:
            assert tasks.auto_mark_attendance.run() is None
        until = datetime.now(timezone.utc)
        after = self.fixture.snapshot()
        assert before != after
        printed = printed.getvalue()
        days = {row['attendance_day_id']: row for row in after['application.AttendanceDay']}
        carnets = {row['carnet_subscription_id']: row for row in after['application.CarnetSubscription']}
        marked = {pk for pk, row in days.items() if row['auto_marked']}
        # Authored checks that the oracle exercised the intended paths.
        if variant == 'main':
            assert '[auto_mark_attendance] OK!' in printed, printed
            assert marked == {U(41), U(42), U(45), U(47)}, marked
            ids = lambda day: [item['course_subscription_id'] for item in days[U(day)]['attendees']]
            assert ids(41) == [str(U(n)) for n in (52, 53, 51, 54, 57, 59, 60)], ids(41)
            assert ids(42) == [str(U(n)) for n in (52, 51, 54, 60)], ids(42)
            assert days[U(41)]['expected_absences'] == [] and days[U(47)]['attendees'] == []
            left = {n: carnets[U(n)]['meta']['lessons_left'] for n in (72, 73, 74, 75, 77, 78, 79, 80, 81)}
            assert left == {72: 1, 73: 1.5, 74: 5, 75: 0, 77: 4, 78: 0, 79: 0, 80: 0, 81: 9}, left
            assert len(carnets[U(72)]['meta']['lessons_registry']) == 3
        elif variant == 'meta':
            assert "error '>' not supported" in printed, printed
            assert marked == {U(47)} and carnets[U(72)]['meta']['lessons_left'] == 2, (marked, carnets[U(72)])
        elif variant == 'absences':
            assert "error 'course_subscription_id'" in printed, printed
            assert marked == {U(47)}, marked
        else:
            assert "error 'lessons_registry'" in printed, printed
            assert marked == {U(47)} and carnets[U(83)]['meta'] == {'lessons_left': 4, 'lessons_counter': 4}
        return self.fixture.normalized(after, since, until)

    def connect(self, name):
        connection = psycopg2.connect(host=str(self.work), user='contracts', dbname=DATABASE,
                                     application_name='fixture-auto-mark-' + name)
        self.connections.append(connection)
        return connection

    def seed_job(self, *, version=1, payload='{}', association=NIL, attempts=3, kind=KIND, variant='main'):
        self.sql('DELETE FROM assozeta_go_jobs')
        before = self.fixture.seed(variant)
        identifier = str(uuid4())
        self.sql('''INSERT INTO assozeta_go_jobs(id,association_id,kind,version,idempotency_key,payload,max_attempts,available_at)
          VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,clock_timestamp())''',
          (identifier, association, kind, version, identifier, payload, attempts))
        self.since = datetime.now(timezone.utc)
        return identifier, before

    def marked_complete(self, identifier, variant='main'):
        await_condition(lambda: self.job(identifier)[0] == 'completed', 'registered auto mark worker durable completion')
        assert self.job(identifier)[:3] == ('completed', 1, '')
        assert self.sql('SELECT result FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(None,)]
        expected = self.django[variant]
        actual = self.fixture.normalized(self.fixture.snapshot(), self.since, datetime.now(timezone.utc))
        if actual != expected:
            differences = {label: {'django': expected.get(label), 'go': actual.get(label)}
                           for label in set(expected) | set(actual) if expected.get(label) != actual.get(label)}
            (self.output / f'difference-{variant}.json').write_text(json.dumps(differences, indent=1, default=str) + '\n')
        assert actual == expected, f'Go auto mark differs from the Django task on the {variant} seed'

    def run(self):
        self.command('migrate')
        assert self.sql('SELECT runtime,generation FROM assozeta_go_job_ownership') == [('django', 1)]
        identifier, before = self.seed_job()
        process = self.worker()
        await_condition(self.observed_claim, 'actual registered worker queue query')
        assert process.poll() is None
        time.sleep(.2)
        assert self.job(identifier)[:2] == ('pending', 0) and self.fixture.snapshot() == before
        self.stop(process)
        self.case('default Django owner does not claim or mutate attendance')

        self.handoff('go')
        process = self.worker()
        self.marked_complete(identifier)
        self.stop(process)
        self.case('native worker reproduces the Django task state')

        for kwargs, code in [({'version': 2}, 'unsupported_version'),
                             ({'payload': '{"unexpected":true}'}, 'task_failed'),
                             ({'association': str(U(11))}, 'task_failed')]:
            identifier, before = self.seed_job(**kwargs)
            process = self.worker()
            await_condition(lambda: self.job(identifier)[0] == 'failed', 'invalid auto mark envelope rejected')
            self.stop(process)
            assert self.job(identifier)[:2] == ('failed', 1)
            assert self.sql('SELECT error_code FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(code,)]
            assert self.fixture.snapshot() == before
        self.case('unsupported version payload and tenant fail before business effects')

        identifier, _ = self.seed_job()
        process = self.worker(); self.marked_complete(identifier); self.stop(process)
        later = self.fixture.snapshot()
        process = self.worker()
        await_condition(self.observed_claim, 'actual registered worker queue query')
        time.sleep(.2); self.stop(process)
        assert self.job(identifier)[:2] == ('completed', 1) and self.fixture.snapshot() == later
        second = str(uuid4())
        self.sql('''INSERT INTO assozeta_go_jobs(id,association_id,kind,version,idempotency_key,payload,max_attempts,available_at)
          VALUES (%s,%s,%s,1,%s,'{}',3,clock_timestamp())''', (second, NIL, KIND, second))
        process = self.worker()
        await_condition(lambda: self.job(second)[0] == 'completed', 'second auto mark run')
        self.stop(process)
        assert self.fixture.snapshot() == later
        self.case('completed job replay and a later run leave marked days untouched')

        for variant, label in (('meta', 'malformed carnet meta stops after the earlier saves'),
                               ('absences', 'malformed expected absences stop before the day is saved'),
                               ('registry', 'missing lessons registry stops after the decrement is computed')):
            identifier, _ = self.seed_job(variant=variant)
            process = self.worker(); self.marked_complete(identifier, variant); self.stop(process)
            self.case(label)
        assert set(self.completed) == EXPECTED and len(self.completed) == len(EXPECTED)


def main():
    require_production_python()
    output = ROOT / 'quality-reports/go-migration/auto-mark-worker-process'
    output.mkdir(parents=True, exist_ok=True)
    report = output / 'results.json'
    result = {'success': False, 'scope': __doc__.strip(), 'schema': 'actual Django current models; no historical migration proof'}
    report.write_text(json.dumps(result) + '\n')
    postgres = shutil.which('postgres') or str(Path('/opt/homebrew/opt/postgresql@16/bin/postgres'))
    if not Path(postgres).is_file() or not shutil.which('go'):
        raise RuntimeError('Private PostgreSQL and Go are required')
    binaries = Path(postgres).parent
    environment = {k: v for k, v in os.environ.items()
                   if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    environment.update(LC_ALL='C', LANG='C')
    with tempfile.TemporaryDirectory(prefix='az-auto-mark-worker-', dir='/tmp') as directory, (output/'services.log').open('w') as log:
        work = Path(directory); attempted = False; rehearsal = None
        def run(args):
            return subprocess.run([str(a) for a in args], env=environment, stdout=log,
                                  stderr=subprocess.STDOUT, check=True, timeout=120)
        try:
            for name in ('worker', 'migrate'):
                subprocess.run(['go', 'build', '-race', '-o', str(work/name), './cmd/'+name], cwd=ROOT/'BE_GO',
                    env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
            run([binaries/'initdb', '-D', work/'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
            attempted = True
            run([binaries/'pg_ctl', '-D', work/'postgres', '-l', work/'postgres.log', '-o', f"-k {work} -h '' -F", '-w', 'start'])
            run([binaries/'createdb', '-h', work, '-U', 'contracts', DATABASE])
            initialize_current_schema(work, work, '5432', DATABASE, log)
            rehearsal = AutoMarkRehearsal(work, output, environment, binaries)
            rehearsal.run()
        finally:
            try:
                if rehearsal:
                    result.update(scenarios=rehearsal.completed, scenario_count=len(rehearsal.completed))
                    rehearsal.close()
            finally:
                try:
                    if 'django.db' in sys.modules:
                        from django.db import connections
                        connections.close_all()
                finally:
                    try:
                        if attempted:
                            try: run([binaries/'pg_ctl', '-D', work/'postgres', '-m', 'immediate', '-w', 'stop'])
                            except subprocess.CalledProcessError:
                                if (work/'postgres/postmaster.pid').exists(): raise
                    finally:
                        if (work/'postgres.log').exists(): shutil.copyfile(work/'postgres.log', output/'postgres.log')
                        report.write_text(json.dumps(result, indent=2)+'\n')
    paths = ['TESTS/adapters/go/auto_mark_worker_process.py', 'TESTS/adapters/go/age_worker_fixture.py',
             'BE_GO/cmd/worker/main.go', 'BE_GO/internal/service/auto_mark_attendance.go',
             'BE_GO/internal/repository/auto_mark_attendance.go', 'BE_GO/internal/domain/auto_mark_attendance.go',
             'BE/application/tasks.py']
    result.update(success=True, private_postgres_stopped=True, race_instrumented=True,
        source_hashes={p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths})
    report.write_text(json.dumps(result, indent=2)+'\n'); print(json.dumps(result))


if __name__ == '__main__':
    main()
