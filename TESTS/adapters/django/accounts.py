"""Read actual Django-created accounts with Go in a private PostgreSQL database.

Current-model schema rehearsal only, not migration-history or login endpoint proof.
No developer environment, shared database, provider or customer account is used.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'BE'), str(ROOT)]


def main():
    from TESTS.adapters.django.production_runtime import require_production_python
    require_production_python()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only',choices=['collaborator-list','impersonation-management','profile-tables','profile-update','profile-password','profile-info','login-http','login-response','medical-orphan-cleanup','subscription-auto-archive','payment-save-inputs','credential-concurrency','payment-description','payment-categories','payment-info','subscription-dates','task-bodies','audit-retention','associate-age-update','subscription-token-cleanup','email-balance-reset','restore-admission','session-auth','refresh','identifiers','two-factor-info','two-factor-update','two-factor-setup','retired-billing'],help='Run the named component with its private schema/identity fixtures; not full account parity')
    options=parser.parse_args()
    output = ROOT / ('quality-reports/go-migration/'+{'collaborator-list':'collaborator-list','impersonation-management':'impersonation-management','profile-tables':'profile-tables','profile-update':'profile-update','profile-password':'profile-password','profile-info':'profile-info','login-http':'login-http','login-response':'image-dispatch-response','medical-orphan-cleanup':'medical-orphan-cleanup','subscription-auto-archive':'subscription-auto-archive','payment-save-inputs':'payment-save-inputs','credential-concurrency':'credential-concurrency','payment-description':'payment-description','payment-categories':'payment-categories','payment-info':'payment-info','subscription-dates':'subscription-dates','task-bodies':'task-bodies','audit-retention':'audit-retention','associate-age-update':'associate-age-update','subscription-token-cleanup':'subscription-token-cleanup','email-balance-reset':'email-balance-reset','restore-admission':'restore-admission','session-auth':'session-auth','refresh':'refresh-http','identifiers':'identifiers','two-factor-info':'two-factor-info','two-factor-update':'two-factor-update','two-factor-setup':'two-factor-setup','retired-billing':'retired-billing'}[options.only] if options.only else 'quality-reports/go-migration/accounts')
    output.mkdir(parents=True, exist_ok=True)
    report = {'success': False, 'phase': 'started'}
    (output / 'results.json').write_text(json.dumps(report) + '\n')
    postgres = shutil.which('postgres')
    if not postgres:
        candidates = sorted(Path('/opt/homebrew/opt').glob('postgresql*/bin/postgres'))
        postgres = str(candidates[-1]) if candidates else None
    if not postgres or not shutil.which('go'):
        raise RuntimeError('PostgreSQL and Go required; account integration is not skipped')
    binaries = Path(postgres).parent
    tool_env = {name: value for name, value in os.environ.items()
                if name in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN',
                            'TESTS_IMPERSONATION_MANAGEMENT_CONCURRENCY', 'TESTS_COLLABORATOR_LIST_GO', 'TESTS_COLLABORATOR_LIST_VERSIONED', 'TESTS_COLLABORATOR_LIST_REPOSITORY', 'TESTS_IMPERSONATION_MANAGEMENT_VERSIONED', 'TESTS_IMPERSONATION_MANAGEMENT_REPOSITORY', 'TESTS_IMPERSONATION_MANAGEMENT_GO', 'TESTS_IMPERSONATION_MANAGEMENT_GO_SOURCE',
                            'TESTS_PAYMENT_CATEGORIES_GO', 'TESTS_PAYMENT_CATEGORIES_VERSIONED',
                            'TESTS_PAYMENT_CATEGORIES_LIFECYCLE', 'TESTS_PAYMENT_INFO_GO',
                            'TESTS_PAYMENT_INFO_REPOSITORY_OVERLAY', 'TESTS_PAYMENT_INFO_VERSIONED')}
    with tempfile.TemporaryDirectory(prefix='az-accounts-', dir='/tmp') as directory:
        work = Path(directory)
        started = False
        with (output / 'services.log').open('w') as log:
            def run(arguments, **kwargs):
                return subprocess.run([str(arg) for arg in arguments], check=True, timeout=180,
                                      stdout=log, stderr=subprocess.STDOUT, **kwargs)
            try:
                run([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts',
                     '--no-sync', '--no-locale', '-E', 'UTF8'])
                run([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log',
                     '-o', f"-k {work} -h '' -F", '-w', 'start'])
                started = True
                run([binaries / 'createdb', '-h', work, '-U', 'contracts', 'assozeta_contract_accounts'])
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
                from cryptography.hazmat.primitives import serialization
                signing_key = Ed25519PrivateKey.generate()
                private_key = signing_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
                public_key = signing_key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
                import environ
                os.environ.clear()
                os.environ.update(tool_env)
                environ.Env.read_env(str(ROOT / 'selfhost/.env.example'))
                os.environ.update(DEBUG='False', JWT_SECRET_KEY_B64='', JWT_PUBLIC_KEY_B64='',
                    JWT_SECRET_KEY=private_key, JWT_PUBLIC_KEY=public_key,
                    DJANGO_SETTINGS_MODULE='TESTS.adapters.django.settings', TESTS_DATABASE='assozeta_contract_accounts',
                    TESTS_WORKDIR=str(work), DBTYPE='POSTGRES', DBHOST=str(work), DBPORT='5432',
                    DBNAME='assozeta_contract_accounts', DBUSER='contracts', DBPASSWORD='test-only',
                    USE_PGBOUNCER='False', STORE_ON_DISK='True')
                import django
                with patch('environ.Env.read_env'):
                    django.setup()
                from django.apps import apps
                from django.conf import settings
                from django.core.management import call_command
                from django.db import connections
                from django.db.models import Q
                from auditlog.context import disable_auditlog
                from application.models import User, SportAssociation, Associate
                from instance.models import InstanceConfiguration
                from auditlog.models import LogEntry
                settings.MIGRATION_MODULES = {app.label: None for app in apps.get_app_configs()}
                call_command('migrate', interactive=False, verbosity=0, run_syncdb=True, stdout=log, stderr=log)
                # Model-schema creation follows django.setup in this adapter. Rebuild
                # the resolver registry as a normal server does after migration.
                from application.audit_resolvers import setup_audit_resolvers
                setup_audit_resolvers()
                if options.only in (None, 'login-http'):
                    from TESTS.adapters.django.login_http import run as login_http
                    login_http_result=login_http(work,log,tool_env)
                    if options.only == 'login-http':
                        connections.close_all()
                        report={'success':True,**login_http_result,'scope':'public login composition; full login, social/image and activation gates remain incomplete'}
                        print(json.dumps(report),flush=True)
                        return
                if options.only == 'login-response':
                    from TESTS.adapters.django.login_response import run as login_response
                    count=login_response(work,log,tool_env)
                    connections.close_all()
                    report={'success':True,'login_response_cases':count,'scope':'response components, not public login'}
                    (output/'results.json').write_text(json.dumps(report)+'\n')
                    print(json.dumps(report),flush=True)
                    return
                if options.only in (None, 'credential-concurrency'):
                    from TESTS.adapters.django.credential_concurrency import run as credential_concurrency
                    credential_concurrency_cases = credential_concurrency(work, log, tool_env)
                    if options.only == 'credential-concurrency':
                        connections.close_all()
                        report = {'success': True, 'scope': 'credential-stage concurrency; public login remains unfinished', 'cases': credential_concurrency_cases}
                        print(json.dumps(report), flush=True)
                        return
                if options.only in (None, 'task-bodies', 'email-balance-reset'):
                    from TESTS.adapters.django.email_balance_reset import run as email_balance_reset
                    email_balance_cases = email_balance_reset(work, log, tool_env)
                    if options.only == 'email-balance-reset':
                        connections.close_all()
                        report = {'success': True, 'scope': 'email balance task body; scheduler and activation not exercised',
                                  'scenario_groups': email_balance_cases, 'schema': 'current Django models; migration history not exercised'}
                        print(json.dumps(report), flush=True)
                        return
                if options.only in (None, 'task-bodies', 'subscription-token-cleanup'):
                    from TESTS.adapters.django.subscription_token_cleanup import run as subscription_token_cleanup
                    token_cleanup_cases = subscription_token_cleanup(work, log, tool_env)
                    if options.only == 'subscription-token-cleanup':
                        connections.close_all()
                        report = {'success': True, 'scope': 'subscription token cleanup body; no scheduler activation', 'cases': token_cleanup_cases}
                        print(json.dumps(report), flush=True)
                        return
                if options.only in (None, 'task-bodies', 'associate-age-update'):
                    from TESTS.adapters.django.associate_age_update import run as associate_age_update
                    age_update_cases = associate_age_update(work, log, tool_env)
                    if options.only == 'associate-age-update':
                        connections.close_all()
                        report = {'success': True, 'scope': 'associate age task body; no scheduler activation', 'cases': age_update_cases}
                        print(json.dumps(report), flush=True)
                        return
                if options.only in (None, 'task-bodies', 'audit-retention'):
                    from TESTS.adapters.django.audit_retention import run as audit_retention
                    audit_retention_cases = audit_retention(work, log, tool_env)
                    if options.only == 'audit-retention':
                        connections.close_all()
                        report = {'success': True, 'scope': 'audit retention task body; no scheduler activation', 'cases': audit_retention_cases}
                        print(json.dumps(report), flush=True)
                        return
                if options.only in (None, 'subscription-dates'):
                    from TESTS.adapters.django.subscription_dates import run as subscription_dates
                    subscription_date_cases = subscription_dates(work, log, tool_env)
                    if options.only == 'subscription-dates':
                        connections.close_all()
                        report = {'success': True, 'scope': 'calendar/default calculation; save and archive parity remain open', 'cases': subscription_date_cases}
                        print(json.dumps(report), flush=True)
                        return
                if options.only in (None, 'payment-description'):
                    from TESTS.adapters.django.payment_description import run as payment_description
                    payment_description_cases = payment_description(work, log, tool_env)
                    if options.only == 'payment-description':
                        connections.close_all()
                        report = {'success': True, 'scope': 'payment description calculation; full save/archive parity remain open', 'cases': payment_description_cases}
                        print(json.dumps(report), flush=True)
                        return
                if options.only in (None, 'payment-save-inputs'):
                    from TESTS.adapters.django.payment_save_inputs import run as payment_save_inputs
                    payment_save_input_cases = payment_save_inputs(work, log, tool_env)
                    if options.only == 'payment-save-inputs':
                        connections.close_all()
                        report = {'success': True, 'scope': 'background payment-save inputs; full save/archive remains open', 'cases': payment_save_input_cases}
                        print(json.dumps(report), flush=True)
                        return
                if options.only in (None, 'task-bodies', 'subscription-auto-archive'):
                    from TESTS.adapters.django.subscription_auto_archive import run as subscription_auto_archive
                    subscription_auto_archive_cases = subscription_auto_archive(work, log, tool_env)
                    if options.only == 'subscription-auto-archive':
                        connections.close_all()
                        report = {'success': True, 'scope': 'automatic archive task body; production scheduling remains open', 'cases': subscription_auto_archive_cases}
                        print(json.dumps(report), flush=True)
                        return
                if options.only == 'task-bodies':
                    connections.close_all()
                    report = {'success': True, 'scope': 'five current-schema task bodies; production ownership and schedules not activated',
                              'email_balance_reset_groups': email_balance_cases, 'subscription_token_cleanup_groups': token_cleanup_cases,
                              'associate_age_update_groups': age_update_cases, 'audit_retention_groups': audit_retention_cases, 'subscription_auto_archive_groups': subscription_auto_archive_cases}
                    print(json.dumps(report), flush=True)
                    return
                if options.only in (None, 'medical-orphan-cleanup'):
                    from TESTS.adapters.django.medical_orphan_cleanup import run as medical_orphan_cleanup
                    medical_orphan_cleanup_cases = medical_orphan_cleanup(work, log, tool_env)
                    if options.only == 'medical-orphan-cleanup':
                        connections.close_all()
                        report = {'success': True, 'scope': 'Django characterization and Go discovery; destructive cleanup remains open', 'cases': medical_orphan_cleanup_cases}
                        print(json.dumps(report), flush=True)
                        return
                from TESTS.adapters.django.social_configuration import run as social_configuration
                social_configuration_cases = social_configuration(work, log, tool_env)
                def uid(number):
                    return f'00000000-0000-4000-8000-{number:012d}'
                with disable_auditlog():
                    owner = User.original_objects.create(user_id=uid(1), username='Owner', email='shared@example.test',
                        role=1, is_superuser=True, password='!synthetic-unusable')
                    User.original_objects.create(user_id=uid(2), username='Staff', email='SHARED@example.test',
                        role=3, connected_user=owner, collaborator_role=3, collaborator_permissions=['association.courses.read'],
                        password='!synthetic-unusable')
                    User.original_objects.create(user_id=uid(3), username='Disabled', email='shared@example.test',
                        is_active=False, password='!synthetic-unusable')
                    User.original_objects.create(user_id=uid(4), username='Deleted', email='shared@example.test',
                        deleted=True, password='!synthetic-unusable')
                    for number, username, email in [(5, 'Literal', 'literal%_@example.test'),
                                                    (6, 'Dup', 'first@example.test'), (7, 'dup', 'second@example.test')]:
                        User.original_objects.create(user_id=uid(number), username=username, email=email, password='!synthetic-unusable')
                # Independently assert the supported Django manager/query behavior.
                def ids(query):
                    return [str(value) for value in query.order_by('user_id').values_list('user_id', flat=True)]
                assert ids(User.objects.filter(email__iexact='SHARED@example.test')) == [uid(1), uid(2), uid(3)]
                assert ids(User.objects.filter(username__iexact='DuP')) == [uid(6), uid(7)]
                assert ids(User.objects.filter(email__iexact='literal%_@example.test')) == [uid(5)]
                assert ids(User.objects.filter(email__iexact="' OR 1=1 --")) == []
                assert ids(User.objects.filter(Q(pk=uid(4)) | Q(pk='00000000-0000-4000-8000-999999999999'))) == []
                print('Django account lookup expectations established in private PostgreSQL', flush=True)
                from TESTS.adapters.django.actor_authentication import credential_fixture
                credentials, actor_cases = credential_fixture(work)
                print(f'Django: {actor_cases} explicit actor authentication cases passed', flush=True)
                from TESTS.adapters.django.identity import identity_fixture
                with identity_fixture(work, log) as (identity_cases_file, identity_cache, identity_cases):
                    from TESTS.adapters.django.scoped_resources import prepare as prepare_scoped_resources
                    scoped_fixture, scoped_cases, scoped_models = prepare_scoped_resources(work)
                    from TESTS.adapters.django.scoped_requests import prepare as prepare_scoped_requests
                    scoped_requests_fixture, scoped_request_cases = prepare_scoped_requests(work)
                    def snapshot():
                        result = {name: list(manager.order_by('pk').values()) for name, manager in (
                            ('users', User.original_objects), ('associations', SportAssociation.original_objects),
                            ('members', Associate.objects.all_objects()), ('instance', InstanceConfiguration.objects),
                            ('audit', LogEntry.objects))}
                        result.update({model._meta.label: list(model._base_manager.order_by('pk').values()) for model in scoped_models})
                        return result
                    before = snapshot()
                    subprocess.run(['go', 'test', '-race', '-tags=integration', './internal/repository', './internal/service'], cwd=ROOT / 'BE_GO',
                        check=True, timeout=120, env={**tool_env, 'ASSOZETA_SCOPED_REQUESTS_FIXTURE': str(scoped_requests_fixture), 'ASSOZETA_SCOPED_RESOURCES_FIXTURE': str(scoped_fixture), 'ASSOZETA_IDENTITY_FIXTURE': str(identity_cases_file), 'ASSOZETA_IDENTITY_CACHE': identity_cache, 'ASSOZETA_ACTOR_CREDENTIAL_FIXTURE': str(credentials), 'ASSOZETA_ACCOUNT_TEST_DSN':
                            f'host={work} port=5432 user=contracts dbname=assozeta_contract_accounts sslmode=disable'})
                    assert snapshot() == before, 'identity reads changed persisted state or left a fixture transition behind'
                    if options.only == 'collaborator-list':
                        from TESTS.adapters.django.collaborator_list import run as collaborator_list
                        result = collaborator_list(work, log, tool_env, identity_cache)
                        connections.close_all()
                        report = {**result, 'schema': 'current Django models; migration history not exercised',
                                  'customer_data': 'none', 'complete_collaborator_parity': False}
                        print(json.dumps({'success': report['success'], 'http_cases': report['http_cases']}), flush=True)
                        return
                    if options.only == 'impersonation-management':
                        from TESTS.adapters.django.impersonation_management import run as impersonation_management
                        result = impersonation_management(work, log, tool_env, identity_cache)
                        connections.close_all()
                        report = {**result, 'schema': 'current Django models; migration history not exercised',
                                  'customer_data': 'none', 'complete_impersonation_parity': False}
                        print(json.dumps(report), flush=True)
                        return
                    if options.only == 'payment-info':
                        from TESTS.adapters.django.payment_info import run as payment_info
                        result = payment_info(work, log, tool_env, identity_cache)
                        connections.close_all()
                        report = {**result, 'schema': 'current Django models; migration history not exercised',
                                  'customer_data': 'none', 'complete_payment_info_parity': False}
                        (output / 'results.json').write_text(json.dumps(report) + '\n')
                        print(json.dumps(report), flush=True)
                        return
                    if options.only == 'payment-categories':
                        from TESTS.adapters.django.payment_categories import run as payment_categories
                        result = payment_categories(work, log, tool_env, identity_cache)
                        connections.close_all()
                        report = {
                            'success': True,
                            **result,
                            'schema': 'current Django models; migration history not exercised',
                            'customer_data': 'none',
                            'complete_payment_category_parity': False,
                        }
                        (output / 'results.json').write_text(json.dumps(report) + '\n')
                        print(json.dumps(report), flush=True)
                        return
                    if options.only == 'profile-tables':
                        from TESTS.adapters.django.profile_tables import run as profile_tables
                        result=profile_tables(work,log,tool_env,identity_cache)
                        connections.close_all()
                        report={'success':True,**result,'schema':'current Django models; migration history not exercised','customer_data':'none'}
                        print(json.dumps(report),flush=True)
                        return
                    if options.only == 'profile-update':
                        from TESTS.adapters.django.profile_update import run as profile_update
                        result=profile_update(work,log,tool_env,identity_cache)
                        connections.close_all()
                        report={'success':True,**result,'schema':'current Django models; migration history not exercised','customer_data':'none'}
                        print(json.dumps(report),flush=True)
                        return
                    if options.only == 'profile-password':
                        from TESTS.adapters.django.profile_password import run as profile_password
                        result=profile_password(work,log,tool_env,identity_cache)
                        connections.close_all()
                        report={'success':True,**result,'schema':'current Django models; migration history not exercised','customer_data':'none'}
                        print(json.dumps(report),flush=True)
                        return
                    if options.only == 'profile-info':
                        from TESTS.adapters.django.profile_info import run as profile_info
                        result=profile_info(work,log,tool_env,identity_cache)
                        connections.close_all()
                        report={'success':True,**result,'schema':'current Django models; migration history not exercised','customer_data':'none'}
                        print(json.dumps(report),flush=True)
                        return
                    if options.only == 'two-factor-setup':
                        from TESTS.adapters.django.two_factor_setup import run as two_factor_setup
                        result=two_factor_setup(work,log,tool_env,identity_cache)
                        connections.close_all()
                        report={'success':True,**result,'schema':'current Django models; migration history not exercised','customer_data':'none'}
                        print(json.dumps(report),flush=True)
                        return
                    if options.only == 'two-factor-update':
                        from TESTS.adapters.django.two_factor_update import run as two_factor_update
                        result=two_factor_update(work,log,tool_env,identity_cache)
                        connections.close_all()
                        report={'success':True,**result,'schema':'current Django models; migration history not exercised','customer_data':'none'}
                        print(json.dumps(report),flush=True)
                        return
                    from TESTS.adapters.django.session_auth import run as session_auth
                    session_auth_cases=session_auth(work,log,tool_env,identity_cache)
                    from TESTS.adapters.django.session_http import run as session_http
                    session_http_contracts=session_http(work,log,tool_env,identity_cache)
                    if options.only == 'session-auth':
                        connections.close_all()
                        report={'success':True,'scope':'session-auth component with supporting account/identity fixtures only',
                                'session_auth_cases':session_auth_cases,'session_http_contracts_per_backend':session_http_contracts,'schema':'current Django models; migration history not exercised','customer_data':'none'}
                        print(json.dumps(report),flush=True)
                        return
                    from TESTS.adapters.django.restore_admission import run as restore_admission
                    restore_admission_contracts=restore_admission(work,log,tool_env,identity_cache)
                    if options.only == 'restore-admission':
                        connections.close_all()
                        report={'success':True,'scope':'restore HTTP admission with supporting account/identity/session fixtures only',
                                'restore_admission_contracts_per_backend':restore_admission_contracts,
                                'schema':'current Django models; migration history not exercised','customer_data':'none'}
                        print(json.dumps(report),flush=True)
                        return
                    from django.core.cache import cache
                    assert cache.get(f'impersonation-upload:{uid(12000)}:{uid(1104)}') is None, 'Go upload-grant revocation not visible in Django'
                    from TESTS.adapters.django.refresh_http import run as refresh_http
                    refresh_contracts = refresh_http(work, log, tool_env, identity_cache)
                    if options.only == 'refresh':
                        connections.close_all()
                        report={'success':True,'scope':'refresh HTTP with supporting account/identity/session fixtures only',
                                'refresh_http_contracts_per_backend':refresh_contracts,'session_auth_cases':session_auth_cases,
                                'session_http_contracts_per_backend':session_http_contracts,
                                'schema':'current Django models; migration history not exercised','customer_data':'none'}
                        print(json.dumps(report),flush=True)
                        return
                    from TESTS.adapters.django.identifier_availability import run as identifier_availability
                    identifier_contracts=identifier_availability(work,log,tool_env,identity_cache)
                    if options.only == 'identifiers':
                        connections.close_all()
                        report={'success':True,'scope':'public identifiers with supporting account/identity/session/refresh fixtures only',
                                'identifier_http_contracts_per_backend':identifier_contracts,'refresh_http_contracts_per_backend':refresh_contracts,
                                'schema':'current Django models; migration history not exercised','customer_data':'none'}
                        print(json.dumps(report),flush=True)
                        return
                    from TESTS.adapters.django.two_factor_info import run as two_factor_info
                    two_factor_info_contracts=two_factor_info(work,log,tool_env,identity_cache)
                    if options.only == 'two-factor-info':
                        connections.close_all()
                        report={'success':True,'scope':'two-factor status with supporting account/identity/session/refresh/identifier fixtures only',
                                'two_factor_info_contracts_per_backend':two_factor_info_contracts,
                                'schema':'current Django models; migration history not exercised','customer_data':'none'}
                        print(json.dumps(report),flush=True)
                        return
                    from TESTS.adapters.django.two_factor_update import run as two_factor_update
                    two_factor_update_result=two_factor_update(work,log,tool_env,identity_cache)
                    from TESTS.adapters.django.two_factor_setup import run as two_factor_setup
                    two_factor_setup_result=two_factor_setup(work,log,tool_env,identity_cache)
                    from TESTS.adapters.django.profile_info import run as profile_info
                    profile_info_result=profile_info(work,log,tool_env,identity_cache)
                    from TESTS.adapters.django.profile_password import run as profile_password
                    profile_password_result=profile_password(work,log,tool_env,identity_cache)
                    from TESTS.adapters.django.profile_update import run as profile_update
                    profile_update_result=profile_update(work,log,tool_env,identity_cache)
                    from TESTS.adapters.django.profile_tables import run as profile_tables
                    profile_tables_result=profile_tables(work,log,tool_env,identity_cache)
                    from TESTS.adapters.django.retired_billing import run as retired_billing
                    retired_billing_contracts=retired_billing(work,log,tool_env,identity_cache)
                    if options.only == 'retired-billing':
                        connections.close_all()
                        report={'success':True,'scope':'retired software billing with supporting account/identity/session/refresh/identifier/2FA fixtures only',
                                'retired_billing_contracts_per_backend':retired_billing_contracts,
                                'schema':'current Django models; migration history not exercised','customer_data':'none'}
                        print(json.dumps(report),flush=True)
                        return
                    from TESTS.adapters.django.login_transport import run as login_transport
                    login_transport_contracts = login_transport(work, log, tool_env)
                    from TESTS.adapters.django.social_login import run as social_login
                    social_cases = social_login(work, log, tool_env)
                    from TESTS.adapters.django.login_profile import run as login_profile
                    profile_cases = login_profile(work, log, tool_env)
                    from TESTS.adapters.django.association_profile import run as association_profile
                    association_profile_cases = association_profile(work, log, tool_env)
                    from TESTS.adapters.django.membership_card_configuration import run as card_configuration
                    card_configuration_cases = card_configuration(work, log, tool_env)
                    from TESTS.adapters.django.login_finalization import run as login_finalization
                    finalization_cases = login_finalization(work, log, tool_env)
                    from TESTS.adapters.django.login_response import run as login_response
                    response_cases = login_response(work, log, tool_env)
                    from TESTS.adapters.django.password_session import run as password_session
                    session_cases = password_session(work, log, tool_env)
                    from TESTS.adapters.django.login_image_pixels import run as login_image_pixels
                    image_pixel_cases = login_image_pixels(work, log, tool_env)
                    from TESTS.adapters.django.login_jpeg import run as login_jpeg
                    jpeg_cases = login_jpeg(work, log, tool_env)
                    from TESTS.adapters.django.login_jpeg_decode import run as login_jpeg_decode
                    from application.utils.api_utils import compress_base64
                    jpeg_decode_cases = login_jpeg_decode(work, log, tool_env, compress_base64)
                    from TESTS.adapters.django.login_jpeg_header import run as login_jpeg_header
                    jpeg_header_cases = login_jpeg_header(work, log, tool_env, compress_base64)
                    from TESTS.adapters.django.login_jpeg_metadata import run as login_jpeg_metadata
                    jpeg_metadata_cases = login_jpeg_metadata(work, log, tool_env, compress_base64)
                    from TESTS.adapters.django.login_jpeg_file import run as login_jpeg_file
                    jpeg_file_cases = login_jpeg_file(work, log, tool_env, compress_base64)
                    from TESTS.adapters.django.login_png_pixels import run as login_png_pixels
                    png_pixel_cases = login_png_pixels(work, log, tool_env, compress_base64)
                    from TESTS.adapters.django.login_png_header import run as login_png_header
                    png_header_cases = login_png_header(work, log, tool_env, compress_base64)
                    from TESTS.adapters.django.login_bilevel import run as login_bilevel
                    bilevel_cases = login_bilevel(work, log, tool_env, compress_base64)
                    from TESTS.adapters.django.login_png_stream import run as login_png_stream
                    png_stream_cases = login_png_stream(work, log, tool_env)
                    from TESTS.adapters.django.login_png_file import run as login_png_file
                    png_file_cases = login_png_file(work, log, tool_env, compress_base64)
                    from TESTS.adapters.django.login_bmp import run as login_bmp
                    bmp_cases = login_bmp(work, log, tool_env, compress_base64)
                    from TESTS.adapters.django.login_ppm import run as login_ppm
                    ppm_cases = login_ppm(work, log, tool_env, compress_base64)
                    from TESTS.adapters.django.login_pcx import run as login_pcx
                    pcx_cases = login_pcx(work, log, tool_env, compress_base64)
                    from TESTS.adapters.django.login_tga import run as login_tga
                    tga_cases = login_tga(work, log, tool_env, compress_base64)
                    from TESTS.adapters.django.login_qoi import run as login_qoi
                    qoi_cases = login_qoi(work, log, tool_env, compress_base64)
                    from TESTS.adapters.django.login_xbm import run as login_xbm
                    xbm_cases = login_xbm(work, log, tool_env, compress_base64)
                    from TESTS.adapters.django.login_sgi import run as login_sgi
                    sgi_cases = login_sgi(work, log, tool_env, compress_base64)
                    from TESTS.adapters.django.login_sun import run as login_sun
                    sun_cases = login_sun(work, log, tool_env, compress_base64)
                    from TESTS.adapters.django.login_gbr import run as login_gbr
                    gbr_cases = login_gbr(work, log, tool_env, compress_base64)
                    from TESTS.adapters.django.login_gif import run as login_gif
                    gif_cases = login_gif(work, log, tool_env, compress_base64)
                    from TESTS.adapters.django.login_ico import run as login_ico
                    ico_cases = login_ico(work, log, tool_env)
                    from TESTS.adapters.django.login_webp import run as login_webp
                    webp_cases = login_webp(work, log, tool_env)
                    from TESTS.adapters.django.login_cur import run as login_cur
                    cur_cases = login_cur(work, log, tool_env)
                    from TESTS.adapters.django.login_pcd import run as login_pcd
                    pcd_cases = login_pcd(work, log, tool_env)
                    from TESTS.adapters.django.login_pixar import run as login_pixar
                    pixar_cases = login_pixar(work, log, tool_env)
                    from TESTS.adapters.django.login_images import run as login_images
                    image_dispatch = login_images(work, log, tool_env)
                    from TESTS.adapters.django.profile_images import run as profile_images
                    profile_image_result = profile_images(work, log, tool_env)
                    from TESTS.adapters.django.login_msp import run as login_msp
                    msp_cases = login_msp(work, log, tool_env, compress_base64)
                    from TESTS.adapters.django.password_login import run as password_login
                    password_cases = password_login(work, log, tool_env)
                    from TESTS.adapters.django.second_factor import run as second_factor
                    second_factor_cases = second_factor(work, log, tool_env)
                    from TESTS.adapters.django.login_preparation import run as login_preparation
                    admission_cases = login_preparation(work, log, tool_env)
                connections.close_all()
                report = {'success': True, 'social_configuration_cases': social_configuration_cases, 'social_admission_cases': social_cases, 'login_transport_contracts_per_backend': login_transport_contracts, 'login_session_cases': session_cases, 'image_dispatch': image_dispatch, 'login_ico_cases': ico_cases, 'login_webp_cases': webp_cases, 'login_cur_cases': cur_cases, 'login_pcd_cases': pcd_cases, 'login_pixar_cases': pixar_cases, 'login_gif_cases': gif_cases, 'login_msp_cases': msp_cases, 'login_gbr_cases': gbr_cases, 'login_sun_cases': sun_cases, 'login_sgi_cases': sgi_cases, 'login_xbm_cases': xbm_cases, 'login_qoi_cases': qoi_cases, 'login_tga_cases': tga_cases, 'login_pcx_cases': pcx_cases, 'login_ppm_cases': ppm_cases, 'login_bmp_cases': bmp_cases, 'login_png_file_cases': png_file_cases, 'login_png_stream_cases': png_stream_cases, 'login_bilevel_cases': bilevel_cases, 'login_png_header_cases': png_header_cases, 'login_png_pixel_cases': png_pixel_cases, 'login_jpeg_file_cases': jpeg_file_cases, 'login_jpeg_metadata_cases': jpeg_metadata_cases, 'login_jpeg_header_cases': jpeg_header_cases, 'login_jpeg_decode_cases': jpeg_decode_cases, 'login_jpeg_cases': jpeg_cases, 'login_image_pixel_cases': image_pixel_cases, 'login_response_cases': response_cases, 'login_finalization_cases': finalization_cases, 'membership_card_configuration_cases': card_configuration_cases, 'association_profile_cases': association_profile_cases, 'login_profile_cases': profile_cases, 'password_admission_cases': admission_cases, 'second_factor_cases': second_factor_cases, 'password_selection_cases': password_cases, 'password_batch_sizes': [1,16,128,129,257], 'concurrent_password_requests': 16, 'refresh_http_contracts_per_backend': refresh_contracts, 'synthetic_accounts': len(before['users']), 'scoped_request_cases': scoped_request_cases, 'scoped_request_batch_sizes': [1,16,128,512,1025], 'concurrent_guard_requests':32, 'scoped_resource_cases': scoped_cases, 'scoped_batch_sizes': [1, 16, 128, 512], 'concurrent_scoped_reads': 32, 'identity_state_transitions': 8, 'concurrent_identity_requests': 32, 'identity_selection_cases': identity_cases, 'actor_authentication_cases': actor_cases, 'schema': 'current Django models; migration history not exercised',
                          'checks': ['Django to Go account fields/nulls', 'soft deletion', 'disabled account distinction',
                                     'legacy duplicate identifiers', 'literal parameter matching', 'context cancellation',
                                     'visitor failure', 'actor/effective/scope separation', 'current impersonation eligibility',
                                     'bounded-pool concurrent reads', 'cross-runtime revocation', 'Django readback unchanged'], 'customer_data': 'none'}
                report['login_http']=login_http_result
                report['restore_admission_contracts_per_backend']=restore_admission_contracts
                report['email_balance_reset_groups']=email_balance_cases
                report['subscription_token_cleanup_cases']=token_cleanup_cases
                report['associate_age_update_cases']=age_update_cases
                report['audit_retention_cases']=audit_retention_cases
                report['subscription_date_cases']=subscription_date_cases
                report['payment_description_cases']=payment_description_cases
                report['payment_save_input_cases']=payment_save_input_cases
                report['subscription_auto_archive_cases']=subscription_auto_archive_cases
                report['medical_orphan_cleanup_cases']=medical_orphan_cleanup_cases
                report['credential_concurrency_cases']=credential_concurrency_cases
                report['retired_billing_contracts_per_backend']=retired_billing_contracts
                report['two_factor_info_contracts_per_backend']=two_factor_info_contracts
                report['two_factor_update']=two_factor_update_result
                report['two_factor_setup']=two_factor_setup_result
                report['profile_info']=profile_info_result
                report['profile_password']=profile_password_result
                report['profile_update']=profile_update_result
                report['profile_tables']=profile_tables_result
                report['profile_images']=profile_image_result
                report['identifier_http_contracts_per_backend']=identifier_contracts
                report['session_auth_cases']=session_auth_cases
                report['session_http_contracts_per_backend']=session_http_contracts
            finally:
                if started:
                    run([binaries / 'pg_ctl', '-D', work / 'postgres', '-m', 'immediate', '-w', 'stop'])
                (output / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
