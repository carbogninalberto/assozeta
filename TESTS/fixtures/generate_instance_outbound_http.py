"""Author the instance outbound HTTP sequence (instance-outbound.md).

Pins BE/instance/administration.py (InstanceReleasesView) with
release_catalog.py, operations.py (EmailTestView), integrations.py
(IntegrationTestView), operation_limits.claim_operation, probe_runner and
diagnostic_probe / integration_probe in Django's order: identity, the
IsInstanceOwner permission, method dispatch, validation, the revision and
lease checks, the probe and the stored result.

Outbound traffic goes only to the run's own servers: an HTTPS proxy that
answers for the fixed provider hosts with a private certificate authority
(each case scripts the replies and states the exact requests), and fake SMTP
servers (STARTTLS, implicit TLS, plain, an untrusted certificate) whose
transcripts each case states. Every value is a placeholder. The sequence
runs on one seeded InstanceConfiguration row; a case may first overwrite
managed fields ('setup') and states the complete managed fields afterwards.
'$port:<server>' is the fake SMTP server's port, '$lease' a claimed lease
(now + seconds), '$isonow' a request-time isoformat(). The release catalog
cache is set before a case ('cache') and checked after it ('cache_after':
the catalog Django pickles for the scripted feed, or unchanged), then
cleared. Run with ``python3 -I``.
"""
import base64
import copy
from email.charset import Charset
import gzip
import json
import re
from pathlib import Path

