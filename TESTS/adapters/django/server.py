"""Create a unique PostgreSQL database, serve real HTTP, then drop only that database."""
import json
import os
import platform
import sys
import threading
from datetime import timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'BE'), str(ROOT)]
if os.environ.get('TESTS_NATIVE') == '1':
    import environ
    environ.Env.read_env(str(ROOT / 'selfhost/.env.dev'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'TESTS.adapters.django.settings'

import django
from django.conf import settings
from django.db import connections
from django.core.management import call_command
from django.utils import timezone
import psycopg2
from psycopg2 import sql


def fixture():
    from application.models import User, SportAssociation, BillingPlan, BillingSubscription, Associate, Payment, Course, Group, Instructor
    from instance.models import InstanceConfiguration
    from auditlog.context import disable_auditlog

    def identity(name):
        return uuid5(NAMESPACE_URL, 'assozeta-contract:' + name)

    accounts, associations = {}, {}
    password = 'ContractPass!123'
    with disable_auditlog():
        for name in ('missing', 'expired', 'cancelled', 'paid'):
            account = {'username': name + '@example.test', 'password': password}
            owner = User.objects.create_user(user_id=identity(name), email=account['username'],
                role=User.ASSOCIATION, date_joined=timezone.now() - timedelta(days=60), **account)
            association = SportAssociation.objects.create(sport_association_id=identity(name + '-association'),
                user=owner, denomination='Contract ' + name, tax_code='12345678901', logo='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAMgAAADICAIAAAAiOjnJAAACFElEQVR4nO3UsQ0AIBADMWD/nZ8luALJHiDVKXtmFrx2ni+CsKh4LBLCIiEsEsIiISwSwiIhLBLCIiEsEsIiISwSwiIhLBLCIiEsEsIiISwSwiIhLBLCIiEsEsIiISwSwiIhLBLCIiEsEsIiISwSwiIhLBLCIiEsEsIiISwSwiIhLBLCIiEsEsIiISwSwiIhLBLCIiEsEsIiISwSwiIhLBLCIiEsEsIiISwSwiIhLBLCIiEsEsIiISwSwiIhLBLCIiEsEsIiISwSwiIhLBLCIiEsEsIiISwSwiIhLBLCIiEsEsIiISwSwiIhLBLCIiEsEsIiISwSwiIhLBLCIiEsEsIiISwSwiIhLBLCIiEsEsIiISwSwiIhLBLCIiEsEsIiISwSwiIhLBLCIiEsEsJCWPzDY5EQFglhkRAWCWGREBYJYZEQFglhkRAWCWGREBYJYZEQFglhkRAWCWGREBYJYZEQFglhkRAWCWGREBYJYZEQFglhkRAWCWGREBYJYZEQFglhkRAWCWGREBYJYZEQFglhkRAWCWGREBYJYZEQFglhkRAWCWGREBYJYZEQFglhkRAWCWGREBYJYZEQFglhkRAWCWGREBYJYZEQFglhkRAWCWGREBYJYZEQFglhkRAWCWGREBYJYZEQFglhkRAWCWGREBYJYZEQFglhkRAWCWGREBYJYZEQFglhkRAWCWGREBarcAEhCgSNcJ1nZQAAAABJRU5ErkJggg==')
            accounts[name], associations[name] = account, association
            if name != 'missing':
                plan = BillingPlan.objects.create(name=name, billing_type=1 if name == 'cancelled' else 2)
                BillingSubscription.objects.create(user=owner, billing_plan=plan,
                    ends_on=timezone.now() + timedelta(days=30 if name == 'paid' else -30),
                    auto_renewal=name != 'cancelled')
        owner = associations['missing'].user
        for name, role, grants in (('staff', User.COLLABORATOR, None),
                                  ('restricted', User.COLLABORATOR, ['association.calendar.read']),
                                  ('instructor', User.COLLABORATOR, ['association.courses.read']),
                                  ('athlete', User.ATHLETE, None)):
            account = {'username': name + '@example.test', 'password': password}
            collaborator = User.objects.create_user(user_id=identity(name), email=account['username'], role=role,
                connected_user=owner if role == User.COLLABORATOR else None,
                collaborator_role=User.CUSTOM_COLLABORATOR_ROLE if grants else User.FULL,
                collaborator_permissions=grants, **account)
            if name == 'instructor':
                Instructor.objects.create(user=owner, associated_user_id=collaborator.pk, first_name='Contract instructor')
            accounts[name] = account
        InstanceConfiguration.objects.create(domain='localhost', name='Contract club', self_hosted=True,
            primary_association=associations['missing'], setup_provenance='import', onboarding_completed_at=timezone.now())
        for i in range(100):
            person = Associate.objects.create(associate_id=identity(f'person-{i}'),
                sport_association=associations['missing'], first_name=f'Person {i:03}', last_name='Contract')
            Payment.objects.create(payment_id=identity(f'payment-{i}'), sport_association=associations['missing'],
                associate=person, amount='20.00', description='Contract membership')
        for i in range(10):
            Course.objects.create(course_id=identity(f'course-{i}'), sport_association=associations['missing'], title=f'Course {i}')
        foreign_person = Associate.objects.create(associate_id=identity('foreign-person'),
            sport_association=associations['paid'], first_name='Foreign')
        foreign_group = Group.objects.create(group_id=identity('foreign-group'),
            sport_association=associations['paid'], name='Foreign group')
        Course.objects.create(course_id=identity('foreign-course'),
            sport_association=associations['paid'], title='Foreign course')
    return {'accounts': accounts, 'person_id': str(identity('person-0')), 'foreign_person_id': str(foreign_person.pk),
            'foreign_group_id': str(foreign_group.pk),
            'dataset': {'primary_association': {'people': 100, 'payments': 100, 'courses': 10},
                        'associations': 4, 'accounts': len(accounts)},
            'setup_token': settings.INSTANCE_SETUP_TOKEN}


def main():
    db = settings.DATABASES['default']
    admin = psycopg2.connect(host=db['HOST'], port=db.get('PORT') or 5432,
                            user=db['USER'], password=db['PASSWORD'], dbname='postgres')
    admin.autocommit = True
    cursor = admin.cursor()
    created = False
    try:
        cursor.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(db['NAME'])))
        created = True
        django.setup()
        if os.environ.get('TESTS_REAL_MIGRATIONS') == '1':
            print('Contract database created; applying full migration history.', flush=True)
        else:
            # API contracts need the current schema, not 400+ historical state
            # transitions. The ordinary backend suite and --migrations exercise
            # that history separately. Never apply this setting to a real DB.
            from django.apps import apps
            settings.MIGRATION_MODULES = {app.label: None for app in apps.get_app_configs()}
            print('Contract database created; synchronizing current model schema.', flush=True)
        call_command('migrate', interactive=False, verbosity=0, run_syncdb=True)
        from application.audit_resolvers import setup_audit_resolvers
        setup_audit_resolvers()
        print('Contract schema ready; loading fixtures.', flush=True)
        manifest = fixture() if os.environ.get('TESTS_SCENARIO', 'configured') == 'configured' else {
            'accounts': {}, 'setup_token': settings.INSTANCE_SETUP_TOKEN, 'dataset': {}}
        manifest['environment'] = {'backend': 'Django', 'django': django.get_version(),
            'python': platform.python_version(), 'platform': platform.platform(),
            'http_workers': 1, 'task_workers': 1, 'debug': False, 'sql_profiler': False,
            'schema_setup': 'migrations' if os.environ.get('TESTS_REAL_MIGRATIONS') == '1' else 'current models',
            'database_storage': 'dedicated temporary PostgreSQL; fsync off',
            'storage': 'local temporary files', 'cache': 'in-process', 'email': 'in-memory'}
        (Path(os.environ.get('TESTS_OUTPUT', '/contracts-output')) / 'fixture.json').write_text(json.dumps(manifest))
        from core.celery import app
        worker = app.Worker(pool='solo', concurrency=1, queues=['contracts'], loglevel='WARNING',
                            without_gossip=True, without_mingle=True, without_heartbeat=True)
        worker_thread = threading.Thread(target=worker.start, daemon=True)
        worker_thread.start()
        print('Contract fixtures ready; starting HTTP server.', flush=True)
        import uvicorn
        try:
            uvicorn.run('core.asgi:application', host='0.0.0.0', port=8000,
                fd=int(os.environ['TESTS_HTTP_FD']) if 'TESTS_HTTP_FD' in os.environ else None,
                log_level='warning', access_log=False, workers=1)
        finally:
            worker.stop()
            worker_thread.join(timeout=10)
    finally:
        connections.close_all()
        if created:
            cursor.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(db['NAME'])))
        admin.close()


if __name__ == '__main__':
    main()
