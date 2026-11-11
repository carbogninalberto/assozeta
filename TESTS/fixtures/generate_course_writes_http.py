"""Author the course add, update and delete and course-overview add-on update and delete HTTP sequence (course-writes.md).

course_add (POST course/add) requires exactly the keys ``new_course`` and
``subscriptions`` (400 'validation errors.'), gets the owner's association
(a 500 for athletes), validates CourseAddSerializer (field errors 400; its
events checks render {'msg:': ..., 'details': ...}), escapes < and > in the
title and description, creates the course (Course.objects.create keeps the
serializer's float 0.0 defaults in memory: auditlog records '0.0'), saves
its association, adds the listed locations of the association and, per
subscription (Subscription.objects.get within the association: an unknown
or other association's subscription is a 500 after the course exists), creates the course subscription with its course-fee payment or
its future installments; every create and save is audited.

course_update (PATCH) checks the uid, finds the live group-aware course
(400 {'msg:': 'course not found.'}), answers 403 'User not allowed.' unless
the course is of the caller's association (athletes and owners without an
association included), assigns the raw body
values (escaping title/description; one_fee only for one-fee courses,
events only for instalment courses, revalidated and stored as DD/MM/YYYY
and 0.00 text), replaces the locations immediately with the listed ones of
the association or already linked to the course (not atomic: a later
event error answers 400 with the locations already changed) and saves:
auditlog diffs str() of the in-memory values; a changed title rewrites the
course association's live group-aware payments whose meta or description
contains the old title.

course_delete (POST) hard-deletes the owner's live group-aware course
through the collector: installments, attendance days, course subscriptions
(all rows, with the pre_delete signals), registries and the course, with
the unpaid payments deleted by the signals.

course_overview_delete (DELETE) deletes the listed (or every unpaid)
installment of the live course subscription with its unpaid payment, then
soft-deletes the row; course_overview_update (POST) switches a course
subscription to instalments (regenerating the payable events, ``all``
forcing past ones) or to the one-fee payment. Run with ``python3 -I``.
"""
import copy
import datetime as dt
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('course_writes_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
OWNER, ATHLETE, FOREIGN_OWNER = IDS['owner'], IDS['athlete'], IDS['foreign_owner']
OWN_GROUP, FOREIGN_GROUP = IDS['own_group'], IDS['foreign_group']
ids = iter(f'c0000000-0000-4000-8000-{n:012d}' for n in range(1, 400))
SERVER = '<stable-server-error-page>'
UNKNOWN = 'c0000000-0000-4000-8000-000000999999'
CATEGORY_NAME = 'entrate e proventi da attività tipiche'
cases = []

# --- seed ----------------------------------------------------------------------

ASSOCIATES = {label: {'associate_id': next(ids), 'sport_association_id': association, 'first_name': first, 'last_name': last,
                      'sex': 'M', 'tax_code': None, 'born_date': '1990-01-01', 'email': None, 'is_minor': False}
              for label, association, first, last in (('mario', OWN, 'Mario', 'Rossi'), ('luisa', OWN, 'Luisa', 'Bianchi'),
                                                      ('hans', FOREIGN, 'Hans', 'Muller'))}
SUBSCRIPTIONS = {}
for label, association, associate, user, group in (('mario', OWN, 'mario', ATHLETE, None), ('luisa', OWN, 'luisa', OWNER, None),
                                                   ('group', OWN, 'luisa', OWNER, OWN_GROUP), ('hans', FOREIGN, 'hans', FOREIGN_OWNER, None)):
    SUBSCRIPTIONS[label] = {'subscription_id': next(ids), 'sport_association_id': association, 'associate_id': ASSOCIATES[associate]['associate_id'],
                            'user_id': user, 'group_id': group, 'start_date': '2026-01-01', 'end_date': '2026-12-31',
                            'creation_date': '2026-01-01T09:00:00Z'}
LOCATIONS = {label: {'course_location_id': next(ids), 'title': title, 'description': None, 'address': None, 'sport_association_id': association}
             for label, title, association in (('own', 'Palestra', OWN), ('far', 'Altrove', FOREIGN), ('old', 'Vecchia', OWN))}
TAGS = {'own': {'tag_id': next(ids), 'tag_name': 'Bambini', 'sport_association_id': OWN, 'creation_date': '2026-01-01T09:00:00Z'}}


def course(title, *, association=OWN, fee='50.00', one_fee='0.00', one_fee_payment=False, multi=False, events=None, deleted=False, group=None,
           course_type=1):
    return {'course_id': next(ids), 'sport_association_id': association, 'status_flag': 2, 'course_type': course_type,
            'creation_date': '2026-02-01T08:00:00Z', 'title': title, 'description': 'Descrizione ' + title, 'multi_payments': multi, 'fee': fee,
            'events': events, 'one_fee_payment': one_fee_payment, 'one_fee': one_fee, 'google_calendar_id': None,
            'google_background_color': '#123456', 'multiple_quotes': None, 'start_date': None, 'end_date': None, 'pinned': False,
            'billed_duration_is_sport_season': False, 'billed_frequency': 1, 'billed_from_subscription_date': False,
            'billed_from_day_of_month': 1, 'auto_renewal': False, 'deleted': deleted, 'group_id': group}


# DRF's DATE_INPUT_FORMATS is ['%d/%m/%Y']: an ISO date fails CourseEventsSerializer.
OV_EVENTS = [{'id': 0, 'amount': '20.00', 'payment_date': '10/01/2020'}, {'id': 1, 'amount': '20.00', 'payment_date': '10/01/2099'},
             {'id': 2, 'amount': '10.00', 'payment_date': '2099-02-10'}]
COURSES = {
    'u_main': course('Nuoto', one_fee='40.00', one_fee_payment=True),
    'u_multi': course('Tennis', multi=True, events=[{'id': 0, 'amount': '25.00', 'payment_date': '10/01/2099'}]),
    'u_plain': course('Scherma'),
    'u_gone': course('Cancellato', deleted=True),
    'u_group': course('Gruppo', group=OWN_GROUP),
    'u_far': course('Lontano', association=FOREIGN),
    'u_linked': course('Collegato'),
    'd_full': course('Calcio'),
    'd_empty': course('Vuoto'),
    'd_far': course('Estero', association=FOREIGN),
    'd_gone': course('Rimosso', deleted=True),
    'd_group': course('Squadra', group=OWN_GROUP),
    'd_orphan': course('Orfano', association=None),
    'o_del': course('Danza', multi=True),
    'o_else': course('Judo', multi=True),
    'o_stale': course('Karate', multi=True),
    'o_text': course('Boxe'),
    'o_upd': course('Pilates', one_fee='30.00', one_fee_payment=True, events=OV_EVENTS),
    # update_metadata_courses_callback returns early for an empty old title.
    'o_raw': course('Yoga raw', one_fee='30.00', events=[{'id': 0, 'amount': '20.00', 'payment_date': '10/01/2099'}], group=OWN_GROUP),
    'u_blank': course(''),
    'u_null': {**course('Senza titolo'), 'title': None},
    'u_clear': course('Ginnastica'),
    'u_forms': course('Atletica', one_fee='40.00', one_fee_payment=True),
    'u_reject': course('Rifiuto', one_fee='40.00', one_fee_payment=True),
    'd_member': course('Sala pesi', course_type=3),
    # impersonation sessions (an owner acting as its full collaborator or as an athlete)
    'd_session': course('Sessione'),
    'u_session': course('Ritmica'),
    'o_session': course('Kick boxing'),
    'o_session_athlete': course('Hockey', one_fee='15.00', one_fee_payment=True),
}
COURSE_LOCATIONS = [('u_main', 'old'), ('d_full', 'own'), ('u_linked', 'far')]
COURSE_TAGS = [('d_full', 'own')]


def payment(amount='10.00', *, paid=False, association=OWN, associate=None, user=None, meta=None, description='Quota', group=None, deleted=False):
    return {'payment_id': next(ids), 'type': 'default', 'subject': 2, 'amount': amount, 'creation_date': '2026-03-01T08:00:00Z',
            'payment_date': None, 'paid': paid, 'expense': False, 'description': description, 'payment_category_id': None,
            'meta_payment_categories': None, 'invoice_id': None, 'user_id': user, 'associate_id': associate, 'supplier_id': None,
            'instructor_id': None, 'sport_association_id': association, 'payment_intent_id': None, 'archived': False,
            'invoice_fiscal_code': None, 'meta': meta, 'notes': None, 'custom_accounts_id': None, 'signature_id': None, 'deleted': deleted,
            'group_id': group, 'course': None}


PAYMENTS = {
    # title rewrite after a course title change (live, group-aware)
    'pm_meta': payment(meta={'course_title': 'Nuoto', 'n': 2, 'note': 'Nuoto e Nuoto'}),
    'pm_desc': payment(description='Iscrizione Nuoto 2026'),
    'pm_far': payment(association=FOREIGN, description='Iscrizione Nuoto 2026', meta={'course_title': 'Nuoto'}),
    'pm_case': payment(description='quota NUOTO', meta={'x': 'nuoto'}),
    'pm_nested': payment(meta={'info': {'t': 'Nuoto'}}),
    'pm_gone': payment(description='Nuoto', deleted=True),
    # title rewrite to an empty title
    'pm_clear': payment(description='Iscrizione Ginnastica 2026', meta={'course_title': 'Ginnastica', 'n': 1}),
    # membership payments of a payment-less membership row (course delete)
    'd_mu': payment('25.00'),
    'd_mp': payment('25.00', paid=True),
    'd_mdel': payment('25.00', deleted=True),
}
for label, values in (('d_pa', {}), ('d_pb', {'paid': True}), ('d_pm', {}), ('d_pg', {'group': OWN_GROUP}), ('d_ph', {}),
                      ('o1_pa', {}), ('o1_pb', {'paid': True}), ('o2_pd', {}), ('o2_pe', {}), ('o3_p', {}),
                      ('u1_p', {}), ('u2_pa', {}), ('u2_pb', {'paid': True}), ('u3_pa', {}), ('u4_p', {}), ('u7_p', {})):
    PAYMENTS[label] = payment(**values)


def cs(course_label, subscription, *, payment_label=None, multi=False, one_fee=False, deleted=False):
    return {'course_subscription_id': next(ids), 'course_id': COURSES[course_label]['course_id'], 'paid': False,
            'creation_date': '2026-06-01T10:00:00Z', 'subscription_id': SUBSCRIPTIONS[subscription]['subscription_id'],
            'payment_id': PAYMENTS[payment_label]['payment_id'] if payment_label else None, 'multi_payments': multi, 'one_fee_payment': one_fee,
            'archived': False, 'type': 1, 'multiple_quote': None, 'billed_frequency': 1, 'billed_from': None, 'billed_until': None,
            'billed_from_day_of_month': 1, 'membership_active': True, 'membership_fee': '0.00', 'auto_renewal': False, 'deleted': deleted}


CS = {
    'd_cs_a': cs('d_full', 'luisa', multi=True),
    'd_cs_b': cs('d_full', 'mario', payment_label='d_pm', deleted=True),
    'd_cs_g': cs('d_group', 'group', multi=True),
    'o1': cs('o_del', 'luisa', multi=True),
    'o1_other': cs('o_del', 'mario', multi=True),
    'o2': cs('o_else', 'luisa', multi=True),
    'o3': cs('o_stale', 'luisa', payment_label='o3_p', multi=True),
    'o4': cs('o_text', 'luisa'),
    'u1': cs('o_upd', 'luisa', payment_label='u1_p', one_fee=True),
    'u2': cs('o_upd', 'mario', multi=True),
    'u3': cs('u_plain', 'luisa', multi=True),
    'u4': cs('o_upd', 'group', payment_label='u4_p'),
    'u5': cs('u_multi', 'luisa'),
    'u7': cs('o_raw', 'group', payment_label='u7_p'),
    'u_far': cs('u_far', 'hans'),
    'd_cs_m': {**cs('d_member', 'luisa'), 'type': 3},
    'o5': cs('o_session', 'luisa'),
    'o6': cs('o_session_athlete', 'mario', multi=True),
}
MEMBERSHIP_PAYMENTS = [('d_cs_m', 'd_mu'), ('d_cs_m', 'd_mp'), ('d_cs_m', 'd_mdel')]


def installment(cs_label, number, *, payment_label=None, paid=False, group=None, amount='10.00'):
    return {'course_subscription_installment_id': next(ids), 'course_subscription_id': CS[cs_label]['course_subscription_id'], 'id': number,
            'amount': amount, 'paid': paid, 'payment_date': '2099-01-10T00:00:00Z',
            'payment_id': PAYMENTS[payment_label]['payment_id'] if payment_label else None, 'archived': False, 'group_id': group, 'deleted': False}


INSTALLMENTS = {
    'd_ia': installment('d_cs_a', 0, payment_label='d_pa'),
    'd_ib': installment('d_cs_a', 1, payment_label='d_pb'),
    'd_ic': installment('d_cs_b', 0),
    'd_ig': installment('d_cs_g', 0, payment_label='d_pg', group=OWN_GROUP),
    'd_ih': installment('d_cs_g', 1, payment_label='d_ph'),
    'o1_a': installment('o1', 0, payment_label='o1_pa'),
    'o1_b': installment('o1', 1, payment_label='o1_pb'),
    'o1_c': installment('o1', 2),
    'o1_x': installment('o1_other', 0),
    'o2_d': installment('o2', 0, payment_label='o2_pd'),
    'o2_e': installment('o2', 1, payment_label='o2_pe', paid=True),
    'o2_f': installment('o2', 2),
    'o3_a': installment('o3', 0, payment_label='o3_p'),
    'u2_a': installment('u2', 0, payment_label='u2_pa'),
    'u2_b': installment('u2', 1, payment_label='u2_pb'),
    'u2_c': installment('u2', 2),
    'u3_a': installment('u3', 0, payment_label='u3_pa'),
    'u3_b': installment('u3', 1),
}
# Stored events of earlier versions carry the installment key: an id no installment has is accepted (the day and year
# may be written with decimal digits of another script), an installment's own id or a null one fails validation.
OV_EVENTS[1].update({'payment_date': '1\u0660/01/\u0662\u0660\u0669\u0669', 'course_subscription_installment_id': UNKNOWN})
OV_EVENTS += [{'id': 3, 'amount': '5.00', 'payment_date': '10/03/2099',
               'course_subscription_installment_id': INSTALLMENTS['u2_b']['course_subscription_installment_id']},
              {'id': 4, 'amount': '5.00', 'payment_date': '10/04/2099', 'course_subscription_installment_id': None}]
REGISTRIES = {'d_ar': {'attendance_registry_id': next(ids), 'course_id': COURSES['d_full']['course_id'], 'creation_date': '2026-02-02T08:00:00Z',
                       'events': [{'day': 'mon'}], 'status': 1}}
DAYS = {'d_ad': {'attendance_day_id': next(ids), 'attendance_registry_id': REGISTRIES['d_ar']['attendance_registry_id'], 'title': 'Lunedì',
                 'date': '2026-02-09T17:00:00Z', 'attendees': [], 'expected_absences': None, 'auto_marked': False, 'associated_event': None}}
SEED = {'associates': copy.deepcopy(ASSOCIATES), 'subscriptions': copy.deepcopy(SUBSCRIPTIONS), 'locations': copy.deepcopy(LOCATIONS),
        'tags': copy.deepcopy(TAGS), 'courses': copy.deepcopy(COURSES), 'payments': copy.deepcopy(PAYMENTS), 'course_subscriptions': copy.deepcopy(CS),
        'installments': copy.deepcopy(INSTALLMENTS), 'registries': copy.deepcopy(REGISTRIES), 'days': copy.deepcopy(DAYS),
        'course_locations': [[COURSES[c]['course_id'], LOCATIONS[l]['course_location_id']] for c, l in COURSE_LOCATIONS],
        'course_tags': [[COURSES[c]['course_id'], TAGS[t]['tag_id']] for c, t in COURSE_TAGS],
        'membership_payments': [[CS[c]['course_subscription_id'], PAYMENTS[p]['payment_id']] for c, p in MEMBERSHIP_PAYMENTS]}

# --- expectation helpers ----------------------------------------------------------

COURSE, CSM, CSI, PAYMENT = 'application.Course', 'application.CourseSubscription', 'application.CourseSubscriptionInstallment', 'application.Payment'
AR, AD = 'application.AttendanceRegistry', 'application.AttendanceDay'
LOCATION_LINK, TAG_LINK = 'application.Course_locations', 'application.Course_tags'
MEMBERSHIP_LINK = 'application.CourseSubscription_membership_payments'
MODEL_NAMES = {COURSE: 'course', CSM: 'coursesubscription', CSI: 'coursesubscriptioninstallment', PAYMENT: 'payment', AR: 'attendanceregistry',
               AD: 'attendanceday'}
DIRECT, ACTOR_INDEX = 'direct', 'actor.sport_association'
CHANGES_INDEX, COURSE_INDEX, CSI_INDEX = 'changes_json.sport_association', 'course.sport_association', 'course_subscription.course.sport_association'


def actor_index(actor):
    """The index fallback for rows that no longer resolve: the actor's own association."""
    return [OWN, ACTOR_INDEX] if actor == 'owner' else None


def key_of(model, label):
    table = {COURSE: COURSES, CSM: CS, CSI: INSTALLMENTS, PAYMENT: PAYMENTS, AR: REGISTRIES, AD: DAYS}[model]
    row = table[label]
    return row[next(iter(row))]


def stored(kind, **overrides):
    """auditlog's create/delete diff of the stored row (create: after, delete: before the case) with in-memory overrides."""
    return {'$stored': kind, 'overrides': overrides}


def entry(model, key, action, changes, index, representation=None):
    return {'model': MODEL_NAMES[model], 'key': key, 'action': action, 'changes': changes, 'index': index, 'repr': representation}


def deleted(model, label, actor, overrides=None):
    """A delete entry of a seeded row; rows without an association field resolve through the actor."""
    index = actor_index(actor)
    if model in (COURSE, PAYMENT):
        row = {COURSE: COURSES, PAYMENT: PAYMENTS}[model][label]
        index = [row['sport_association_id'], CHANGES_INDEX] if row['sport_association_id'] else None
    representation = cs_repr(CS[label]) if model == CSM else None
    return entry(model, key_of(model, label), 2, stored('delete', **(overrides or {})), index, representation)


def moment_repr(text):
    value = dt.datetime.fromisoformat(text.replace('Z', '+00:00')).replace(tzinfo=dt.timezone.utc)
    return repr(value)


def cs_repr(row, **memory):
    """CourseSubscription.__str__ (str(self.__dict__)) of an instance loaded from the database; memory: rendered overrides."""
    uuid = lambda value: 'None' if value is None else f"UUID('{value}')"
    when = lambda value: 'None' if value is None else moment_repr(value)
    fields = {'course_subscription_id': uuid(row['course_subscription_id']), 'course_id': uuid(row['course_id']), 'paid': repr(row['paid']),
              'creation_date': when(row['creation_date']), 'subscription_id': uuid(row['subscription_id']), 'payment_id': uuid(row['payment_id']),
              'multi_payments': repr(row['multi_payments']), 'one_fee_payment': repr(row['one_fee_payment']), 'archived': repr(row['archived']),
              'type': repr(row['type']), 'multiple_quote': repr(row['multiple_quote']), 'billed_frequency': repr(row['billed_frequency']),
              'billed_from': when(row['billed_from']), 'billed_until': when(row['billed_until']),
              'billed_from_day_of_month': repr(row['billed_from_day_of_month']), 'membership_active': repr(row['membership_active']),
              'membership_fee': f"Decimal('{row['membership_fee']}')", 'auto_renewal': repr(row['auto_renewal']), 'deleted': repr(row['deleted'])}
    fields.update(memory)
    return "{'_state': <django.db.models.base.ModelState object at 0x?>, " + ', '.join(f"'{k}': {v}" for k, v in fields.items()) + '}'


def new_cs_repr(sym, course_sym, subscription, multi, payment=None):
    """repr of a CourseSubscription.objects.create(course=..., subscription=..., multi_payments=...) instance."""
    row = {'course_subscription_id': None, 'course_id': None, 'paid': False, 'creation_date': None, 'subscription_id': subscription,
           'payment_id': None, 'multi_payments': multi, 'one_fee_payment': False, 'archived': False, 'type': 1, 'multiple_quote': None,
           'billed_frequency': 1, 'billed_from': None, 'billed_until': None, 'billed_from_day_of_month': 1, 'membership_active': True,
           'membership_fee': '0.00', 'auto_renewal': False, 'deleted': False}
    return cs_repr(row, course_subscription_id=f"UUID('{sym}')", course_id=f"UUID('{course_sym}')", creation_date='{created:' + sym + '}',
                   payment_id=f"UUID('{payment}')" if payment else 'None', membership_fee='0.0')


def new_payment(sym, amount, *, user, associate, created='$now', description='Quota Corso', meta=None, group=None):
    return {'$sym': sym, 'payment_id': sym, 'type': 'default', 'subject': 2, 'amount': amount, 'creation_date': created, 'payment_date': None,
            'paid': False, 'expense': False, 'description': description, 'payment_category_id': '$category', 'meta_payment_categories': None,
            'invoice_id': None, 'user_id': user, 'associate_id': associate, 'supplier_id': None, 'instructor_id': None,
            'sport_association_id': OWN, 'payment_intent_id': None, 'archived': False, 'invoice_fiscal_code': None, 'meta': meta, 'notes': None,
            'custom_accounts_id': '$bank', 'signature_id': None, 'deleted': False, 'group_id': group, 'course': None}


def new_installment(sym, cs_key, number, amount, payment_sym, date, group=None):
    return {'$sym': sym, 'course_subscription_installment_id': sym, 'course_subscription_id': cs_key, 'id': number, 'amount': amount, 'paid': False,
            'payment_date': date + 'T00:00:00Z', 'payment_id': payment_sym, 'archived': False, 'group_id': group, 'deleted': False}


def new_cs(sym, course_sym, subscription, multi, payment=None):
    return {'$sym': sym, 'course_subscription_id': sym, 'course_id': course_sym, 'paid': False, 'creation_date': '$now',
            'subscription_id': subscription, 'payment_id': payment, 'multi_payments': multi, 'one_fee_payment': False, 'archived': False,
            'type': 1, 'multiple_quote': None, 'billed_frequency': 1, 'billed_from': None, 'billed_until': None, 'billed_from_day_of_month': 1,
            'membership_active': True, 'membership_fee': '0.00', 'auto_renewal': False, 'deleted': False}


def new_course(sym, title, description, **values):
    row = {'$sym': sym, 'course_id': sym, 'sport_association_id': OWN, 'status_flag': 2, 'course_type': 1, 'creation_date': '$now',
           'title': title, 'description': description, 'multi_payments': False, 'fee': '0.00', 'events': None, 'one_fee_payment': False,
           'one_fee': '0.00', 'google_calendar_id': None, 'google_background_color': '$color', 'multiple_quotes': None, 'start_date': None,
           'end_date': None, 'pinned': False, 'billed_duration_is_sport_season': False, 'billed_frequency': 1,
           'billed_from_subscription_date': False, 'billed_from_day_of_month': 1, 'auto_renewal': False, 'deleted': False, 'group_id': None}
    row.update(values)
    return row


def effect(actor, *, rows=None, created=None, links=None, audit=()):
    return {'actor': actor, 'rows': rows or {}, 'created': created or {}, 'links': links or {}, 'audit': list(audit)}


def add(name, actor, path, status, response, *, method='POST', body=None, headers=None, result=None):
    case = {'name': f'course-writes-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': result}
    if body is not None:
        case['body'] = body
    cases.append(case)


def options(name, description=''):
    return {'name': name, 'description': description, 'renders': ['application/json'],
            'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}


def change_row(model, label, **values):
    """Apply a column change to the tracked row and return the rows entry."""
    table = {COURSE: COURSES, CSM: CS, CSI: INSTALLMENTS, PAYMENT: PAYMENTS}[model]
    table[label].update(values)
    return values


ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
SCOPED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
MALFORMED = ['badly formed hexadecimal UUID string']
NOT_ALLOWED = {'detail': 'User not allowed.'}
ATHLETE_SESSION = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': ATHLETE}
FULL_SESSION = {'X-Impersonation-Id': '$session:owner_full', 'User-Id': IDS['full_collaborator']}
HIDDEN = {'detail': 'Risorsa non disponibile per questo utente e associazione.'}
GROUP = lambda key: {'X-Group-Id': IDS[key]}
RAW_GROUP = IDS['own_group'].upper()
RAW = {'X-Group-Id': RAW_GROUP}
missing = lambda key: {'detail': f'Missing permission: {key}'}
method_not_allowed = lambda verb: {'detail': f'Method "{verb}" not allowed.'}
cid = lambda label: COURSES[label]['course_id']
sid = lambda label: SUBSCRIPTIONS[label]['subscription_id']
lid = lambda label: LOCATIONS[label]['course_location_id']
iid = lambda label: INSTALLMENTS[label]['course_subscription_installment_id']
pid = lambda label: PAYMENTS[label]['payment_id']
csid = lambda label: CS[label]['course_subscription_id']
UPDATE_DOC = 'API endpoint to update a course\n:param request: data: {}\n:return: token, refresh token'
ADD_DOC = 'API endpoint to add a new course\n:param request: data: {}\n:return: token, refresh token'
OV_UPDATE_DOC = ('API endpoint to update a user in a course\n:param request: data: {}\n:param uid: course_id\n'
                 ':param uid_subscription: subscription_id\n:return: token, refresh token')

# --- course/<uid>/delete ----------------------------------------------------------

DELETE_OK = {'data': {'msg': 'course deleted.'}}
add('delete-anonymous', 'anonymous', f'/course/{cid("d_empty")}/delete', 401, ANONYMOUS)
add('delete-admin-without-session', 'admin', f'/course/{cid("d_empty")}/delete', 403, ADMIN)
add('delete-restricted-missing-permission', 'restricted_collaborator', f'/course/{cid("d_empty")}/delete', 403,
    missing('association.courses.delete'))
add('delete-owner-scoped-athlete-denied', 'owner', f'/course/{cid("d_empty")}/delete', 403, SCOPED, headers=ATHLETE_SESSION)
add('delete-wrong-method', 'owner', f'/course/{cid("d_empty")}/delete', 405, method_not_allowed('GET'), method='GET')
add('delete-options', 'owner', f'/course/{cid("d_empty")}/delete', 200, options('Course Delete'), method='OPTIONS')
add('delete-malformed', 'owner', '/course/abc/delete', 400, MALFORMED)
add('delete-athlete-server-error', 'athlete', f'/course/{cid("d_empty")}/delete', 500, SERVER)
add('delete-owner-without-association', 'owner_without_association', f'/course/{cid("d_empty")}/delete', 500, SERVER)
add('delete-unknown', 'owner', f'/course/{UNKNOWN}/delete', 404, {'msg': 'Course not found.'})
add('delete-deleted', 'owner', f'/course/{cid("d_gone")}/delete', 404, {'msg': 'Course not found.'})
add('delete-group-header-hides', 'owner', f'/course/{cid("d_group")}/delete', 404, {'msg': 'Course not found.'}, headers=GROUP('foreign_group'))
add('delete-foreign-course', 'owner', f'/course/{cid("d_far")}/delete', 403, NOT_ALLOWED)
add('delete-course-without-association', 'owner', f'/course/{cid("d_orphan")}/delete', 500, SERVER)
add('delete-empty-course', 'owner', f'/course/{cid("d_empty")}/delete', 200, DELETE_OK,
    result=effect('owner', rows={COURSE: {cid('d_empty'): None}}, audit=[deleted(COURSE, 'd_empty', 'owner')]))

# Impersonating a full collaborator: the owner's course inside the session's association.
add('delete-impersonated-collaborator', 'owner', f'/course/{cid("d_session")}/delete', 200, DELETE_OK, headers=FULL_SESSION,
    result=effect('owner', rows={COURSE: {cid('d_session'): None}}, audit=[deleted(COURSE, 'd_session', 'owner')]))
add('delete-impersonated-foreign-hidden', 'owner', f'/course/{cid("d_far")}/delete', 403, HIDDEN, headers=FULL_SESSION)

# The collector: installments (pre_delete deletes the unpaid payment: P, I, P), course
# subscriptions (the soft-deleted one with a payment fails on Payment.objects.get(id=...);
# the other deletes its live installments), then deletes in reversed key order per model.
actor = 'full_collaborator'
add('delete-full-cascade', actor, f'/course/{cid("d_full")}/delete', 200, DELETE_OK, result=effect(
    actor,
    rows={COURSE: {cid('d_full'): None}, CSM: {csid('d_cs_a'): None, csid('d_cs_b'): None},
          CSI: {iid('d_ia'): None, iid('d_ib'): None, iid('d_ic'): None}, PAYMENT: {pid('d_pa'): None},
          AR: {REGISTRIES['d_ar']['attendance_registry_id']: None}, AD: {DAYS['d_ad']['attendance_day_id']: None}},
    links={LOCATION_LINK: {'remove': [[cid('d_full'), lid('own')]]}, TAG_LINK: {'remove': [[cid('d_full'), TAGS['own']['tag_id']]]}},
    audit=[deleted(PAYMENT, 'd_pa', actor), deleted(CSI, 'd_ia', actor), deleted(PAYMENT, 'd_pa', actor), deleted(CSI, 'd_ib', actor),
           deleted(CSI, 'd_ic', actor), deleted(CSI, 'd_ib', actor), deleted(CSI, 'd_ia', actor), deleted(AD, 'd_ad', actor),
           deleted(CSM, 'd_cs_b', actor), deleted(CSM, 'd_cs_a', actor), deleted(AR, 'd_ar', actor), deleted(COURSE, 'd_full', actor)]))
# X-Group-Id filters the signals' live installment queries, not the collector.
actor = 'owner'
add('delete-grouped-cascade', actor, f'/course/{cid("d_group")}/delete', 200, DELETE_OK, headers=GROUP('own_group'), result=effect(
    actor,
    rows={COURSE: {cid('d_group'): None}, CSM: {csid('d_cs_g'): None}, CSI: {iid('d_ig'): None, iid('d_ih'): None},
          PAYMENT: {pid('d_pg'): None, pid('d_ph'): None}},
    audit=[deleted(PAYMENT, 'd_pg', actor), deleted(CSI, 'd_ig', actor), deleted(PAYMENT, 'd_pg', actor), deleted(PAYMENT, 'd_ph', actor),
           deleted(CSI, 'd_ih', actor), deleted(CSI, 'd_ig', actor), deleted(CSM, 'd_cs_g', actor), deleted(COURSE, 'd_group', actor)]))

# A payment-less membership row: delete_related_course_payment deletes its live unpaid
# membership payments (the paid and the soft-deleted ones stay); the links go with the row.
add('delete-membership-cascade', actor, f'/course/{cid("d_member")}/delete', 200, DELETE_OK, result=effect(
    actor,
    rows={COURSE: {cid('d_member'): None}, CSM: {csid('d_cs_m'): None}, PAYMENT: {pid('d_mu'): None}},
    links={MEMBERSHIP_LINK: {'remove': [[csid('d_cs_m'), pid(p)] for c, p in MEMBERSHIP_PAYMENTS]}},
    audit=[deleted(PAYMENT, 'd_mu', actor), deleted(CSM, 'd_cs_m', actor), deleted(COURSE, 'd_member', actor)]))

# --- course/<uid>/update ----------------------------------------------------------

UPDATED = {'status': 'success'}
NOT_FOUND_UPDATE = {'msg:': 'course not found.'}


def updated(label, actor, changes, index=None, **extra):
    """The Course.save() update entry (direct index) plus the row change."""
    row = COURSES[label]
    rows = {COURSE: {row['course_id']: change_row(COURSE, label, **extra)}} if extra else {}
    audit = [entry(COURSE, row['course_id'], 1, changes, index or [row['sport_association_id'], DIRECT])] if changes else []
    return rows, audit


add('update-anonymous', 'anonymous', f'/course/{cid("u_plain")}/update', 401, ANONYMOUS, method='PATCH', body={})
add('update-restricted-missing-permission', 'restricted_collaborator', f'/course/{cid("u_plain")}/update', 403,
    missing('association.courses.update'), method='PATCH', body={})
add('update-owner-scoped-athlete-denied', 'owner', f'/course/{cid("u_plain")}/update', 403, SCOPED, method='PATCH', body={},
    headers=ATHLETE_SESSION)
add('update-wrong-method', 'owner', f'/course/{cid("u_plain")}/update', 405, method_not_allowed('POST'), body={})
add('update-options', 'owner', f'/course/{cid("u_plain")}/update', 200, options('Course Update', UPDATE_DOC), method='OPTIONS')
add('update-malformed', 'owner', '/course/abc/update', 400, MALFORMED, method='PATCH', body={})
add('update-unknown', 'owner', f'/course/{UNKNOWN}/update', 400, NOT_FOUND_UPDATE, method='PATCH', body={'title': 'x'})
add('update-deleted', 'owner', f'/course/{cid("u_gone")}/update', 400, NOT_FOUND_UPDATE, method='PATCH', body={'title': 'x'})
add('update-group-header-hides', 'owner', f'/course/{cid("u_group")}/update', 400, NOT_FOUND_UPDATE, method='PATCH', body={'title': 'x'},
    headers=GROUP('foreign_group'))
add('update-title-not-text', 'owner', f'/course/{cid("u_plain")}/update', 500, SERVER, method='PATCH', body={'title': 5})
add('update-start-date-invalid', 'owner', f'/course/{cid("u_plain")}/update', 500, SERVER, method='PATCH', body={'start_date': '2026-02-30'})

# Every assignment is a raw body value: auditlog compares str() of it with the stored value.
title = 'Nuoto &lt;b&gt;libero&lt;/b&gt;'
rows, audit = updated('u_main', 'owner', {
    'title': ['Nuoto', title], 'description': ['Descrizione Nuoto', 'Vasca &lt;25m&gt;'], 'fee': ['50.00', '60.5'], 'one_fee': ['40.00', '45'],
    'multiple_quotes': ['null', '[{"amount": "10,00", "quote_id": "q1"}]'], 'billed_duration_is_sport_season': ['False', 'True'],
    'billed_frequency': ['1', '3'], 'billed_from_subscription_date': ['False', '1'], 'billed_from_day_of_month': ['1', '15'],
    'auto_renewal': ['False', 'True'], 'start_date': ['None', '2026-09-01 00:00:00'], 'end_date': ['None', '2027-06-30 16:30:00']},
    title=title, description='Vasca &lt;25m&gt;', fee='60.50', one_fee='45.00', multiple_quotes=[{'quote_id': 'q1', 'amount': '10,00'}],
    billed_duration_is_sport_season=True, billed_frequency=3, billed_from_subscription_date=True, billed_from_day_of_month=15,
    auto_renewal=True, start_date='2026-09-01T00:00:00Z', end_date='2027-06-30T16:30:00Z')
# The old title is matched case-insensitively in meta text and description, replaced case-sensitively,
# in the payments of the course's association only (pm_far stays); only the association's
# locations or ones already linked are linked (far is another association's).
rows[PAYMENT] = {pid('pm_meta'): change_row(PAYMENT, 'pm_meta', meta={'course_title': title, 'n': 2, 'note': f'{title} e {title}'}),
                 pid('pm_desc'): change_row(PAYMENT, 'pm_desc', description=f'Iscrizione {title} 2026')}
add('update-full', 'owner', f'/course/{cid("u_main")}/update', 200, UPDATED, method='PATCH', body={
    'title': 'Nuoto <b>libero</b>', 'description': 'Vasca <25m>', 'fee': '60.5', 'one_fee': 45,
    'locations': [{'course_location_id': lid('own')}, {'course_location_id': lid('far')}, {'course_location_id': UNKNOWN},
                  {'course_location_id': lid('own')}],
    'multiple_quotes': [{'quote_id': 'q1', 'amount': '10,00'}], 'events': [{'id': 0}], 'billed_duration_is_sport_season': True,
    'billed_frequency': 3, 'billed_from_subscription_date': 1, 'billed_from_day_of_month': 15, 'auto_renewal': 'True',
    'start_date': '2026-09-01', 'end_date': '2027-06-30T18:30:00+02:00', 'ignored': 'x'},
    result=effect('owner', rows=rows, audit=audit,
                  links={LOCATION_LINK: {'remove': [[cid('u_main'), lid('old')]], 'add': [[cid('u_main'), lid('own')]]}}))
# A location already linked to the course stays linkable, whatever its association.
add('update-linked-location-kept', 'owner', f'/course/{cid("u_linked")}/update', 200, UPDATED, method='PATCH',
    body={'locations': [{'course_location_id': lid('far')}, {'course_location_id': lid('own')}]},
    result=effect('owner', links={LOCATION_LINK: {'add': [[cid('u_linked'), lid('own')]]}}))
add('update-unlisted-location-removed', 'owner', f'/course/{cid("u_linked")}/update', 200, UPDATED, method='PATCH',
    body={'locations': [{'course_location_id': lid('own')}]},
    result=effect('owner', links={LOCATION_LINK: {'remove': [[cid('u_linked'), lid('far')]]}}))
add('update-other-association-location-not-linked', 'owner', f'/course/{cid("u_linked")}/update', 200, UPDATED, method='PATCH',
    body={'locations': [{'course_location_id': lid('far')}]},
    result=effect('owner', links={LOCATION_LINK: {'remove': [[cid('u_linked'), lid('own')]]}}))

new_events = [{'id': 1, 'amount': '20.00', 'payment_date': '01/02/2099'}, {'id': 2, 'amount': '30.50', 'payment_date': '01/03/2099'}]
rows, audit = updated('u_multi', 'owner', {'events': ['[{"amount": "25.00", "id": 0, "payment_date": "10/01/2099"}]',
                                                      json.dumps(new_events, sort_keys=True)]}, events=new_events)
add('update-events', 'owner', f'/course/{cid("u_multi")}/update', 200, UPDATED, method='PATCH',
    body={'events': [{'id': 1, 'amount': 20, 'payment_date': '01/02/2099'}, {'id': '2', 'amount': '30.5', 'payment_date': '1/3/2099', 'paid': 'x'}]},
    result=effect('owner', rows=rows, audit=audit))
add('update-event-errors', 'owner', f'/course/{cid("u_multi")}/update', 400,
    {'id': ['Ensure this value is less than or equal to 30.'], 'amount': ['This field is required.'], 'payment_date': ['This field is required.']},
    method='PATCH', body={'events': [{'id': 31}]})
KEY = 'course_subscription_installment_id'
TAKEN = 'course subscription installment with this course subscription installment id already exists.'
add('update-event-installment-id-malformed', 'owner', f'/course/{cid("u_multi")}/update', 400, {KEY: ['Must be a valid UUID.']},
    method='PATCH', body={'events': [{'id': 0, 'amount': '1', 'payment_date': '01/01/2099', KEY: 'abc'}]})
add('update-event-installment-id-taken', 'owner', f'/course/{cid("u_multi")}/update', 400,
    {KEY: [TAKEN], 'payment_date': ['Date has wrong format. Use one of these formats instead: DD/MM/YYYY.']},
    method='PATCH', body={'events': [{'id': 0, 'amount': '1', 'payment_date': '\u0661\u0660/01/2099', KEY: iid('u2_b').upper()}]})
add('update-event-not-object', 'owner', f'/course/{cid("u_multi")}/update', 400,
    {'non_field_errors': ['Invalid data. Expected a dictionary, but got int.']}, method='PATCH', body={'events': [3]})
# Not atomic: the locations are replaced before the event fails.
add('update-event-invalid-after-locations', 'owner', f'/course/{cid("u_multi")}/update', 400, {'amount': ['A valid number is required.']},
    method='PATCH', body={'locations': [{'course_location_id': lid('own')}], 'events': [{'id': 0, 'amount': 'x', 'payment_date': '01/01/2099'}]},
    result=effect('owner', links={LOCATION_LINK: {'add': [[cid('u_multi'), lid('own')]]}}))
rows, audit = updated('u_plain', 'full_collaborator', {'fee': ['50.00', '70']}, fee='70.00')
add('update-ignored-fields', 'full_collaborator', f'/course/{cid("u_plain")}/update', 200, UPDATED, method='PATCH',
    body={'one_fee': 99, 'events': [{'id': 0}], 'fee': 70}, result=effect('full_collaborator', rows=rows, audit=audit))
# Only a course of the caller's association: athletes and owners without one are denied.
add('update-athlete-denied', 'athlete', f'/course/{cid("u_plain")}/update', 403, NOT_ALLOWED, method='PATCH',
    body={'fee': None, 'multiple_quotes': {'a': 'è'}})
add('update-owner-without-association', 'owner_without_association', f'/course/{cid("u_plain")}/update', 403, NOT_ALLOWED,
    method='PATCH', body={'fee': None})
rows, audit = updated('u_plain', 'owner', {'fee': ['70.00', 'None'], 'multiple_quotes': ['null', '{"a": "\\u00e8"}']}, fee=None,
                      multiple_quotes={'a': 'è'})
add('update-clear-fee', 'owner', f'/course/{cid("u_plain")}/update', 200, UPDATED, method='PATCH',
    body={'fee': None, 'multiple_quotes': {'a': 'è'}}, result=effect('owner', rows=rows, audit=audit))
add('update-same-values', 'owner', f'/course/{cid("u_plain")}/update', 200, UPDATED, method='PATCH',
    body={'fee': None, 'title': 'Scherma', 'multiple_quotes': {'a': 'è'}, 'locations': []}, result=effect('owner'))
add('update-empty-body', 'owner', f'/course/{cid("u_plain")}/update', 200, UPDATED, method='PATCH', body={}, result=effect('owner'))
rows, audit = updated('u_session', 'owner', {'description': ['Descrizione Ritmica', 'Sessione']}, description='Sessione')
add('update-impersonated-collaborator', 'owner', f'/course/{cid("u_session")}/update', 200, UPDATED, method='PATCH', headers=FULL_SESSION,
    body={'description': 'Sessione'}, result=effect('owner', rows=rows, audit=audit))
add('update-impersonated-foreign-hidden', 'owner', f'/course/{cid("u_far")}/update', 403, HIDDEN, method='PATCH', headers=FULL_SESSION,
    body={'description': 'Altra'})
add('update-other-association-course-denied', 'owner', f'/course/{cid("u_far")}/update', 403, NOT_ALLOWED, method='PATCH',
    body={'description': 'Altra', 'locations': []})
add('update-other-association-owner', 'foreign_owner', f'/course/{cid("u_main")}/update', 403, NOT_ALLOWED, method='PATCH',
    body={'title': 'Altro'})
rows, audit = updated('u_group', 'owner', {'title': ['Gruppo', 'Gruppo 2'], 'end_date': ['None', '2026-12-31 10:00:00']}, title='Gruppo 2',
                      end_date='2026-12-31T10:00:00Z')
add('update-grouped', 'owner', f'/course/{cid("u_group")}/update', 200, UPDATED, method='PATCH', headers=GROUP('own_group'),
    body={'title': 'Gruppo 2', 'end_date': '2026-12-31T10:00:00Z', 'start_date': None}, result=effect('owner', rows=rows, audit=audit))
# Course.save() dispatches update_metadata_courses for any title change; the callback
# returns before reading payments when the old title is empty or null.
rows, audit = updated('u_blank', 'owner', {'title': ['', 'Nuovo corso']}, title='Nuovo corso')
add('update-title-from-blank', 'owner', f'/course/{cid("u_blank")}/update', 200, UPDATED, method='PATCH', body={'title': 'Nuovo corso'},
    result=effect('owner', rows=rows, audit=audit))
rows, audit = updated('u_null', 'owner', {'title': ['None', 'Nuovo corso']}, title='Nuovo corso')
add('update-title-from-null', 'owner', f'/course/{cid("u_null")}/update', 200, UPDATED, method='PATCH', body={'title': 'Nuovo corso'},
    result=effect('owner', rows=rows, audit=audit))
# A new empty title removes the old one from the matching payments.
rows, audit = updated('u_clear', 'owner', {'title': ['Ginnastica', '']}, title='')
rows[PAYMENT] = {pid('pm_clear'): change_row(PAYMENT, 'pm_clear', description='Iscrizione  2026', meta={'course_title': '', 'n': 1})}
add('update-title-to-empty', 'owner', f'/course/{cid("u_clear")}/update', 200, UPDATED, method='PATCH', body={'title': ''},
    result=effect('owner', rows=rows, audit=audit))

# Raw assignments Python accepts in other spellings: decimals as exponent text or floats (rounded by the column),
# booleans as letters or floats, integers as floats or text, dates without zero padding or with an offset.
rows, audit = updated('u_forms', 'owner', {
    'fee': ['50.00', '1e1'], 'one_fee': ['40.00', '12.345'], 'billed_duration_is_sport_season': ['False', 't'],
    'billed_from_subscription_date': ['False', '1.0'], 'auto_renewal': ['False', '0'], 'billed_frequency': ['1', '2.9'],
    'billed_from_day_of_month': ['1', '7'], 'start_date': ['None', '2026-09-01 00:00:00'],
    'end_date': ['None', '2027-06-30 14:30:15.250000']},
    fee='10.00', one_fee='12.35', billed_duration_is_sport_season=True, billed_from_subscription_date=True, billed_frequency=2,
    billed_from_day_of_month=7, start_date='2026-09-01T00:00:00Z', end_date='2027-06-30T14:30:15.250000Z')
add('update-raw-value-forms', 'owner', f'/course/{cid("u_forms")}/update', 200, UPDATED, method='PATCH', body={
    'fee': '1e1', 'one_fee': 12.345, 'billed_duration_is_sport_season': 't', 'billed_from_subscription_date': 1.0, 'auto_renewal': '0',
    'billed_frequency': 2.9, 'billed_from_day_of_month': '7', 'start_date': '2026-9-1', 'end_date': '2027-06-30T16:30:15.250000+02:00'},
    result=effect('owner', rows=rows, audit=audit))
# A value the column rejects: auditlog's entry is written, then the UPDATE fails.
for name, body, changes in (
        ('fee-text', {'fee': 'abc'}, {'fee': ['50.00', 'abc']}),
        ('fee-too-large', {'fee': '12345678.9'}, {'fee': ['50.00', '12345678.9']}),
        ('billed-frequency-range', {'billed_frequency': 40000}, {'billed_frequency': ['1', '40000']}),
        ('boolean-text', {'auto_renewal': 'maybe'}, {'auto_renewal': ['False', 'maybe']}),
        ('boolean-null', {'billed_from_subscription_date': None}, {'billed_from_subscription_date': ['False', 'None']})):
    add(f'update-rejected-{name}', 'owner', f'/course/{cid("u_reject")}/update', 500, SERVER, method='PATCH', body=body,
        result=effect('owner', audit=[entry(COURSE, COURSES['u_reject']['course_id'], 1, changes, [OWN, DIRECT])]))

# --- course/<uid>/overview/<uid_subscription>/delete ----------------------------------

OV_DELETED = {'data': {'msg': 'associate deleted.'}}
od = lambda course_label, sub: f'/course/{cid(course_label)}/overview/{sid(sub)}/delete'
add('overview-delete-anonymous', 'anonymous', od('o_text', 'luisa'), 401, ANONYMOUS, method='DELETE')
add('overview-delete-restricted-missing-permission', 'restricted_collaborator', od('o_text', 'luisa'), 403,
    missing('association.courses.update'), method='DELETE')
add('overview-delete-wrong-method', 'owner', od('o_text', 'luisa'), 405, method_not_allowed('POST'))
add('overview-delete-options', 'owner', od('o_text', 'luisa'), 200, options('Course Overview Delete'), method='OPTIONS')
add('overview-delete-malformed-course', 'owner', f'/course/abc/overview/{sid("luisa")}/delete', 400, MALFORMED, method='DELETE')
add('overview-delete-malformed-subscription', 'owner', f'/course/{cid("o_text")}/overview/abc/delete', 400, MALFORMED, method='DELETE')
add('overview-delete-athlete-server-error', 'athlete', od('o_text', 'mario'), 500, SERVER, method='DELETE')
add('overview-delete-unknown-course', 'owner', f'/course/{UNKNOWN}/overview/{sid("luisa")}/delete', 500, SERVER, method='DELETE')
add('overview-delete-foreign-course', 'owner', od('u_far', 'hans'), 403, NOT_ALLOWED, method='DELETE')
add('overview-delete-missing-row', 'owner', od('o_text', 'mario'), 500, SERVER, method='DELETE')
add('overview-delete-installments-text', 'owner', od('o_text', 'luisa'), 500, SERVER, method='DELETE', body={'installments': 'abc'})


def soft_deleted(label, actor, memory=None, changes=None):
    row = CS[label]
    representation = cs_repr({**row, 'deleted': True}, **(memory or {}))
    change_row(CSM, label, deleted=True)
    return entry(CSM, row['course_subscription_id'], 1, changes or {'deleted': ['False', 'True']}, [OWN, COURSE_INDEX], representation)


actor = 'owner'
audit = [deleted(PAYMENT, 'o1_pa', actor), deleted(CSI, 'o1_a', actor), deleted(PAYMENT, 'o1_pa', actor), deleted(CSI, 'o1_a', actor),
         deleted(CSI, 'o1_b', actor), deleted(CSI, 'o1_c', actor), soft_deleted('o1', actor)]
add('overview-delete-listed-installments', actor, od('o_del', 'luisa'), 200, OV_DELETED, method='DELETE',
    body={'installments': [iid('o1_a'), iid('o1_b'), iid('o1_x'), UNKNOWN, None]},
    result=effect(actor, rows={CSM: {csid('o1'): {'deleted': True}}, CSI: {iid('o1_a'): None, iid('o1_b'): None, iid('o1_c'): None},
                               PAYMENT: {pid('o1_pa'): None}}, audit=audit))
actor = 'full_collaborator'
audit = [deleted(PAYMENT, 'o2_pd', actor), deleted(CSI, 'o2_d', actor), deleted(PAYMENT, 'o2_pd', actor), deleted(CSI, 'o2_d', actor),
         deleted(CSI, 'o2_f', actor), deleted(PAYMENT, 'o2_pe', actor), deleted(CSI, 'o2_e', actor), deleted(PAYMENT, 'o2_pe', actor),
         deleted(CSI, 'o2_e', actor), soft_deleted('o2', actor)]
add('overview-delete-unpaid-installments', actor, od('o_else', 'luisa'), 200, OV_DELETED, method='DELETE', result=effect(
    actor, rows={CSM: {csid('o2'): {'deleted': True}}, CSI: {iid('o2_d'): None, iid('o2_e'): None, iid('o2_f'): None},
                 PAYMENT: {pid('o2_pd'): None, pid('o2_pe'): None}}, audit=audit))
# The row's own payment is deleted with its installment: the soft delete's save still
# carries it (auditlog records the entry), then the deferred foreign key fails.
actor = 'owner'
row = CS['o3']
audit = [deleted(PAYMENT, 'o3_p', actor), deleted(CSI, 'o3_a', actor), deleted(PAYMENT, 'o3_p', actor), deleted(CSI, 'o3_a', actor),
         entry(CSM, row['course_subscription_id'], 1, {'payment': ['None', pid('o3_p')], 'deleted': ['False', 'True']}, [OWN, COURSE_INDEX],
               cs_repr({**row, 'deleted': True}))]
add('overview-delete-own-payment-fails', actor, od('o_stale', 'luisa'), 500, SERVER, method='DELETE', result=effect(
    actor, rows={CSM: {csid('o3'): change_row(CSM, 'o3', payment_id=None)}, CSI: {iid('o3_a'): None}, PAYMENT: {pid('o3_p'): None}},
    audit=audit))
add('overview-delete-installments-empty-text', 'owner', od('o_text', 'luisa'), 200, OV_DELETED, method='DELETE', body={'installments': ''},
    result=effect('owner', rows={CSM: {csid('o4'): {'deleted': True}}}, audit=[soft_deleted('o4', 'owner')]))

add('overview-delete-impersonated-collaborator', 'owner', od('o_session', 'luisa'), 200, OV_DELETED, method='DELETE', headers=FULL_SESSION,
    result=effect('owner', rows={CSM: {csid('o5'): {'deleted': True}}}, audit=[soft_deleted('o5', 'owner')]))
add('overview-delete-impersonated-foreign-hidden', 'owner', od('u_far', 'hans'), 403, HIDDEN, method='DELETE', headers=FULL_SESSION)

# --- course/<uid>/overview/<uid_subscription>/update ----------------------------------

OV_UPDATED = {'data': {'msg': 'course subscription update.'}}
ou = lambda course_label, sub: f'/course/{cid(course_label)}/overview/{sid(sub)}/update'
add('overview-update-anonymous', 'anonymous', ou('o_upd', 'luisa'), 401, ANONYMOUS, body={})
add('overview-update-restricted-missing-permission', 'restricted_collaborator', ou('o_upd', 'luisa'), 403,
    missing('association.courses.update'), body={})
add('overview-update-wrong-method', 'owner', ou('o_upd', 'luisa'), 405, method_not_allowed('GET'), method='GET')
add('overview-update-options', 'owner', ou('o_upd', 'luisa'), 200, options('Course Overview Update', OV_UPDATE_DOC), method='OPTIONS')
add('overview-update-malformed-course', 'owner', f'/course/abc/overview/{sid("luisa")}/update', 400, MALFORMED, body={})
add('overview-update-malformed-subscription', 'owner', f'/course/{cid("o_upd")}/overview/abc/update', 400, MALFORMED, body={})
add('overview-update-missing-row', 'owner', ou('o_upd', 'hans'), 500, SERVER, body={'multi_payments': True})
add('overview-update-foreign-course', 'owner', ou('u_far', 'hans'), 403, NOT_ALLOWED, body={'multi_payments': True})
add('overview-update-owner-without-association', 'owner_without_association', ou('o_upd', 'luisa'), 403, NOT_ALLOWED,
    body={'multi_payments': True})
add('overview-update-athlete-other-subscription', 'athlete', ou('o_upd', 'luisa'), 403, NOT_ALLOWED, body={'multi_payments': True})
add('overview-update-no-flags', 'owner', ou('o_upd', 'luisa'), 200, OV_UPDATED, body={'multi_payments': False, 'one_fee_payment': 0},
    result=effect('owner'))


def event_payment(sym, index, event, cs_label, course_label, *, user, associate):
    date = dt.datetime.strptime(event['payment_date'], '%d/%m/%Y').date()
    text = f"Rata n.{index + 1} del {date.strftime('%d/%m/%y')} ({COURSES[course_label]['title']})"
    meta = {'description': text, 'course_id': cid(course_label), 'course_title': COURSES[course_label]['title'],
            'course_subscription_id': csid(cs_label), 'payment_date': date.isoformat()}
    return new_payment(sym, event['amount'], user=user, associate=associate, created=date.isoformat() + 'T00:00:00Z', description=text,
                       meta=meta)


def created_entries(model, sym, index, **overrides):
    return entry(model, sym, 0, stored('create', **overrides), index)


luisa = (OWNER, ASSOCIATES['luisa']['associate_id'])
mario = (ATHLETE, ASSOCIATES['mario']['associate_id'])
# A single-payment row switches to instalments: its unpaid payment goes, the row is saved,
# then only future events are generated (the DD/MM/YYYY event fails validation and is skipped).
actor = 'owner'
row = CS['u1']
audit = [deleted(PAYMENT, 'u1_p', actor),
         entry(CSM, row['course_subscription_id'], 1, {'multi_payments': ['False', 'True'], 'one_fee_payment': ['True', 'False']},
               [OWN, COURSE_INDEX], cs_repr({**row, 'payment_id': None, 'multi_payments': True, 'one_fee_payment': False})),
         created_entries(PAYMENT, '@u1p1', [OWN, DIRECT]), created_entries(CSI, '@u1i1', [OWN, CSI_INDEX])]
add('overview-update-to-installments', actor, ou('o_upd', 'luisa'), 200, OV_UPDATED, body={'multi_payments': True, 'all': False}, result=effect(
    actor, rows={CSM: {csid('u1'): change_row(CSM, 'u1', payment_id=None, multi_payments=True, one_fee_payment=False)},
                 PAYMENT: {pid('u1_p'): None}},
    created={PAYMENT: [event_payment('@u1p1', 1, OV_EVENTS[1], 'u1', 'o_upd', user=luisa[0], associate=luisa[1])],
             CSI: [new_installment('@u1i1', csid('u1'), 1, '20.00', '@u1p1', '2099-01-10')]}, audit=audit))
# Instalment rows drop their unpaid and payment-less installments (the dropped installment's
# entry carries payment None), keep the paid one, and regenerate every valid event with all=True.
actor = 'athlete'
audit = [deleted(PAYMENT, 'u2_pa', actor), deleted(CSI, 'u2_a', actor), deleted(PAYMENT, 'u2_pa', actor),
         deleted(CSI, 'u2_a', actor, {'payment': ['None', 'None']}), deleted(CSI, 'u2_c', actor),
         created_entries(PAYMENT, '@u2p0', [OWN, DIRECT]), created_entries(CSI, '@u2i0', [OWN, CSI_INDEX]),
         created_entries(PAYMENT, '@u2p1', [OWN, DIRECT]), created_entries(CSI, '@u2i1', [OWN, CSI_INDEX])]
add('overview-update-regenerate-athlete', actor, ou('o_upd', 'mario'), 200, OV_UPDATED, body={'multi_payments': 'yes', 'all': True}, result=effect(
    actor, rows={CSI: {iid('u2_a'): None, iid('u2_c'): None}, PAYMENT: {pid('u2_pa'): None}},
    created={PAYMENT: [event_payment('@u2p0', 0, OV_EVENTS[0], 'u2', 'o_upd', user=mario[0], associate=mario[1]),
                       event_payment('@u2p1', 1, OV_EVENTS[1], 'u2', 'o_upd', user=mario[0], associate=mario[1])],
             CSI: [new_installment('@u2i0', csid('u2'), 0, '20.00', '@u2p0', '2020-01-10'),
                   new_installment('@u2i1', csid('u2'), 1, '20.00', '@u2p1', '2099-01-10')]}, audit=audit))
# Instalments to one fee: the course's one_fee payment, its description taken from meta.
actor = 'full_collaborator'
row = CS['u3']
text = "Pagamento in un'unica soluzione per Scherma"
one_fee_meta = {'one_fee': 'true', 'description': text, 'course_id': cid('u_plain'), 'course_title': 'Scherma', 'course_type': '1',
                'payment_date': '2026-06-01'}
audit = [deleted(PAYMENT, 'u3_pa', actor), deleted(CSI, 'u3_a', actor), deleted(PAYMENT, 'u3_pa', actor),
         deleted(CSI, 'u3_a', actor, {'payment': ['None', 'None']}), deleted(CSI, 'u3_b', actor), created_entries(PAYMENT, '@u3p', [OWN, DIRECT]),
         entry(CSM, row['course_subscription_id'], 1, {'payment': ['None', '@u3p'], 'multi_payments': ['True', 'False'],
                                                       'one_fee_payment': ['False', 'True']},
               [OWN, COURSE_INDEX], cs_repr({**row, 'multi_payments': False, 'one_fee_payment': True}, payment_id="UUID('@u3p')"))]
add('overview-update-to-one-fee', actor, ou('u_plain', 'luisa'), 200, OV_UPDATED, body={'one_fee_payment': True}, result=effect(
    actor, rows={CSM: {csid('u3'): change_row(CSM, 'u3', payment_id='@u3p', multi_payments=False, one_fee_payment=True)},
                 CSI: {iid('u3_a'): None, iid('u3_b'): None}, PAYMENT: {pid('u3_pa'): None}},
    created={PAYMENT: [new_payment('@u3p', '0.00', user=luisa[0], associate=luisa[1], description=text, meta=one_fee_meta)]}, audit=audit))
add('overview-update-one-fee-single-payment', 'owner', ou('u_multi', 'luisa'), 200, OV_UPDATED, body={'one_fee_payment': True},
    result=effect('owner'))
# Impersonating an athlete: the athlete's own instalment row switches to one fee in the subscription's association.
actor = 'owner'
row = CS['o6']
text = "Pagamento in un'unica soluzione per Hockey"
session_meta = {'one_fee': 'true', 'description': text, 'course_id': cid('o_session_athlete'), 'course_title': 'Hockey', 'course_type': '1',
                'payment_date': '2026-06-01'}
audit = [created_entries(PAYMENT, '@u6p', [OWN, DIRECT]),
         entry(CSM, row['course_subscription_id'], 1, {'payment': ['None', '@u6p'], 'multi_payments': ['True', 'False'],
                                                       'one_fee_payment': ['False', 'True']},
               [OWN, COURSE_INDEX], cs_repr({**row, 'multi_payments': False, 'one_fee_payment': True}, payment_id="UUID('@u6p')"))]
add('overview-update-impersonated-athlete', actor, ou('o_session_athlete', 'mario'), 200, OV_UPDATED, body={'one_fee_payment': True},
    headers=ATHLETE_SESSION, result=effect(
        actor, rows={CSM: {csid('o6'): change_row(CSM, 'o6', payment_id='@u6p', multi_payments=False, one_fee_payment=True)}},
        created={PAYMENT: [new_payment('@u6p', '15.00', user=mario[0], associate=mario[1], description=text, meta=session_meta)]}, audit=audit))
add('overview-update-impersonated-collaborator', 'owner', ou('o_upd', 'luisa'), 200, OV_UPDATED, headers=FULL_SESSION,
    body={'multi_payments': False}, result=effect('owner'))
add('overview-update-impersonated-athlete-other-subscription', 'owner', ou('o_upd', 'luisa'), 403, HIDDEN, headers=ATHLETE_SESSION,
    body={'one_fee_payment': True})

# Instalments under a non-canonical X-Group-Id: each created row is audited with the header as sent and saved once more.
actor = 'owner'
row = CS['u7']
audit = [deleted(PAYMENT, 'u7_p', actor),
         entry(CSM, row['course_subscription_id'], 1, {'multi_payments': ['False', 'True']}, [OWN, COURSE_INDEX],
               cs_repr({**row, 'payment_id': None, 'multi_payments': True})),
         created_entries(PAYMENT, '@u7p0', [OWN, DIRECT], group=['None', RAW_GROUP]),
         entry(PAYMENT, '@u7p0', 1, {'group': [OWN_GROUP, RAW_GROUP]}, [OWN, DIRECT]),
         created_entries(CSI, '@u7i0', [OWN, CSI_INDEX], group=['None', RAW_GROUP]),
         entry(CSI, '@u7i0', 1, {'group': [OWN_GROUP, RAW_GROUP]}, [OWN, CSI_INDEX])]
raw_event = event_payment('@u7p0', 0, COURSES['o_raw']['events'][0], 'u7', 'o_raw', user=luisa[0], associate=luisa[1])
raw_event['group_id'] = OWN_GROUP
add('overview-update-to-installments-raw-group', actor, ou('o_raw', 'group'), 200, OV_UPDATED, headers=RAW, body={'multi_payments': True, 'all': False},
    result=effect(actor, rows={CSM: {csid('u7'): change_row(CSM, 'u7', payment_id=None, multi_payments=True)}, PAYMENT: {pid('u7_p'): None}},
                  created={PAYMENT: [raw_event], CSI: [new_installment('@u7i0', csid('u7'), 0, '20.00', '@u7p0', '2099-01-10', group=OWN_GROUP)]},
                  audit=audit))

# Not atomic: data['all'] is read only for a past event, after the payment and the save.
actor = 'owner'
row = CS['u4']
audit = [deleted(PAYMENT, 'u4_p', actor),
         entry(CSM, row['course_subscription_id'], 1, {'multi_payments': ['False', 'True']}, [OWN, COURSE_INDEX],
               cs_repr({**row, 'payment_id': None, 'multi_payments': True}))]
add('overview-update-without-all-fails', actor, ou('o_upd', 'group'), 500, SERVER, body={'multi_payments': 1}, result=effect(
    actor, rows={CSM: {csid('u4'): change_row(CSM, 'u4', payment_id=None, multi_payments=True)}, PAYMENT: {pid('u4_p'): None}},
    audit=audit))

# A body that is not an object: the views test their keys in it (list membership, substring). Holding a key, or not
# supporting the test, fails before any write; holding none reads as an empty object.
for name, body in (('list-with-key', ['multi_payments']), ('text-with-key', 'the one_fee_payment flag'), ('number', 5)):
    add(f'overview-update-body-{name}-server-error', 'owner', ou('o_upd', 'luisa'), 500, SERVER, body=body)
add('overview-update-body-list-without-key', 'owner', ou('o_upd', 'luisa'), 200, OV_UPDATED, body=['all', 7], result=effect('owner'))
add('overview-update-body-text-without-key', 'owner', ou('o_upd', 'luisa'), 200, OV_UPDATED, body='multi', result=effect('owner'))
for name, body in (('list-with-key', ['installments']), ('text-with-key', 'installments'), ('boolean', True)):
    add(f'overview-delete-body-{name}-server-error', 'owner', od('o_upd', 'luisa'), 500, SERVER, method='DELETE', body=body)

# --- course/add ------------------------------------------------------------------

ADDED = {'status': 'success'}
VALIDATION = {'msg': 'validation errors.'}
NOT_VALID = lambda message: {'msg:': 'not valid course information.', 'details': {'events': [message]}}
course_body = lambda **values: {'title': 'Base', 'description': 'Note', **values}
add('add-anonymous', 'anonymous', '/course/add', 401, ANONYMOUS, body={})
add('add-restricted-missing-permission', 'restricted_collaborator', '/course/add', 403, missing('association.courses.create'), body={})
add('add-owner-scoped-athlete-denied', 'owner', '/course/add', 403, SCOPED, body={}, headers=ATHLETE_SESSION)
add('add-wrong-method', 'owner', '/course/add', 405, method_not_allowed('GET'), method='GET')
add('add-options', 'owner', '/course/add', 200, options('Course Add', ADD_DOC), method='OPTIONS')
add('add-missing-key', 'owner', '/course/add', 400, VALIDATION, body={'new_course': course_body()})
add('add-extra-key', 'owner', '/course/add', 400, VALIDATION, body={'new_course': course_body(), 'subscriptions': [], 'x': 1})
add('add-list-body', 'owner', '/course/add', 500, SERVER, body=[])
add('add-athlete-invalid-body', 'athlete', '/course/add', 400, VALIDATION, body={})
add('add-athlete-server-error', 'athlete', '/course/add', 500, SERVER, body={'new_course': course_body(), 'subscriptions': []})
add('add-new-course-not-object', 'owner', '/course/add', 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got str.']},
    body={'new_course': 'x', 'subscriptions': []})
add('add-blank-title', 'owner', '/course/add', 400, {'title': ['This field may not be blank.']},
    body={'new_course': course_body(title='  '), 'subscriptions': []})
add('add-field-errors', 'owner', '/course/add', 400, {
    'title': ['Ensure this field has no more than 255 characters.'], 'fee': ['A valid number is required.'],
    'one_fee': ['Ensure that there are no more than 2 decimal places.'], 'course_type': ['"9" is not a valid choice.'],
    'billed_frequency': ['Ensure this value is greater than or equal to 0.'],
    'billed_from_day_of_month': ['Ensure this value is less than or equal to 28.'], 'auto_renewal': ['Must be a valid boolean.']},
    body={'new_course': course_body(title='x' * 256, fee='abc', one_fee='1.234', course_type=9, billed_frequency=-1, billed_from_day_of_month=29,
                                    auto_renewal='maybe'), 'subscriptions': []})
add('add-negative-fee', 'owner', '/course/add', 400, {'fee': ['Ensure this value is greater than or equal to 0.0.']},
    body={'new_course': course_body(fee='-1'), 'subscriptions': []})
add('add-missing-title', 'owner', '/course/add', 500, SERVER, body={'new_course': {'description': 'Note'}, 'subscriptions': []})
ev = lambda number, amount, date='10/01/2099': {'id': number, 'amount': amount, 'payment_date': date}
add('add-installments-without-events', 'owner', '/course/add', 400, NOT_VALID('This field is required.'),
    body={'new_course': course_body(fee='10', multi_payments_split=True, events=[]), 'subscriptions': []})
add('add-events-not-allowed', 'owner', '/course/add', 400, NOT_VALID('This field is not allowed.'),
    body={'new_course': course_body(fee='10', events=[ev(0, '10')]), 'subscriptions': []})
add('add-one-event', 'owner', '/course/add', 400, NOT_VALID('The minimum number of events is 2.'),
    body={'new_course': course_body(fee='10', multi_payments_split=True, events=[ev(0, '10')]), 'subscriptions': []})
add('add-event-over-fee', 'owner', '/course/add', 400, NOT_VALID('The amount of the event cannot be greater than the course fee.'),
    body={'new_course': course_body(fee='10', multi_payments_split=True, events=[ev(0, '5'), ev(1, '11')]), 'subscriptions': []})
add('add-events-sum', 'owner', '/course/add', 400, NOT_VALID('The sum of the events amount must be equal to the course fee.'),
    body={'new_course': course_body(fee='10', multi_payments_split=True, events=[ev(0, 5), ev(1, '4.99')]), 'subscriptions': []})
add('add-event-invalid', 'owner', '/course/add', 400, {'payment_date': ['Date has wrong format. Use one of these formats instead: DD/MM/YYYY.']},
    body={'new_course': course_body(fee='10', multi_payments_split=True, events=[ev(0, '5', '2099-01-10'), ev(1, '5')]), 'subscriptions': []})
# The events' installment key is validated (a UUID no live installment of the request's group has) and their dates
# are read as Python reads them; the amounts are compared afterwards.
SUM = NOT_VALID('The sum of the events amount must be equal to the course fee.')
keyed = lambda value, date='10/01/2099': {**ev(0, '5', date), 'course_subscription_installment_id': value}
for name, value, expected in (('null', None, {KEY: ['This field may not be null.']}), ('float', 1.5, {KEY: ['Must be a valid UUID.']}),
                              ('taken', iid('u2_b'), {KEY: [TAKEN]}), ('unknown', UNKNOWN, SUM), ('integer', 7, SUM)):
    add(f'add-event-installment-id-{name}', 'owner', '/course/add', 400, expected,
        body={'new_course': course_body(fee='10', multi_payments_split=True, events=[keyed(value), ev(1, '4')]), 'subscriptions': []})
add('add-event-installment-id-other-group', 'owner', '/course/add', 400, SUM, headers=GROUP('own_group'),
    body={'new_course': course_body(fee='10', multi_payments_split=True, events=[keyed(iid('u2_b')), ev(1, '4')]), 'subscriptions': []})
add('add-event-date-other-digits', 'owner', '/course/add', 400, SUM,
    body={'new_course': course_body(fee='10', multi_payments_split=True,
                                    events=[ev(0, '5', '2\u0669/01/\u0662\u0660\u0669\u0669'), ev(1, '4')]), 'subscriptions': []})
add('add-event-month-other-digits-invalid', 'owner', '/course/add', 400,
    {'payment_date': ['Date has wrong format. Use one of these formats instead: DD/MM/YYYY.']},
    body={'new_course': course_body(fee='10', multi_payments_split=True, events=[ev(0, '5', '10/\u0660\u0661/2099'), ev(1, '5')]),
          'subscriptions': []})
# Float amounts are compared by their binary value: 0.1 + 0.2 is not 0.3.
add('add-event-float-amounts-sum', 'owner', '/course/add', 400, SUM,
    body={'new_course': course_body(fee='0.3', multi_payments_split=True, events=[ev(0, 0.1), ev(1, 0.2)]), 'subscriptions': []})
add('add-event-float-amount-not-exact', 'owner', '/course/add', 400, SUM,
    body={'new_course': course_body(fee='10', multi_payments_split=True, events=[ev(0, 5), ev(1, 4.99)]), 'subscriptions': []})
add('add-event-not-object', 'owner', '/course/add', 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got str.']},
    body={'new_course': course_body(fee='10', multi_payments_split=True, events=['a', 'b']), 'subscriptions': []})

# Course.objects.create (association None: the entry resolves through the actor), then the
# association save (auditlog writes before the UPDATE: resolved through its changes); the
# serializer's float defaults ('0.0') differ from the stored 0.00.
course_entries = lambda sym, actor, defaults=(): [
    created_entries(COURSE, sym, actor_index(actor), sport_association=['None', 'None'], **{name: ['None', '0.0'] for name in defaults}),
    entry(COURSE, sym, 1, {'sport_association': ['None', OWN], **{name: ['0.00', '0.0'] for name in defaults}}, [OWN, CHANGES_INDEX])]
actor = 'owner'
audit = course_entries('@nc1', actor, ('one_fee',)) + [
    entry(CSM, '@cs1', 0, stored('create', membership_fee=['None', '0.0'], payment=['None', 'None']), [OWN, COURSE_INDEX], new_cs_repr('@cs1', '@nc1', sid('luisa'), False)),
    created_entries(PAYMENT, '@p1', [OWN, DIRECT]),
    entry(CSM, '@cs1', 1, {'payment': ['None', '@p1'], 'membership_fee': ['0.00', '0.0']}, [OWN, COURSE_INDEX],
          new_cs_repr('@cs1', '@nc1', sid('luisa'), False, '@p1'))]
add('add-course-fee', actor, '/course/add', 200, ADDED, body={
    'new_course': {'title': 'Yoga <i>', 'description': 'Mattina > sera', 'fee': '50', 'locations': [
        {'course_location_id': lid('own')}, {'course_location_id': lid('far')}, {'course_location_id': UNKNOWN}]},
    'subscriptions': [sid('luisa')]}, result=effect(actor, created={
        COURSE: [new_course('@nc1', 'Yoga &lt;i&gt;', 'Mattina &gt; sera', fee='50.00')],
        CSM: [new_cs('@cs1', '@nc1', sid('luisa'), False, '@p1')],
        PAYMENT: [new_payment('@p1', '50.00', user=luisa[0], associate=luisa[1])]},
        links={LOCATION_LINK: {'add': [['@nc1', lid('own')]]}}, audit=audit))
# Instalments: one installment per future event and subscription (past events need
# full_installments_plan); each row's final save records only the float membership_fee.
actor = 'full_collaborator'
events = [ev(0, '30.00'), ev(1, 30, '10/01/2020')]
audit = course_entries('@nc2', actor)
created = {COURSE: [new_course('@nc2', 'Corsa', 'Serale', fee='60.00', one_fee='20.50', one_fee_payment=True, multi_payments=True, events=events,
                               course_type=2, billed_frequency=2, billed_from_day_of_month=5, auto_renewal=True)],
           CSM: [], PAYMENT: [], CSI: []}
for n, (sub, person) in enumerate((('mario', mario), ('luisa', luisa)), start=1):
    cs_sym, pay_sym, inst_sym = f'@cs2{n}', f'@p2{n}', f'@i2{n}'
    created[CSM].append(new_cs(cs_sym, '@nc2', sid(sub), True))
    created[PAYMENT].append(new_payment(pay_sym, '30.00', user=person[0], associate=person[1], created='2099-01-10T00:00:00Z'))
    created[CSI].append(new_installment(inst_sym, cs_sym, 0, '30.00', pay_sym, '2099-01-10'))
    audit += [entry(CSM, cs_sym, 0, stored('create', membership_fee=['None', '0.0']), [OWN, COURSE_INDEX],
                    new_cs_repr(cs_sym, '@nc2', sid(sub), True)),
              created_entries(PAYMENT, pay_sym, [OWN, DIRECT]), created_entries(CSI, inst_sym, [OWN, CSI_INDEX]),
              entry(CSM, cs_sym, 1, {'membership_fee': ['0.00', '0.0']}, [OWN, COURSE_INDEX], new_cs_repr(cs_sym, '@nc2', sid(sub), True))]
add('add-installments', actor, '/course/add', 200, ADDED, body={
    'new_course': {'title': 'Corsa', 'description': 'Serale', 'fee': 60, 'one_fee': '20.5', 'one_fee_payment': True,
                   'multi_payments_split': True, 'events': events, 'course_type': '2', 'billed_frequency': 2, 'billed_from_day_of_month': 5,
                   'auto_renewal': True},
    'subscriptions': [sid('mario'), sid('luisa')]}, result=effect(actor, created=created, audit=audit))
actor = 'owner'
add('add-grouped-defaults', actor, '/course/add', 200, ADDED, headers=GROUP('own_group'),
    body={'new_course': {'title': 'Gruppo nuovo', 'description': 'Note'}, 'subscriptions': []},
    result=effect(actor, created={COURSE: [new_course('@nc3', 'Gruppo nuovo', 'Note', group_id=OWN_GROUP)]},
                  audit=course_entries('@nc3', actor, ('fee', 'one_fee'))))
# The defaulted float fee reaches the payment: its own second save records it.
audit = course_entries('@nc4', actor, ('fee', 'one_fee')) + [
    entry(CSM, '@cs4', 0, stored('create', membership_fee=['None', '0.0'], payment=['None', 'None']), [OWN, COURSE_INDEX], new_cs_repr('@cs4', '@nc4', sid('luisa'), False)),
    created_entries(PAYMENT, '@p4', [OWN, DIRECT], amount=['None', '0.0']),
    entry(PAYMENT, '@p4', 1, {'amount': ['0.00', '0.0']}, [OWN, DIRECT]),
    entry(CSM, '@cs4', 1, {'payment': ['None', '@p4'], 'membership_fee': ['0.00', '0.0']}, [OWN, COURSE_INDEX],
          new_cs_repr('@cs4', '@nc4', sid('luisa'), False, '@p4'))]
add('add-default-fee-payment', actor, '/course/add', 200, ADDED,
    body={'new_course': {'title': 'Gratis', 'description': 'Note'}, 'subscriptions': [sid('luisa')]},
    result=effect(actor, created={COURSE: [new_course('@nc4', 'Gratis', 'Note')], CSM: [new_cs('@cs4', '@nc4', sid('luisa'), False, '@p4')],
                                  PAYMENT: [new_payment('@p4', '0.00', user=luisa[0], associate=luisa[1])]}, audit=audit))
# A non-canonical X-Group-Id is stored as the UUID and audited as sent in every created row.
actor = 'owner'
events = [ev(0, '30.00'), ev(1, 30, '10/01/2020')]
cs_sym, pay_sym, inst_sym = '@cs9', '@p9', '@i9'
audit = [created_entries(COURSE, '@nc9', actor_index(actor), sport_association=['None', 'None'], group=['None', RAW_GROUP], one_fee=['None', '0.0']),
         entry(COURSE, '@nc9', 1, {'sport_association': ['None', OWN], 'group': [OWN_GROUP, RAW_GROUP], 'one_fee': ['0.00', '0.0']}, [OWN, CHANGES_INDEX]),
         entry(CSM, cs_sym, 0, stored('create', membership_fee=['None', '0.0']), [OWN, COURSE_INDEX], new_cs_repr(cs_sym, '@nc9', sid('group'), True)),
         created_entries(PAYMENT, pay_sym, [OWN, DIRECT], group=['None', RAW_GROUP]),
         entry(PAYMENT, pay_sym, 1, {'group': [OWN_GROUP, RAW_GROUP]}, [OWN, DIRECT]),
         created_entries(CSI, inst_sym, [OWN, CSI_INDEX], group=['None', RAW_GROUP]),
         entry(CSI, inst_sym, 1, {'group': [OWN_GROUP, RAW_GROUP]}, [OWN, CSI_INDEX]),
         entry(CSM, cs_sym, 1, {'membership_fee': ['0.00', '0.0']}, [OWN, COURSE_INDEX], new_cs_repr(cs_sym, '@nc9', sid('group'), True))]
add('add-installments-raw-group', actor, '/course/add', 200, ADDED, headers=RAW, body={
    'new_course': {'title': 'Gruppo grezzo', 'description': 'Serale', 'fee': 60, 'multi_payments_split': True, 'events': events},
    'subscriptions': [sid('group')]}, result=effect(actor, created={
        COURSE: [new_course('@nc9', 'Gruppo grezzo', 'Serale', fee='60.00', multi_payments=True, events=events, group_id=OWN_GROUP)],
        CSM: [new_cs(cs_sym, '@nc9', sid('group'), True)],
        PAYMENT: [new_payment(pay_sym, '30.00', user=luisa[0], associate=luisa[1], created='2099-01-10T00:00:00Z', group=OWN_GROUP)],
        CSI: [new_installment(inst_sym, cs_sym, 0, '30.00', pay_sym, '2099-01-10', group=OWN_GROUP)]}, audit=audit))

# Single payment, group spelled with braces and mixed case: the payment records the header as sent twice.
BRACED = '{' + IDS['own_group'].upper() + '}'
audit = [created_entries(COURSE, '@nc10', actor_index(actor), sport_association=['None', 'None'], group=['None', BRACED], one_fee=['None', '0.0'],
                         fee=['None', '0.0']),
         entry(COURSE, '@nc10', 1, {'sport_association': ['None', OWN], 'group': [OWN_GROUP, BRACED], 'one_fee': ['0.00', '0.0'], 'fee': ['0.00', '0.0']},
               [OWN, CHANGES_INDEX]),
         entry(CSM, '@cs10', 0, stored('create', membership_fee=['None', '0.0'], payment=['None', 'None']), [OWN, COURSE_INDEX],
               new_cs_repr('@cs10', '@nc10', sid('group'), False)),
         created_entries(PAYMENT, '@p10', [OWN, DIRECT], amount=['None', '0.0'], group=['None', BRACED]),
         entry(PAYMENT, '@p10', 1, {'amount': ['0.00', '0.0'], 'group': [OWN_GROUP, BRACED]}, [OWN, DIRECT]),
         entry(CSM, '@cs10', 1, {'payment': ['None', '@p10'], 'membership_fee': ['0.00', '0.0']}, [OWN, COURSE_INDEX],
               new_cs_repr('@cs10', '@nc10', sid('group'), False, '@p10'))]
add('add-default-fee-braced-group', actor, '/course/add', 200, ADDED, headers={'X-Group-Id': BRACED},
    body={'new_course': {'title': 'Graffe', 'description': 'Note'}, 'subscriptions': [sid('group')]},
    result=effect(actor, created={COURSE: [new_course('@nc10', 'Graffe', 'Note', group_id=OWN_GROUP)],
                                  CSM: [new_cs('@cs10', '@nc10', sid('group'), False, '@p10')],
                                  PAYMENT: [new_payment('@p10', '0.00', user=luisa[0], associate=luisa[1], group=OWN_GROUP)]}, audit=audit))

# Not atomic: an unknown subscription fails after the course exists.
add('add-unknown-subscription', actor, '/course/add', 500, SERVER,
    body={'new_course': {'title': 'Parziale', 'description': 'Note', 'fee': '5'}, 'subscriptions': [UNKNOWN]},
    result=effect(actor, created={COURSE: [new_course('@nc5', 'Parziale', 'Note', fee='5.00')]}, audit=course_entries('@nc5', actor, ('one_fee',))))
# Subscriptions resolve within the association: another association's one fails like an unknown one.
add('add-other-association-subscription', actor, '/course/add', 500, SERVER,
    body={'new_course': {'title': 'Estera', 'description': 'Note', 'fee': '5'}, 'subscriptions': [sid('hans')]},
    result=effect(actor, created={COURSE: [new_course('@nc6', 'Estera', 'Note', fee='5.00')]}, audit=course_entries('@nc6', actor, ('one_fee',))))

# Float event amounts that are exact in binary (30.5) are stored as sent.
actor = 'owner'
events = [ev(0, 30.5), ev(1, 30.5, '10/01/2020')]
audit = course_entries('@nc8', actor, ('one_fee',))
created = {COURSE: [new_course('@nc8', 'Float', 'Nota', fee='61.00', multi_payments=True, events=events)], CSM: [], PAYMENT: [], CSI: []}
created[CSM].append(new_cs('@cs81', '@nc8', sid('luisa'), True))
created[PAYMENT].append(new_payment('@p81', '30.50', user=luisa[0], associate=luisa[1], created='2099-01-10T00:00:00Z'))
created[CSI].append(new_installment('@i81', '@cs81', 0, '30.50', '@p81', '2099-01-10'))
audit += [entry(CSM, '@cs81', 0, stored('create', membership_fee=['None', '0.0']), [OWN, COURSE_INDEX], new_cs_repr('@cs81', '@nc8', sid('luisa'), True)),
          created_entries(PAYMENT, '@p81', [OWN, DIRECT]), created_entries(CSI, '@i81', [OWN, CSI_INDEX]),
          entry(CSM, '@cs81', 1, {'membership_fee': ['0.00', '0.0']}, [OWN, COURSE_INDEX], new_cs_repr('@cs81', '@nc8', sid('luisa'), True))]
add('add-installments-float-amounts', actor, '/course/add', 200, ADDED, body={
    'new_course': {'title': 'Float', 'description': 'Nota', 'fee': 61, 'multi_payments_split': True, 'events': events},
    'subscriptions': [sid('luisa')]}, result=effect(actor, created=created, audit=audit))

# Impersonating a full collaborator: the course is created in the session's association.
add('add-impersonated-collaborator', actor, '/course/add', 200, ADDED, headers=FULL_SESSION,
    body={'new_course': {'title': 'Sessione nuova', 'description': 'Note'}, 'subscriptions': []},
    result=effect(actor, created={COURSE: [new_course('@nc7', 'Sessione nuova', 'Note')]}, audit=course_entries('@nc7', actor, ('fee', 'one_fee'))))
add('add-impersonated-foreign-subscription-hidden', actor, '/course/add', 403, HIDDEN, headers=FULL_SESSION,
    body={'new_course': {'title': 'Estera', 'description': 'Note'}, 'subscriptions': [sid('hans')]})

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Course add, update and delete and course-overview update and delete authored '
                              'sequence with row, link, audit and index effects', 'ids': IDS, 'unknown': UNKNOWN, 'category_name': CATEGORY_NAME,
                              **SEED, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
