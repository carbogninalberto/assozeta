"""Parent-only instructor/<uid>/hours HTTP runner; imports are inert.

Seeds the instructor-list instructors plus documents, payments, associates,
subscriptions, courses, course subscriptions (with membership payments),
installments and instructor hours (audit disabled). Each case is checked
against the complete model state: only the authored hours and documents
change (created hours are matched as the one new key, or the authored key;
a copied row equals its source but for the authored columns), and every new
auditlog entry matches an authored event with its index row: an hour stored
when its entry is written resolves through its instructor's owner's
association; otherwise (and when that owner has none) the actor's association,
none when the actor has none. Rows the run creates are removed
before the fixture cleanup compares the model state.

A CURRENT_HOST print stub (invoice-sync-print's service_context) runs for the
whole run: each case's outbound requests must be exactly its authored 'prints'
(the list prints each page row with a payment and no document).
"""
import ipaddress
import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import types

from TESTS.contracts import test_instructor_hours_http as base_contract
from TESTS.adapters.django import invoice_sync_print_http as printer
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/instructor_hours_http.json', 'TESTS/contracts/test_instructor_hours_http.py',
           'TESTS/adapters/django/instructor_hours_http.py', 'TESTS/adapters/django/carnet_replace_http.py',
           'BE/application/views/instructor_views.py', 'BE/application/serializers/user_serializers.py',
           'BE/application/models/user_models.py', 'BE/application/models/payment_models.py', 'BE/application/models/courses_models.py',
           'BE/application/models/subscriptions_models.py', 'BE/docmanager/models.py', 'BE/application/mixin.py',
           'BE/application/utils/api_utils.py', 'BE/application/tenant_scope.py', 'BE/application/audit_resolvers.py',
           'BE/application/signals.py', 'BE/application/printing_tasks.py', 'TESTS/adapters/django/invoice_sync_print_http.py',
           'BE/instance/restore/task_base.py') + harness.SOURCES


def _assert_case(case, base_url, *, token, sessions):
    """The authored request, with the print stub scripted for it and its outbound requests checked after."""
    stub = printer.STUB
    with stub['lock']:
        stub['log'] = []
        stub['script'] = [item['reply'] for item in case['prints']]
    stub['token'] = token
    status = base_contract.assert_case(case, base_url, token=token, sessions=sessions)
    printer.check_prints(case, None)
    return status


contract = types.SimpleNamespace(**{name: getattr(base_contract, name) for name in dir(base_contract) if not name.startswith('__')})
contract.assert_case = _assert_case
service_context = printer.service_context
HOURS, DOCUMENT = 'application.InstructorHours', 'docmanager.Document'
LINKS = 'application.Instructor_documents'
MANAGED = (HOURS, DOCUMENT, LINKS, harness.AUDIT, harness.INDEX)


def models():
    from application.models import InstructorHours
    from docmanager.models import Document
    return InstructorHours, Document