OUTPUT = Path(__file__).with_name('instance_outbound_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
SERVER = '<stable-server-error-page>'
NOW = '$isonow'
ANON = {'detail': 'Authentication credentials were not provided.'}
DENIED = {'detail': 'Only the instance owner or an administrator can administer this installation.'}
REQUIRED, NULL, BLANK = 'This field is required.', 'This field may not be null.', 'This field may not be blank.'
INTEGER = 'A valid integer is required.'
RELEASES, EMAIL_TEST = '/instance/admin/releases', '/instance/admin/email/test'
REPOSITORY = 'carbogninalberto/assozeta'
FEED = f'/repos/{REPOSITORY}/releases?per_page=100&page='
GITHUB_HEADERS = {'host': 'api.github.com', 'accept-encoding': 'identity', 'user-agent': 'Assozeta-Self-Instance',
                  'connection': 'close', 'accept': 'application/vnd.github+json', 'x-github-api-version': '2022-11-28'}
REQUESTS = {'user-agent': 'python-requests/2.34.2', 'accept-encoding': 'gzip, deflate', 'accept': '*/*', 'connection': 'keep-alive'}
GENERIC = {'status': 'failed', 'level': 'connectivity',
           'message': 'Verifica non riuscita. Controlla configurazione e disponibilità del servizio e riprova.'}
SMTP_PASSWORD = 'placeholder-smtp-password'
STRIPE_SECRET = 'sk_test_placeholder'
AI_KEY = 'placeholder-ai-key'
GOOGLE_CLIENT = 'fixture-client.apps.googleusercontent.com'


def test_path(provider):
    return f'/instance/admin/integrations/{provider}/test'


INSTANCE = {
    'id': 7302, 'domain': 'instance-outbound.example.test', 'name': 'Istanza di prova', 'abbreviation': 'IP',
    'primary_color': '#123456', 'support_email': 'supporto@example.test', 'logo_path': '', 'setup_provenance': 'fresh',
    'display_settings': {}, 'self_hosted': True, 'primary_association': 'own_association',
    'google_client_id': '', 'apple_client_id': '', 'email_settings': {}, 'email_password_encrypted': '', 'email_revision': 0,
    'diagnostic_results': {'checked_at': '2026-01-02T03:04:06+00:00'}, 'integration_settings': {},
}
# The generator's model of the managed fields (instance_admin_http's format).
state = {'email_settings': {}, 'email_password': {'$secret': ''}, 'email_revision': 0,
         'diagnostic_results': copy.deepcopy(INSTANCE['diagnostic_results']), 'integration_settings': {}}
cases = []


def add(name, actor, path, status, response, *, method='GET', body=None, raw_body=None, content_type=None, headers=None,
        setup=None, effect=False, outbound=(), smtp=None, cache=None, cache_after=None, retry=None, no_store=None):
    if no_store is None:
        no_store = path.split('?')[0] != RELEASES and response != SERVER
    case = {'name': f'instance-outbound-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'setup': setup, 'effect': copy.deepcopy(state) if effect else None,
            'cache_control': 'no-store' if no_store else None, 'retry_after': retry, 'outbound': list(outbound),
            'smtp': smtp, 'cache': cache, 'cache_after': cache_after}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def setup(**fields):
    """Overwrite managed fields before the next case (and in the model)."""
    for key, value in fields.items():
        if key == 'email_password':
            state['email_password'] = {'$secret': value} if not (isinstance(value, dict)) else value
        else:
            state[key] = copy.deepcopy(value)
    return copy.deepcopy(fields)


def release_lease():
    """Expire every lease before the next POST: setup 'leases' replaces
    diagnostic_results['operation_leases'] (None removes it) and keeps the rest."""
    state['diagnostic_results'].pop('operation_leases', None)
    return {'leases': None}


def claim(name):
    state['diagnostic_results'].setdefault('operation_leases', {})[name] = '$lease'


def options(name):
    return {'name': name, 'description': '', 'renders': ['application/json'],
            'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}


def reply(status=200, body=None, *, raw=None, headers=None):
    value = {'status': status, 'headers': headers or {}}
    if raw is not None:
        value['raw_b64'] = base64.b64encode(raw if isinstance(raw, bytes) else raw.encode()).decode()
    else:
        value['raw_b64'] = base64.b64encode(json.dumps(body).encode()).decode()
        value['headers'] = {'Content-Type': 'application/json', **value['headers']}
    return value


def github(page, answer):
    return {'connect': 'api.github.com:443', 'method': 'GET', 'path': FEED + str(page), 'headers': GITHUB_HEADERS, 'reply': answer}


def provider_request(host, path, answer, authorization=None):
    headers = {'host': host, **REQUESTS}
    if authorization is not None:
        headers['authorization'] = 'Bearer ' + authorization
    return {'connect': host + ':443', 'method': 'GET', 'path': path, 'headers': headers, 'reply': answer}


# ---------------------------------------------------------------------------
# Release feed model (release_catalog.normalize_release / release_summary).
# ---------------------------------------------------------------------------
def raw_release(rid, tag, *, name=None, body='', draft=False, prerelease=False, published='2026-01-10T10:00:00Z', ready=True,
                extra=None):
    assets = []
    if ready:
        assets = [{'name': f'assozeta-selfhost-{tag}.tar.gz', 'state': 'uploaded', 'size': 1024},
                  {'name': 'assozeta-update.json', 'state': 'uploaded', 'size': 12}]
    value = {'id': rid, 'tag_name': tag, 'name': name, 'body': body, 'draft': draft, 'prerelease': prerelease,
             'published_at': published, 'assets': assets}
    value.update(extra or {})
    return value


STABLE = re.compile(r'^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$')


def version_tuple(tag):
    """release_catalog.version_tuple: \\d is any Unicode digit, int() reads it."""
    match = STABLE.fullmatch(tag)
    return tuple(map(int, match.groups())) if match else None


def normalized(raw):
    tag = raw['tag_name']
    assets = {item.get('name'): item for item in raw.get('assets', [])}
    ready = all(assets.get(n, {}).get('state') == 'uploaded' and assets[n].get('size', 0) > 0
                for n in (f'assozeta-selfhost-{tag}.tar.gz', 'assozeta-update.json'))
    quoted = ''.join(c if c.isascii() else ''.join('%%%02X' % b for b in c.encode()) for c in tag)
    return {'id': raw['id'], 'tag': tag, 'version': '.'.join(map(str, version_tuple(tag))), 'name': raw.get('name') or tag,
            'published_at': raw['published_at'], 'notes': raw.get('body') or '',
            'url': f'https://github.com/{REPOSITORY}/releases/tag/{quoted}', 'artifacts_ready': ready,
            'unavailable_reason': None if ready else 'The complete update distribution has not been published yet.'}


def catalog(*raws):
    """fetch_releases over stable, published releases: deduplicated, newest first."""
    kept = {}
    for raw in raws:
        if raw.get('draft') or raw.get('prerelease') or not raw.get('published_at'):
            continue
        if not isinstance(raw['tag_name'], str) or version_tuple(raw['tag_name']) is None:
            continue
        kept[raw['id']] = normalized(raw)
    return sorted(kept.values(), key=lambda item: version_tuple(item['tag']), reverse=True)


def summary(releases, page=1):
    current = (0, 0, 0)  # RUNNING_VERSION v0.0.0
    latest = releases[0] if releases else None
    relation = 'unavailable' if latest is None else 'behind' if current < version_tuple(latest['tag']) else 'current'
    return {'repository': REPOSITORY, 'latest': latest, 'relation': relation,
            'pending': [item for item in releases if version_tuple(item['tag']) > current],
            'history': releases[(page - 1) * 10:page * 10], 'page': page,
            'next_page': page + 1 if len(releases) > page * 10 else None, 'total': len(releases)}


# ---------------------------------------------------------------------------
# Identity, permission, dispatch.
# ---------------------------------------------------------------------------
add('releases-anonymous', 'anonymous', RELEASES, 401, ANON)
add('releases-athlete-denied', 'athlete', RELEASES, 403, DENIED)
add('releases-collaborator-denied', 'full_collaborator', RELEASES, 403, DENIED)
add('releases-wrong-method', 'owner', RELEASES, 405, {'detail': 'Method "POST" not allowed.'}, method='POST', body={})
add('releases-athlete-wrong-method-denied-first', 'athlete', RELEASES, 403, DENIED, method='DELETE')
add('releases-options', 'owner', RELEASES, 200, options('Instance Releases'), method='OPTIONS')
add('email-test-anonymous', 'anonymous', EMAIL_TEST, 401, ANON, method='POST', body={})
add('email-test-athlete-denied', 'athlete', EMAIL_TEST, 403, DENIED, method='POST', body={'revision': 0, 'action': 'connect'})
add('email-test-wrong-method', 'owner', EMAIL_TEST, 405, {'detail': 'Method "GET" not allowed.'})
add('email-test-options', 'owner', EMAIL_TEST, 200, options('Email Test'), method='OPTIONS')
add('integration-test-anonymous', 'anonymous', test_path('stripe'), 401, ANON, method='POST', body={})
add('integration-test-foreign-owner-denied', 'foreign_owner', test_path('stripe'), 403, DENIED, method='POST', body={'revision': 0})
add('integration-test-wrong-method', 'owner', test_path('stripe'), 405, {'detail': 'Method "GET" not allowed.'})
add('integration-test-put-not-allowed', 'owner', test_path('google'), 405, {'detail': 'Method "PUT" not allowed.'}, method='PUT', body={})
add('integration-test-options', 'owner', test_path('apple'), 200, options('Integration Test'), method='OPTIONS')
add('integration-test-options-unknown-provider', 'owner', test_path('github'), 200, options('Integration Test'), method='OPTIONS')
add('integration-test-unknown-provider', 'owner', test_path('github'), 404, {'detail': 'Not found.'}, method='POST', body={'revision': 0})

# ---------------------------------------------------------------------------
# admin/releases: page validation, the cached catalog, the feed.
# ---------------------------------------------------------------------------
PAGE = ['Invalid release history page.']
for label, query in (('text', 'abc'), ('zero', '0'), ('negative', '-1'), ('empty', ''), ('float', '1.5')):
    add(f'releases-page-{label}', 'owner', f'{RELEASES}?page={query}', 400, PAGE)
FEED_A = [
    raw_release(101, 'v1.2.0', name='Assozeta 1.2.0', body='Note 1.2.0'),
    raw_release(102, 'v1.3.0', name='', body=None, ready=False),
    raw_release(103, 'v1.4.0-rc1', name='RC'),
    raw_release(104, 'v1.5.0', draft=True),
    raw_release(105, 'v1.6.0', prerelease=True),
    raw_release(106, 'v1.7.0', published=None),
    raw_release(107, '1.1.0', name='R', body='R', extra={'assets': [
        {'name': 'assozeta-selfhost-1.1.0.tar.gz', 'state': 'uploaded', 'size': 0},
        {'name': 'assozeta-update.json', 'state': 'uploaded', 'size': 1}]}),
    raw_release(3000000000, 'v1.1.0', name='Duplicate version', body='x' * 300),
    raw_release(109, 'v01.2.3'),
    raw_release(110, 'v1.1٣.0', name='Unicode digits'),
    raw_release(111, 'v1.٣.0', name='Unicode digit alone'),
]
CATALOG_A = catalog(*FEED_A)
add('releases-miss-fetches', 'owner', RELEASES, 200, summary(CATALOG_A), outbound=[github(1, reply(200, FEED_A))],
    cache={'catalog': None}, cache_after={'catalog': [FEED_A]})
add('releases-cached', 'owner', RELEASES, 200, summary(CATALOG_A), cache={'catalog': [FEED_A]}, cache_after={'catalog': 'unchanged'})
add('releases-cached-refresh-bounded', 'owner', f'{RELEASES}?refresh=1', 200, summary(CATALOG_A),
    cache={'catalog': [FEED_A], 'refresh': True}, cache_after={'catalog': 'unchanged', 'refresh': True})
FEED_B = [raw_release(200 + n, f'v2.{n}.0', name=f'Release 2.{n}', body=f'Note {n}') for n in range(12)]
CATALOG_B = catalog(*FEED_B)
add('releases-refresh-fetches', 'owner', f'{RELEASES}?refresh=1&page=2', 200, summary(CATALOG_B, 2),
    outbound=[github(1, reply(200, FEED_B))], cache={'catalog': [FEED_A]}, cache_after={'catalog': [FEED_B], 'refresh': True})
add('releases-refresh-other-value', 'owner', f'{RELEASES}?refresh=true&page=2&page=1', 200, summary(CATALOG_A, 1),
    cache={'catalog': [FEED_A]}, cache_after={'catalog': 'unchanged'})
add('releases-page-beyond', 'owner', f'{RELEASES}?page=+0003', 200, summary(CATALOG_B, 3),
    cache={'catalog': [FEED_B]}, cache_after={'catalog': 'unchanged'})
add('releases-page-huge', 'owner', f'{RELEASES}?page=1_000_000_000_000_000_000_000', 200, summary(CATALOG_B, 10 ** 21),
    cache={'catalog': [FEED_B]}, cache_after={'catalog': 'unchanged'})
# Two pages: the first holds 100 entries.
PAGE_ONE = [raw_release(1000 + n, f'v3.0.{n}', draft=n % 3 != 0, name=f'Serie {n}') for n in range(100)]
PAGE_TWO = [raw_release(1000, 'v3.0.0', name='Replaced by page two'), raw_release(1200, 'v0.0.0', name='Current')]
CATALOG_C = catalog(*PAGE_ONE, *PAGE_TWO)
add('releases-two-pages', 'owner', f'{RELEASES}?page=4', 200, summary(CATALOG_C, 4),
    outbound=[github(1, reply(200, PAGE_ONE)), github(2, reply(200, PAGE_TWO))], cache={'catalog': None},
    cache_after={'catalog': [PAGE_ONE, PAGE_TWO]})
add('releases-empty-feed', 'owner', RELEASES, 200, summary([]), outbound=[github(1, reply(200, []))], cache={'catalog': None},
    cache_after={'catalog': [[]]})
add('releases-empty-cached', 'owner', RELEASES, 200, summary([]), cache={'catalog': [[]]}, cache_after={'catalog': 'unchanged'})
ONLY_CURRENT = [raw_release(1300, 'v0.0.0', name='', body='')]
add('releases-single-current', 'owner', RELEASES, 200, summary(catalog(*ONLY_CURRENT)), outbound=[github(1, reply(200, ONLY_CURRENT))],
    cache={'catalog': None}, cache_after={'catalog': [ONLY_CURRENT]})
# Long notes: pickle frames are committed past 64 KiB and large strings are written outside them.
LONG = [raw_release(1400 + n, f'v4.{n}.0', name=f'Lunga {n}', body=('n%d ' % n) * (12000 if n != 2 else 25000)) for n in range(5)]
add('releases-long-notes', 'owner', f'{RELEASES}?page=1', 200, summary(catalog(*LONG)), outbound=[github(1, reply(200, LONG))],
    cache={'catalog': None}, cache_after={'catalog': [LONG]})
# Failures: nothing is cached; a manual refresh keeps its bound.
for label, answer, message in (
        ('rate-limited', reply(403, {'message': 'limit'}), 'Release service rate limit reached. Please try again later.'),
        ('too-many', reply(429, {'message': 'limit'}), 'Release service rate limit reached. Please try again later.'),
        ('server-error', reply(502, {'message': 'bad gateway'}), 'Release service returned HTTP 502.'),
        ('not-found', reply(404, {'message': 'Not Found'}), 'Release service returned HTTP 404.'),
        ('invalid-json', reply(200, raw='[{"id": 1,'), 'Release service is unavailable. Please try again later.'),
        ('object', reply(200, {'message': 'not a list'}), 'Invalid release catalog response.')):
    add(f'releases-feed-{label}', 'owner', RELEASES, 503, {'error': message}, outbound=[github(1, answer)],
        cache={'catalog': None}, cache_after={'catalog': None})
add('releases-refresh-fails-keeps-bound', 'owner', f'{RELEASES}?refresh=1', 503,
    {'error': 'Release service returned HTTP 500.'}, outbound=[github(1, reply(500, {}))],
    cache={'catalog': [FEED_A]}, cache_after={'catalog': 'unchanged', 'refresh': True})
add('releases-second-page-fails', 'owner', RELEASES, 503, {'error': 'Release service rate limit reached. Please try again later.'},
    outbound=[github(1, reply(200, PAGE_ONE)), github(2, reply(429, {}))], cache={'catalog': None}, cache_after={'catalog': None})
# A list item that is not an object is an exception the view does not catch.
add('releases-feed-item-not-object', 'owner', RELEASES, 500, SERVER, outbound=[github(1, reply(200, [raw_release(1, 'v1.0.0'), 'x']))],
    cache={'catalog': None}, cache_after={'catalog': None})
add('releases-feed-null-assets', 'owner', RELEASES, 500, SERVER,
    outbound=[github(1, reply(200, [raw_release(1, 'v1.0.0', extra={'assets': None})]))], cache={'catalog': None}, cache_after={'catalog': None})
# A skipped release is never inspected further.
SKIPPED = [raw_release(1, 'v1.0.0', draft=True, extra={'assets': None}), 'ignored' and raw_release(2, 'nightly', extra={'assets': 5})]
add('releases-skipped-entries-not-inspected', 'owner', RELEASES, 200, summary([]), outbound=[github(1, reply(200, SKIPPED))],
    cache={'catalog': None}, cache_after={'catalog': [SKIPPED]})

# ---------------------------------------------------------------------------
# admin/email/test: validation, revision, lease, the probe.
# ---------------------------------------------------------------------------
add('email-test-required', 'owner', EMAIL_TEST, 400, {'revision': [REQUIRED], 'action': [REQUIRED]}, method='POST', body={})
add('email-test-list-body', 'owner', EMAIL_TEST, 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']},
    method='POST', body=[])
add('email-test-invalid', 'owner', EMAIL_TEST, 400, {'revision': ['Ensure this value is greater than or equal to 0.'],
                                                     'action': ['"probe" is not a valid choice.'], 'recipient': ['Enter a valid email address.']},
    method='POST', body={'revision': -1, 'action': 'probe', 'recipient': 'not-an-email'})
add('email-test-types', 'owner', EMAIL_TEST, 400, {'revision': [INTEGER], 'action': [NULL], 'recipient': [BLANK]},
    method='POST', body={'revision': 'zero', 'action': None, 'recipient': ''})
add('email-test-send-without-recipient', 'owner', EMAIL_TEST, 400, {'recipient': ['Inserisci il destinatario del messaggio di prova.']},
    method='POST', body={'revision': 0, 'action': 'send'})
add('email-test-stale', 'owner', EMAIL_TEST, 409, {'error': 'La configurazione è cambiata. Ricarica prima di eseguire il test.'},
    method='POST', body={'revision': 1, 'action': 'connect'})
# The environment has no SMTP host.
claim('email_test')
state['diagnostic_results']['email_test'] = {'status': 'not_configured', 'level': 'configuration',
                                             'message': 'Configura server SMTP e indirizzo mittente nella sezione Email.',
                                             'checked_at': NOW, 'revision': 0, 'action': 'connect'}
add('email-test-environment-not-configured', 'owner', EMAIL_TEST, 200,
    {k: v for k, v in state['diagnostic_results']['email_test'].items() if k not in ('revision', 'action')},
    method='POST', body={'revision': '0', 'action': 'connect', 'recipient': 'ignored@example.test'}, effect=True)
add('email-test-leased', 'owner', EMAIL_TEST, 429,
    {'error': 'Un test è già in corso o è appena terminato. Attendi fino a 20 secondi e riprova.'},
    method='POST', body={'revision': 0, 'action': 'connect'}, retry='20', effect=True)


def email_case(name, settings, password, revision, action, response, smtp, recipient=None, status=200):
    fields = setup(email_settings=settings, email_password=password, email_revision=revision)
    fields.update(release_lease())
    claim('email_test')
    stored = {**response, 'checked_at': NOW, 'revision': revision, 'action': action}
    state['diagnostic_results']['email_test'] = stored
    body = {'revision': revision, 'action': action}
    if recipient is not None:
        body['recipient'] = recipient
    add(name, 'owner', EMAIL_TEST, status, {**response, 'checked_at': NOW}, method='POST', body=body, setup=fields,
        effect=True, smtp=smtp)


def smtp_settings(server, security, username='mailer', sender='Prova'):
    return {'host': '127.0.0.1', 'port': f'$port:{server}', 'security': security, 'username': username,
            'from_email': 'mittente@example.test', 'sender_name': sender}


PLAIN_AUTH = 'AUTH PLAIN ' + base64.b64encode(f'\0mailer\0{SMTP_PASSWORD}'.encode()).decode() + '\r\n'
CONNECTED = {'status': 'passed', 'level': 'connectivity',
             'message': 'Connessione SMTP e autenticazione (se configurata) riuscite. Nessun messaggio inviato.'}
SENT = {'status': 'passed', 'level': 'functional',
        'message': 'Il server SMTP ha accettato il messaggio. Verifica la casella del destinatario e lo spam; la consegna non è garantita.'}
email_case('email-test-starttls-connect', smtp_settings('starttls', 'tls'), SMTP_PASSWORD, 1, 'connect', CONNECTED,
           {'server': 'starttls', 'transcript': ['ehlo <local>\r\n', 'STARTTLS\r\n', '<tls>', 'ehlo <local>\r\n', PLAIN_AUTH,
                                                  'quit\r\n', '<quit>']})
email_case('email-test-ssl-connect', smtp_settings('ssl', 'ssl'), SMTP_PASSWORD, 2, 'connect', CONNECTED,
           {'server': 'ssl', 'transcript': ['ehlo <local>\r\n', PLAIN_AUTH, 'quit\r\n', '<quit>']})
# security none without credentials: no EHLO before QUIT.
email_case('email-test-plain-connect', smtp_settings('plain', 'none', username=''), '', 3, 'connect', CONNECTED,
           {'server': 'plain', 'transcript': ['quit\r\n', '<quit>']})
email_case('email-test-login-denied', smtp_settings('starttls', 'tls'), 'wrong-placeholder', 4, 'connect', GENERIC,
           {'server': 'starttls', 'transcript': ['ehlo <local>\r\n', 'STARTTLS\r\n', '<tls>', 'ehlo <local>\r\n',
                                                  'AUTH PLAIN ' + base64.b64encode(b'\0mailer\0wrong-placeholder').decode() + '\r\n',
                                                  'AUTH LOGIN ' + base64.b64encode(b'mailer').decode() + '\r\n',
                                                  'quit\r\n', '<quit>']})
# A certificate outside the trusted roots: the handshake fails, no QUIT can follow.
email_case('email-test-untrusted-certificate', smtp_settings('untrusted', 'tls'), SMTP_PASSWORD, 5, 'connect', GENERIC,
           {'server': 'untrusted', 'transcript': ['ehlo <local>\r\n', 'STARTTLS\r\n', '<closed>']})
email_case('email-test-unreadable-password', smtp_settings('starttls', 'tls'), {'$literal': 'not-a-fernet-token'}, 6, 'connect',
           GENERIC, None)
email_case('email-test-incomplete-credentials', smtp_settings('starttls', 'tls'), '', 7, 'connect',
           {'status': 'warning', 'level': 'configuration',
            'message': 'Nome utente e password SMTP incompleti. Completa entrambe le credenziali oppure rimuovile per un relay senza autenticazione.'},
           None)
email_case('email-test-plain-with-user', smtp_settings('plain', 'none'), '', 8, 'connect',
           {'status': 'warning', 'level': 'configuration',
            'message': 'SMTP senza cifratura. Configura TLS o SSL prima di inviare credenziali o messaggi.'}, None)
email_case('email-test-no-sender', {**smtp_settings('starttls', 'tls'), 'from_email': ''}, SMTP_PASSWORD, 9, 'connect',
           {'status': 'not_configured', 'level': 'configuration',
            'message': 'Configura server SMTP e indirizzo mittente nella sezione Email.'}, None)


def message(sender_header, recipient):
    body = 'Questo messaggio è stato richiesto dal proprietario dell’istanza per verificare la configurazione email.'
    return ('Content-Type: text/plain; charset="utf-8"\r\nMIME-Version: 1.0\r\nContent-Transfer-Encoding: 8bit\r\n'
            'Subject: =?utf-8?q?Assozeta_=E2=80=94_verifica_email?=\r\n'
            f'From: {sender_header}\r\nTo: {recipient}\r\nDate: <date>\r\nMessage-ID: <message-id>\r\n\r\n'
            + body.encode().decode('latin-1') + '\r\n.\r\n')


SEND_PREFIX = ['ehlo <local>\r\n', 'STARTTLS\r\n', '<tls>', 'ehlo <local>\r\n', PLAIN_AUTH,
               'mail from:<mittente@example.test> size=<size>\r\n']
email_case('email-test-send', smtp_settings('starttls', 'tls'), SMTP_PASSWORD, 10, 'send', SENT,
           {'server': 'starttls', 'transcript': SEND_PREFIX + ['rcpt to:<destinatario@example.test>\r\n', 'data\r\n',
                                                                message('Prova <mittente@example.test>', 'destinatario@example.test'),
                                                                'quit\r\n', '<quit>']},
           recipient=' destinatario@example.test ')
email_case('email-test-send-encoded-sender', smtp_settings('ssl', 'ssl', sender='Società "Più"'), SMTP_PASSWORD, 11, 'send', SENT,
           {'server': 'ssl', 'transcript': ['ehlo <local>\r\n', PLAIN_AUTH, 'mail from:<mittente@example.test> size=<size>\r\n',
                                            'rcpt to:<altro@example.test>\r\n', 'data\r\n',
                                            message(Charset('utf-8').header_encode('Società "Più"') + ' <mittente@example.test>', 'altro@example.test'),
                                            'quit\r\n', '<quit>']},
           recipient='altro@example.test')
email_case('email-test-send-quoted-sender', smtp_settings('ssl', 'ssl', sender='Prova, Uno'), SMTP_PASSWORD, 12, 'send', SENT,
           {'server': 'ssl', 'transcript': ['ehlo <local>\r\n', PLAIN_AUTH, 'mail from:<mittente@example.test> size=<size>\r\n',
                                            'rcpt to:<altro@example.test>\r\n', 'data\r\n',
                                            message('"Prova, Uno" <mittente@example.test>', 'altro@example.test'),
                                            'quit\r\n', '<quit>']},
           recipient='altro@example.test')
email_case('email-test-send-recipient-refused', smtp_settings('starttls', 'tls'), SMTP_PASSWORD, 13, 'send', GENERIC,
           {'server': 'starttls', 'transcript': SEND_PREFIX + ['rcpt to:<rifiutato@example.test>\r\n', 'rset\r\n', 'quit\r\n', '<quit>']},
           recipient='rifiutato@example.test')
# A request for an older revision.
fields = release_lease()
add('email-test-stale-revision-send', 'owner', EMAIL_TEST, 409, {'error': 'La configurazione è cambiata. Ricarica prima di eseguire il test.'},
    method='POST', body={'revision': 12, 'action': 'send', 'recipient': 'altro@example.test'}, setup=fields, effect=True)

# ---------------------------------------------------------------------------
# admin/integrations/<provider>/test.
# ---------------------------------------------------------------------------
add('integration-test-revision-missing', 'owner', test_path('stripe'), 400, [NULL], method='POST', body={})
add('integration-test-revision-invalid', 'owner', test_path('stripe'), 400, [INTEGER], method='POST', body={'revision': 'x'})
add('integration-test-revision-negative', 'owner', test_path('google'), 400, ['Ensure this value is greater than or equal to 0.'],
    method='POST', body={'revision': -1})
add('integration-test-list-body', 'owner', test_path('google'), 500, SERVER, method='POST', body=[1])
add('integration-test-stale', 'owner', test_path('stripe'), 409, {'error': 'Configurazione cambiata. Ricarica prima di verificare.'},
    method='POST', body={'revision': 1})
add('integration-test-environment-disabled', 'owner', test_path('ai'), 400,
    {'error': 'Abilita e salva l’integrazione prima di verificarla.'}, method='POST', body={'revision': 0})

KEYS = {'keys': [{'kid': 'k1', 'kty': 'RSA', 'n': 'AQAB-modulus', 'e': 'AQAB', 'alg': 'RS256'}]}
WARNING_KEYS = ('Provider raggiungibile e chiavi pubbliche disponibili. Validità del client ID, origini autorizzate e accesso '
                'utente richiedono una prova di login.')
FAILED_KEYS = {'status': 'failed', 'level': 'connectivity', 'message': 'Il provider non ha restituito chiavi pubbliche valide.'}
REJECTED = {'status': 'failed', 'level': 'connectivity',
            'message': 'Credenziale rifiutata o permessi insufficienti. Verifica la chiave e le autorizzazioni del provider.'}
LIMITED = {'status': 'warning', 'level': 'connectivity', 'message': 'Il provider limita le richieste. Attendi e ripeti la verifica.'}
BAD_PROVIDER = {'status': 'failed', 'level': 'connectivity',
                'message': 'Il provider non ha risposto correttamente. Verifica endpoint, rete e disponibilità del servizio.'}


def integration_case(name, provider, settings, revision, response, outbound=(), *, status=200, request_revision=None):
    integrations = copy.deepcopy(state['integration_settings'])
    integrations[provider] = {'revision': revision, 'settings': settings}
    fields = setup(integration_settings=integrations)
    fields.update(release_lease())
    claim('integration_' + provider)
    request_revision = revision if request_revision is None else request_revision
    tests = state['diagnostic_results'].setdefault('integration_tests', {})
    tests[provider] = {**response, 'checked_at': NOW, 'revision': request_revision}
    add(name, 'owner', test_path(provider), status, {**response, 'checked_at': NOW}, method='POST', body={'revision': request_revision},
        setup=fields, effect=True, outbound=outbound)


GOOGLE = {'enabled': True, 'client_id': GOOGLE_CLIENT}
GOOGLE_CERTS = '/oauth2/v3/certs'


def google(answer):
    return [provider_request('www.googleapis.com', GOOGLE_CERTS, answer)]


integration_case('integration-test-google-keys', 'google', GOOGLE, 1,
                 {'status': 'warning', 'level': 'connectivity', 'message': WARNING_KEYS}, google(reply(200, KEYS)))
integration_case('integration-test-google-gzip', 'google', GOOGLE, 2,
                 {'status': 'warning', 'level': 'connectivity', 'message': WARNING_KEYS},
                 google(reply(200, raw=gzip.compress(json.dumps(KEYS).encode(), mtime=0),
                              headers={'Content-Type': 'application/json', 'Content-Encoding': 'gzip'})))
integration_case('integration-test-google-no-rsa', 'google', GOOGLE, 3, FAILED_KEYS,
                 google(reply(200, {'keys': [{'kid': 'k1', 'kty': 'EC', 'n': 'x', 'e': 'y'}, 'k2', {'kid': '', 'kty': 'RSA', 'n': 'x', 'e': 'y'}]})))
integration_case('integration-test-google-keys-object', 'google', GOOGLE, 4, FAILED_KEYS, google(reply(200, {'keys': {'kid': 'k1'}})))
integration_case('integration-test-google-keys-number', 'google', GOOGLE, 5, GENERIC, google(reply(200, {'keys': 5})))
integration_case('integration-test-google-list-body', 'google', GOOGLE, 6, FAILED_KEYS, google(reply(200, [KEYS])))
integration_case('integration-test-google-rejected', 'google', GOOGLE, 7, REJECTED, google(reply(403, {'error': 'forbidden'})))
integration_case('integration-test-google-limited', 'google', GOOGLE, 8, LIMITED, google(reply(429, {})))
integration_case('integration-test-google-redirect-not-followed', 'google', GOOGLE, 9, BAD_PROVIDER,
                 google(reply(302, raw='', headers={'Location': 'https://www.googleapis.com/elsewhere'})))
integration_case('integration-test-google-invalid-json', 'google', GOOGLE, 10, GENERIC, google(reply(200, raw='{"keys": [')))
integration_case('integration-test-google-too-large', 'google', GOOGLE, 11,
                 {'status': 'failed', 'level': 'connectivity', 'message': 'Risposta del provider troppo grande per la verifica.'},
                 google(reply(200, {'keys': [], 'padding': 'p' * 262200})))
integration_case('integration-test-google-nan-keys', 'google', GOOGLE, 12, {'status': 'warning', 'level': 'connectivity', 'message': WARNING_KEYS},
                 google(reply(200, raw='{"keys": [{"kid": NaN, "kty": "RSA", "n": Infinity, "e": "AQAB"}]}')))
integration_case('integration-test-google-client-format', 'google', {'enabled': True, 'client_id': 'fixture-client'}, 13,
                 {'status': 'failed', 'level': 'configuration', 'message': 'Il client ID Google non ha il formato di un client web.'})
integration_case('integration-test-google-no-client', 'google', {'enabled': True, 'client_id': ''}, 14,
                 {'status': 'not_configured', 'level': 'configuration', 'message': 'Client ID mancante.'})
integration_case('integration-test-apple-keys', 'apple', {'enabled': True, 'client_id': 'org.example.fixture'}, 1,
                 {'status': 'warning', 'level': 'connectivity',
                  'message': WARNING_KEYS + ' Il pulsante Apple sul web non è disponibile in questa versione.'},
                 [provider_request('appleid.apple.com', '/auth/keys', reply(200, KEYS))])
# A disabled stored integration is refused by the view, before the lease.
integrations = copy.deepcopy(state['integration_settings'])
integrations['apple'] = {'revision': 2, 'settings': {'enabled': False, 'client_id': 'org.example.fixture'}}
fields = setup(integration_settings=integrations)
fields.update(release_lease())
add('integration-test-apple-disabled', 'owner', test_path('apple'), 400, {'error': 'Abilita e salva l’integrazione prima di verificarla.'},
    method='POST', body={'revision': 2}, setup=fields, effect=True)
# An active lease: 429 before the probe.
fields = {'leases': {'integration_google': 4102444800.0}}
state['diagnostic_results']['operation_leases'] = {'integration_google': 4102444800.0}
add('integration-test-leased', 'owner', test_path('google'), 429,
    {'error': 'Verifica già in corso o appena terminata. Attendi fino a 20 secondi.'}, method='POST', body={'revision': 14},
    setup=fields, effect=True, retry='20')

STRIPE = {'enabled': True, 'public_key': 'pk_test_placeholder', 'secret_key': '$fernet-integration:' + STRIPE_SECRET,
          'webhook_secret': '$fernet-integration:whsec_placeholder'}


def stripe(answer):
    return [provider_request('api.stripe.com', '/v1/balance', answer, STRIPE_SECRET)]


integration_case('integration-test-stripe-passed', 'stripe', STRIPE, 1,
                 {'status': 'passed', 'level': 'connectivity',
                  'message': 'Chiave segreta accettata da Stripe e saldo leggibile. Nessun pagamento eseguito; chiave pubblica e consegna webhook non verificate.'},
                 stripe(reply(200, {'object': 'balance', 'livemode': False, 'available': []})))
MODES = {'status': 'failed', 'level': 'configuration', 'message': 'La chiave pubblica e l’account Stripe indicano ambienti diversi.'}
integration_case('integration-test-stripe-live-account', 'stripe', STRIPE, 2, MODES, stripe(reply(200, {'object': 'balance', 'livemode': True})))
integration_case('integration-test-stripe-livemode-integer', 'stripe', STRIPE, 3, MODES, stripe(reply(200, {'object': 'balance', 'livemode': 0})))
integration_case('integration-test-stripe-not-balance', 'stripe', STRIPE, 4,
                 {'status': 'failed', 'level': 'connectivity', 'message': 'Risposta Stripe non valida per la verifica.'},
                 stripe(reply(200, {'object': 'list'})))
integration_case('integration-test-stripe-rejected', 'stripe', STRIPE, 5, REJECTED, stripe(reply(401, {'error': {'type': 'invalid_request_error'}})))
integration_case('integration-test-stripe-unreadable-secret', 'stripe', {**STRIPE, 'secret_key': 'not-a-fernet-token'}, 6, GENERIC)
integration_case('integration-test-stripe-incomplete', 'stripe', {**STRIPE, 'webhook_secret': ''}, 7,
                 {'status': 'not_configured', 'level': 'configuration', 'message': 'Completa le chiavi Stripe e la firma webhook.'})

AI = {'enabled': True, 'api_key': '$fernet-integration:' + AI_KEY, 'model': 'fixture-model', 'cheap_model': 'fixture-cheap',
      'base_url': 'https://llm.example.test/v1/', 'max_iterations': 15, 'max_results': 5000, 'query_timeout': 10,
      'ws_rate_limit': 10, 'ws_timeout': 240, 'history_cap': 50}
MODELS = {'object': 'list', 'data': [{'id': 'other-model'}, 'not-an-object', {'id': 'fixture-model', 'owned_by': 'fixture'}]}


def ai(path, answer, host='llm.example.test'):
    return [provider_request(host, path, answer, AI_KEY)]


integration_case('integration-test-ai-passed', 'ai', AI, 1,
                 {'status': 'passed', 'level': 'connectivity',
                  'message': 'Elenco modelli accessibile con la chiave salvata e modello principale presente. Nessun messaggio generato; capacità del modello e strumenti non provati.'},
                 ai('/v1/models', reply(200, MODELS)))
integration_case('integration-test-ai-model-missing', 'ai', {**AI, 'model': 'absent-model'}, 2,
                 {'status': 'warning', 'level': 'connectivity',
                  'message': 'Provider raggiungibile, ma il modello principale non compare nell’elenco. Verifica l’identificativo e i permessi. Generazione non provata.'},
                 ai('/v1/models', reply(200, MODELS)))
integration_case('integration-test-ai-data-not-list', 'ai', AI, 3,
                 {'status': 'failed', 'level': 'connectivity', 'message': 'Il provider non ha restituito un elenco modelli compatibile.'},
                 ai('/v1/models', reply(200, {'data': {'id': 'fixture-model'}})))
integration_case('integration-test-ai-unhashable-id', 'ai', AI, 4, GENERIC, ai('/v1/models', reply(200, {'data': [{'id': ['x']}]})))
integration_case('integration-test-ai-default-base', 'ai', {**AI, 'base_url': ''}, 5, BAD_PROVIDER,
                 ai('/v1/models', reply(503, {}), host='api.openai.com'))
integration_case('integration-test-ai-unreachable', 'ai', {**AI, 'base_url': 'http://127.0.0.1:9/v1'}, 6, GENERIC)
integration_case('integration-test-ai-invalid-url', 'ai', {**AI, 'base_url': 'ftp://llm.example.test/v1'}, 7,
                 {'status': 'failed', 'level': 'configuration', 'message': 'URL del provider non valido.'})
integration_case('integration-test-ai-no-key', 'ai', {**AI, 'api_key': ''}, 8,
                 {'status': 'not_configured', 'level': 'configuration', 'message': 'Chiave API mancante.'})
# requests sends the scheme and the host in lower case; user information is the probe's invalid URL.
integration_case('integration-test-ai-upper-case-host', 'ai', {**AI, 'base_url': 'HTTPS://LLM.Example.TEST/v1/'}, 9,
                 {'status': 'failed', 'level': 'connectivity', 'message': 'Il provider non ha restituito un elenco modelli compatibile.'},
                 ai('/v1/models', reply(200, {'data': {'id': 'fixture-model'}})))
for number, address in ((10, 'https://user:pw@llm.example.test/v1'), (11, 'https://user@LLM.example.test/v1'),
                        (12, 'https://:pw@llm.example.test')):
    integration_case(f'integration-test-ai-user-info-{number}', 'ai', {**AI, 'base_url': address}, number,
                     {'status': 'failed', 'level': 'configuration', 'message': 'URL del provider non valido.'})
# The request revision is compared with Python equality.
integration_case('integration-test-ai-revision-string', 'ai', AI, 9,
                 {'status': 'passed', 'level': 'connectivity',
                  'message': 'Elenco modelli accessibile con la chiave salvata e modello principale presente. Nessun messaggio generato; capacità del modello e strumenti non provati.'},
                 ai('/v1/models', reply(200, MODELS)))
cases[-1]['body'] = {'revision': ' 9 '}

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'instance outbound operations (release feed, e-mail test, integration tests)',
                              'ids': IDS, 'instance': INSTANCE, 'smtp_password': SMTP_PASSWORD, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
