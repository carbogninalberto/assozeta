"""Parent-only instance administration HTTP runner; imports are inert.

Seeds the singleton InstanceConfiguration (self-hosted, primary association
the shared owner's) with stored email settings, diagnostics, an active
diagnostics lease and a Stripe override; secret markers are encrypted with
the run's SECRET_KEY. Every case is checked against the complete model state:
only that row may change, and a write case states every managed field. Stored
secrets are compared by decrypting them; updated_at must advance exactly when
the view saves it. The model is not registered with auditlog; the only entry
is the explicit UPDATE log of a web UI API version change (case['audit']),
with its AuditLogIndex row resolved from the actor's association.
"""
import ipaddress
import time
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
AUDIT, INDEX = harness.AUDIT, harness.INDEX
FIELDS = ('name', 'abbreviation', 'primary_color', 'support_email', 'ui_api_version', 'email_settings', 'email_revision',
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
    harness.require_unmanaged_unchanged(contract, before, after, (CONFIG, AUDIT, INDEX))
    check_audit(case, before, after, source, context)
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


def check_audit(case, before, after, source, context):
    """No entry, or the one UPDATE entry of a web UI API version change."""
    added, indexes = harness.audit_delta(contract, before, after)
    expected = case.get('audit')
    contract.require(len(added) == (1 if expected else 0), 'audit.count')
    if not expected:
        contract.require(not indexes, 'audit.index-count')
        return
    event = added[0]
    types = {(ct['app_label'], ct['model']): ct['id'] for ct in before['contenttypes.ContentType']}
    instance = source['instance']
    after_row = keyed(after[CONFIG], 'id')[str(instance['id'])]
    actor = source['ids'][expected['actor']]
    contract.require(event['content_type_id'] == types[('instance', 'instanceconfiguration')] and event['action'] == 1
                     and event['object_pk'] == str(instance['id']) and event['object_id'] == instance['id']
                     and event['object_repr'] == f"Instance: {after_row['name']} ({instance['domain']})"
                     and event['serialized_data'] is None, 'audit.identity')
    contract.require(event['changes'] == expected['changes'] and event['changes_text'] == '', 'audit.changes')
    contract.require(str(event['actor_id']) == actor and event['actor_email'] is None, 'audit.actor')
    contract.require(event['cid'] == case['name'], 'audit.cid')
    contract.require(event['additional_data'] == {'actor_id': actor}, 'audit.additional-data')
    ipaddress.ip_address(event['remote_addr'])
    contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    stamp = event['timestamp']
    stamp = datetime.fromisoformat(stamp.replace('Z', '+00:00')) if isinstance(stamp, str) else stamp
    contract.require(stamp.tzinfo is not None and time.time() - 600 <= stamp.timestamp() <= time.time() + 1, 'audit.timestamp')
    if expected['association'] is None:
        contract.require(not indexes, 'index.none')
    else:
        index = indexes.get(str(event['id']))
        contract.require(index is not None and len(indexes) == 1
                         and str(index['sport_association_id']) == source['ids'][expected['association']]
                         and index['resolution_path'] == 'actor.sport_association', 'index.fields')
