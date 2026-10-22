"""Packaged scheduler on real Beat schema in private Docker PostgreSQL.

Consumes an existing immutable image; never builds/retags or activates services.
The host uses an ephemeral loopback port only to migrate the actual Beat schema.
Runtime scheduler connections stay on the private Docker network.
"""
import argparse
import contextlib
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.worker_image import Proof, LABEL, wait

EXPECTED={
    'image retains server entrypoint and ships scheduler for nonroot read-only execution',
    'packaged scheduler leaves default Django source and queue unchanged',
    'Linux exclusive lifecycle lease defers packaged publication',
    'packaged scheduler publishes exactly one actual Beat occurrence and exits cleanly on TERM',
}

class SchedulerImage(Proof):
    def scheduler(self,suffix):
        name=self.go(suffix,'/app/scheduler',['--poll-interval','100ms'],detached=True,network=self.network)
        wait(lambda:'scheduler process initialized' in self.logs(name) or not self.running(name),'packaged scheduler initialization')
        assert self.running(name),'packaged scheduler failed initialization'
        assert self.inspect(name,'{{.Config.User}}|{{.HostConfig.ReadonlyRootfs}}|{{json .Config.Healthcheck.Test}}')=='10001:10001|true|["NONE"]'
        return name

    def run(self):
        self.image=self.docker('image','inspect','--format','{{.Id}}',self.image).stdout.strip()
        config=json.loads(self.docker('image','inspect','--format','{{json .Config}}',self.image).stdout)
        assert config['User']=='10001:10001' and config['Entrypoint']==['/app/server']
        assert 'readyz' in ' '.join(config['Healthcheck']['Test'])
        assert 'scheduler' in self.go('presence','/app/scheduler',['--help'],environment=False).lower()
        architecture=self.docker('image','inspect','--format','{{.Architecture}}',self.image).stdout.strip()
        assert architecture in ('arm64','amd64')
        env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
        subprocess.run(['go','build','-o',str(self.probe),str(ROOT/'TESTS/adapters/go/testdata/worker_image_probe.go')],
            cwd=ROOT/'BE_GO',env={**env,'CGO_ENABLED':'0','GOOS':'linux','GOARCH':architecture},check=True,capture_output=True,timeout=120)
        self.probe.chmod(0o755)
        values=dict(DBTYPE='POSTGRES',DBHOST='postgres',DBPORT='5432',DBNAME='worker_contracts',DBUSER='contracts',
            DBPASSWORD='private-image-password-canary',DEBUG='False',SECRET_KEY='private-image-session-canary',
            JWT_SECRET_KEY='fixture',JWT_PUBLIC_KEY='fixture',SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='',SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET='',
            AWS_ACCESS_KEY_ID='fixture',AWS_SECRET_ACCESS_KEY='fixture',AWS_STORAGE_BUCKET_NAME='fixture',AWS_S3_REGION_NAME='us-east-1',
            AWS_S3_ENDPOINT_URL='http://127.0.0.1:9000',AWS_LOCATION='private',DATA_RESTORE_LOCK_PATH='/operations/lifecycle.lock')
        self.environment.write_text(''.join(f'{k}={v}\n' for k,v in values.items()));self.environment.chmod(0o600)
        self.case('image retains server entrypoint and ships scheduler for nonroot read-only execution')
        self.docker('image','inspect',self.postgres)
        self.docker('network','create','--label',LABEL+'='+self.identifier,self.network);self.network_created=True
        self.database=self.identifier+'-postgres';self.containers.append(self.database)
        self.docker('run','--detach','--name',self.database,'--label',LABEL+'='+self.identifier,
            '--network',self.network,'--network-alias','postgres','--publish','127.0.0.1::5432',
            '--tmpfs','/var/lib/postgresql/data','--env','POSTGRES_USER=contracts','--env','POSTGRES_DB=worker_contracts',
            '--env','POSTGRES_HOST_AUTH_METHOD=trust',self.postgres)
        wait(lambda:self.docker('exec',self.database,'pg_isready','-U','contracts','-d','worker_contracts',success=False).returncode==0,'private PostgreSQL')
        binding=json.loads(self.inspect(self.database,'{{json .NetworkSettings.Ports}}'))['5432/tcp'][0]
        assert binding['HostIp']=='127.0.0.1'
        sys.path.insert(0,str(ROOT/'BE'))
        import environ
        os.environ.clear();os.environ.update(env)
        environ.Env.read_env(str(ROOT/'selfhost/.env.example'))
        os.environ.update(DJANGO_SETTINGS_MODULE='core.settings',DEBUG='False',JWT_SECRET_KEY_B64='',JWT_PUBLIC_KEY_B64='',
            JWT_SECRET_KEY='fixture',JWT_PUBLIC_KEY='fixture',DBTYPE='POSTGRES',DBHOST='127.0.0.1',DBPORT=binding['HostPort'],
            DBNAME='worker_contracts',DBUSER='contracts',DBPASSWORD='fixture',USE_PGBOUNCER='False',STORE_ON_DISK='True')
        import django
        with (self.output/'schema.log').open('w') as log,contextlib.redirect_stdout(log),contextlib.redirect_stderr(log),patch('environ.Env.read_env'):
            django.setup()
            from django.core.management import call_command
            call_command('migrate','django_celery_beat',interactive=False,verbosity=0)
            from django_celery_beat.models import IntervalSchedule,PeriodicTask
            from django.utils import timezone
            source=PeriodicTask.objects.create(name='packaged-fixture',task='reset_communication_daily_email_balance',
                interval=IntervalSchedule.objects.create(every=86400,period='seconds'),last_run_at=timezone.now()-datetime.timedelta(days=2))
        from django.db import connections
        connections.close_all()
        self.sql('CREATE TABLE instance_datarestore(state text,completed_at timestamptz);')
        gate=self.barrier('migration-lock','hold-exclusive')
        self.go('migrate','/app/migrate',network=self.network)
        self.stop(gate)
        assert self.sql('SELECT runtime FROM assozeta_go_job_ownership;')=='django'
        scheduler=self.scheduler('django-default')
        wait(lambda:'scheduler inactive' in self.logs(scheduler),'default authority observation')
        assert self.sql('SELECT count(*) FROM assozeta_go_jobs;')=='0'
        assert self.sql('SELECT count(*) FROM assozeta_go_periodic_occurrences;')=='0'
        assert self.sql(f'SELECT total_run_count FROM django_celery_beat_periodictask WHERE id={source.pk};')=='0'
        self.stop(scheduler)
        self.case('packaged scheduler leaves default Django source and queue unchanged')
        gate=self.barrier('maintenance-lock','hold-exclusive')
        # Fixture-only handoff: no Django producer or consumer runs in this network.
        self.sql("UPDATE assozeta_go_job_ownership SET runtime='go',generation=generation+1;")
        scheduler=self.scheduler('go-owner')
        wait(lambda:'scheduler deferred' in self.logs(scheduler),'maintenance deferral')
        assert self.sql('SELECT count(*) FROM assozeta_go_jobs;')=='0'
        self.stop(gate)
        self.case('Linux exclusive lifecycle lease defers packaged publication')
        wait(lambda:self.sql('SELECT count(*) FROM assozeta_go_periodic_occurrences;')=='1','packaged occurrence')
        assert self.docker('exec',scheduler,'id','-u').stdout.strip()=='10001'
        self.docker('exec',scheduler,'sh','-c','test -w /operations/lifecycle.lock')
        self.stop(scheduler)
        assert 'scheduler stopped' in self.logs(scheduler)
        assert self.sql(f'SELECT total_run_count FROM django_celery_beat_periodictask WHERE id={source.pk};')=='1'
        assert self.sql('SELECT count(*) FROM assozeta_go_jobs;')=='1'
        assert self.exclusive(),'exited process retained lifecycle lease'
        self.case('packaged scheduler publishes exactly one actual Beat occurrence and exits cleanly on TERM')
        assert set(self.completed)==EXPECTED and len(self.completed)==len(EXPECTED)


