"""Author the testimonial add HTTP sequence from docs/architecture/go-migration/testimonials.md.

Effects name the persisted Testimonial (text after TextField str() coercion,
score as the stored float) and, when an association's ``reviewed`` flag flips,
the exact auditlog update entry. Order matters: later cases see earlier flips.
Run with ``python3 -I`` and commit the output.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('testimonials_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
PATH = '/testimonials/add'
THANKS = {'msg': 'Thank you for your review.'}
MISSING, INVALID = {'msg': 'missing params.'}, {'msg': 'invalid score.'}
cases = []


def add(name, actor, status, response, *, body=None, raw_body=None, content_type=None, method='POST', headers=None,
        testimonial=None, reviewed=None):
    case = {'name': f'testimonials-{name}', 'actor': actor, 'method': method, 'path': PATH, 'headers': headers or {},
            'status': status, 'response': response, 'effect': {'testimonial': testimonial, 'reviewed': reviewed}}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def stored(association, text, score):
    return {'association': association, 'text': text, 'score': score}


def flipped(association, actor, impersonation=None):
    # impersonation names the session whose audit additional_data is expected.
    return {'association': association, 'actor': actor, 'changes': {'reviewed': ['False', 'True']},
            'impersonation': impersonation}


OWN, FOREIGN = 'own_association', 'foreign_association'
# Impersonation first, while the owner's association is still unreviewed.
add('admin-impersonating-owner', 'admin', 200, THANKS, body={'text': 'Da amministratore', 'score': 4},
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']},
    testimonial=stored(OWN, 'Da amministratore', 4.0), reviewed=flipped(OWN, 'admin', 'admin_owner'))
add('owner-already-reviewed', 'owner', 200, THANKS, body={'text': 'Ottimo è 😀', 'score': 5},
    testimonial=stored(OWN, 'Ottimo è 😀', 5.0))
add('foreign-owner-flips-own-association', 'foreign_owner', 200, THANKS, body={'score': 1, 'text': 'Estero'},
    testimonial=stored(FOREIGN, 'Estero', 1.0), reviewed=flipped(FOREIGN, 'foreign_owner'))
# TextField.get_prep_value stores str(value) for non-string values.
add('text-integer-score-float', 'owner', 200, THANKS, body={'text': 123, 'score': 2.5}, testimonial=stored(OWN, '123', 2.5))
add('text-true-score-true', 'owner', 200, THANKS, body={'text': True, 'score': True}, testimonial=stored(OWN, 'True', 1.0))
add('text-float-repr', 'owner', 200, THANKS, body={'text': 1e16, 'score': 1.0}, testimonial=stored(OWN, '1e+16', 1.0))
add('text-object-repr', 'owner', 200, THANKS, body={'text': {'a': [1, "b'c", None, False], 'z': 'x\n'}, 'score': 3},
    testimonial=stored(OWN, "{'a': [1, \"b'c\", None, False], 'z': 'x\\n'}", 3.0))
add('extra-fields-ignored', 'owner', 200, THANKS,
    body={'text': 'Extra', 'score': 5, 'sport_association': IDS['foreign_association'], 'testimonial_id': IDS['missing']},
    testimonial=stored(OWN, 'Extra', 5.0))
for label, body in (('missing-text', {'score': 3}), ('missing-score', {'text': 'x'}), ('empty-object', {}),
                    ('list-body', ['testo'])):
    add(label, 'owner', 400, MISSING, body=body)
for label, score in (('zero', 0), ('above', 6), ('just-above', 5.0001), ('negative', -1), ('false', False), ('huge', 10 ** 30)):
    add(f'score-{label}', 'owner', 400, INVALID, body={'text': 'x', 'score': score})
for label, body in (('score-string', {'text': 'x', 'score': '3'}), ('score-null', {'text': 'x', 'score': None}),
                    ('score-list', {'text': 'x', 'score': [3]}), ('text-null', {'text': None, 'score': 3}),
                    # Membership on a list or string body passes; indexing it fails.
                    ('list-members-body', ['text', 'score']), ('substring-body', 'textscore'), ('number-body', 5)):
    add(f'{label}-server-error', 'owner', 500, '<stable-server-error-page>', body=body)
add('null-body-server-error', 'owner', 500, '<stable-server-error-page>', raw_body='null', content_type='application/json')
add('form-body-server-error', 'owner', 500, '<stable-server-error-page>', raw_body='text=x&score=3',
    content_type='application/x-www-form-urlencoded')
add('athlete-missing-params', 'athlete', 400, MISSING, body={'text': 'x'})
add('athlete-not-association', 'athlete', 403, {'detail': 'User is not a sport association'}, body={'text': 'x', 'score': 3})
add('deleted-association-server-error', 'deleted_association_owner', 500, '<stable-server-error-page>', body={'text': 'x', 'score': 3})
add('owner-without-association-server-error', 'owner_without_association', 500, '<stable-server-error-page>',
    body={'text': 'x', 'score': 3})
# Identity resolution sets request.user to the tenant owner for collaborators,
# so the reviewed lookup finds the owner's (already reviewed) association.
add('full-collaborator-owner-scope', 'full_collaborator', 200, THANKS,
    body={'text': 'Collaboratore', 'score': 4}, testimonial=stored(OWN, 'Collaboratore', 4.0))
add('restricted-collaborator-missing-permission', 'restricted_collaborator', 403,
    {'detail': 'Missing permission: other.settings.update'}, body={'text': 'x', 'score': 3})
add('admin-without-session', 'admin', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'},
    body={'text': 'x', 'score': 3})
add('forged-session', 'admin', 403, {'detail': 'Sessione di impersonificazione scaduta o revocata. Torna al tuo account.',
    'impersonation_invalid': 'True'}, body={'text': 'x', 'score': 3},
    headers={'X-Impersonation-Id': '$session:forged', 'User-Id': IDS['owner']})
add('anonymous', 'anonymous', 401, {'detail': 'Authentication credentials were not provided.'}, body={'text': 'x', 'score': 3})
add('wrong-method', 'owner', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('options', 'owner', 200, {'name': 'Testimonials Create', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

assert len({case['name'] for case in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Testimonial add authored sequence with reviewed-flag audit effects',
                              'ids': IDS, 'index_path': 'self', 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
