"""Author the email-logs HTTP sequence from docs/architecture/go-migration/email-logs.md.

Rows carry ``sent_at`` offsets in seconds before an anchor that the runner fixes
at seed time; responses list row labels and the contract materializes them from
the anchored seed, never from responses. Margins keep every row at least one
three hours from a window boundary, so equal host-clock shifts in both backends
cannot move a row across one. Run with ``python3 -I`` and commit the output.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('email_logs_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
HOUR, DAY = 3600, 86400
OWN, FOREIGN, DELETED = IDS['own_association'], IDS['foreign_association'], IDS['deleted_association']
ADMIN_ASSOCIATION = IDS['admin_association']
row_ids = iter(f'a1000000-0000-4000-8000-{740001 + n:012d}' for n in range(100))
ROWS = {}


def log(label, association, offset, *, recipient='socio@example.test', subject='Avviso', result='sent'):
    ROWS[label] = {'id': next(row_ids), 'association': association, 'offset': offset, 'recipient': recipient,
                   'subject': subject, 'result': result}


log('own_recent', OWN, 2 * HOUR)
log('own_today_edge', OWN, DAY - 3 * HOUR, recipient=None, subject='Senza destinatario', result='bounced')
log('own_yesterday', OWN, DAY + 3 * HOUR, subject='Ieri à')
log('own_window_edge', OWN, 90 * DAY - 3 * HOUR)
log('own_expired', OWN, 90 * DAY + 3 * HOUR)
log('foreign_recent', FOREIGN, HOUR)
log('deleted_association_recent', DELETED, 3 * HOUR)
log('admin_recent', ADMIN_ASSOCIATION, 4 * HOUR)
log('null_association', None, HOUR)


def listing(*labels):
    # Ordered -sent_at: a smaller offset is newer.
    labels = sorted(labels, key=lambda label: ROWS[label]['offset'])
    return {'today_count': sum(ROWS[label]['offset'] < DAY for label in labels), 'total_count': len(labels),
            'results': ['$row:' + label for label in labels]}


cases = []
PATH = '/communications/email-logs/list'


def add(name, actor, status, response, *, method='GET', headers=None):
    cases.append({'name': f'email-logs-{name}', 'actor': actor, 'method': method, 'path': PATH,
                  'headers': headers or {}, 'status': status, 'response': response})


add('owner', 'owner', 200, listing('own_recent', 'own_today_edge', 'own_yesterday', 'own_window_edge'))
add('foreign-owner-isolated', 'foreign_owner', 200, listing('foreign_recent'))
# SportAssociation.objects is ActiveManager (deleted=False): a soft-deleted
# association raises DoesNotExist (500); its row is a never-listed sentinel.
add('deleted-association-server-error', 'deleted_association_owner', 500, '<stable-server-error-page>')
# Identity resolution denies administrators outside impersonation on this path;
# their own association's row is a sentinel that must never be listed.
add('admin-without-session', 'admin', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('admin-impersonating-owner', 'admin', 200, listing('own_recent', 'own_today_edge', 'own_yesterday', 'own_window_edge'),
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
# FULL collaborators return before the registry lookup and act on the owner's scope.
add('full-collaborator-owner-scope', 'full_collaborator', 200, listing('own_recent', 'own_today_edge', 'own_yesterday', 'own_window_edge'))
add('restricted-collaborator-unmapped', 'restricted_collaborator', 403, {'detail': "Access denied: No permission mapping for endpoint 'communications/email-logs/list'. Contact administrator to grant access."})
add('athlete-not-association', 'athlete', 403, {'detail': 'User is not a sport association'})
add('anonymous', 'anonymous', 401, {'detail': 'Authentication credentials were not provided.'})
add('owner-without-association', 'owner_without_association', 500, '<stable-server-error-page>')
add('wrong-method', 'owner', 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('options', 'owner', 200, {'name': 'Communication Email Logs List', 'description': '',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']},
    method='OPTIONS')
add('forged-session', 'admin', 403, {'detail': 'Sessione di impersonificazione scaduta o revocata. Torna al tuo account.', 'impersonation_invalid': 'True'},
    headers={'X-Impersonation-Id': '$session:forged', 'User-Id': IDS['owner']})

names = [case['name'] for case in cases]
assert len(names) == len(set(names))
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Email-logs list authored sequence', 'ids': IDS,
                              'rows': ROWS, 'window_margin_seconds': 3 * HOUR, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
