"""Author the audit-logs stats/models HTTP sequence from audit-log-summary.md.

Both views read the LogEntry rows indexed to
SportAssociation.objects.get(user=request.user) (a miss answers 404
{"error": "Sport association not found"}). Stats counts every indexed entry
and groups them by action; models lists the distinct content types with their
Italian label (MODEL_VERBOSE_NAMES, else model.replace('_', ' ').title()),
sorted by label. Neither path is in the permission registry. Run with
``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('audit_log_summary_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
# (content type, action, association).
ENTRIES = [('application.payment', 0, OWN), ('application.payment', 1, OWN), ('application.carnet', 2, OWN),
           ('application.associatetutorrelation', 1, OWN), ('application.user', 0, OWN),
           ('application.course', 0, FOREIGN)]
LABELS = {'payment': 'Pagamento', 'carnet': 'Carnet', 'associatetutorrelation': 'Associatetutorrelation', 'user': 'Utente',
          'course': 'Corso'}


def stats(association):
    rows = [e for e in ENTRIES if e[2] == association]
    count = lambda action: sum(1 for e in rows if e[1] == action)
    return {'data': {'total': len(rows), 'by_action': {'create': count(0), 'update': count(1), 'delete': count(2)}}}


def models(association):
    names = {e[0].split('.')[1] for e in ENTRIES if e[2] == association}
    return {'data': sorted(({'name': n, 'label': LABELS[n]} for n in names), key=lambda row: row['label'])}


cases = []


def add(name, actor, path, status, response, *, method='GET', headers=None):
    cases.append({'name': f'audit-summary-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
                  'status': status, 'response': response})


NO_ASSOCIATION = {'error': 'Sport association not found'}
unmapped = lambda path: {'detail': f"Access denied: No permission mapping for endpoint '{path.strip('/')}'. Contact administrator to grant access."}
for kind, build, description in (
        ('stats', stats, 'API endpoint to get audit log statistics for the current sport association.'),
        ('models', models, 'API endpoint to list available model types for filtering.\nReturns models that have audit logs for the current sport association.')):
    path = f'/audit-logs/{kind}'
    add(f'{kind}-owner', 'owner', path, 200, build(OWN))
    add(f'{kind}-full-collaborator-owner-scope', 'full_collaborator', path, 200, build(OWN))
    add(f'{kind}-admin-impersonating-owner', 'admin', path, 200, build(OWN), headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
    add(f'{kind}-foreign-owner', 'foreign_owner', path, 200, build(FOREIGN))
    add(f'{kind}-owner-without-association', 'owner_without_association', path, 404, NO_ASSOCIATION)
    add(f'{kind}-athlete-without-association', 'athlete', path, 404, NO_ASSOCIATION)
    add(f'{kind}-owner-scoped-athlete-denied', 'owner', path, 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
        headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
    add(f'{kind}-restricted-unmapped', 'restricted_collaborator', path, 403, unmapped(path))
    add(f'{kind}-admin-without-session', 'admin', path, 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
    add(f'{kind}-anonymous', 'anonymous', path, 401, {'detail': 'Authentication credentials were not provided.'})
    add(f'{kind}-wrong-method', 'owner', path, 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
    add(f'{kind}-options', 'owner', path, 200, {'name': f'Audit Log {kind.title()}', 'description': description, 'renders': ['application/json'],
        'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Audit-log stats and models authored sequence', 'ids': IDS, 'entries': ENTRIES,
                              'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
