"""Parent-only collaborators/add, payment/<uid>/request and invoice/<uid>/send HTTP runner; imports are inert.

Seeds documents, associates, suppliers, invoices and payments (audit
disabled). Every case checks the complete model state: payment request and
invoice send change nothing; an invitation adds exactly one
CollaborationInvites row (owner, email, role, permissions, a 32-hex token,
expiration 90 days after creation, both within the request window) with its
auditlog create entry and index row. The Celery messages on the run's private
broker must be exactly the authored send_mail_async calls; each message body
is rendered here with Django's render_to_string from the authored template and
context, under translation.override(<language>) and the run's
InstanceConfiguration, so the published HTML is compared byte for byte.
Placeholders ('$token', '$email:<actor>', '$name:<actor>', '$online:<actor>',
'$denomination:<own|foreign>', '$app_url', '$whitelabel', '$is_whitelabel',
'$from_email', '$team_email') come from the request's new row, the stored
rows and Django's settings.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import ipaddress
import json
import re
import time

from TESTS.contracts import test_email_render_routes_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/email_render_routes_http.json', 'TESTS/contracts/test_email_render_routes_http.py',
           'TESTS/adapters/django/email_render_routes_http.py', 'BE/application/views/collaborator_views.py',
           'BE/application/views/payment_views.py', 'BE/application/views/invoice_views.py', 'BE/application/utils/api_utils.py',
           'BE/application/models/user_models.py', 'BE/application/models/payment_models.py', 'BE/application/models/invoices_models.py',
           'BE/application/templatetags/instance_branding.py', 'BE/templates/email/_base.html',
           'BE/templates/email/account/email_welcome_collaborator_message.html', 'BE/templates/email/payment/pending.html',
           'BE/templates/email/payment/payment_receipt.html', 'BE/instance/models.py', 'BE/core/tasks.py', 'BE/core/celery.py',
           'BE/core/settings.py', 'BE/instance/restore/task_base.py', 'BE/application/mixin.py', 'BE/application/permissions.py',
           'BE/application/impersonation_scope.py', 'BE/application/audit_resolvers.py', 'BE/application/signals.py',
           'BE/application/permissions_registry.py', 'BE/docmanager/models.py') + harness.SOURCES
INVITES = 'application.CollaborationInvites'
MANAGED = (INVITES, harness.AUDIT, harness.INDEX)


def field_values(model, row):
    values = {}
    for field in model._meta.concrete_fields:
        if field.name not in row:
            continue
        value, kind = row[field.name], field.get_internal_type()
        if value is not None and kind == 'DateTimeField':
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        elif value is not None and kind == 'DateField':
            value = date.fromisoformat(value)
        elif value is not None and kind == 'DecimalField':
            value = Decimal(value)
        values[field.attname] = value
    return values


def seed_rows(source, created):
    from application.models import Associate, Invoice, Payment, SupplierAndCustomers
    from docmanager.models import Document

    for model, rows in ((Document, source['documents'].values()), (Associate, source['associates'].values()),
                        (SupplierAndCustomers, source['suppliers'].values()), (Invoice, source['invoices'].values()),
                        (Payment, source['payments'].values())):
        for row in rows:
            instance = model(**field_values(model, row))
            contract.require(not model._base_manager.filter(pk=instance.pk).exists(), 'fixture.collision')
            model._base_manager.bulk_create([instance])
            created.append((model, instance.pk))


def invites_model():
    from application.models import CollaborationInvites
    return CollaborationInvites


fixture_context = harness.with_celery_broker(harness.fixture_context_for(contract, seed_rows, purge=(invites_model,)))


def prepare(source):
    return {'started': time.time()}


def plain(value):
    if isinstance(value, dict) and set(value) == {'type', 'value'}:
        return value['value']
    return value


def bindings(case, before, after_invite, ids):
    from django.conf import settings
    users = keyed(before['application.User'], 'user_id')
    associations = keyed(before['application.SportAssociation'], 'sport_association_id')
    values = {'$app_url': settings.APP_URL, '$whitelabel': settings.WHITELABEL_NAME, '$is_whitelabel': settings.IS_WHITELABEL,
              '$from_email': settings.DEFAULT_FROM_EMAIL, '$team_email': settings.DEFAULT_TEAM_EMAIL,
              '$denomination:own': associations[ids['own_association']]['denomination'],
              '$denomination:foreign': associations[ids['foreign_association']]['denomination']}
    for actor in ('owner', 'athlete', 'foreign_owner'):
        user = users[ids[actor]]
        values[f'$email:{actor}'] = user['email']
        values[f'$name:{actor}'] = f"{user['first_name']} {user['last_name']}"
        values[f'$online:{actor}'] = user['online_payments'] is True
    if after_invite is not None:
        values['$token'] = after_invite['token']
    return values


def resolve(value, values):
    if isinstance(value, str):
        if value in values:
            return values[value]
        for key in sorted(values, key=len, reverse=True):
            if key in value and isinstance(values[key], str):
                value = value.replace(key, values[key])
        return value
    if isinstance(value, list):
        return [resolve(item, values) for item in value]
    if isinstance(value, dict):
        if set(value) == {'decimal'}:
            return Decimal(value['decimal'])
        if set(value) == {'float_sum'}:
            left, right = value['float_sum']
            return float(Decimal(left)) + float(Decimal(right))
        if set(value) == {'uuid'}:
            return value
        if '$render' in value:
            from django.template.loader import render_to_string
            from django.utils import translation
            with translation.override(value['language']):
                return render_to_string(value['$render'], resolve(value['context'], values))
        return {key: resolve(item, values) for key, item in value.items()}
    return value


def created_changes(model, instance):
    """auditlog's create diff: every stored field against None; None-valued fields
    kept only for foreign keys and DateTimeFields ('None') and JSON fields ('null')."""
    changes = {}
    for field in model._meta.concrete_fields:
        value = getattr(instance, field.attname)
        if value is None:
            if field.is_relation or field.get_internal_type() == 'DateTimeField':
                changes[field.name] = ['None', 'None']
            elif field.get_internal_type() == 'JSONField':
                changes[field.name] = ['None', 'null']
            continue
        if field.get_internal_type() == 'DateTimeField':
            value = str(value.astimezone(timezone.utc).replace(tzinfo=None))
        elif field.get_internal_type() == 'JSONField':
            value = json.dumps(value, sort_keys=True)
        changes[field.name] = ['None', str(value)]
    return changes


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    fixture, ids = context['fixture'], source['ids']
    since, until = context['prepared']['started'], time.time()
    effect = case['effect']
    old, new = keyed(before[INVITES], 'collaboration_invite_id'), keyed(after[INVITES], 'collaboration_invite_id')
    contract.require(all(new.get(k) == v for k, v in old.items()), 'state.invites-kept')
    added = [new[k] for k in set(new) - set(old)]
    invite = None
    if effect is not None and effect['kind'] == 'invite':
        contract.require(len(added) == 1, 'state.invite-count')
        invite = {k: plain(v) for k, v in added[0].items()}
    else:
        contract.require(not added, 'state.no-invite')
    values = bindings(case, before, invite, ids)
    celery = resolve(effect['celery'], values) if effect else []
    harness.celery_published(contract, fixture, celery, since=since, until=until)
    if invite is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    owner = ids[effect.get('owner', 'owner')]
    contract.require(str(invite['user_id']) == owner and invite['email'] == effect['email'] and invite['collaborator_role'] == effect['role']
                     and invite['collaborator_permissions'] == effect['permissions'] and invite['accepted'] is False, 'state.invite')
    contract.require(re.fullmatch(r'[0-9a-f]{32}', invite['token']) is not None, 'state.token')
    created = datetime.fromisoformat(invite['creation_date'].replace('Z', '+00:00')).timestamp()
    expires = datetime.fromisoformat(invite['expiration_date'].replace('Z', '+00:00')).timestamp()
    contract.require(since - 1 <= created <= until + 1 and abs(expires - created - timedelta(days=90).total_seconds()) < 2, 'state.dates')
    audit, indexes = harness.audit_delta(contract, before, after)
    contract.require(len(audit) == 1, 'audit.count')
    event = audit[0]
    model = invites_model()
    stored = model._base_manager.get(pk=invite['collaboration_invite_id'])
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    key = str(invite['collaboration_invite_id'])
    contract.require(event['content_type_id'] == types['collaborationinvites'] and event['action'] == 0 and event['object_pk'] == key
                     and event['object_repr'] == f'CollaborationInvites object ({key})', 'audit.identity')
    contract.require(event['changes'] == created_changes(model, stored), 'audit.changes')
    contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
    session = case['headers'].get('X-Impersonation-Id', '')
    additional = None
    if session.startswith('$session:'):
        label = session[9:]
        additional = {'impersonation_session': fixture['sessions'][label], 'actor_id': actor, 'target_user_id': case['headers']['User-Id'],
                      'association_id': None if label.startswith('admin_') else ids['own_association']}
    contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '', 'audit.context')
    ipaddress.ip_address(event['remote_addr'])
    contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    index = indexes.get(str(event['id']))
    if effect['index'] is None:
        contract.require(not indexes, 'index.none')
    else:
        contract.require(len(indexes) == 1 and index is not None and index['sport_association_id'] == effect['index'][0]
                         and index['resolution_path'] == effect['index'][1], 'index.fields')


run_workload = harness.run_workload_for(contract, assert_effect, prepare=prepare)
