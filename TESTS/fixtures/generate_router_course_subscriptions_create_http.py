"""Author the DefaultRouter course-subscriptions create HTTP sequence (router-stock.md).

POST on the router's course-subscriptions list route (and its format twin)
is CreateModelMixin.create on CourseSubscriptionViewSet: one item
(get_serializer without many, so a list body is the non-dict 400), the
viewset's perform_create(serializer) with is_athlete=False and no
transaction: serializer.create() (subscription, row, the body's or the
course's events as payments and installments), then for default and quote
courses add_course_subscription with User.sport_association, for memberships
the membership_created signal. get_success_headers(serializer.data) then
renders the validated dict, whose installments method field filters by the
dict and raises: every successful create answers the 500 page with its rows
kept. A failure after the first write keeps what was written (an invalid
event after the row; an athlete's or an association-less owner's
User.sport_association after the row). Athletes take the same path as
owners (no prefill). The seed, the request bodies and the effects of the
course-subscriptions/add successes come from the course enrolment fixture
(those requests write the same rows); the effects of the partial writes are
derived from them. Run with ``python3 -I`` after the course enrolment
generator.
"""
import copy
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('router_course_subscriptions_create_http.json')
ENROLMENT = json.loads(Path(__file__).with_name('course_enrolment_http.json').read_text())
IDS = ENROLMENT['ids']
OWN = IDS['own_association']
BY_NAME = {case['name'].removeprefix('course-enrolment-add-'): case for case in ENROLMENT['cases'] if case['path'] == '/course-subscriptions/add'}
SUBSCRIPTION_OPTIONS = next(case['response'] for case in json.loads(Path(__file__).with_name('course_subscriptions_http.json').read_text())['cases']
                            if case['name'] == 'course-subscriptions-list-options')
SERVER = '<stable-server-error-page>'
LIST = '/course-subscriptions/'
cases = []


