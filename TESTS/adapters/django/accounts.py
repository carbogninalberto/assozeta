"""Read actual Django-created accounts with Go in a private PostgreSQL database.

Current-model schema rehearsal only, not migration-history or login endpoint proof.
No developer environment, shared database, provider or customer account is used.
"""
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
    output = ROOT / 'quality-reports/go-migration/accounts'
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
                if name in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
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
                    from django.core.cache import cache
                    assert cache.get(f'impersonation-upload:{uid(12000)}:{uid(1104)}') is None, 'Go upload-grant revocation not visible in Django'
                    from TESTS.adapters.django.refresh_http import run as refresh_http
                    refresh_contracts = refresh_http(work, log, tool_env, identity_cache)
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
                    from TESTS.adapters.django.password_login import run as password_login
                    password_cases = password_login(work, log, tool_env)
                    from TESTS.adapters.django.second_factor import run as second_factor
                    second_factor_cases = second_factor(work, log, tool_env)
                    from TESTS.adapters.django.login_preparation import run as login_preparation
                    admission_cases = login_preparation(work, log, tool_env)
                connections.close_all()
                report = {'success': True, 'login_png_header_cases': png_header_cases, 'login_png_pixel_cases': png_pixel_cases, 'login_jpeg_file_cases': jpeg_file_cases, 'login_jpeg_metadata_cases': jpeg_metadata_cases, 'login_jpeg_header_cases': jpeg_header_cases, 'login_jpeg_decode_cases': jpeg_decode_cases, 'login_jpeg_cases': jpeg_cases, 'login_image_pixel_cases': image_pixel_cases, 'login_response_cases': response_cases, 'login_finalization_cases': finalization_cases, 'membership_card_configuration_cases': card_configuration_cases, 'association_profile_cases': association_profile_cases, 'login_profile_cases': profile_cases, 'password_admission_cases': admission_cases, 'second_factor_cases': second_factor_cases, 'password_selection_cases': password_cases, 'password_batch_sizes': [1,16,128,129,257], 'concurrent_password_requests': 16, 'refresh_http_contracts_per_backend': refresh_contracts, 'synthetic_accounts': len(before['users']), 'scoped_request_cases': scoped_request_cases, 'scoped_request_batch_sizes': [1,16,128,512,1025], 'concurrent_guard_requests':32, 'scoped_resource_cases': scoped_cases, 'scoped_batch_sizes': [1, 16, 128, 512], 'concurrent_scoped_reads': 32, 'identity_state_transitions': 8, 'concurrent_identity_requests': 32, 'identity_selection_cases': identity_cases, 'actor_authentication_cases': actor_cases, 'schema': 'current Django models; migration history not exercised',
                          'checks': ['Django to Go account fields/nulls', 'soft deletion', 'disabled account distinction',
                                     'legacy duplicate identifiers', 'literal parameter matching', 'context cancellation',
                                     'visitor failure', 'actor/effective/scope separation', 'current impersonation eligibility',
                                     'bounded-pool concurrent reads', 'cross-runtime revocation', 'Django readback unchanged'], 'customer_data': 'none'}
            finally:
                if started:
                    run([binaries / 'pg_ctl', '-D', work / 'postgres', '-m', 'immediate', '-w', 'stop'])
                (output / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
