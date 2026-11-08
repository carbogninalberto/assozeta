"""Parent-only instance administration HTTP runner; imports are inert.

Seeds the singleton InstanceConfiguration (self-hosted, primary association
the shared owner's) with stored email settings, diagnostics, an active
diagnostics lease and a Stripe override; secret markers are encrypted with
the run's SECRET_KEY. Every case is checked against the complete model state:
only that row may change, and a write case states every managed field. Stored
secrets are compared by decrypting them; updated_at must advance exactly when
the view saves it. No auditlog entries are expected (the model is not
registered).
"""
from datetime import datetime

from TESTS.contracts import test_instance_admin_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/instance_admin_http.json', 'TESTS/contracts/test_instance_admin_http.py',
           'TESTS/adapters/django/instance_admin_http.py', 'BE/instance/administration.py', 'BE/instance/operations.py',
           'BE/instance/integrations.py', 'BE/instance/diagnostics.py', 'BE/instance/email_configuration.py',
           'BE/instance/integration_configuration.py', 'BE/instance/operation_limits.py', 'BE/instance/permissions.py',
           'BE/instance/serializers.py', 'BE/instance/models.py', 'BE/instance/updater_client.py',
           'BE/instance/defaults.py', 'BE/application/impersonation.py', 'BE/core/settings.py') + harness.SOURCES
CONFIG = 'instance.InstanceConfiguration'
FIELDS = ('name', 'abbreviation', 'primary_color', 'support_email', 'email_settings', 'email_revision',
          'diagnostic_results', 'integration_settings')
FERNET = '$fernet-integration:'


def seed_rows(source, created):
    from instance.integration_configuration import encrypt_secret
    from instance.models import InstanceConfiguration
    spec = dict(source['instance'])
    contract.require(not InstanceConfiguration._base_manager.exists(), 'fixture.instance-exists')

    def encrypted(value):
        if isinstance(value, dict):
            return {key: encrypted(item) for key, item in value.items()}
        if isinstance(value, str) and value.startswith(FERNET):
            return encrypt_secret(value[len(FERNET):])
        return value
    spec['integration_settings'] = encrypted(spec['integration_settings'])
    spec['primary_association_id'] = source['ids'][spec.pop('primary_association')]
    InstanceConfiguration._base_manager.bulk_create([InstanceConfiguration(**spec)])
    created.append((InstanceConfiguration, spec['id']))


def same(expected, actual, decrypt):
    """Strict JSON equality; {'$secret': plain} / {'$literal': stored} match stored secrets."""
    if isinstance(expected, dict) and set(expected) == {'$secret'}:
        if not isinstance(actual, str):
            return False
        if expected['$secret'] == '':
            return actual == ''
        try:
            return actual != '' and decrypt(actual) == expected['$secret']
        except Exception:
            return False
    if isinstance(expected, dict) and set(expected) == {'$literal'}:
        return actual == expected['$literal']
    if isinstance(expected, dict):
        return isinstance(actual, dict) and set(expected) == set(actual) and all(same(expected[k], actual[k], decrypt) for k in expected)
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(same(e, a, decrypt) for e, a in zip(expected, actual))
    return type(expected) is type(actual) and expected == actual


def moment(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def assert_effect(case, before, after, source, context):
    from instance.email_configuration import decrypt_password
    from instance.integration_configuration import decrypt_secret
    harness.require_unmanaged_unchanged(contract, before, after, (CONFIG,))
    key = str(source['instance']['id'])
    old, new = keyed(before[CONFIG], 'id'), keyed(after[CONFIG], 'id')
    contract.require(set(old) == set(new) == {key}, 'state.instance-row')
    old, new = old[key], new[key]
    effect = case['effect']
    if effect is None:
        contract.require(old == new, 'state.instance-unchanged')
        return
    expected = effect['fields']
    settled = set(FIELDS) | {'email_password_encrypted', 'updated_at'}
    contract.require({k: v for k, v in old.items() if k not in settled} == {k: v for k, v in new.items() if k not in settled},
                     'state.instance-other-fields')
    for field in FIELDS:
        contract.require(same(expected[field], new[field], decrypt_secret), 'state.' + field)
    contract.require(same(expected['email_password'], new['email_password_encrypted'], decrypt_password), 'state.email-password')
    if effect['touched']:
        contract.require(moment(new['updated_at']) > moment(old['updated_at']), 'state.updated-at-advanced')
    else:
        contract.require(new['updated_at'] == old['updated_at'], 'state.updated-at-kept')


fixture_context = harness.fixture_context_for(contract, seed_rows)
run_workload = harness.run_workload_for(contract, assert_effect)
