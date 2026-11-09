"""Parent-only instance setup HTTP runner; imports are inert.

service_context owns a private MinIO (the instance-logo image and lifetime)
for the whole run and switches Django's default storage to S3Boto3Storage on
it before either server starts, so the Go server receives the same AWS_*
settings. It also restores Django's default PASSWORD_HASHERS (the contract
settings prefer MD5 for cheap fixture passwords), the production hasher. Each workload starts from an empty bucket; after every case the
complete object listing under AWS_LOCATION (key, size, sha256, ContentType,
CacheControl, no ContentEncoding or user metadata) must equal the case's
'storage'.

Nothing is seeded: the sequence starts without an InstanceConfiguration.
Rows the fresh setup creates (configuration, owner, association, accounts,
onboarding) are purged at cleanup. Reads and refusals must leave the complete
model state unchanged. A configuration write may change only that row: every
managed field is stated and updated_at must advance. The fresh setup is
checked row by row: the owner's password is verified with Django's
check_password (PBKDF2-SHA256, 1,000,000 iterations), every field not set by
the view holds its model default, and the five auditlog entries (actor None)
and three index rows match auditlog's create diff of the stored rows, with the
two values that differed in memory at save time stated explicitly. Its effect
check signs an access token for the created owner ('setup_owner').
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import ipaddress
import os
import re

from TESTS.contracts import test_instance_setup_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/instance_setup_http.json', 'TESTS/contracts/test_instance_setup_http.py',
           'TESTS/adapters/django/instance_setup_http.py', 'TESTS/adapters/django/instance_logo.py',
           'TESTS/adapters/go/payment_category_server.py', 'BE/instance/views.py', 'BE/instance/administration.py',
           'BE/instance/permissions.py', 'BE/instance/serializers.py', 'BE/instance/models.py', 'BE/instance/defaults.py',
           'BE/application/models/user_models.py', 'BE/application/models/balance_sheet_models.py',
           'BE/application/models/utils.py', 'BE/application/audit_resolvers.py', 'BE/application/signals.py',
           'BE/application/impersonation.py', 'BE/core/settings.py') + harness.SOURCES
BUCKET = 'instance-setup-fixture'
CONFIG, USER, ASSOCIATION = 'instance.InstanceConfiguration', 'application.User', 'application.SportAssociation'
ACCOUNTS, ONBOARDING = 'application.CustomAccounts', 'application.UsersOnboarding'
AUDIT, INDEX = harness.AUDIT, harness.INDEX
CREATED = {CONFIG: 1, USER: 1, ASSOCIATION: 1, ACCOUNTS: 2, ONBOARDING: 1, AUDIT: 5, INDEX: 3}
STORAGE = {}


@contextmanager
def service_context(report, log):
    import boto3
    from botocore.config import Config
    from django.conf import global_settings
    from django.test.utils import override_settings
    from TESTS.adapters.django.instance_logo import minio
    source = contract.load_source()
    with minio(log, dict(os.environ), report) as (endpoint, access, secret):
        client = boto3.client('s3', endpoint_url=endpoint, aws_access_key_id=access, aws_secret_access_key=secret,
                              region_name='us-east-1',
                              config=Config(signature_version='s3v4', retries={'max_attempts': 0}, s3={'addressing_style': 'path'}))
        client.create_bucket(Bucket=BUCKET)
        STORAGE.update(client=client, location=source['location'])
        storages = {'default': {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'},
                    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
        try:
            with override_settings(STORAGES=storages, AWS_ACCESS_KEY_ID=access, AWS_SECRET_ACCESS_KEY=secret,
                                   AWS_STORAGE_BUCKET_NAME=BUCKET, AWS_S3_REGION_NAME='us-east-1', AWS_S3_ENDPOINT_URL=endpoint,
                                   AWS_LOCATION=source['location'], AWS_S3_ADDRESSING_STYLE='path',
                                   PASSWORD_HASHERS=global_settings.PASSWORD_HASHERS):
                yield
        finally:
            STORAGE.clear()


def stored_objects():
    client, location = STORAGE['client'], STORAGE['location']
    result = {}
    for item in client.list_objects_v2(Bucket=BUCKET).get('Contents', []):
        key = item['Key']
        contract.require(key.startswith(location + '/'), 'storage.location')
        head = client.head_object(Bucket=BUCKET, Key=key)
        body = client.get_object(Bucket=BUCKET, Key=key)['Body'].read()
        contract.require(head.get('ContentEncoding') is None and not head.get('Metadata'), 'storage.object-metadata')
        result[key[len(location) + 1:]] = {'size': len(body), 'sha256': hashlib.sha256(body).hexdigest(),
                                           'content_type': head.get('ContentType'), 'cache_control': head.get('CacheControl')}
    return result


def clear_storage():
    client = STORAGE['client']
    for item in client.list_objects_v2(Bucket=BUCKET).get('Contents', []):
        client.delete_object(Bucket=BUCKET, Key=item['Key'])
    contract.require(stored_objects() == {}, 'storage.cleared')


def seed_rows(source, created):
    from instance.models import InstanceConfiguration
    contract.require(not InstanceConfiguration._base_manager.exists(), 'fixture.instance-exists')


def models():
    from application.models import CustomAccounts, SportAssociation, User
    from application.models.user_models import UsersOnboarding
    from instance.models import InstanceConfiguration
    return InstanceConfiguration, User, SportAssociation, CustomAccounts, UsersOnboarding


def moment(row, field):
    value = row[field]
    return datetime.fromisoformat((value['value'] if isinstance(value, dict) else value).replace('Z', '+00:00'))


def recent(value):
    now = datetime.now(timezone.utc)
    return now - timedelta(minutes=5) < value <= now + timedelta(seconds=5)


def added(before, after, label):
    """Rows of label added by the case; existing rows must be unchanged."""
    old, new = before[label], after[label]
    contract.require(all(row in new for row in old), 'state.kept:' + label)
    return [row for row in new if row not in old]


def check_config(case, before, after):
    harness.require_unmanaged_unchanged(contract, before, after, (CONFIG,))
    old, new = keyed(before[CONFIG], 'id'), keyed(after[CONFIG], 'id')
    contract.require(len(old) == 1 and set(old) == set(new), 'state.instance-row')
    (old,), (new,) = old.values(), new.values()
    expected = case['effect']['fields']
    for key, value in expected.items():
        if key == 'onboarding_completed':
            contract.require((new['onboarding_completed_at'] is not None) == value, 'state.onboarding')
        else:
            contract.require(type(new[key]) is type(value) and new[key] == value, 'state.' + key)
    settled = set(expected) | {'updated_at', 'onboarding_completed_at'}
    contract.require({k: v for k, v in old.items() if k not in settled} == {k: v for k, v in new.items() if k not in settled},
                     'state.instance-other-fields')
    contract.require(moment(new, 'updated_at') > moment(old, 'updated_at') and recent(moment(new, 'updated_at')),
                     'state.updated-at-advanced')
    if case['response'].get('logo_url') == '$logo-version':
        from instance.models import InstanceConfiguration
        stamp = InstanceConfiguration.objects.get().updated_at.astimezone(timezone.utc).strftime('%Y%m%d%H%M%S%f')
        contract.require(contract.RUNTIME['response']['logo_url'] == '/api/instance/logo.png?v=' + stamp, 'response.logo-version')


def defaults_kept(instance, explicit, code):
    """Every concrete field the view does not set holds its model default."""
    for field in instance._meta.concrete_fields:
        if field.attname in explicit:
            continue
        contract.require(getattr(instance, field.attname) == field.get_default(), f'{code}.default:{field.attname}')


def audit_changes(instance, overrides=None, absent=()):
    from auditlog.diff import model_instance_diff
    changes = {key: list(value) for key, value in model_instance_diff(None, instance).items() if key not in absent}
    changes.update(overrides or {})
    return changes


def check_configure(case, before, after, context):
    from auditlog.models import LogEntry
    from django.contrib.auth.hashers import check_password, get_hashers, identify_hasher
    from django.contrib.contenttypes.models import ContentType
    from rest_framework_simplejwt.tokens import AccessToken
    Config, User, Association, Accounts, Onboarding = models()
    managed = tuple(CREATED)
    harness.require_unmanaged_unchanged(contract, before, after, managed)
    for label, count in CREATED.items():
        contract.require(len(added(before, after, label)) == count, 'state.created:' + label)
    effect = case['effect']
    config = Config.objects.get()
    for key, value in effect['config'].items():
        actual = config.onboarding_completed_at is not None if key == 'onboarding_completed' else getattr(config, key)
        contract.require(type(actual) is type(value) and actual == value, 'configure.config:' + key)
    contract.require(recent(config.configured_at) and config.configured_at <= config.updated_at and recent(config.updated_at),
                     'configure.config-times')
    defaults_kept(config, {'id', 'configured_at', 'updated_at', 'primary_association_id', 'onboarding_completed_at', *effect['config']},
                  'configure.config')
    association = Association.objects.get(pk=config.primary_association_id)
    contract.require(contract.RUNTIME['response']['association_id'] == str(association.pk), 'configure.association-id')
    user = User.original_objects.get(pk=association.user_id)
    # The stored hash is Django's: PBKDF2-SHA256 at 1,000,000 iterations, and
    # check_password accepts the (trimmed) setup password.
    algorithm, iterations, salt, digest = user.password.split('$')
    default = get_hashers()[0]
    contract.require(default.algorithm == 'pbkdf2_sha256' and identify_hasher(user.password).algorithm == default.algorithm
                     and algorithm == default.algorithm and iterations == str(default.iterations) == '1000000'
                     and len(salt) >= 22 and salt.isalnum() and len(digest) == 44, 'configure.password-format')
    contract.require(check_password(effect['owner']['password'], user.password)
                     and not check_password('  ' + effect['owner']['password'], user.password), 'configure.password')
    contract.require(user.email == effect['owner']['email'] and re.fullmatch(r'[0-9a-f]{20}', user.username)
                     and user.role == User.ASSOCIATION and user.is_active is True and user.is_staff is False
                     and user.is_superuser is False and user.last_login is None and recent(user.date_joined), 'configure.user')
    defaults_kept(user, {'user_id', 'password', 'username', 'email', 'role', 'is_active', 'is_staff', 'is_superuser',
                         'last_login', 'date_joined'}, 'configure.user')
    contract.require(association.denomination == effect['association']['denomination'] and association.tax_code == ''
                     and association.subscription_fee == Decimal('20.00') and association.membership_fee == Decimal('20.00'),
                     'configure.association')
    defaults_kept(association, {'sport_association_id', 'user_id', 'denomination', 'tax_code', 'subscription_fee',
                                'membership_fee'}, 'configure.association')
    accounts = {account.name: account for account in Accounts.objects.filter(sport_association=association)}
    contract.require(set(accounts) == {'cassa', 'banca'}, 'configure.accounts')
    for name, kind in (('cassa', 1), ('banca', 2)):
        account = accounts[name]
        contract.require(account.initial_balance == Decimal('0.00') and account.account_type == kind and account.account_code == name
                         and account.editable is False, 'configure.account:' + name)
        defaults_kept(account, {'custom_account_id', 'name', 'initial_balance', 'account_type', 'account_code', 'editable',
                                'sport_association_id'}, 'configure.account')
    (onboarding,) = Onboarding.objects.filter(user=user)
    defaults_kept(onboarding, {'users_onboarding_id', 'user_id'}, 'configure.onboarding')
    # Five create entries in save order. The user's reverse one-to-one
    # association did not exist when it was saved, and the accounts' balance
    # was the int 0 in memory.
    created = [(user, audit_changes(user, absent=('sportassociation',)), None),
               (association, audit_changes(association), 'self'),
               (accounts['cassa'], audit_changes(accounts['cassa'], {'initial_balance': ['None', '0']}), 'direct'),
               (accounts['banca'], audit_changes(accounts['banca'], {'initial_balance': ['None', '0']}), 'direct'),
               (onboarding, audit_changes(onboarding), None)]
    events = list(LogEntry.objects.filter(pk__in=[row['id'] for row in added(before, after, AUDIT)]).order_by('pk'))
    indexes = {str(row['log_entry_id']): row for row in (keyed(added(before, after, INDEX), 'audit_log_index_id').values())}
    remote = None
    for event, (instance, changes, path) in zip(events, created, strict=True):
        contract.require(event.content_type_id == ContentType.objects.get_for_model(type(instance)).id and event.action == 0
                         and event.object_pk == str(instance.pk) and event.object_id is None
                         and event.object_repr == str(instance), 'audit.identity')
        contract.require(event.changes == changes, 'audit.changes:' + type(instance).__name__)
        contract.require(event.actor_id is None and event.actor_email is None and event.cid == case['name']
                         and event.additional_data is None and event.changes_text == '' and event.serialized_data is None,
                         'audit.context')
        ipaddress.ip_address(event.remote_addr)
        remote = remote or event.remote_addr
        contract.require(event.remote_addr == remote and recent(event.timestamp), 'audit.remote-time')
        index = indexes.get(str(event.pk))
        if path is None:
            contract.require(index is None, 'index.absent')
        else:
            contract.require(index is not None and str(index['sport_association_id']) == str(association.pk)
                             and index['resolution_path'] == path, 'index.fields')
    contract.require(len(indexes) == 3, 'index.count')
    token = AccessToken()
    token['user_id'] = str(user.pk)
    context['fixture']['tokens']['setup_owner'] = str(token)


def assert_effect(case, before, after, source, context):
    contract.require(stored_objects() == case['storage'], 'storage.objects')
    effect = case['effect']
    if effect is None:
        contract.require(before == after, 'state.unchanged')
    elif effect['kind'] == 'config':
        check_config(case, before, after)
    else:
        check_configure(case, before, after, context)


# The harness purges the UUID-keyed rows the setup creates; the integer-keyed
# configuration row (none exists before the run) is removed here first.
_fixture_context = harness.fixture_context_for(contract, seed_rows, purge=tuple((lambda index=index: models()[index]) for index in range(1, 5)))


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    with _fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
        try:
            yield fixture
        finally:
            from auditlog.context import disable_auditlog
            with disable_auditlog():
                models()[0]._base_manager.all().delete()
_run_workload = harness.run_workload_for(contract, assert_effect)


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from django.conf import settings
    clear_storage()
    contract.RUNTIME.clear()
    contract.RUNTIME['setup_token'] = settings.INSTANCE_SETUP_TOKEN
    try:
        return _run_workload(label, backend, base_url, fixture, apps, raw, observer)
    finally:
        contract.RUNTIME.clear()
        clear_storage()