def add(name, actor, path, status, response, *, method='POST', body=None, headers=None, effect=None, raw_body=None, content_type=None):
    case = {'name': f'router-course-subscriptions-create-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def reissue(name, source, *, path=LIST, status=None, response=None):
    """The add case's request on the router route; its writes are the same."""
    case = BY_NAME[source]
    add(name, case['actor'], path, status or case['status'], case['response'] if response is None else response, body=case.get('body'),
        headers=case['headers'], effect=copy.deepcopy(case['effect']), raw_body=case.get('raw_body'), content_type=case.get('content_type'))


def row_only(source, sym, *, actor, course=None, subscription=None, billed=True, float_fee=False):
    """The first created CourseSubscription of an add case's effect alone (the
    rows written before a later failure): its row and its create entry."""
    effect = copy.deepcopy(BY_NAME[source]['effect'])
    old = effect['created']['application.CourseSubscription'][0]['$sym']
    row = {**effect['created']['application.CourseSubscription'][0], '$sym': sym, 'course_subscription_id': sym, 'payment_id': None}
    entry = effect['audit'][0]
    text = entry['repr'].replace(old, sym)
    if course:
        text = text.replace(row['course_id'], course)
        row['course_id'] = course
    if subscription:
        text = text.replace(row['subscription_id'], subscription)
        row['subscription_id'] = subscription
    if not billed:
        for name in ('billed_from', 'billed_until'):
            start = text.index(f"'{name}': ") + len(name) + 4
            end = text.index(", 'billed_", start) if name == 'billed_from' else text.index(", 'billed_from_day", start)
            text = text[:start] + 'None' + text[end:]
            row[name] = None
    overrides = dict(entry['changes']['overrides'])
    if float_fee:
        text = text.replace("'membership_fee': Decimal('0.00')", "'membership_fee': 0.0")
        overrides['membership_fee'] = ['None', '0.0']
    entry = {**entry, 'key': sym, 'repr': text, 'changes': {'$stored': 'create', 'overrides': overrides}}
    return {'actor': actor, 'rows': {}, 'created': {'application.CourseSubscription': [row]}, 'links': {}, 'audit': [entry]}


ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
NOT_ASSOCIATION = {'detail': 'User is not a sport association'}
unmapped = lambda path: {'detail': f"Access denied: No permission mapping for endpoint '{path}'. Contact administrator to grant access."}
COURSE_FEE, COURSE_FREE, MEMBER = (BY_NAME['course-fee']['body']['course'], BY_NAME['event-errors']['body']['course'],
                                   BY_NAME['membership']['body']['course'])
MARIO = BY_NAME['athlete-denied']['body']['subscription_id']

# Admission, validation and the failures before any write.
add('anonymous', 'anonymous', LIST, 401, ANONYMOUS, body={})
add('restricted-unmapped', 'restricted_collaborator', LIST, 403, unmapped('course-subscriptions'), body={})
add('owner-scoped-athlete-denied', 'owner', LIST, 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}, body={},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('options', 'owner', LIST, 200, {**SUBSCRIPTION_OPTIONS, 'name': 'Course Subscription List'}, method='OPTIONS')
for source in ('empty-body', 'field-errors', 'deleted-course', 'text-body', 'membership-errors',
               'membership-other-association', 'unknown-subscription', 'group-header-hides-subscription'):
    reissue(source, source)
add('list-body-is-one-item', 'owner', LIST, 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']},
    body=BY_NAME['list']['body'])
add('format-unknown-not-found', 'owner', '/course-subscriptions.csv', 404, {'detail': 'Not found.'}, body={})
# Successful creates: the add route's writes, then the 500.
for source in ('course-fee', 'one-fee', 'installment-course', 'body-events-grouped', 'quote', 'quote-invalid-category', 'membership',
               'course-of-other-association'):
    reissue(source + '-server-error', source, status=500, response=SERVER)
# HTML form input reads like JSON apart from the boolean defaults: the same writes, then the 500.
for source in ('form-urlencoded', 'form-multipart', 'form-quote'):
    reissue(source + '-server-error', source, status=500, response=SERVER)
reissue('format-json-quote-not-selected-server-error', 'quote-not-selected', path='/course-subscriptions.json', status=500, response=SERVER)
reissue('format-json-slash-membership-without-fee-server-error', 'membership-without-fee', path='/course-subscriptions.json/', status=500,
        response=SERVER)
# Partial writes: the row stays before the failure.
add('event-errors-keep-row', 'owner', LIST, 400, BY_NAME['event-errors']['response'], body=BY_NAME['event-errors']['body'],
    effect=row_only('course-fee', '@re1', actor='owner', course=COURSE_FREE))
add('athlete-default-course-keeps-row', 'athlete', LIST, 403, NOT_ASSOCIATION, body=BY_NAME['athlete-denied']['body'],
    effect=row_only('course-fee', '@re2', actor='athlete', subscription=MARIO))
add('owner-without-association-keeps-row-server-error', 'owner_without_association', LIST, 500, SERVER,
    body=BY_NAME['owner-without-association']['body'], effect=row_only('course-fee', '@re3', actor='owner_without_association'))
add('athlete-membership-without-prefill-server-error', 'athlete', LIST, 500, SERVER, body=BY_NAME['athlete-membership-server-error']['body'],
    effect=row_only('membership-without-fee', '@re4', actor='athlete', billed=False, float_fee=True))

import urllib.parse

# The stock create's form JSON recursion bands (owner decision X15, see course-enrolment.md). D is the maximum nesting
# of the field's text. Its edge moves a level or two with the value's shape and the live stack, so the cases sit inside
# each band with a margin instead of at the edges: 9900 for today's answer, 9938 inside the row INSERT's dump band
# (9943 is still inside it) and 9980 past the measured validation edge (9943 for both fields).
# The narrow band where only the quote payment fails (9929..9933) is pinned by the Go unit tests. The 9900/9980 texts
# are shaped so nothing deep is stored, which the shared state snapshot could not traverse: events is write-only and
# the event serializer drops an unknown key, and the quote text repeats the amount key before the real one, so
# json.loads (and Go's reader) keep the last value and the row, the payment meta and the audit reprs stay the shallow
# ones the reused effects describe. The 9938 quote is a real nested value, so nothing is written at all.
FORM = 'application/x-www-form-urlencoded'


def add_parts(name, actor, path, parts, *, effect=None):
    """A 500-page form case whose body is raw_parts (the filler is stored as a repeat count)."""
    add(name, actor, path, 500, SERVER, effect=effect)
    cases[-1].update(raw_parts=parts, content_type=FORM)


EVENTS = BY_NAME['form-events']['effect']
EVENTS_ROW = EVENTS['created']['application.CourseSubscription'][0]
QUOTE = BY_NAME['form-quote']
QUOTE_REQUEST = urllib.parse.parse_qs(QUOTE['raw_body'])
QUOTE_FIELDS = json.loads(QUOTE_REQUEST['multiple_quote'][0])


def deep_events_parts(depth):
    return [f'course={EVENTS_ROW["course_id"]}&subscription_id={EVENTS_ROW["subscription_id"]}&events='
            + urllib.parse.quote('[{"id":3,"amount":"12","payment_date":"05/05/2099","deep":'),
            {'repeat': '%5B', 'count': depth - 2}, {'repeat': '%5D', 'count': depth - 2}, urllib.parse.quote('}]')]


def deep_quote_parts(depth):
    head = '{"quote_id": %s, "title": %s, "amount":' % (json.dumps(QUOTE_FIELDS['quote_id']), json.dumps(QUOTE_FIELDS['title']))
    tail = ', "payment_category": %s, "amount": %s}' % (json.dumps(QUOTE_FIELDS['payment_category']), json.dumps(QUOTE_FIELDS['amount']))
    return [f'course={QUOTE_REQUEST["course"][0]}&subscription_id={QUOTE_REQUEST["subscription_id"][0]}&multiple_quote='
            + urllib.parse.quote(head),
            {'repeat': '%5B', 'count': depth - 1}, {'repeat': '%5D', 'count': depth - 1}, urllib.parse.quote(tail)]


def deep_quote_value_parts(depth):
    """A real nested quote (no repeated key): the row's INSERT dump answers at D 9934, nothing written."""
    head = '{"quote_id": %s, "title": %s, "amount": %s, "deep": ' % (
        json.dumps(QUOTE_FIELDS['quote_id']), json.dumps(QUOTE_FIELDS['title']), json.dumps(QUOTE_FIELDS['amount']))
    return [f'course={QUOTE_REQUEST["course"][0]}&subscription_id={QUOTE_REQUEST["subscription_id"][0]}&multiple_quote='
            + urllib.parse.quote(head),
            {'repeat': urllib.parse.quote('{"a":'), 'count': depth - 1}, '1',
            {'repeat': '%7D', 'count': depth - 1}, urllib.parse.quote('}')]


# The event still creates its payment and installment before the 500, so its writes are the shallow form events' effect.
add_parts('form-events-depth-9900-server-error', 'owner', LIST, deep_events_parts(9900), effect=copy.deepcopy(EVENTS))
add_parts('form-events-depth-9980-server-error', 'owner', LIST, deep_events_parts(9980))
add_parts('form-multiple-quote-depth-9900-server-error', 'owner', LIST, deep_quote_parts(9900), effect=copy.deepcopy(QUOTE['effect']))
# Inside the row INSERT's dump band (D 9934..9942): the stock create wrote nothing before the check.
add_parts('form-multiple-quote-depth-9938-server-error', 'owner', LIST, deep_quote_value_parts(9938))
add_parts('form-multiple-quote-depth-9980-server-error', 'owner', LIST, deep_quote_parts(9980))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({**{k: v for k, v in ENROLMENT.items() if k != 'cases'},
                              'scope': 'DefaultRouter course-subscriptions stock create with row, link, audit and index effects',
                              'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
