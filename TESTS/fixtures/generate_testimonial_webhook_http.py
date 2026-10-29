"""Author the Senja testimonial webhook HTTP sequence from testimonials.md.

Bodies are exact request bytes. ``signed`` is the text the view signs,
json.dumps(request.data, separators=(',', ':')), authored here with CPython;
the runner signs it with the private SENJA_WEBHOOK_SECRET when ``signature``
is ``valid``. ``<empty>`` responses have no body. Order matters: later cases
see earlier reviewed flips. Run with ``python3 -I`` and commit the output.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('testimonial_webhook_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
PATH = '/testimonials/update'
cases = []


def created(association_id, **extra):
    return {'type': 'testimonial_created', 'data': {'new': {'endorser': {'custom_data': {'sport-association-id': association_id}}}}, **extra}


def add(name, status, response, raw, *, signature='valid', signed=None, actor='anonymous', method='POST',
        content_type='application/json', reviewed=None, headers=None):
    if signed is None and signature == 'valid':
        signed = json.dumps(json.loads(raw), separators=(',', ':'))
    case = {'name': f'testimonial-webhook-{name}', 'actor': actor, 'method': method, 'path': PATH,
            'headers': headers or {}, 'raw_body': raw, 'content_type': content_type, 'signature': signature,
            'signed': signed, 'status': status, 'response': response,
            'effect': {'testimonial': None, 'reviewed': reviewed}}
    cases.append(case)


def flipped(association, actor=None):
    return {'association': association, 'actor': actor, 'changes': {'reviewed': ['False', 'True']}, 'impersonation': None}


body = lambda value: json.dumps(value, ensure_ascii=False)
add('missing-signature', 401, '<empty>', body(created(IDS['foreign_association'])), signature=None)
add('wrong-signature', 401, '<empty>', body(created(IDS['foreign_association'])), signature='0' * 64)
# The view signs its re-serialization, not the received bytes.
raw = body(created(IDS['foreign_association']))
add('signature-over-raw-bytes', 401, '<empty>', raw, signature='valid', signed=raw)
add('non-ascii-signature-server-error', 500, '<stable-server-error-page>', body(created(IDS['foreign_association'])),
    signature='é' * 64)
add('missing-type', 400, '<empty>', body({'data': {}}))
add('other-type', 200, '<empty>', body({'type': 'testimonial_updated', 'data': {'v': 1}}))
# Unicode text, unsorted keys, float repr and duplicate keys in the signed text.
add('created-flips-foreign', 200, '<empty>',
    '{"z": "Grazie è 😀", "type": "testimonial_created", "n": 1.50, "e": 1e5, "data": {"new": {"endorser": '
    '{"custom_data": {"sport-association-id": "' + IDS['foreign_association'] + '"}}}}, "z": "ultimo"}',
    reviewed=flipped('foreign_association'))
add('created-already-reviewed', 200, '<empty>', body(created(IDS['foreign_association'])))
add('created-braced-uppercase-id', 200, '<empty>', body(created('{' + IDS['own_association'].upper() + '}')),
    reviewed=flipped('own_association'))
add('created-missing-association', 404, '<empty>', body(created(IDS['missing'])))
add('created-deleted-association', 404, '<empty>', body(created(IDS['deleted_association'])))
for label, value in (('invalid-id', created('nope')), ('integer-id', created(5)),
                     ('missing-endorser', {'type': 'testimonial_created', 'data': {'new': {}}}),
                     ('data-list', {'type': 'testimonial_created', 'data': []})):
    add(f'created-{label}', 400, '<empty>', body(value))
add('empty-list-body', 400, '<empty>', '[]')
for label, raw in (('list-body', '["type"]'), ('string-body', '"type"'), ('null-body', 'null')):
    add(f'{label}-server-error', 500, '<stable-server-error-page>', raw)
add('form-body', 200, '<empty>', 'type=testimonial_updated', content_type='application/x-www-form-urlencoded',
    signed='{"type":"testimonial_updated"}')
add('authenticated-owner', 200, '<empty>', body({'type': 'testimonial_updated'}), actor='owner')
add('admin-without-session', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'},
    body({'type': 'testimonial_updated'}), actor='admin')
add('restricted-collaborator-missing-permission', 403, {'detail': 'Missing permission: other.settings.update'},
    body({'type': 'testimonial_updated'}), actor='restricted_collaborator')
add('wrong-method', 405, {'detail': 'Method "GET" not allowed.'}, '', signature=None, method='GET')
add('options', 200, {'name': 'Testimonials Update', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, '', signature=None, method='OPTIONS')

assert len({case['name'] for case in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Senja testimonial webhook authored sequence with reviewed-flag audit effects',
                              'ids': IDS, 'index_path': 'self', 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
