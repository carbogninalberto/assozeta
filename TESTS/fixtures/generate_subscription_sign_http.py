"""Author the subscription/sign HTTP sequence (subscription-sign.md).

subscription_sign (@api_view(['POST']), IsAuthenticated, subscriptions_views.py), in Django's order:
- request.data must have exactly the keys subscription_id and signature, else
  400 {'msg': 'validation errors.'} (a body that is not a mapping: 500);
- is_valid_uuid(subscription_id): ValidationError(<the uuid.UUID exception>) (400 list);
- scoped_queryset(request, Subscription.objects).filter(subscription_id=...)[0]: the group-aware
  manager resolves the row by id (an unknown id is an IndexError: 500);
- SignatureRequestSerializer(data=signature).is_valid(raise_exception=True) (400 field errors);
- transaction.atomic(): the row again with select_for_update; with there_is_signature
  and data: Signature.objects.create(signature=data, user=request.user) (create entry,
  indexed through the actor's own association), set_signature_from_base64(data):
  * an http(s) URL: signature_url = data, signature_storage_key = None, nothing stored;
  * otherwise the text after the first ',' through base64.b64decode(validate=True)
    (invalid or empty: 400 {'msg': 'Firma non valida.', ...}, rolled back), then
    default_storage.save('subscriptions/<id>/signature_<datetime.now().timestamp()>.png')
    (HeadObject, PutObject image/png), with AWS_S3_USE_OBJECT_ACL the object ACL
    public-read (a failure deletes the object: 503 {'msg': 'Storage temporaneamente non
    disponibile.', ...}, rolled back), signature_storage_key = the saved name and
    signature_url = AWS_S3_PUBLIC_BASE_URL + '/' + the object key;
  status_flag = PENDING and save(update_fields=[signature_url, signature_storage_key,
  status_flag]) (update entry with the changed fields, indexed direct);
- on commit, in order: the old key's object is deleted when it changed and no row
  references it any more, subscription_signed (check_workflows_trigger.delay(
  trigger_type='subscription_signed', sport_association_id=UUID, subscription_id=UUID)) and
  print_document_subscription.delay(str(id), <Authorization header>), also without a signature.
Response 200 {'status': 'success'}.

The run sets AWS_S3_USE_OBJECT_ACL and AWS_S3_PUBLIC_BASE_URL for both servers; a recording
storage proxy in front of the private MinIO answers the object ACL calls itself and denies the
ACL of the 'acl_denied' row. Run with ``python3 -I``.
"""
import base64
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('subscription_sign_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
OWNER, ATHLETE = IDS['owner'], IDS['athlete']
ids = iter(f'a1000000-0000-4000-8000-{998001 + n:012d}' for n in range(100))
SERVER = '<stable-server-error-page>'
UNKNOWN = 'a1000000-0000-4000-8000-000000998999'
LOCATION = 'subscription-sign'
PUBLIC_BASE = 'https://firme.example.test/cdn'
SIGNED_URL = 'https://firme.example.test/esterne/firma-remota.png'
SUCCESS = {'status': 'success'}
INVALID = {'msg': 'Firma non valida.', 'code': '400', 'ref': 'invalid_signature'}
UNAVAILABLE = {'msg': 'Storage temporaneamente non disponibile.', 'code': '503', 'ref': 'signature_storage'}
VALIDATION = {'msg': 'validation errors.'}


def png(label):
    raw = b'\x89PNG\r\n\x1a\n' + f'firma {label}'.encode()
    return raw, base64.b64encode(raw).decode()


def subscription(**fields):
    row = {'id': next(ids), 'association': OWN, 'user': 'owner', 'status': 1, 'start': '2026-01-01', 'end': '2026-12-31',
           'group': None, 'signature_key': None, 'signature_url': None}
    row.update(fields)
    return row


SUBSCRIPTIONS = {
    'first': subscription(),
    'url': subscription(),
    'keyed': subscription(status=2),
    'shared_a': subscription(),
    'shared_b': subscription(status=4),
    'acl_denied': subscription(),
    'athlete_row': subscription(user='athlete'),
    'scoped_row': subscription(user='athlete'),
    'admin_row': subscription(),
    'grouped': subscription(group='own_group'),
    'undated': subscription(start=None, end=None),
    'other': subscription(association=FOREIGN, user='foreign_owner'),
    'plain': subscription(),
    'long_url': subscription(),
}
# Seeded keys with an object in the bucket (relative to AWS_LOCATION).
SUBSCRIPTIONS['keyed']['signature_key'] = f'subscriptions/{SUBSCRIPTIONS["keyed"]["id"]}/signature_1700000000.25.png'
SUBSCRIPTIONS['shared_a']['signature_key'] = SUBSCRIPTIONS['shared_b']['signature_key'] = 'signatures/firma-condivisa.png'
SUBSCRIPTIONS['shared_b']['signature_url'] = PUBLIC_BASE + '/' + LOCATION + '/signatures/firma-condivisa.png'
OBJECTS = {SUBSCRIPTIONS['keyed']['signature_key']: base64.b64encode(b'seeded keyed signature').decode(),
           'signatures/firma-condivisa.png': base64.b64encode(b'seeded shared signature').decode()}
ACL_DENIED = SUBSCRIPTIONS['acl_denied']['id']

cases = []


def add(name, actor, status, response, *, body=None, raw_body=None, content_type=None, method='POST', headers=None, effect=None):
    case = {'name': f'subscription-sign-{name}', 'actor': actor, 'method': method, 'path': '/subscription/sign',
            'headers': headers or {}, 'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def payload(label, signature):
    return {'subscription_id': SUBSCRIPTIONS[label]['id'], 'signature': signature}


def actor_association(actor):
    return {'owner': OWN, 'admin': IDS['admin_association']}.get(actor)


def dispatched(label):
    """The on_commit Celery messages of every success, in registration order."""
    row = SUBSCRIPTIONS[label]
    return [{'task': 'check_workflows_trigger', 'args': [],
             'kwargs': [['trigger_type', 'subscription_signed'], ['sport_association_id', {'uuid': row['association']}],
                        ['subscription_id', {'uuid': row['id']}]]},
            {'task': 'print_document_subscription', 'args': [row['id'], '$authorization'], 'kwargs': []}]


def signed(label, actor, data, *, stored=None, old=None, user=None):
    """A signature saved for the row: stored is the decoded PNG bytes (an upload) or None (a URL).

    old: what happens to the row's previous key after the commit: 'deleted', 'kept' or None (no previous key)."""
    row = SUBSCRIPTIONS[label]
    association = actor_association(actor)
    columns = {'status_flag': 2}
    changes = {}
    if row['status'] != 2:
        changes['status_flag'] = [str(row['status']), '2']
    if stored is not None:
        columns.update(signature_storage_key='$key', signature_url='$public')
        changes['signature_storage_key'] = [row['signature_key'] or 'None', '$key']
        changes['signature_url'] = [row['signature_url'] or 'None', '$public']
        storage = ['HEAD $new', 'PUT $new', 'ACL $new']
    else:
        columns.update(signature_storage_key=None, signature_url=data)
        if row['signature_key'] is not None:
            changes['signature_storage_key'] = [row['signature_key'], 'None']
        if row['signature_url'] != data:
            changes['signature_url'] = [row['signature_url'] or 'None', data]
        storage = []
    if old == 'deleted':
        storage.append('DELETE $old')
    return {'subscription': label, 'columns': columns,
            'signature': {'user': user or ('athlete' if actor == 'athlete' else 'owner'), 'data': data,
                          'index': [association, 'actor.sport_association'] if association else None},
            'stored': base64.b64encode(stored).decode() if stored is not None else None, 'old': old, 'storage': storage,
            'audit': [{'model': 'signature', 'changes': '$created'},
                      {'model': 'subscription', 'changes': changes, 'index': [row['association'], 'direct']}],
            'celery': dispatched(label)}


def unsigned(label):
    """there_is_signature false: nothing written, the on_commit messages still sent."""
    return {'subscription': label, 'columns': {}, 'signature': None, 'stored': None, 'old': None, 'storage': [], 'audit': [],
            'celery': dispatched(label)}


# Collaborators and impersonating administrators act as the owner: the Signature belongs to request.user.
# --- Transport and identity ---------------------------------------------------------------------------
add('options', 'owner', 200, {'name': 'Subscription Sign', 'description': 'API endpoint to sign a subscription\n'
                                ':param request: data: {subscription_id, signature}\n:return: token, refresh token',
                              'renders': ['application/json'],
                              'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']},
    method='OPTIONS')
add('wrong-method', 'owner', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('anonymous', 'anonymous', 401, {'detail': 'Authentication credentials were not provided.'},
    body=payload('plain', {'there_is_signature': False, 'data': None}))
add('restricted-missing-permission', 'restricted_collaborator', 403, {'detail': 'Missing permission: association.members.update'},
    body=payload('plain', {'there_is_signature': False, 'data': None}))
add('admin-without-session', 'admin', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'},
    body=payload('plain', {'there_is_signature': False, 'data': None}))
# subscription_sign is athlete-facing: an owner's association-scoped session on an athlete checks the body's
# subscription_id against the athlete's rows of the association, then the view acts as the athlete.
add('owner-scoped-athlete-hidden-row', 'owner', 403, {'detail': 'Risorsa non disponibile per questo utente e associazione.'},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': ATHLETE},
    body=payload('plain', {'there_is_signature': False, 'data': None}))

# --- Body shape (nothing written) -----------------------------------------------------------------------
add('missing-signature-key', 'owner', 400, VALIDATION, body={'subscription_id': SUBSCRIPTIONS['plain']['id']})
add('extra-key', 'owner', 400, VALIDATION, body={**payload('plain', {'data': None}), 'note': 'x'})
add('empty-object', 'owner', 400, VALIDATION, body={})
add('list-body-server-error', 'owner', 500, SERVER, body=[SUBSCRIPTIONS['plain']['id']])
add('string-body-server-error', 'owner', 500, SERVER, body='firma')
add('malformed-uuid', 'owner', 400, ['badly formed hexadecimal UUID string'],
    body={'subscription_id': 'not-a-uuid', 'signature': {'data': None}})
add('integer-uuid', 'owner', 400, ["'int' object has no attribute 'replace'"], body={'subscription_id': 7, 'signature': {'data': None}})
add('null-uuid', 'owner', 400, ['one of the hex, bytes, bytes_le, fields, or int arguments must be given'],
    body={'subscription_id': None, 'signature': {'data': None}})
add('unknown-subscription-server-error', 'owner', 500, SERVER, body={'subscription_id': UNKNOWN, 'signature': {'there_is_signature': 'x'}})
add('group-header-hides-row-server-error', 'owner', 500, SERVER, headers={'X-Group-Id': IDS['foreign_group']},
    body=payload('grouped', {'there_is_signature': False, 'data': None}))

# --- SignatureRequestSerializer (after the lookup; nothing written) -------------------------------------
add('signature-not-object', 'owner', 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got str.']},
    body=payload('plain', 'firma'))
add('signature-null', 'owner', 400, {'non_field_errors': ['No data provided']}, body=payload('plain', None))
add('data-required', 'owner', 400, {'data': ['This field is required.']}, body=payload('plain', {'there_is_signature': True}))
add('data-blank-required-signature', 'owner', 400, {'data': ['La firma è obbligatoria.']},
    body=payload('plain', {'there_is_signature': True, 'data': '   '}))
add('data-null-required-signature', 'owner', 400, {'data': ['La firma è obbligatoria.']},
    body=payload('plain', {'there_is_signature': True, 'data': None}))
add('flag-not-boolean', 'owner', 400, {'there_is_signature': ['Must be a valid boolean.']},
    body=payload('plain', {'there_is_signature': 'forse', 'data': 'abc'}))
add('data-list-not-string', 'owner', 400, {'data': ['Not a valid string.']},
    body=payload('plain', {'there_is_signature': True, 'data': ['abc']}))
add('form-body-signature-string', 'owner', 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got str.']},
    raw_body=f'subscription_id={UNKNOWN}&subscription_id={SUBSCRIPTIONS["plain"]["id"]}&signature=firma',
    content_type='application/x-www-form-urlencoded')

# --- Decoding (rolled back: nothing written) ------------------------------------------------------------
add('invalid-base64', 'owner', 400, INVALID, body=payload('plain', {'there_is_signature': True, 'data': 'data:image/png;base64,abc'}))
add('base64-non-alphabet', 'owner', 400, INVALID, body=payload('plain', {'there_is_signature': True, 'data': 'YWJj\nZA=='}))
add('base64-excess-padding', 'owner', 400, INVALID, body=payload('plain', {'there_is_signature': True, 'data': 'YWI==='}))
add('base64-non-ascii', 'owner', 400, INVALID, body=payload('plain', {'there_is_signature': True, 'data': 'firmà'}))
add('base64-empty-after-comma', 'owner', 400, INVALID, body=payload('plain', {'there_is_signature': True, 'data': 'data:image/png;base64,'}))
add('data-number-invalid', 'owner', 400, INVALID, body=payload('plain', {'there_is_signature': 'true', 'data': 12}))
add('url-too-long-server-error', 'owner', 500, SERVER,
    body=payload('long_url', {'there_is_signature': True, 'data': 'https://firme.example.test/' + 'a' * 1000}))
add('acl-denied-unavailable', 'owner', 503, UNAVAILABLE, body=payload('acl_denied', {'there_is_signature': True, 'data': png('acl')[1]}),
    effect={'subscription': 'acl_denied', 'columns': {}, 'signature': None, 'stored': None, 'old': None,
            'storage': ['HEAD $new', 'PUT $new', 'ACL $new denied', 'DELETE $new'], 'audit': [], 'celery': []})

# --- Without a signature: only the on_commit messages ---------------------------------------------------
add('no-signature', 'owner', 200, SUCCESS, body=payload('plain', {'there_is_signature': False, 'data': None}), effect=unsigned('plain'))
add('no-signature-flag-default', 'owner', 200, SUCCESS, body=payload('plain', {'data': 'ignorata'}), effect=unsigned('plain'))
add('no-signature-urn-uuid', 'owner', 200, SUCCESS,
    body={'subscription_id': 'urn:uuid:' + SUBSCRIPTIONS['plain']['id'].upper(), 'signature': {'there_is_signature': 0, 'data': ''}},
    effect=unsigned('plain'))
add('no-signature-group-collaborator', 'full_collaborator', 200, SUCCESS, headers={'X-Group-Id': IDS['own_group']},
    body=payload('grouped', {'there_is_signature': False, 'data': None}), effect=unsigned('grouped'))
add('other-association-row-no-signature', 'owner', 200, SUCCESS, body=payload('other', {'there_is_signature': False, 'data': None}),
    effect=unsigned('other'))

# --- Signatures ------------------------------------------------------------------------------------------
first_raw, first_b64 = png('prima')
add('upload-data-uri', 'owner', 200, SUCCESS, body=payload('first', {'there_is_signature': True, 'data': 'data:image/png;base64,' + first_b64}),
    effect=signed('first', 'owner', 'data:image/png;base64,' + first_b64, stored=first_raw))
again_raw, again_b64 = png('seconda')
resign = signed('first', 'owner', '  ' + again_b64 + '  ', stored=again_raw, old='deleted')
resign['signature']['data'] = again_b64  # CharField trims the text
resign['audit'][1]['changes'] = {'signature_storage_key': ['$previous', '$key'], 'signature_url': ['$previous_url', '$public']}
add('resign-replaces-own-object', 'owner', 200, SUCCESS, body=payload('first', {'there_is_signature': True, 'data': '  ' + again_b64 + '  '}),
    effect=resign)
add('url-signature', 'full_collaborator', 200, SUCCESS, body=payload('url', {'there_is_signature': True, 'data': SIGNED_URL}),
    effect=signed('url', 'full_collaborator', SIGNED_URL))
add('url-replaces-stored-key', 'owner', 200, SUCCESS, body=payload('keyed', {'there_is_signature': 'yes', 'data': SIGNED_URL}),
    effect=signed('keyed', 'owner', SIGNED_URL, old='deleted'))
shared_raw, shared_b64 = png('condivisa')
add('upload-keeps-referenced-object', 'owner', 200, SUCCESS, body=payload('shared_a', {'there_is_signature': True, 'data': shared_b64}),
    effect=signed('shared_a', 'owner', shared_b64, stored=shared_raw, old='kept'))
athlete_raw, athlete_b64 = png('atleta')
add('athlete-upload', 'athlete', 200, SUCCESS, body=payload('athlete_row', {'there_is_signature': 1, 'data': athlete_b64}),
    effect=signed('athlete_row', 'athlete', athlete_b64, stored=athlete_raw))
admin_raw, admin_b64 = png('amministratore')
add('admin-impersonating-owner-upload', 'admin', 200, SUCCESS, headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER},
    body=payload('admin_row', {'there_is_signature': True, 'data': 'data:image/png;base64,' + admin_b64}),
    effect=signed('admin_row', 'admin', 'data:image/png;base64,' + admin_b64, stored=admin_raw))
add('owner-scoped-athlete-url-signature', 'owner', 200, SUCCESS, headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': ATHLETE},
    body=payload('scoped_row', {'there_is_signature': True, 'data': SIGNED_URL}),
    effect=signed('scoped_row', 'owner', SIGNED_URL, user='athlete'))
undated_raw, undated_b64 = png('senza date')
add('undated-row-keeps-dates', 'owner', 200, SUCCESS, body=payload('undated', {'there_is_signature': True, 'data': 'x,' + undated_b64}),
    effect=signed('undated', 'owner', 'x,' + undated_b64, stored=undated_raw))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'subscription/sign authored sequence with row, audit, storage and Celery effects',
    'ids': IDS, 'location': LOCATION, 'public_base': PUBLIC_BASE, 'acl_denied': ACL_DENIED, 'subscriptions': SUBSCRIPTIONS,
    'objects': OBJECTS, 'unknown': UNKNOWN, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