def when(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None


def seed_rows(source, created):
    from application.models import (Associate, Course, CourseSubscription, CourseSubscriptionInstallment, Instructor, InstructorHours,
                                    Payment, Subscription)
    from docmanager.models import Document
    ids = source['ids']

    def insert(model, row):
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    for label, pk in source['documents'].items():
        insert(Document, Document(document_id=pk, filename=f'{label}.pdf'))
    for row in source['hour_documents'].values():
        insert(Document, Document(document_id=row['id'], token=row['token'], filename=row['filename'], creation_date=when(row['created'])))
    day = lambda text: date.fromisoformat(text) if text else None
    for row in source['instructors'].values():
        values = {k: row[k] for k in ('first_name', 'last_name', 'email', 'born_city', 'born_province', 'tax_code', 'address_city', 'address',
                                      'address_province', 'civic_number', 'study_title', 'role', 'phone', 'phone_2', 'phone_3', 'phone_4',
                                      'draft', 'associated_user_id', 'is_volunteer')}
        insert(Instructor, Instructor(instructor_id=row['id'], user_id=ids[row['owner']], born_date=day(row['born_date']),
                                      stipulated_contract_in=day(row['stipulated_contract_in']),
                                      default_hourly_billing=Decimal(row['default_hourly_billing']),
                                      default_percentage_billing=Decimal(row['default_percentage_billing'])
                                      if row['default_percentage_billing'] else None, **values))
        Instructor.documents.through.objects.bulk_create([Instructor.documents.through(instructor_id=row['id'], document_id=source['documents'][d])
                                                         for d in row['documents']])
    Instructor.documents.through.objects.bulk_create([
        Instructor.documents.through(instructor_id=source['instructors'][link['instructor']]['id'],
                                     document_id=source['hour_documents'][link['document']]['id']) for link in source.get('document_links', ())])
    for row in source['payments'].values():
        insert(Payment, Payment(payment_id=row['id'], sport_association_id=row['association'], subject=Payment.COURSE,
                                amount=Decimal(row['amount']), paid=row['paid'], payment_date=when(row['date']), deleted=row['deleted'],
                                group_id=ids[row['group']] if row['group'] else None, creation_date=when('2026-08-01T08:00:00Z')))
    for row in source['associates'].values():
        insert(Associate, Associate(associate_id=row['id'], sport_association_id=ids['own_association'], first_name=row['first'],
                                    last_name=row['last']))
    for row in source['subscriptions'].values():
        insert(Subscription, Subscription(subscription_id=row['id'], sport_association_id=ids['own_association'], user_id=ids['athlete'],
                                          associate_id=source['associates'][row['associate']]['id'], status_flag=Subscription.ACCEPTED))
    for row in source['courses'].values():
        insert(Course, Course(course_id=row['id'], sport_association_id=row['association'], title=row['title'], course_type=row['type'],
                              multi_payments=row['multi'], deleted=row['deleted'], group_id=ids[row['group']] if row['group'] else None,
                              fee=Decimal('50.00'), status_flag=Course.ACTIVE))
    payments = source['payments']
    for row in source['course_subscriptions'].values():
        insert(CourseSubscription, CourseSubscription(
            course_subscription_id=row['id'], course_id=source['courses'][row['course']]['id'],
            subscription_id=source['subscriptions'][row['subscription']]['id'], deleted=row['deleted'],
            payment_id=payments[row['payment']]['id'] if row['payment'] else None))
        through = CourseSubscription.membership_payments.through
        through.objects.bulk_create([through(coursesubscription_id=row['id'], payment_id=payments[p]['id']) for p in row['memberships']])
    for row in source['installments'].values():
        insert(CourseSubscriptionInstallment, CourseSubscriptionInstallment(
            course_subscription_installment_id=row['id'], course_subscription_id=source['course_subscriptions'][row['course_subscription']]['id'],
            amount=Decimal('1.00'), paid=True, payment_date=when('2026-09-15T08:30:00Z'), deleted=row['deleted'],
            group_id=ids[row['group']] if row['group'] else None, payment_id=payments[row['payment']]['id'] if row['payment'] else None))
    documents = source['hour_documents']
    for row in source['hours'].values():
        insert(InstructorHours, InstructorHours(
            instructor_hours_id=row['id'], instructor_id=source['instructors'][row['instructor']]['id'], creation_date=when(row['created']),
            date=contract.hour_date(row['date']), compensation_type=row['compensation_type'], hours=Decimal(row['hours']),
            hourly_billing=Decimal(row['hourly_billing']),
            percentage_billing=Decimal(row['percentage_billing']) if row['percentage_billing'] is not None else None,
            courses=row['courses'], period=row['period'], amount=Decimal(row['amount']), paid=row['paid'],
            payment_id=payments[row['payment']]['id'] if row['payment'] else None, notes=row['notes'],
            calculation_data=row['calculation_data'], document_id=documents[row['document']]['id'] if row['document'] else None))


def document_deleted_changes(row):
    """auditlog's delete diff of a Document: its stored fields, plus the reverse accessors declaring a
    related_name (their manager's str against None)."""
    _, Document = models()
    changes = replace.deleted_changes(Document, row)
    for relation in Document._meta.related_objects:
        if not relation.many_to_many and relation.related_name and not relation.one_to_one:
            changes[relation.name] = [f'{relation.related_model._meta.label}.None', 'None']
    return changes


def prepared(source):
    """The delete diffs of every hour and hour document, captured before each case."""
    InstructorHours, Document = models()
    result = {str(row.pk): replace.deleted_changes(InstructorHours, row) for row in InstructorHours._base_manager.all()}
    keys = [row['id'] for row in source['hour_documents'].values()]
    result.update({str(row.pk): document_deleted_changes(row) for row in Document._base_manager.filter(pk__in=keys)})
    return result


def plain(value):
    if isinstance(value, dict) and set(value) == {'type', 'value'} and value['type'] in ('uuid', 'datetime', 'decimal'):
        return value['value']
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [plain(item) for item in value]
    if isinstance(value, date):
        return value.isoformat()
    return value


def rows_of(state, label, key):
    return {k: plain(row) for k, row in keyed(state[label], key).items()}


def created_changes(row, overrides):
    """auditlog's create diff of the stored hour, with the authored in-memory overrides."""
    InstructorHours, _ = models()
    changes = {}
    for field in InstructorHours._meta.concrete_fields:
        value = getattr(row, field.attname)
        kind = field.get_internal_type()
        if value is None:
            if field.is_relation or kind == 'DateTimeField':
                changes[field.name] = ['None', 'None']
            elif kind == 'JSONField':
                changes[field.name] = ['None', 'null']
            continue
        if kind == 'DateTimeField':
            value = str(value.astimezone(timezone.utc).replace(tzinfo=None))
        elif kind == 'JSONField':
            value = json.dumps(value, sort_keys=True)
        changes[field.name] = ['None', overrides.get(field.name, str(value))]
    return changes


NOW_TEXT = re.compile(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(\.\d{6})?\+00:00\Z')


def check_hours(case, before, after, effect, source, context):
    old, new = rows_of(before, HOURS, 'instructor_hours_id'), rows_of(after, HOURS, 'instructor_hours_id')
    authored = {source['hours'][label]['id'] if label in source['hours'] else label: columns for label, columns in effect['rows'].items()}
    fresh = set(new) - set(old)
    expected_new = [key for key in authored if key == '$new' or key not in old]
    contract.require(len(fresh) == len(expected_new), 'state.created-count')
    for key in expected_new:
        actual = key
        if key == '$new':
            candidates = fresh - set(authored)
            contract.require(len(candidates) == 1, 'state.created')
            actual = candidates.pop()
        contract.require(actual in fresh, 'state.created-key')
        context['keys'][key] = actual
        row, columns = new[actual], dict(authored[key])
        if '$copy' in columns:
            source_row = old[source['hours'][columns.pop('$copy')]['id']]
            expected = {**source_row, 'instructor_hours_id': actual, **columns}
            contract.require(row == expected, 'state.copied-row')
            continue
        moment = datetime.fromisoformat(row['creation_date'].replace('Z', '+00:00'))
        if 'creation_date' not in columns:
            contract.require(abs(datetime.now(timezone.utc) - moment) < timedelta(minutes=10), 'state.created-now')
        if columns.get('date') == '$today':
            columns['date'] = moment.date().isoformat()
        contract.require(all(row[c] == v and type(row[c]) is type(v) for c, v in columns.items()), 'state.created-row')
    for key, row in old.items():
        if key in authored and authored[key] is None:
            contract.require(key not in new, 'state.deleted')
        elif key in authored:
            contract.require(key in new and new[key] == {**row, **authored[key]}, 'state.updated')
        else:
            contract.require(new.get(key) == row, 'state.unchanged')


def check_documents(before, after, effect, source):
    old, new = rows_of(before, DOCUMENT, 'document_id'), rows_of(after, DOCUMENT, 'document_id')
    removed = {source['hour_documents'][label]['id'] for label, value in effect['documents'].items() if value is None}
    contract.require(set(new) == set(old) - removed and all(new[k] == old[k] for k in new), 'state.documents')


def check_links(before, after, effect, source):
    """Document.delete() cascades to the instructor document links of the removed documents."""
    removed = {source['hour_documents'][label]['id'] for label in effect['links']}
    plain = lambda value: value['value'] if isinstance(value, dict) else str(value)
    expected = [row for row in before[LINKS] if plain(row['document_id']) not in removed]
    contract.require(after[LINKS] == expected and (not effect['links'] or len(expected) < len(before[LINKS])), 'state.links')


def check_event(case, event, indexes, *, before, after, ids, spec, key, changes, context):
    app = 'docmanager' if spec['model'] == 'Document' else 'application'
    types = {(ct['app_label'], ct['model']): ct['id'] for ct in before['contenttypes.ContentType']}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[case['effect']['actor']]
    contract.require(event['content_type_id'] == types[(app, spec['model'].lower())] and event['action'] == spec['action']
                     and event['object_pk'] == key and event['object_repr'] == f'{spec["model"]} object ({key})', 'audit.identity')
    contract.require(event['changes'] == changes, 'audit.changes')
    contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
    contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
    ipaddress.ip_address(event['remote_addr'])
    contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    associations = keyed(before['application.SportAssociation'], 'sport_association_id').values()
    owned_by = lambda user: sorted(str(row['sport_association_id']) for row in associations if str(row['user_id']) == user and not row['deleted'])
    expected = None
    if spec['model'] == 'InstructorHours' and spec['action'] in (0, 1):
        # instructor_hours_resolver reads the stored hour when the entry is written: auditlog's
        # update entry precedes the save (the previous row), its create entry follows it.
        stored = rows_of(before if spec['action'] == 1 else after, HOURS, 'instructor_hours_id').get(key)
        if stored is not None:
            instructors = rows_of(before, 'application.Instructor', 'instructor_id')
            owner_associations = owned_by(str(instructors[str(stored['instructor_id'])]['user_id']))
            if owner_associations:
                expected = (owner_associations[0], 'instructor.user.sport_association')
    if expected is None:
        # Neither diff names a sport_association: the actor's association.
        owned = owned_by(actor)
        expected = (owned[0], 'actor.sport_association') if owned else None
    index = indexes.get(str(event['id']))
    if expected is None:
        contract.require(index is None, 'index.absent')
    else:
        contract.require(index is not None and (index['sport_association_id'], index['resolution_path']) == expected, 'index.fields')


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    context['keys'] = {}
    check_hours(case, before, after, effect, source, context)
    check_documents(before, after, effect, source)
    check_links(before, after, effect, source)
    added, indexes = harness.audit_delta(contract, before, after)
    contract.require(len(added) == len(effect['audit']), 'audit.count')
    remaining = sorted(added, key=lambda event: event['id'])
    InstructorHours, _ = models()
    for spec in effect['audit']:
        key = spec['key']
        key = context['keys'].get(key, key)
        matches = [event for event in remaining if event['object_pk'] == key and event['action'] == spec['action']]
        contract.require(len(matches) == 1 and matches[0] is remaining[0], 'audit.order')
        event = remaining.pop(0)
        if spec['changes'] == '$deleted':
            changes = context['prepared'][key]
        elif spec['changes'] == '$created':
            row = InstructorHours._base_manager.get(pk=key)
            overrides = dict(spec['overrides'])
            if overrides.get('date') == '$now':
                actual = (event['changes'].get('date') or [None, ''])[1]
                contract.require(NOW_TEXT.match(actual or '') is not None, 'audit.date-now')
                moment = datetime.fromisoformat(actual)
                contract.require(moment.date() == row.date and moment >= row.creation_date, 'audit.date-now-order')
                overrides['date'] = actual
            changes = created_changes(row, overrides)
        else:
            changes = spec['changes']
        check_event(case, event, indexes, before=before, after=after, ids=source['ids'], spec=spec, key=key, changes=changes, context=context)


fixture_context = harness.fixture_context_for(contract, seed_rows, purge=(lambda: models()[0],))
run_workload = harness.run_workload_for(contract, assert_effect, prepare=prepared)
