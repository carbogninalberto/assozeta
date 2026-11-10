"""Parent-only course enrolment HTTP runner; imports are inert.

Seeds the course-writes shapes (associates, subscriptions, courses, payments,
course subscriptions, installments) plus a payment category and the own
association's default bank account (audit disabled), and verifies each case
against the complete model state like course_writes_http:
- seeded rows change exactly by the authored columns;
- created rows (authored with every column; '@name' binds a generated key,
  pre-bound from the response's placeholders, '$now' a timestamp of the
  request, '{today:FMT}' the request's UTC date, '$category' / '$bank_far'
  Payment.save()'s default category and the foreign association's default
  bank account) must be exactly the new rows;
- membership links change by the authored pairs;
- the new auditlog entries match the authored sequence in order, with their
  index rows ('$stored' changes: auditlog's create diff of the stored row with
  in-memory overrides).
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import ipaddress
import re

from TESTS.contracts import test_course_enrolment_http as contract
from TESTS.adapters.django import course_writes_http as writes
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import default_payment_category
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/course_enrolment_http.json', 'TESTS/contracts/test_course_enrolment_http.py',
           'TESTS/adapters/django/course_enrolment_http.py', 'TESTS/adapters/django/course_writes_http.py',
           'TESTS/adapters/django/carnet_replace_http.py', 'TESTS/adapters/django/camps_periods_http.py',
           'TESTS/contracts/test_course_subscriptions_http.py', 'TESTS/fixtures/course_subscriptions_http.json',
           'BE/application/views/course_views.py', 'BE/application/serializers/courses_serializers.py',
           'BE/application/serializers/payment_serializers.py', 'BE/application/serializers/subscriptions_serializers.py',
           'BE/application/serializers/user_serializers.py', 'BE/application/utils/subscriptions_utils.py',
           'BE/application/models/courses_models.py', 'BE/application/models/payment_models.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/models/user_models.py',
           'BE/application/models/balance_sheet_models.py', 'BE/application/signals.py', 'BE/application/mixin.py',
           'BE/application/impersonation_scope.py', 'BE/application/tenant_scope.py', 'BE/application/audit_resolvers.py',
           'BE/application/utils/api_utils.py') + harness.SOURCES + default_payment_category.SOURCES
ROWS, LINKS, KEYS, NAMES = writes.ROWS, writes.LINKS, writes.KEYS, writes.NAMES
MANAGED = writes.MANAGED
TODAY = re.compile(r'\{today:([^}]+)\}')


def seed_rows(source, created):
    from application.models import PaymentCategory
    from application.models.balance_sheet_models import CustomAccounts
    writes.seed_rows(source, created)
    for key, name in source['categories'].items():
        PaymentCategory._base_manager.bulk_create([PaymentCategory(payment_category_id=key, name=name)])
        created.append((PaymentCategory, key))
    # A same-named default category of the other association, keyed below every uuid4 key.
    default_payment_category.seed_other_association_category(source['ids']['foreign_association'], created)
    CustomAccounts._base_manager.bulk_create([CustomAccounts(
        custom_account_id=source['bank'], enabled=True, name='Banca fixture', initial_balance=0, account_type=CustomAccounts.BANK,
        account_code='BANK-FIXTURE', sport_association_id=source['ids']['own_association'], editable=False)])
    created.append((CustomAccounts, source['bank']))


base_fixture_context = harness.fixture_context_for(contract, seed_rows, purge=tuple((lambda label=label: writes.models()[label])
                                                                                    for label in ROWS))


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from application.models import Payment, PaymentCategory
    with base_fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
        source = contract.load_source()
        ids = source['ids']
        category = default_payment_category.default_category(ids['own_association'])
        contract.require(category is not None, 'fixture.default-category')
        own = Payment.get_default_bank_account(ids['own_association'])
        contract.require(own is not None and str(own.pk) == source['bank'], 'fixture.default-bank')
        far = Payment.get_default_bank_account(ids['foreign_association'])
        tokens = {'$category': str(category.pk), '$bank_far': str(far.pk) if far else None}
        fixture['tokens_course_enrolment'] = tokens
        fixture['sessions'] = {**fixture['sessions'], **tokens, '$category_name': category.name,
                               '$bank_far_type': far.account_type if far else None, '$bank_far_name': far.name if far else None}
        yield fixture


class Binder(writes.Binder):
    def resolve(self, value):
        if isinstance(value, str) and '{today:' in value:
            value = TODAY.sub(lambda match: self.now.strftime(match.group(1)), value)
            for sym in sorted(self.binds, key=len, reverse=True):
                value = value.replace(sym, self.binds[sym])
            return value
        if isinstance(value, str) and value.startswith('@') and value in self.binds:
            return self.binds[value]
        return super().resolve(value)


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    ids = source['ids']
    binder = Binder(context['fixture']['tokens_course_enrolment'], datetime.now(timezone.utc))
    binder.binds.update(contract.BINDINGS.get(case['name'], {}))
    writes.match_created(effect, before, after, binder)
    for label in ROWS:
        writes.check_rows(label, before, after, effect['rows'].get(label, {}), binder)
    for label in LINKS:
        authored = effect['links'].get(label, {})
        expected = (writes.link_pairs(before, label) - {tuple(binder.resolve(p)) for p in authored.get('remove', [])}) \
            | {tuple(binder.resolve(p)) for p in authored.get('add', [])}
        contract.require(writes.link_pairs(after, label) == expected, 'state.links:' + label)

    added, indexes = harness.audit_delta(contract, before, after)
    added.sort(key=lambda row: int(row['id']))
    contract.require(len(added) == len(effect['audit']) and len(indexes) == sum(1 for e in effect['audit'] if e['index']), 'audit.count')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    additional = writes.impersonation_additional(case, context, ids, actor)
    created_times = {str(row.pk): row.creation_date.astimezone(timezone.utc)
                     for row in writes.models()[writes.CSM]._base_manager.filter(pk__in=list(binder.binds.values()))}
    for position, (event, spec) in enumerate(zip(added, effect['audit'])):
        name, label = NAMES[spec['model']]
        key = binder.resolve(spec['key'])
        where = f":{position}:{spec['model']}:{key}"
        representation = re.sub(r' object at 0x[0-9a-f]+>', ' object at 0x?>', event['object_repr'])
        expected_repr = binder.text(spec['repr'], created_times) if spec['repr'] else f'{name} object ({key})'
        contract.require(event['content_type_id'] == types[spec['model']] and event['action'] == spec['action'] and event['object_pk'] == key,
                         'audit.identity' + where)
        contract.require(representation == expected_repr, 'audit.repr' + where)
        changes = spec['changes']
        if '$stored' in changes:
            model = writes.models()[label]
            base = writes.created_changes(model, model._base_manager.get(pk=key))
            changes = {**base, **binder.resolve(changes['overrides'])}
        else:
            changes = binder.resolve(changes)
        contract.require(event['changes'] == changes, 'audit.changes' + where)
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
        contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '', 'audit.context')
        ipaddress.ip_address(event['remote_addr'])
        contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = indexes.get(str(event['id']))
        if spec['index'] is None:
            contract.require(index is None, 'index.absent' + where)
        else:
            contract.require(index is not None and index['sport_association_id'] == spec['index'][0]
                             and index['resolution_path'] == spec['index'][1], 'index.fields' + where)


run_workload = harness.run_workload_for(contract, assert_effect)