def main():
    require_production_python()
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--image',required=True)
    parser.add_argument('--postgres-image',default='postgres:16-alpine');args=parser.parse_args()
    output=ROOT/'quality-reports/go-migration/scheduler-image';output.mkdir(parents=True,exist_ok=True)
    result={'success':False,'scope':'packaged restricted scheduler on actual Beat schema; fixture-only ownership, no installed handoff'}
    report=output/'results.json';report.write_text(json.dumps(result)+'\n')
    with tempfile.TemporaryDirectory(prefix='az-scheduler-image-',dir='/tmp') as directory:
        work=Path(directory);work.chmod(0o755)
        proof=SchedulerImage(args.image,'unused',args.postgres_image,work,output)
        try:proof.run()
        finally:
            try:
                if 'django.db' in sys.modules:
                    from django.db import connections
                    connections.close_all()
            finally:
                try:proof.cleanup()
                finally:
                    result.update(scenarios=proof.completed);report.write_text(json.dumps(result,indent=2)+'\n')
        assert proof.docker('ps','-aq','--filter','label='+LABEL+'='+proof.identifier).stdout.strip()==''
        assert proof.docker('network','ls','-q','--filter','label='+LABEL+'='+proof.identifier).stdout.strip()==''
        paths=['TESTS/adapters/go/scheduler_image.py','BE_GO/Dockerfile','BE_GO/cmd/scheduler/main.go',
               'BE_GO/internal/repository/schedule_schema.go','BE_GO/internal/service/schedule_loop.go']
        result.update(success=True,image_id=proof.image,scenario_count=len(proof.completed),uid=10001,read_only=True,
            scheduler_http_health_disabled=True,private_resources_removed=True,
            source_hashes={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths})
        report.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))

if __name__=='__main__':main()
