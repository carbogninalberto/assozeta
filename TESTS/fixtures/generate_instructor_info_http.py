"""Author the instructor/<uid>/info HTTP sequence from instructor-info.md.

Builds on the instructor-list seed plus instructor hours. The instructor must
belong to the requesting user's association, and a collaborator who is an
instructor may only read their own record. Stats sum the hours in a date
window: ``date_range`` 'A al B' (YYYY/MM/DD, else DD/MM/YYYY, else the last
365 days to tomorrow), or the current month; the contract recomputes the
window from today's local date (``$today`` dates an hour at run time).
Run with ``python3 -I`` after the instructor-list generator.
"""
import json
from pathlib import Path
from urllib.parse import quote

OUTPUT = Path(__file__).with_name('instructor_info_http.json')
SOURCE = json.loads(Path(__file__).with_name('instructor_list_http.json').read_text())
IDS, INSTRUCTORS = SOURCE['ids'], SOURCE['instructors']
ids = iter(f'a1000000-0000-4000-8000-{960001 + n:012d}' for n in range(10))
HOURS = [
    {'id': next(ids), 'instructor': 'full', 'date': '2026-09-05', 'hours': '2.50', 'amount': '25.00', 'paid': True},
    {'id': next(ids), 'instructor': 'full', 'date': '2026-09-20', 'hours': '1.00', 'amount': '10.50', 'paid': False},
    {'id': next(ids), 'instructor': 'full', 'date': '2025-10-01', 'hours': '3.00', 'amount': '30.00', 'paid': False},
    {'id': next(ids), 'instructor': 'full', 'date': '$today', 'hours': '1.25', 'amount': '5.00', 'paid': False},
    {'id': next(ids), 'instructor': 'draft', 'date': '2026-09-10', 'hours': '4.00', 'amount': '40.00', 'paid': True},
]
path = lambda key, query='': f'/instructor/{INSTRUCTORS[key]["id"] if key in INSTRUCTORS else key}/info{query}'
ranged = lambda text: '?date_range=' + quote(text)


def serialized(label):
    row = INSTRUCTORS[label]
    fields = [k for k in row if k not in ('id', 'owner', 'documents')]
    return {'instructor_id': row['id'], 'user': IDS[row['owner']], **{f: row[f] for f in fields},
            'documents': [SOURCE['documents'][d] for d in row['documents']]}


cases = []


def add(name, actor, target, status, response, *, query='', method='GET', headers=None):
    cases.append({'name': f'instructor-info-{name}', 'actor': actor, 'method': method, 'path': path(target, query), 'headers': headers or {},
                  'status': status, 'response': response})


info = lambda label, window: {'data': serialized(label), 'stats': {'$window': window, 'instructor': label}}
NOT_ALLOWED = {'error': 'not allowed'}
add('owner-iso-range', 'owner', 'full', 200, info('full', ['2026/09/01', '2026/09/30']), query=ranged('2026/09/01 al 2026/09/30'))
add('owner-italian-range', 'owner', 'full', 200, info('full', ['01/10/2025', '31/10/2025']), query=ranged('01/10/2025 al 31/10/2025'))
add('owner-current-month', 'owner', 'full', 200, info('full', 'month'))
add('owner-unparsed-range-last-year', 'owner', 'full', 200, info('full', 'fallback'), query=ranged('ieri al domani'))
add('owner-range-without-separator-server-error', 'owner', 'full', 500, '<stable-server-error-page>', query=ranged('2026/09/01'))
add('owner-empty-range-server-error', 'owner', 'full', 500, '<stable-server-error-page>', query='?date_range=')
add('owner-draft-no-hours-in-month', 'owner', 'draft', 200, info('draft', 'month'))
add('full-collaborator-own-instructor', 'full_collaborator', 'full', 200, info('full', 'month'))
# A collaborator who is an instructor may only read their own record.
add('full-collaborator-other-instructor', 'full_collaborator', 'draft', 403, NOT_ALLOWED)
add('admin-impersonating-owner', 'admin', 'draft', 200, info('draft', ['2026/09/01', '2026/09/30']), query=ranged('2026/09/01 al 2026/09/30'),
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
add('foreign-owner-not-allowed', 'foreign_owner', 'full', 403, NOT_ALLOWED)
add('athlete-not-allowed', 'athlete', 'full', 403, NOT_ALLOWED)
add('owner-without-association-not-allowed', 'owner_without_association', 'full', 403, NOT_ALLOWED)
add('missing-not-found', 'owner', IDS['missing'], 404, {'error': 'instructor not found'})
add('malformed', 'owner', 'abc', 400, ['badly formed hexadecimal UUID string'])
add('restricted-missing-permission', 'restricted_collaborator', 'full', 403, {'detail': 'Missing permission: association.instructor.read'})
add('owner-scoped-athlete-denied', 'owner', 'full', 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('admin-without-session', 'admin', 'full', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('anonymous', 'anonymous', 'full', 401, {'detail': 'Authentication credentials were not provided.'})
add('wrong-method', 'owner', 'full', 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('options', 'owner', 'full', 200, {'name': 'Instructor Info', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({**{k: v for k, v in SOURCE.items() if k != 'cases'}, 'scope': 'Instructor info authored sequence', 'hours': HOURS,
                              'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
