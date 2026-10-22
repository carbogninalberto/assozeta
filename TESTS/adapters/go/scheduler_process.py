"""Native scheduler on actual migrated Beat tables and private PostgreSQL.

Restore/business rows are synthetic effect sentinels. Fixture-only ownership
changes are not an installed handoff protocol. No Compose service is activated.
"""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.worker_process import Rehearsal, await_condition, KIND

EXPECTED = {
    'missing empty and blank file lease refuse readiness',
    'missing operational schema refuses readiness without migration',
    'dirty and unknown operational schema refuse readiness',
    'missing Beat table and required column refuse readiness',
    'invalid bounds fail closed with redacted diagnostics',
    'Django and absent ownership leave actual Beat sources unchanged',
    'two native schedulers publish one occurrence and native worker completes one effect',
    'persisted markerless edits are reread without static seeding',
    'owner loss defers newly due sources until trusted fixture handoff',
    'pending restore and exclusive file lease defer publication',
    'TERM cancels admitted publication and joins cleanup before exit',
    'KILL during commit reconciles durable outcome on restart without duplicate publication',
}

class SchedulerRehearsal(Rehearsal):
    def __init__(self, work, output, environment, binaries):
        self.work, self.output, self.env, self.binaries = work, output, environment, binaries
        self.children, self.connections, self.completed = [], [], []
        self.connection = self.connect('fixture')
        self.connection.autocommit = True
        self.env_file = work / 'existing.env'
        values = dict(DBTYPE='POSTGRES', DBNAME='worker_contracts', DBUSER='contracts',
                      DBPASSWORD='private-worker-password-canary', DBHOST=str(work), DBPORT='5432',
                      DEBUG='False', SECRET_KEY='private-worker-secret-canary', JWT_SECRET_KEY='fixture',
                      JWT_PUBLIC_KEY='fixture', SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='',
                      SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET='', AWS_ACCESS_KEY_ID='fixture',
                      AWS_SECRET_ACCESS_KEY='fixture', AWS_STORAGE_BUCKET_NAME='fixture',
                      AWS_S3_REGION_NAME='us-east-1', AWS_S3_ENDPOINT_URL='http://127.0.0.1:9000',
                      AWS_LOCATION='private', DATA_RESTORE_LOCK_PATH=str(work / 'operations.lock'))
        self.env_file.write_text(''.join(f'{k}={v}\n' for k,v in values.items()))
        self.env_file.chmod(0o600)
        self.sql('CREATE TABLE instance_datarestore (state text, completed_at timestamptz)')
        self.sql('CREATE TABLE communications_communicationconfiguration (id integer PRIMARY KEY,daily_email_balance integer)')
        self.sql('CREATE TABLE fixture_effects (old_balance integer,new_balance integer)')
        self.sql('''CREATE FUNCTION fixture_record_effect() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN INSERT INTO fixture_effects VALUES (OLD.daily_email_balance,NEW.daily_email_balance); RETURN NEW; END $$''')
        self.sql('''CREATE TRIGGER fixture_effect AFTER UPDATE ON communications_communicationconfiguration
          FOR EACH ROW WHEN (OLD.daily_email_balance IS DISTINCT FROM NEW.daily_email_balance)
          EXECUTE FUNCTION fixture_record_effect()''')
        self.history = self.sql('SELECT app,name FROM django_migrations ORDER BY app,name')

    def source(self, name, enabled=True):
        from django_celery_beat.models import IntervalSchedule, PeriodicTask
        from django.utils import timezone
        import datetime
        return PeriodicTask.objects.create(name=name, task=KIND, enabled=enabled,
            interval=IntervalSchedule.objects.create(every=86400, period='seconds'),
            last_run_at=timezone.now()-datetime.timedelta(days=2)).pk

    def receipt_count(self, source):
        return self.sql('SELECT count(*) FROM assozeta_go_periodic_occurrences WHERE periodic_task_id=%s',(source,))[0][0]

    def source_state(self, source):
        return self.sql('SELECT total_run_count,last_run_at FROM django_celery_beat_periodictask WHERE id=%s',(source,))[0]

    def scheduler(self, *arguments):
        log = self.output / f'scheduler-{len(self.children)+1}.log'
        stream = log.open('w')
        process = subprocess.Popen([str(self.work/'scheduler'),'--env-file',str(self.env_file),
            '--poll-interval','50ms','--page-size','2','--tick-timeout','2s','--source-timeout','1s',
            *arguments],env=self.env,stdout=stream,stderr=subprocess.STDOUT)
        self.children.append((process,stream,log))
        await_condition(lambda:'scheduler process initialized' in log.read_text() or process.poll() is not None,'scheduler startup')
        assert process.poll() is None, 'scheduler startup failed: '+log.read_text()
        return process

    def quiet(self, process, source):
        state=self.source_state(source)
        time.sleep(.35)
        assert process.poll() is None
        assert self.source_state(source)==state and self.receipt_count(source)==0

    def published(self, source):
        await_condition(lambda:self.receipt_count(source)==1,'one durable occurrence')
        assert self.source_state(source)[0]==1

    def blocked(self):
        return bool(self.sql("SELECT 1 FROM pg_stat_activity WHERE application_name='assozeta-go-scheduler' AND wait_event='advisory'"))

    def gate(self, deferred):
        self.sql('''CREATE FUNCTION fixture_pause_publication() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN PERFORM pg_advisory_xact_lock(987613); RETURN NEW; END $$''')
        if deferred:
            self.sql('''CREATE CONSTRAINT TRIGGER fixture_pause_publication AFTER INSERT ON assozeta_go_periodic_occurrences
              DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fixture_pause_publication()''')
        else:
            self.sql('''CREATE TRIGGER fixture_pause_publication BEFORE INSERT ON assozeta_go_periodic_occurrences
              FOR EACH ROW EXECUTE FUNCTION fixture_pause_publication()''')
        gate=self.connect('publication-gate')
        with gate.cursor() as c: c.execute('SELECT pg_advisory_xact_lock(987613)')
        return gate

    def ungate(self, gate):
        gate.rollback()
        self.sql('DROP TRIGGER fixture_pause_publication ON assozeta_go_periodic_occurrences')
        self.sql('DROP FUNCTION fixture_pause_publication()')

    def run(self):
        for label,value in [('missing',None),('empty',''),('blank','   ')]:
            path=self.work/(label+'.env')
            lines=[x for x in self.env_file.read_text().splitlines() if not x.startswith('DATA_RESTORE_LOCK_PATH=')]
            if value is not None: lines.append('DATA_RESTORE_LOCK_PATH='+value)
            path.write_text('\n'.join(lines)+'\n')
            failed=self.command('scheduler',success=False,env_file=path)
            assert 'scheduler process initialized' not in failed.stderr
        path=self.work/'missing-volume.env'
        path.write_text(self.env_file.read_text().replace(str(self.work/'operations.lock'),str(self.work/'absent-volume/operations.lock')))
        failed=self.command('scheduler',success=False,env_file=path)
        assert 'scheduler process initialized' not in failed.stderr
        self.case('missing empty and blank file lease refuse readiness')
        failed=self.command('scheduler',success=False)
        assert 'scheduler process initialized' not in failed.stderr
        assert self.sql("SELECT count(*) FROM pg_tables WHERE tablename LIKE 'assozeta_go_%'")==[(0,)]
        self.case('missing operational schema refuses readiness without migration')
        self.command('migrate')
        failed=self.command('scheduler',success=False,env_file=path)
        assert 'scheduler process initialized' not in failed.stderr
        for version,dirty in [(3,True),(2,False),(999,False)]:
            self.sql('UPDATE assozeta_go_schema_migrations SET version=%s,dirty=%s',(version,dirty))
            failed=self.command('scheduler',success=False)
            assert 'scheduler process initialized' not in failed.stderr
        self.sql('UPDATE assozeta_go_schema_migrations SET version=3,dirty=false')
        self.case('dirty and unknown operational schema refuse readiness')
        self.sql('ALTER TABLE django_celery_beat_periodictask RENAME TO fixture_hidden_beat')
        try:
            failed=self.command('scheduler',success=False)
            assert 'scheduler process initialized' not in failed.stderr
        finally: self.sql('ALTER TABLE fixture_hidden_beat RENAME TO django_celery_beat_periodictask')
        self.sql('ALTER TABLE django_celery_beat_periodictask RENAME COLUMN headers TO fixture_hidden_headers')
        try:
            failed=self.command('scheduler',success=False)
            assert 'scheduler process initialized' not in failed.stderr
        finally: self.sql('ALTER TABLE django_celery_beat_periodictask RENAME COLUMN fixture_hidden_headers TO headers')
        self.sql('ALTER TABLE django_celery_beat_periodictask ALTER COLUMN enabled TYPE text USING enabled::text')
        try:
            failed=self.command('scheduler',success=False)
            assert 'scheduler process initialized' not in failed.stderr
        finally:
            self.sql('ALTER TABLE django_celery_beat_periodictask ALTER COLUMN enabled TYPE boolean USING enabled::boolean')
        self.case('missing Beat table and required column refuse readiness')
        for arguments in [('--page-size','0'),('--page-size','65'),('--poll-interval','0s'),
                          ('--source-timeout','3s','--tick-timeout','1s'),('--private-worker-password-canary',)]:
            failed=self.command('scheduler',*arguments,success=False)
            assert 'scheduler process initialized' not in failed.stderr
        self.case('invalid bounds fail closed with redacted diagnostics')

        first=self.source('default-inert')
        process=self.scheduler()
        self.quiet(process,first)
        await_condition(lambda: 'scheduler inactive' in self.children[-1][2].read_text(), 'inactive authority diagnostic')
        assert self.sql('SELECT count(*) FROM assozeta_go_jobs')==[(0,)] and not self.effects()
        assert self.sql('SELECT runtime,generation FROM assozeta_go_job_ownership')==[('django',1)]
        self.sql('DELETE FROM assozeta_go_job_ownership')
        self.quiet(process,first)
        self.stop(process)
        self.sql("INSERT INTO assozeta_go_job_ownership(singleton,runtime,generation) VALUES(1,'django',1)")
        self.case('Django and absent ownership leave actual Beat sources unchanged')
        self.handoff('go')
        self.sql('INSERT INTO communications_communicationconfiguration VALUES(1,9),(2,0),(3,-1)')
        a,b=self.scheduler(),self.scheduler()
        self.published(first)
        time.sleep(.25)
        self.stop(a);self.stop(b)
        assert self.receipt_count(first)==1
        assert self.sql('SELECT count(*) FROM assozeta_go_jobs')==[(1,)]
        job=self.sql('SELECT job_id FROM assozeta_go_periodic_occurrences WHERE periodic_task_id=%s',(first,))[0][0]
        worker=self.worker()
        self.complete(job)
        self.stop(worker)
        self.case('two native schedulers publish one occurrence and native worker completes one effect')

        edited=self.source('markerless-edit',enabled=False)
        marker=self.sql('SELECT last_update FROM django_celery_beat_periodictasks')
        process=self.scheduler()
        self.quiet(process,edited)
        # Django clears last_run_at when saving a disabled row; restore an authored due cursor too.
        self.sql("UPDATE django_celery_beat_periodictask SET enabled=true,last_run_at=clock_timestamp()-interval '2 days' WHERE id=%s",(edited,))
        self.published(edited)
        assert self.sql('SELECT last_update FROM django_celery_beat_periodictasks')==marker
        assert self.sql('SELECT count(*) FROM django_celery_beat_periodictask')==[(2,)]
        self.case('persisted markerless edits are reread without static seeding')
        self.handoff('django')
        held=self.source('owner-loss')
        self.quiet(process,held)
        self.handoff('go')
        self.published(held)
        self.stop(process)
        self.case('owner loss defers newly due sources until trusted fixture handoff')

        self.sql("INSERT INTO instance_datarestore VALUES('running',NULL)")
        held=self.source('pending-restore')
        process=self.scheduler()
        self.quiet(process,held)
        with (self.work/'operations.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            self.sql('DELETE FROM instance_datarestore')
            self.quiet(process,held)
        self.published(held)
        self.stop(process)
        self.case('pending restore and exclusive file lease defer publication')

        gate=self.gate(False)
        held=self.source('term-admitted')
        process=self.scheduler('--source-timeout','5s','--tick-timeout','5s')
        await_condition(self.blocked,'admitted scheduler SQL')
        self.assert_file_lease(True)
        self.stop(process)
        self.assert_file_lease(False)
        assert self.receipt_count(held)==0 and self.source_state(held)[0]==0
        self.ungate(gate)
        process=self.scheduler();self.published(held);self.stop(process)
        self.case('TERM cancels admitted publication and joins cleanup before exit')

        gate=self.gate(True)
        held=self.source('kill-commit')
        process=self.scheduler('--source-timeout','5s','--tick-timeout','5s')
        await_condition(self.blocked,'deferred commit blocked')
        assert self.sql("SELECT count(*) FROM pg_stat_activity WHERE application_name='assozeta-go-scheduler' AND lower(query)='commit' AND wait_event='advisory'")[0][0]==1
        process.kill();assert process.wait(timeout=5)==-signal.SIGKILL
        self.assert_file_lease(False)
        gate.rollback()
        await_condition(lambda:not self.blocked(),'old remote transaction finished')
        before=self.receipt_count(held)
        assert before in (0,1)
        self.ungate(gate)
        process=self.scheduler();self.published(held);time.sleep(.25);self.stop(process)
        assert self.receipt_count(held)==1
        assert self.sql('SELECT count(*) FROM assozeta_go_jobs WHERE id IN (SELECT job_id FROM assozeta_go_periodic_occurrences WHERE periodic_task_id=%s)',(held,))==[(1,)]
        self.case('KILL during commit reconciles durable outcome on restart without duplicate publication')
        assert self.sql('SELECT app,name FROM django_migrations ORDER BY app,name')==self.history
        assert set(self.completed)==EXPECTED and len(self.completed)==len(EXPECTED)


def main():
    require_production_python()
    output=ROOT/'quality-reports/go-migration/scheduler-process'
    output.mkdir(parents=True,exist_ok=True)
    result={'success':False,'scope':'native scheduler; actual Beat schema; synthetic restore/business effects; no installed handoff'}
    report=output/'results.json';report.write_text(json.dumps(result)+'\n')
    environment={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    environment.update(LC_ALL='C',LANG='C')
    postgres=shutil.which('postgres') or '/opt/homebrew/opt/postgresql@16/bin/postgres'
    binaries=Path(postgres).parent
    if not Path(postgres).exists() or not shutil.which('go'): raise RuntimeError('PostgreSQL and Go required')
    with tempfile.TemporaryDirectory(prefix='az-scheduler-',dir='/tmp') as directory, (output/'services.log').open('w') as log:
        work=Path(directory);started=False;rehearsal=None
        def run(args):
            return subprocess.run([str(x) for x in args],env=environment,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=120)
        try:
            for name in ('scheduler','worker','migrate'):
                subprocess.run(['go','build','-race','-o',str(work/name),'./cmd/'+name],cwd=ROOT/'BE_GO',env=environment,
                    stdout=log,stderr=subprocess.STDOUT,check=True,timeout=120)
            run([binaries/'initdb','-D',work/'postgres','-A','trust','-U','contracts','--no-sync','--no-locale','-E','UTF8'])
            started=True
            run([binaries/'pg_ctl','-D',work/'postgres','-l',work/'postgres.log','-o',f"-k {work} -h '' -F",'-w','start'])
            run([binaries/'createdb','-h',work,'-U','contracts','worker_contracts'])
            sys.path.insert(0,str(ROOT/'BE'))
            import environ
            os.environ.clear();os.environ.update(environment)
            environ.Env.read_env(str(ROOT/'selfhost/.env.example'))
            os.environ.update(DJANGO_SETTINGS_MODULE='core.settings',DEBUG='False',JWT_SECRET_KEY_B64='',JWT_PUBLIC_KEY_B64='',
                JWT_SECRET_KEY='fixture',JWT_PUBLIC_KEY='fixture',DBTYPE='POSTGRES',DBHOST=str(work),DBPORT='5432',
                DBNAME='worker_contracts',DBUSER='contracts',DBPASSWORD='fixture',USE_PGBOUNCER='False',STORE_ON_DISK='True')
            import django
            with contextlib.redirect_stdout(log),contextlib.redirect_stderr(log),patch('environ.Env.read_env'):
                django.setup()
                from django.core.management import call_command
                call_command('migrate','django_celery_beat',interactive=False,verbosity=0)
            rehearsal=SchedulerRehearsal(work,output,environment,binaries)
            rehearsal.run()
            result.update(scenarios=rehearsal.completed,scenario_count=len(rehearsal.completed),race_instrumented=True)
        finally:
            try:
                if rehearsal: rehearsal.close()
                if 'django.db' in sys.modules:
                    from django.db import connections
                    connections.close_all()
            finally:
                try:
                    if started: run([binaries/'pg_ctl','-D',work/'postgres','-m','immediate','-w','stop'])
                finally:
                    if (work/'postgres.log').exists():shutil.copyfile(work/'postgres.log',output/'postgres.log')
                    report.write_text(json.dumps(result,indent=2)+'\n')
    paths=['TESTS/adapters/go/scheduler_process.py','BE_GO/cmd/scheduler/main.go',
           'BE_GO/internal/service/schedule_loop.go','BE_GO/internal/service/schedule_page.go',
           'BE_GO/internal/repository/occurrence.go','BE_GO/internal/repository/transaction.go',
           'BE_GO/internal/repository/schedule_schema.go']
    result.update(success=True,private_postgres_stopped=True,source_hashes={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths})
    report.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))

if __name__=='__main__': main()
