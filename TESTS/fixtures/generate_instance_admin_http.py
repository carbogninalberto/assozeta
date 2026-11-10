"""Author the instance administration HTTP sequence (instance-admin.md).

Pins BE/instance/administration.py (InstanceAdminView, InstanceUpdatesView,
InstanceRestartsView), operations.py (EmailSettingsView), integrations.py
(IntegrationSettingsView) and diagnostics.py (DiagnosticsView) in Django's
order: identity resolution (administration paths never impersonate), the
IsInstanceOwner permission on the authenticated actor, method dispatch, the
serializer, the revision checks and the single-row save. The sequence runs on
one seeded InstanceConfiguration row, so the order matters: the generator
keeps a model of the row and every write case states the complete expected
managed fields. Secrets are synthetic placeholders; stored secrets are
checked by decrypting them with the run's SECRET_KEY ({'$secret': plain}).
The test environment is development mode with the tracked example
environment (APP_URL https://example.org, ASSOZETA_VERSION latest, no SMTP
host or provider credentials), WHITELABEL_NAME 'A.S.D. Polisportiva "Città"
(Aurora)' and EMAIL_HOST_USER 'segreteria@polisportiva.example' (the
environment sender is parseaddr of DEFAULT_FROM_EMAIL). The diagnostics runs
at the end script the services the probes reach (see the adapter): the
public URL through an HTTPS proxy, the API on 127.0.0.1:8000 (Go's API probe
target is set to the same stub), the renderer and S3 stubs, a pidbox worker
on the broker and the scheduler heartbeat in Django's cache. Run with
``python3 -I``.
"""
import base64
import copy
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('instance_admin_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
SERVER = '<stable-server-error-page>'
NOW = '$isonow'
ANON = {'detail': 'Authentication credentials were not provided.'}
DENIED = {'detail': 'Only the instance owner or an administrator can administer this installation.'}
LEAVE = {'detail': 'Esci dall’impersonificazione per accedere all’amministrazione.'}
REQUIRED, NULL, BLANK = 'This field is required.', 'This field may not be null.', 'This field may not be blank.'
INTEGER = 'A valid integer is required.'
ADMIN, EMAIL, DIAGNOSTICS = '/instance/admin', '/instance/admin/email', '/instance/admin/diagnostics'
UPDATES, RESTARTS = '/instance/admin/updates', '/instance/admin/restarts'
APP_URL = 'https://example.org'
APPLICATION = 'Nuovi invii da API e worker; le connessioni già aperte terminano con la configurazione precedente.'
NO_STORE = (EMAIL, '/instance/admin/integrations/')
# parseaddr('A.S.D. Polisportiva "Città" (Aurora) <segreteria@polisportiva.example>'):
# the quotes are dropped, the comment is kept after the phrase.
ENV_USER = 'segreteria@polisportiva.example'
ENV_SENDER = 'A.S.D. Polisportiva Città (Aurora)'
REQUEST_ID = 'b2000000-0000-4000-8000-000000000001'


def integration(provider):
    return '/instance/admin/integrations/' + provider


SEEDED_CHECK = {'id': 'database', 'status': 'passed', 'level': 'connectivity', 'message': 'Database raggiungibile.',
                'checked_at': '2026-01-02T03:04:05+00:00'}
SEEDED_EMAIL_CHECK = {'id': 'email', 'status': 'failed', 'level': 'connectivity', 'message': 'SMTP non raggiungibile.',
                      'checked_at': '2026-01-02T03:04:05+00:00'}
SEEDED_EMAIL_TEST = {'status': 'failed', 'level': 'connectivity', 'message': 'Connessione rifiutata.',
                     'checked_at': '2026-01-02T03:04:07+00:00', 'revision': 0, 'action': 'connect'}
GOOGLE_TEST = {'status': 'passed', 'level': 'configuration', 'message': 'Verifica completata.',
               'checked_at': '2026-01-02T03:04:08+00:00', 'revision': 0}
APPLE_TEST = {'status': 'passed', 'level': 'configuration', 'message': 'Verifica completata.',
              'checked_at': '2026-01-02T03:04:09+00:00', 'revision': 9}
# The seeded row. '$fernet-integration:' values are encrypted with the run's
# SECRET_KEY when seeding; 'not-a-fernet-token' never decrypts.
INSTANCE = {
    'id': 7301, 'domain': 'instance-admin.example.test', 'name': 'Istanza di prova', 'abbreviation': 'IP',
    'primary_color': '#123456', 'support_email': 'supporto@example.test', 'logo_path': '', 'setup_provenance': 'fresh',
    'display_settings': {}, 'self_hosted': True, 'primary_association': 'own_association',
    'google_client_id': 'legacy-google-id', 'apple_client_id': '',
    'email_settings': {'host': 'smtp.seed.example.test', 'port': 587, 'security': 'tls', 'username': 'seed-user',
                       'from_email': 'seed@example.test', 'sender_name': 'Seed'},
    'email_password_encrypted': 'not-a-fernet-token', 'email_revision': 0,
    'diagnostic_results': {'checks': [SEEDED_CHECK, SEEDED_EMAIL_CHECK], 'checked_at': '2026-01-02T03:04:06+00:00',
                           'email_test': SEEDED_EMAIL_TEST, 'integration_tests': {'google': GOOGLE_TEST, 'apple': APPLE_TEST},
                           'operation_leases': {'diagnostics': 4102444800.0}},
    'integration_settings': {'stripe': {'revision': 3, 'settings': {
        'enabled': False, 'public_key': 'pk_test_seed', 'secret_key': 'not-a-fernet-token',
        'webhook_secret': '$fernet-integration:legacy-format'}}},
}

# The generator's model of the managed fields. Secrets: {'$secret': plain}
# (decrypts to plain; '' stays '') or {'$literal': stored}.
state = {
    'name': INSTANCE['name'], 'abbreviation': INSTANCE['abbreviation'], 'primary_color': INSTANCE['primary_color'],
    'support_email': INSTANCE['support_email'], 'ui_api_version': 'v1', 'email_settings': copy.deepcopy(INSTANCE['email_settings']),
    'email_password': {'$literal': 'not-a-fernet-token'}, 'email_revision': 0,
    'diagnostic_results': copy.deepcopy(INSTANCE['diagnostic_results']),
    'integration_settings': {'stripe': {'revision': 3, 'settings': {
        'enabled': False, 'public_key': 'pk_test_seed', 'secret_key': {'$literal': 'not-a-fernet-token'},
        'webhook_secret': {'$secret': 'legacy-format'}}}},
}
# Plain values the generator needs to reason about secrets.
plain = {'email': None, 'stripe': {'secret_key': None, 'webhook_secret': 'legacy-format'}}
cases = []


def add(name, actor, path, status, response, *, method='GET', body=None, raw_body=None, content_type=None, headers=None,
        effect=None, cache=None, retry=None, audit=None, **probes):
    if cache is None:
        cache = 'no-store' if path.startswith(NO_STORE) and response != SERVER else None
    case = {'name': f'instance-admin-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect, 'cache_control': cache, 'retry_after': retry,
            'audit': audit}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    case.update(probes)
    cases.append(case)


def write(touched=True):
    """The complete expected managed fields after a write case."""
    return {'fields': copy.deepcopy(state), 'touched': touched}


def options(name):
    return {'name': name, 'description': '', 'renders': ['application/json'],
            'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}


# ---------------------------------------------------------------------------
# Expected bodies, from the Django serializers and helpers.
# ---------------------------------------------------------------------------
DISPLAY = {'general': {'searchBar': True, 'cookieConsent': True, 'showDemoButton': False},
           'login': {'showSportAssociationCreation': False, 'allowOauthLogin': True, 'showFooterInfo': True,
                     'allowOnlyAthletes': False, 'showTestimonials': False, 'termsAndConditionsUrl': ''},
           'navbar': {'showAffiliateLink': False, 'showReview': False, 'showNotifications': True},
           'sidebar': {'showPlanUpgrades': False, 'showPrivacyPolicy': True, 'showTermsOfService': True, 'showPreferences': True}}
ENV_INTEGRATIONS = {
    'ai': {'api_key': '', 'model': 'deepseek-v4-flash', 'cheap_model': 'deepseek-v4-flash', 'base_url': 'http://127.0.0.1:9',
           'max_iterations': 15, 'max_results': 5000, 'query_timeout': 10, 'ws_rate_limit': 10, 'ws_timeout': 240,
           'history_cap': 50, 'enabled': False},
    'stripe': {'public_key': '', 'secret_key': '', 'webhook_secret': '', 'enabled': False},
    'google': {'client_id': '', 'enabled': False}, 'apple': {'client_id': '', 'enabled': False},
}
SECRETS = {'ai': ('api_key',), 'stripe': ('secret_key', 'webhook_secret'), 'google': (), 'apple': ()}


def effective(provider):
    """effective_integration(provider, config, decrypt=False) as (value, decryptable)."""
    record = state['integration_settings'].get(provider, {})
    settings = record.get('settings')
    if settings is None:
        value = dict(ENV_INTEGRATIONS[provider])
        source = 'environment'
    else:
        value = dict(settings)
        source = 'instance'
    return {**value, 'source': source, 'revision': record.get('revision', 0)}


def public_integration(provider):
    value = effective(provider)
    credential_error = False
    for key in SECRETS[provider]:
        secret = value.pop(key, '')
        value[key + '_configured'] = secret != '' and secret != {'$secret': ''}
        if value['source'] == 'instance' and isinstance(secret, dict) and '$literal' in secret:
            credential_error = True
    last_test = state['diagnostic_results'].get('integration_tests', {}).get(provider)
    if last_test and last_test.get('revision') != value['revision']:
        last_test = None
    return {**value, 'provider': provider, 'credential_error': credential_error, 'last_test': last_test,
            'restart_required': False, 'webhook_url': APP_URL + '/api/stripe/webhook' if provider == 'stripe' else None,
            'authorized_origin': APP_URL if provider == 'google' else None,
            'web_login_available': provider == 'google' if provider != 'stripe' else None}


def public_email(get=False):
    revision = state['email_revision']
    if state['email_settings']:
        value = {**state['email_settings'], 'source': 'instance', 'revision': revision}
        stored = state['email_password']
        credential_error = '$literal' in stored
        configured = stored != {'$secret': ''}
    else:
        value = {'source': 'environment', 'host': '', 'port': 465, 'username': ENV_USER, 'security': 'none',
                 'from_email': ENV_USER, 'sender_name': ENV_SENDER, 'revision': revision}
        credential_error, configured = False, False
    body = {**value, 'password_configured': configured, 'credential_error': credential_error, 'restart_required': False,
            'application': APPLICATION}
    if get:
        last_test = state['diagnostic_results'].get('email_test')
        if last_test and last_test.get('revision') != revision:
            last_test = None
        body['last_test'] = last_test
    return body


def config_body():
    google, apple, stripe, ai = effective('google'), effective('apple'), effective('stripe'), effective('ai')
    legacy_google = 'legacy-google-id'

    def client(value, legacy):
        if not value['enabled']:
            return None
        return (value['client_id'] if value['source'] == 'instance' else legacy or value['client_id']) or None
    return {
        'oem': {'name': state['name'], 'abbreviation': state['abbreviation'], 'logo': '/oem/assozeta/brand/logo.svg',
                'supportEmail': state['support_email'], 'primaryColor': state['primary_color'], 'displaySettings': DISPLAY,
                'manualUrl': '/#/manuale'},
        'oauth': {'googleClientId': client(google, legacy_google), 'appleClientId': client(apple, ''),
                  'googleEnabled': google['enabled'] and (google['source'] == 'instance' or DISPLAY['login']['allowOauthLogin'])},
        'stripe': {'publicKey': (stripe['public_key'] or None) if stripe['enabled'] else None, 'pricingTable': None, 'clientPortal': None},
        'meta': {'title': state['name'], 'description': 'Gestionale per associazioni sportive', 'manifest': '/api/instance/manifest.json'},
        'features': {'aiEnabled': ai['enabled'], 'manualEnabled': False, 'isReseller': False, 'selfHosted': True,
                     'supportMultipleAssociations': False, 'showCourseAthletes': True, 'showMinorExemption': True,
                     'showTutorial': True, 'familyRegistration': True, 'federationRegistration': False, 'multipleRegistration': False,
                     'uiApiVersion': state['ui_api_version']},
        'setup': {'provenance': 'fresh', 'onboardingComplete': False},
    }


def admin_body():
    return {'config': config_body(), 'running_version': 'v0.0.0', 'configured_version': 'latest', 'mode': 'development'}


CHECKS = (('public_url', 'URL pubblico e HTTPS', True, 'overview'), ('api', 'API', True, 'diagnostics'),
          ('database', 'Database', True, 'diagnostics'), ('storage', 'Archivio file', True, 'diagnostics'),
          ('worker', 'Worker', True, 'diagnostics'), ('scheduler', 'Scheduler', True, 'diagnostics'),
          ('renderer', 'Renderer PDF', True, 'diagnostics'), ('email', 'Configurazione email', False, 'email'),
          ('updater', 'Servizio aggiornamenti', False, 'backups'), ('backups', 'Preparazione backup', False, 'backups'))
NOT_CHECKED = {'status': 'not_checked', 'level': 'connectivity', 'message': 'Esegui la diagnostica per verificare questo servizio.',
               'checked_at': None}


def diagnostics_body():
    stored = state['diagnostic_results']
    values = {value['id']: value for value in stored.get('checks', [])}
    checks = [{**values.get(key, NOT_CHECKED), 'id': key, 'label': label, 'core': core, 'section': section}
              for key, label, core, section in CHECKS]
    core = [check['status'] for check in checks if check['core']]
    overall = 'failed' if 'failed' in core else 'warning' if any(v in ('warning', 'not_configured') for v in core) else \
        'passed' if core and all(v == 'passed' for v in core) else 'not_checked'
    integrations = []
    for key, label in (('stripe', 'Stripe'), ('google', 'Accesso Google'), ('apple', 'Accesso Apple')):
        value = public_integration(key)
        status, message = 'not_configured', 'Integrazione facoltativa non configurata.'
        if not value['enabled']:
            if value['source'] == 'instance' or value.get('client_id') or value.get('public_key'):
                status, message = 'not_applicable', 'Integrazione disabilitata. Le credenziali salvate sono conservate.'
        elif value['credential_error']:
            status, message = 'warning', 'Una credenziale salvata non può essere letta. Reinseriscila nella configurazione dell’integrazione.'
        elif key == 'stripe':
            secret = plain['stripe']['secret_key'] or ''
            if not (value['public_key'] and value['secret_key_configured'] and value['webhook_secret_configured']):
                status, message = 'warning', 'Completa chiave pubblica, chiave segreta e firma webhook nella configurazione Stripe.'
            elif value['public_key'].startswith('pk_test_') and secret.startswith(('sk_test_', 'rk_test_')):
                status, message = 'passed', 'Credenziali sandbox presenti. Pagamenti e webhook non verificati.'
            else:
                status, message = 'warning', 'Le chiavi Stripe non indicano lo stesso ambiente. Correggi la configurazione.'
        elif value['client_id']:
            status, message = 'passed', 'Client ID configurato. Un accesso reale con il provider non è stato verificato.'
            if key == 'apple':
                status, message = 'warning', 'Client ID configurato per verificare token Apple. Il pulsante di accesso Apple sul web non è disponibile in questa versione.'
        integrations.append({'id': key, 'label': label, 'status': status, 'level': 'configuration', 'message': message,
                             'checked_at': NOW, 'core': False})
    return {'checks': checks, 'overall': overall, 'checked_at': stored.get('checked_at'), 'email_test': stored.get('email_test'),
            'integrations': integrations}


def email_persist(settings, password):
    """EmailSettingsView.persist after setting email_settings and the password."""
    state['email_settings'] = settings
    state['email_password'] = {'$secret': password}
    plain['email'] = password
    state['email_revision'] += 1
    results = state['diagnostic_results']
    results['checks'] = [item for item in results.get('checks', []) if item.get('id') != 'email']
    results.pop('email_test', None)


def integration_persist(provider, revision, settings):
    state['integration_settings'][provider] = {'revision': revision, 'settings': settings}


# ---------------------------------------------------------------------------
# Identity, permission and dispatch (DRF: authentication, then
# IsInstanceOwner on the authenticated actor, then the method handler).
# ---------------------------------------------------------------------------
add('anonymous', 'anonymous', ADMIN, 401, ANON)
add('anonymous-options', 'anonymous', ADMIN, 401, ANON, method='OPTIONS')
add('anonymous-email', 'anonymous', EMAIL, 401, ANON)
add('athlete-denied', 'athlete', ADMIN, 403, DENIED)
add('restricted-collaborator-denied', 'restricted_collaborator', ADMIN, 403, DENIED)
# A collaborator acts for its owner elsewhere; here the actor itself is checked.
add('full-collaborator-denied', 'full_collaborator', ADMIN, 403, DENIED, method='PUT', body={'oem': {'name': 'x'}})
add('foreign-owner-email-denied', 'foreign_owner', EMAIL, 403, DENIED)
add('owner-without-association-diagnostics-denied', 'owner_without_association', DIAGNOSTICS, 403, DENIED)
add('athlete-wrong-method-denied-first', 'athlete', ADMIN, 403, DENIED, method='DELETE')
add('athlete-integration-denied', 'athlete', integration('stripe'), 403, DENIED, method='PUT', body={})
add('athlete-updates-denied', 'athlete', UPDATES, 403, DENIED)
add('athlete-restarts-denied', 'athlete', RESTARTS, 403, DENIED, method='POST', body={'request_id': REQUEST_ID})
add('owner-impersonation-header', 'owner', ADMIN, 403, LEAVE, headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
add('owner-wrong-method', 'owner', ADMIN, 405, {'detail': 'Method "DELETE" not allowed.'}, method='DELETE')
add('owner-email-wrong-method', 'owner', EMAIL, 405, {'detail': 'Method "POST" not allowed.'}, method='POST', body={})
add('owner-integration-wrong-method', 'owner', integration('stripe'), 405, {'detail': 'Method "POST" not allowed.'}, method='POST', body={})
add('owner-diagnostics-wrong-method', 'owner', DIAGNOSTICS, 405, {'detail': 'Method "PUT" not allowed.'}, method='PUT', body={})
add('owner-restarts-wrong-method', 'owner', RESTARTS, 405, {'detail': 'Method "GET" not allowed.'})
add('owner-updates-wrong-method', 'owner', UPDATES, 405, {'detail': 'Method "DELETE" not allowed.'}, method='DELETE')
add('options-admin', 'owner', ADMIN, 200, options('Instance Admin'), method='OPTIONS')
add('options-email', 'owner', EMAIL, 200, options('Email Settings'), method='OPTIONS')
add('options-integration', 'owner', integration('google'), 200, options('Integration Settings'), method='OPTIONS')
add('options-diagnostics', 'owner', DIAGNOSTICS, 200, options('Diagnostics'), method='OPTIONS')
add('options-updates', 'owner', UPDATES, 200, options('Instance Updates'), method='OPTIONS')
add('options-restarts', 'owner', RESTARTS, 200, options('Instance Restarts'), method='OPTIONS')
# The provider is resolved inside the handler, after permission checks.
add('options-unknown-provider', 'owner', integration('github'), 200, options('Integration Settings'), method='OPTIONS')

# ---------------------------------------------------------------------------
# admin: GET and the branding-only PUT.
# ---------------------------------------------------------------------------
add('owner-admin', 'owner', ADMIN, 200, admin_body())
add('superuser-admin-without-session', 'admin', ADMIN, 200, admin_body())
# Administration paths ignore an administrator's session header.
add('superuser-admin-session-ignored', 'admin', ADMIN, 200, admin_body(), headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
ONLY_BRANDING = ['Only instance branding can be edited here.']
add('put-empty-body', 'owner', ADMIN, 400, ONLY_BRANDING, method='PUT')
add('put-empty-object', 'owner', ADMIN, 400, ONLY_BRANDING, method='PUT', body={})
add('put-extra-key', 'owner', ADMIN, 400, ONLY_BRANDING, method='PUT', body={'oem': {'name': 'x'}, 'meta_title': 'x'})
add('put-other-key', 'owner', ADMIN, 400, ONLY_BRANDING, method='PUT', body={'meta_title': 'x'})
# set('oem') is a set of characters.
add('put-string-body', 'owner', ADMIN, 400, ONLY_BRANDING, method='PUT', body='oem')
# set(['oem']) == {'oem'}: the serializer then rejects the list.
add('put-list-body', 'owner', ADMIN, 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']},
    method='PUT', body=['oem'])
add('put-number-body', 'owner', ADMIN, 500, SERVER, method='PUT', body=5)
add('put-null-body', 'owner', ADMIN, 500, SERVER, method='PUT', raw_body='null', content_type='application/json')
add('put-unhashable-list', 'owner', ADMIN, 500, SERVER, method='PUT', body=[{'oem': 1}])
add('put-oem-null', 'owner', ADMIN, 400, {'oem': [NULL]}, method='PUT', body={'oem': None})
add('put-oem-string', 'owner', ADMIN, 400, {'oem': {'non_field_errors': ['Invalid data. Expected a dictionary, but got str.']}},
    method='PUT', body={'oem': 'x'})
add('put-oem-list', 'owner', ADMIN, 400, {'oem': {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']}},
    method='PUT', body={'oem': ['name']})
add('put-oem-invalid', 'owner', ADMIN, 400, {'oem': {
    'name': [BLANK], 'abbreviation': ['Ensure this field has no more than 50 characters.'],
    'primaryColor': ['This value does not match the required pattern.'], 'supportEmail': ['Enter a valid email address.']}},
    method='PUT', body={'oem': {'name': '   ', 'abbreviation': 'x' * 51, 'primaryColor': 'red', 'supportEmail': 'not-an-email'}})
# EmailField and RegexField render a non-string as their own invalid message.
add('put-oem-types', 'owner', ADMIN, 400, {'oem': {'name': ['Not a valid string.'], 'primaryColor': [NULL],
                                                    'supportEmail': ['Enter a valid email address.'], 'abbreviation': [NULL]}},
    method='PUT', body={'oem': {'name': True, 'primaryColor': None, 'supportEmail': ['a@example.test'], 'abbreviation': None}})
add('put-oem-long-name', 'owner', ADMIN, 400, {'oem': {'name': ['Ensure this field has no more than 255 characters.'],
                                                        'primaryColor': ['This value does not match the required pattern.']}},
    method='PUT', body={'oem': {'name': 'n' * 256, 'primaryColor': '#12345G'}})
add('put-oem-color-type', 'owner', ADMIN, 400, {'oem': {'primaryColor': ['This value does not match the required pattern.']}},
    method='PUT', body={'oem': {'primaryColor': 123456}})
add('put-oem-null-character', 'owner', ADMIN, 400, {'oem': {'name': ['Null characters are not allowed.']}},
    method='PUT', body={'oem': {'name': 'a\x00b'}})
# An empty nested object validates to nothing: only updated_at is saved.
add('put-oem-empty', 'owner', ADMIN, 200, {'config': config_body()}, method='PUT', body={'oem': {}}, effect=write())
state.update(name='Nuovo nome', primary_color='#ABCDEF')
add('put-oem-name-color', 'owner', ADMIN, 200, {'config': config_body()}, method='PUT',
    body={'oem': {'name': '  Nuovo nome  ', 'primaryColor': '#ABCDEF', 'ignored': 1}}, effect=write())
state.update(abbreviation='', support_email='')
add('put-oem-blank-optional', 'owner', ADMIN, 200, {'config': config_body()}, method='PUT',
    body={'oem': {'abbreviation': '', 'supportEmail': ''}}, effect=write())
state.update(name='42', support_email='Admin@Example.test', abbreviation='AB')
add('superuser-put-oem', 'admin', ADMIN, 200, {'config': config_body()}, method='PUT',
    body={'oem': {'name': 42, 'supportEmail': ' Admin@Example.test ', 'abbreviation': 'AB'}}, effect=write())

# The web UI API version (ChoiceField v1/v2, source ui_api_version) is the
# other editable key. A change writes one UPDATE log entry on the
# configuration with [previous, new]; an unchanged value saves without one.
add('ui-version-denied-collaborator', 'full_collaborator', ADMIN, 403, DENIED, method='PUT', body={'uiApiVersion': 'v2'})
add('ui-version-anonymous', 'anonymous', ADMIN, 401, ANON, method='PUT', body={'uiApiVersion': 'v2'})
add('ui-version-extra-key', 'owner', ADMIN, 400, ONLY_BRANDING, method='PUT', body={'uiApiVersion': 'v2', 'meta_title': 'x'})
add('ui-version-string-body', 'owner', ADMIN, 400, ONLY_BRANDING, method='PUT', body='uiApiVersion')
add('ui-version-list-body', 'owner', ADMIN, 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']},
    method='PUT', body=['uiApiVersion', 'oem'])
add('ui-version-list-other-item', 'owner', ADMIN, 400, ONLY_BRANDING, method='PUT', body=['uiApiVersion', 1])
add('ui-version-list-unhashable', 'owner', ADMIN, 500, SERVER, method='PUT', body=['uiApiVersion', ['oem']])
CHOICE = '"{}" is not a valid choice.'
add('ui-version-invalid-choice', 'owner', ADMIN, 400, {'uiApiVersion': [CHOICE.format('v3')]}, method='PUT', body={'uiApiVersion': 'v3'})
add('ui-version-blank', 'owner', ADMIN, 400, {'uiApiVersion': [CHOICE.format('')]}, method='PUT', body={'uiApiVersion': ''})
add('ui-version-padded', 'owner', ADMIN, 400, {'uiApiVersion': [CHOICE.format(' v2')]}, method='PUT', body={'uiApiVersion': ' v2'})
add('ui-version-upper', 'owner', ADMIN, 400, {'uiApiVersion': [CHOICE.format('V2')]}, method='PUT', body={'uiApiVersion': 'V2'})
add('ui-version-null', 'owner', ADMIN, 400, {'uiApiVersion': [NULL]}, method='PUT', body={'uiApiVersion': None})
add('ui-version-bool', 'owner', ADMIN, 400, {'uiApiVersion': [CHOICE.format('True')]}, method='PUT', body={'uiApiVersion': True})
add('ui-version-int', 'owner', ADMIN, 400, {'uiApiVersion': [CHOICE.format('2')]}, method='PUT', body={'uiApiVersion': 2})
add('ui-version-float', 'owner', ADMIN, 400, {'uiApiVersion': [CHOICE.format('2.0')]}, method='PUT', body={'uiApiVersion': 2.0})
add('ui-version-list', 'owner', ADMIN, 400, {'uiApiVersion': [CHOICE.format("['v2']")]}, method='PUT', body={'uiApiVersion': ['v2']})
add('ui-version-dict', 'owner', ADMIN, 400, {'uiApiVersion': [CHOICE.format("{'v': 2, 'x': None}")]}, method='PUT',
    body={'uiApiVersion': {'v': 2, 'x': None}})
add('ui-version-with-invalid-oem', 'owner', ADMIN, 400, {'oem': {'primaryColor': ['This value does not match the required pattern.']},
                                                       'uiApiVersion': [CHOICE.format('v9')]},
    method='PUT', body={'oem': {'primaryColor': 'red'}, 'uiApiVersion': 'v9'})
add('ui-version-oem-null', 'owner', ADMIN, 400, {'oem': [NULL]}, method='PUT', body={'oem': None, 'uiApiVersion': 'v2'})
state.update(ui_api_version='v2')
add('ui-version-enable', 'owner', ADMIN, 200, {'config': config_body()}, method='PUT', body={'uiApiVersion': 'v2'}, effect=write(),
    audit={'actor': 'owner', 'association': 'own_association', 'changes': {'ui_api_version': ['v1', 'v2']}})
add('ui-version-unchanged', 'owner', ADMIN, 200, {'config': config_body()}, method='PUT', body={'uiApiVersion': 'v2', 'oem': {}},
    effect=write())
add('ui-version-admin-read', 'owner', ADMIN, 200, admin_body())
state.update(name='Istanza v1', ui_api_version='v1')
add('ui-version-superuser-disable-with-branding', 'admin', ADMIN, 200, {'config': config_body()}, method='PUT',
    body={'oem': {'name': 'Istanza v1'}, 'uiApiVersion': 'v1'}, effect=write(),
    audit={'actor': 'admin', 'association': 'admin_association', 'changes': {'ui_api_version': ['v2', 'v1']}})

# ---------------------------------------------------------------------------
# admin/updates and admin/restarts in development mode: validation first,
# then the development refusal; the runner is never contacted.
# ---------------------------------------------------------------------------
add('updates-status', 'owner', UPDATES, 200, {'available': False, 'reason': 'Gli aggiornamenti dell’installazione sono disabilitati in sviluppo.',
                                              'active': None, 'history': []})
add('updates-required', 'owner', UPDATES, 400, {'release_id': [REQUIRED], 'tag': [REQUIRED], 'request_id': [REQUIRED]},
    method='POST', body={})
add('updates-invalid', 'owner', UPDATES, 400, {'release_id': ['Ensure this value is greater than or equal to 1.'],
                                               'tag': ['Ensure this field has no more than 64 characters.'],
                                               'request_id': ['Must be a valid UUID.']},
    method='POST', body={'release_id': '0', 'tag': 'v' * 65, 'request_id': 'not-a-uuid'})
add('updates-types', 'owner', UPDATES, 400, {'release_id': [INTEGER], 'tag': [BLANK], 'request_id': ['Must be a valid UUID.']},
    method='POST', body={'release_id': 1.5, 'tag': ' ', 'request_id': 1.5})
add('updates-list-body', 'owner', UPDATES, 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']},
    method='POST', body=[1])
add('updates-development', 'owner', UPDATES, 409, {'error': 'Gli aggiornamenti reali sono disabilitati in sviluppo.'},
    method='POST', body={'release_id': 7, 'tag': 'v9.9.9', 'request_id': REQUEST_ID})
add('updates-development-integer-uuid', 'admin', UPDATES, 409, {'error': 'Gli aggiornamenti reali sono disabilitati in sviluppo.'},
    method='POST', body={'release_id': '7.0', 'tag': 'v9.9.9', 'request_id': 12345})
ONLY_REQUEST = ['È richiesto soltanto l’identificativo della richiesta.']
add('restarts-empty', 'owner', RESTARTS, 400, ONLY_REQUEST, method='POST', body={})
add('restarts-extra-key', 'owner', RESTARTS, 400, ONLY_REQUEST, method='POST', body={'request_id': REQUEST_ID, 'force': True})
add('restarts-invalid-uuid', 'owner', RESTARTS, 400, {'request_id': ['Must be a valid UUID.']}, method='POST', body={'request_id': 'nope'})
add('restarts-null-uuid', 'owner', RESTARTS, 400, {'request_id': [NULL]}, method='POST', body={'request_id': None})
add('restarts-list-body', 'owner', RESTARTS, 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']},
    method='POST', body=['request_id'])
add('restarts-development', 'owner', RESTARTS, 409, {'error': 'Il riavvio dei container è disabilitato in sviluppo.'},
    method='POST', body={'request_id': REQUEST_ID})
add('restarts-development-hex', 'admin', RESTARTS, 409, {'error': 'Il riavvio dei container è disabilitato in sviluppo.'},
    method='POST', body={'request_id': '{B2000000000040008000000000000001}'})

# ---------------------------------------------------------------------------
# admin/diagnostics: the stored snapshot; POST only while a lease is active.
# ---------------------------------------------------------------------------
add('diagnostics-seeded', 'owner', DIAGNOSTICS, 200, diagnostics_body(), cache='no-store')
add('diagnostics-superuser', 'admin', DIAGNOSTICS, 200, diagnostics_body(), cache='no-store')
add('diagnostics-run-leased', 'owner', DIAGNOSTICS, 429,
    {'error': 'Diagnostica già in corso o appena terminata. Attendi fino a 45 secondi e riprova.'}, method='POST', body={}, retry='45')

# ---------------------------------------------------------------------------
# admin/email.
# ---------------------------------------------------------------------------
add('email-seeded-unreadable', 'owner', EMAIL, 200, public_email(get=True))
add('email-put-required', 'owner', EMAIL, 400, {key: [REQUIRED] for key in (
    'revision', 'host', 'port', 'security', 'username', 'from_email', 'sender_name')}, method='PUT', body={})
add('email-put-list-body', 'owner', EMAIL, 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']},
    method='PUT', body=[])
add('email-put-invalid', 'owner', EMAIL, 400, {
    'revision': ['Ensure this value is greater than or equal to 0.'],
    'host': ['Inserisci un nome host o indirizzo IP senza protocollo o percorso.'],
    'port': ['Ensure this value is less than or equal to 65535.'], 'security': ['"starttls" is not a valid choice.'],
    'username': ['Ensure this field has no more than 254 characters.'], 'password': [BLANK],
    'clear_password': ['Must be a valid boolean.'], 'from_email': ['Enter a valid email address.'],
    'sender_name': ['Il nome mittente deve essere su una sola riga.']}, method='PUT', body={
    'revision': -1, 'host': 'smtp://mail.example.test', 'port': 70000, 'security': 'starttls', 'username': 'u' * 255,
    'password': '', 'clear_password': 'maybe', 'from_email': 'not-an-email', 'sender_name': 'Riga\nseconda'})
add('email-put-types', 'owner', EMAIL, 400, {
    'revision': [INTEGER], 'host': [BLANK], 'port': [INTEGER], 'security': ['"5" is not a valid choice.'],
    'username': [NULL], 'from_email': [BLANK], 'sender_name': ['Not a valid string.']}, method='PUT', body={
    'revision': True, 'host': ' ', 'port': '1e3', 'security': 5, 'username': None, 'from_email': '', 'sender_name': False})
add('email-put-host-label', 'owner', EMAIL, 400, {'host': ['Inserisci un nome host o indirizzo IP senza protocollo o percorso.'],
                                                  'port': ['Ensure this value is greater than or equal to 1.']},
    method='PUT', body={'revision': 0, 'host': '-bad.example.test', 'port': 0, 'security': 'tls', 'username': '',
                        'from_email': 'a@example.test', 'sender_name': ''})
add('email-put-password-and-clear', 'owner', EMAIL, 400, {'non_field_errors': ['Scegli se sostituire o rimuovere la password.']},
    method='PUT', body={'revision': 0, 'host': 'smtp.example.test', 'port': 465, 'security': 'ssl', 'username': '',
                        'password': 'placeholder', 'clear_password': True, 'from_email': 'a@example.test', 'sender_name': ''})
add('email-put-credentials-without-tls', 'owner', EMAIL, 400, {'non_field_errors': ['Usa TLS o SSL per inviare credenziali SMTP.']},
    method='PUT', body={'revision': 0, 'host': '192.0.2.10', 'port': 25, 'security': 'none', 'username': 'mailer',
                        'from_email': 'a@example.test', 'sender_name': ''})
add('email-put-stale', 'owner', EMAIL, 409, {'error': 'Configurazione modificata in un’altra sessione. Ricarica prima di salvare.'},
    method='PUT', body={'revision': 7, 'host': 'smtp.example.test', 'port': 465, 'security': 'ssl', 'username': '',
                        'from_email': 'a@example.test', 'sender_name': ''})
# The saved password cannot be decrypted, so it must be replaced or cleared.
add('email-put-unreadable-retained', 'owner', EMAIL, 400, {'error': 'Reinserisci o rimuovi esplicitamente la password salvata.'},
    method='PUT', body={'revision': 0, 'host': 'smtp.example.test', 'port': 465, 'security': 'ssl', 'username': '',
                        'from_email': 'a@example.test', 'sender_name': ''})
email_persist({'host': 'smtp.example.test', 'port': 2525, 'security': 'ssl', 'username': 'mailer',
               'from_email': 'Mailer@Example.test', 'sender_name': 'Assozeta Test'}, 'placeholder secret 1 ')
add('email-put-replace-password', 'owner', EMAIL, 200, public_email(), method='PUT', body={
    'revision': '0', 'host': ' smtp.example.test ', 'port': '2525', 'security': 'ssl', 'username': ' mailer ',
    'password': 'placeholder secret 1 ', 'clear_password': 'false', 'from_email': ' Mailer@Example.test ',
    'sender_name': 'Assozeta Test', 'unknown': 'ignored'}, effect=write())
add('email-after-replace', 'owner', EMAIL, 200, public_email(get=True))
email_persist({'host': '2001:db8::25', 'port': 587, 'security': 'tls', 'username': '', 'from_email': 'mailer@example.test',
               'sender_name': ''}, 'placeholder secret 1 ')
add('email-put-retain-password', 'owner', EMAIL, 200, public_email(), method='PUT', body={
    'revision': 1, 'host': '2001:db8::25', 'port': 587, 'security': 'tls', 'username': '',
    'from_email': 'mailer@example.test', 'sender_name': ''}, effect=write())
add('email-put-retained-password-without-tls', 'owner', EMAIL, 400, {'error': 'Rimuovi la password oppure abilita TLS o SSL.'},
    method='PUT', body={'revision': 2, 'host': 'smtp.example.test', 'port': 25, 'security': 'none', 'username': '',
                        'from_email': 'mailer@example.test', 'sender_name': ''})
email_persist({'host': 'smtp.example.test.', 'port': 25, 'security': 'none', 'username': '', 'from_email': 'mailer@example.test',
               'sender_name': 'Senza TLS'}, '')
add('email-put-clear-password', 'owner', EMAIL, 200, public_email(), method='PUT', body={
    'revision': 2, 'host': 'smtp.example.test.', 'port': 25, 'security': 'none', 'username': '', 'clear_password': 'true',
    'from_email': 'mailer@example.test', 'sender_name': 'Senza TLS'}, effect=write())
add('email-delete-missing-revision', 'owner', EMAIL, 400, [NULL], method='DELETE')
add('email-delete-invalid-revision', 'owner', EMAIL, 400, [INTEGER], method='DELETE', body={'revision': 'x'})
add('email-delete-negative-revision', 'owner', EMAIL, 400, ['Ensure this value is greater than or equal to 0.'], method='DELETE',
    body={'revision': -2})
add('email-delete-stale', 'owner', EMAIL, 409, {'error': 'Configurazione modificata. Ricarica prima di ripristinare.'},
    method='DELETE', body={'revision': 1})
add('email-delete-list-body', 'owner', EMAIL, 500, SERVER, method='DELETE', body=[3])
state['email_settings'] = {}
email_persist({}, '')
add('email-delete', 'owner', EMAIL, 200, public_email(), method='DELETE', body={'revision': '3'}, effect=write())
add('email-environment', 'owner', EMAIL, 200, public_email(get=True))
# From the environment source, the (empty) environment password is retained.
email_persist({'host': 'mail.example.test', 'port': 465, 'security': 'tls', 'username': 'relay', 'from_email': 'relay@example.test',
               'sender_name': 'Relay'}, '')
add('email-superuser-put-from-environment', 'admin', EMAIL, 200, public_email(), method='PUT', body={
    'revision': 4, 'host': 'mail.example.test', 'port': 465, 'security': 'tls', 'username': 'relay',
    'from_email': 'relay@example.test', 'sender_name': 'Relay'}, effect=write())

# ---------------------------------------------------------------------------
# admin/integrations/<provider>.
# ---------------------------------------------------------------------------
NOT_FOUND = {'detail': 'Not found.'}
add('integration-stripe-seeded', 'owner', integration('stripe'), 200, public_integration('stripe'))
add('integration-google-environment', 'owner', integration('google'), 200, public_integration('google'))
add('integration-apple-environment', 'owner', integration('apple'), 200, public_integration('apple'))
add('integration-ai-environment', 'owner', integration('ai'), 200, public_integration('ai'))
add('integration-unknown', 'owner', integration('github'), 404, NOT_FOUND)
add('integration-unknown-put', 'owner', integration('github'), 404, NOT_FOUND, method='PUT', body={'revision': 0})
add('integration-unknown-delete', 'owner', integration('Stripe'), 404, NOT_FOUND, method='DELETE', body={'revision': 0})
add('integration-stripe-required', 'owner', integration('stripe'), 400,
    {'revision': [REQUIRED], 'enabled': [REQUIRED], 'public_key': [REQUIRED]}, method='PUT', body={})
add('integration-stripe-invalid', 'owner', integration('stripe'), 400, {
    'enabled': ['Must be a valid boolean.'], 'public_key': ['Ensure this field has no more than 300 characters.'],
    'clear_secrets': ['Expected a list of items but got type "str".'], 'secret_key': [NULL]},
    method='PUT', body={'revision': 3, 'enabled': 'maybe', 'public_key': 'p' * 301, 'clear_secrets': 'secret_key', 'secret_key': None})
add('integration-stripe-clear-choice', 'owner', integration('stripe'), 400, {'clear_secrets': {'1': ['"bogus" is not a valid choice.']}},
    method='PUT', body={'revision': 3, 'enabled': False, 'public_key': '', 'clear_secrets': ['secret_key', 'bogus']})
add('integration-stripe-format', 'owner', integration('stripe'), 400, {'public_key': ['Formato della chiave non valido.']},
    method='PUT', body={'revision': 3, 'enabled': False, 'public_key': 'pub_test', 'secret_key': 'nope'})
add('integration-stripe-secret-format', 'owner', integration('stripe'), 400, {'secret_key': ['Formato della chiave non valido.']},
    method='PUT', body={'revision': 3, 'enabled': False, 'public_key': 'pk_live_x', 'secret_key': 'sk_prod_x'})
add('integration-stripe-replace-and-clear', 'owner', integration('stripe'), 400,
    {'non_field_errors': ['Scegli se sostituire o rimuovere ciascuna credenziale.']},
    method='PUT', body={'revision': 3, 'enabled': False, 'public_key': '', 'secret_key': 'sk_test_a', 'clear_secrets': ['secret_key']})
add('integration-stripe-stale', 'owner', integration('stripe'), 409,
    {'error': 'Integrazione modificata in un’altra sessione. Ricarica prima di salvare.'},
    method='PUT', body={'revision': 2, 'enabled': False, 'public_key': ''})
add('integration-stripe-unreadable-retained', 'owner', integration('stripe'), 400,
    {'secret_key': 'Reinserisci o rimuovi esplicitamente questa credenziale.'},
    method='PUT', body={'revision': 3, 'enabled': False, 'public_key': 'pk_test_seed'})
# The retained (decrypted) webhook secret is validated again.
add('integration-stripe-retained-format', 'owner', integration('stripe'), 400, {'webhook_secret': ['Formato della chiave non valido.']},
    method='PUT', body={'revision': 3, 'enabled': False, 'public_key': 'pk_test_seed', 'secret_key': 'sk_test_fixture'})
add('integration-stripe-enable-missing', 'owner', integration('stripe'), 400,
    {'webhook_secret': 'Campo richiesto per abilitare Stripe.', 'public_key': 'Campo richiesto per abilitare Stripe.'},
    method='PUT', body={'revision': 3, 'enabled': True, 'public_key': '', 'secret_key': 'sk_test_fixture', 'clear_secrets': ['webhook_secret']})
add('integration-stripe-enable-mixed', 'owner', integration('stripe'), 400,
    ['Chiave pubblica e segreta devono appartenere allo stesso ambiente Stripe.'],
    method='PUT', body={'revision': 3, 'enabled': True, 'public_key': 'pk_live_fixture', 'secret_key': 'sk_test_fixture',
                        'webhook_secret': 'whsec_fixture'})
integration_persist('stripe', 4, {'enabled': True, 'public_key': 'pk_test_fixture', 'secret_key': {'$secret': 'rk_test_fixture'},
                                  'webhook_secret': {'$secret': 'whsec_fixture'}})
plain['stripe'] = {'secret_key': 'rk_test_fixture', 'webhook_secret': 'whsec_fixture'}
add('integration-stripe-enable', 'owner', integration('stripe'), 200, public_integration('stripe'), method='PUT', body={
    'revision': '3', 'enabled': 'true', 'public_key': ' pk_test_fixture ', 'secret_key': 'rk_test_fixture',
    'webhook_secret': 'whsec_fixture', 'ignored': 1}, effect=write())
integration_persist('stripe', 5, {'enabled': True, 'public_key': 'pk_test_fixture', 'secret_key': {'$secret': 'rk_test_fixture'},
                                  'webhook_secret': {'$secret': 'whsec_fixture'}})
add('integration-stripe-retain', 'owner', integration('stripe'), 200, public_integration('stripe'), method='PUT', body={
    'revision': 4, 'enabled': True, 'public_key': 'pk_test_fixture'}, effect=write())
add('diagnostics-stripe-sandbox', 'owner', DIAGNOSTICS, 200, diagnostics_body(), cache='no-store')
add('admin-stripe-public-key', 'owner', ADMIN, 200, admin_body())
add('integration-stripe-delete-stale', 'owner', integration('stripe'), 409,
    {'error': 'Integrazione modificata. Ricarica prima di ripristinare.'}, method='DELETE', body={'revision': 4})
add('integration-stripe-delete-missing-revision', 'owner', integration('stripe'), 400, [NULL], method='DELETE', body={})
integration_persist('stripe', 6, None)
plain['stripe'] = {'secret_key': None, 'webhook_secret': None}
add('integration-stripe-delete', 'owner', integration('stripe'), 200, public_integration('stripe'), method='DELETE',
    body={'revision': 5}, effect=write())
add('integration-google-client-format', 'owner', integration('google'), 400,
    {'client_id': ['Inserisci un client ID senza spazi, URL o percorsi.']},
    method='PUT', body={'revision': 0, 'enabled': True, 'client_id': 'https://accounts.example.test/id'})
add('integration-google-required', 'owner', integration('google'), 400, {'revision': [REQUIRED], 'enabled': [REQUIRED], 'client_id': [REQUIRED]},
    method='PUT', body={'clear_secrets': ['x']})
add('integration-google-enable-empty', 'owner', integration('google'), 400,
    {'client_id': 'Client ID richiesto per abilitare l’integrazione.'}, method='PUT', body={'revision': 0, 'enabled': True, 'client_id': ''})
add('integration-google-enable-not-web', 'owner', integration('google'), 400,
    {'client_id': 'Inserisci il client ID web fornito da Google.'}, method='PUT', body={'revision': 0, 'enabled': 1, 'client_id': 'fixture-client'})
integration_persist('google', 1, {'enabled': True, 'client_id': 'fixture.apps.googleusercontent.com'})
add('integration-google-enable', 'owner', integration('google'), 200, public_integration('google'), method='PUT', body={
    'revision': 0, 'enabled': True, 'client_id': 'fixture.apps.googleusercontent.com'}, effect=write())
integration_persist('apple', 1, {'enabled': True, 'client_id': 'org.example.fixture'})
add('integration-apple-enable', 'admin', integration('apple'), 200, public_integration('apple'), method='PUT', body={
    'revision': 0, 'enabled': 'on', 'client_id': 'org.example.fixture'}, effect=write())
add('integration-apple-delete-stale', 'owner', integration('apple'), 409,
    {'error': 'Integrazione modificata. Ricarica prima di ripristinare.'}, method='DELETE', body={'revision': 0})
AI = {'revision': 0, 'enabled': True, 'model': 'fixture-model', 'cheap_model': 'fixture-cheap', 'base_url': '',
      'max_iterations': 15, 'max_results': 5000, 'query_timeout': 10, 'ws_rate_limit': 10, 'ws_timeout': 240, 'history_cap': 50}
add('integration-ai-enable-without-key', 'owner', integration('ai'), 400, {'api_key': 'Chiave API richiesta per abilitare il bot AI.'},
    method='PUT', body=AI)
add('integration-ai-invalid', 'owner', integration('ai'), 400, {
    'base_url': ['Usa un URL HTTP o HTTPS senza credenziali, query o frammenti.'], 'max_iterations': ['Ensure this value is greater than or equal to 1.'],
    'history_cap': ['Ensure this value is greater than or equal to 2.'], 'query_timeout': ['Ensure this value is less than or equal to 300.'],
    'ws_timeout': [INTEGER], 'max_results': [REQUIRED], 'model': [BLANK]}, method='PUT', body={
    **{k: v for k, v in AI.items() if k != 'max_results'}, 'base_url': 'ftp://files.example.test/models', 'max_iterations': 0,
    'history_cap': 1, 'query_timeout': 301, 'ws_timeout': 'abc', 'model': ''})
for label, url in (('not-url', 'not a url'), ('no-tld', 'http://example'), ('space', 'http://exa mple.test'),
                   ('bad-port', 'http://example.test:123456/'), ('bad-ipv6', 'http://[::1::2]/'), ('label-dash', 'https://-x.example.test')):
    add(f'integration-ai-url-{label}', 'owner', integration('ai'), 400, {'base_url': ['Enter a valid URL.']}, method='PUT',
        body={**AI, 'base_url': url})
for label, url in (('credentials', 'https://user:secret@llm.example.test/v1'), ('query', 'https://llm.example.test/v1?key=1'),
                   ('fragment', 'https://llm.example.test/v1#top')):
    add(f'integration-ai-url-{label}', 'owner', integration('ai'), 400,
        {'base_url': ['Usa un URL HTTP o HTTPS senza credenziali, query o frammenti.']}, method='PUT', body={**AI, 'base_url': url})
add('integration-ai-url-too-long', 'owner', integration('ai'), 400,
    {'base_url': ['Ensure this field has no more than 2000 characters.']}, method='PUT',
    body={**AI, 'base_url': 'https://llm.example.test/' + 'p' * 1990})
add('integration-ai-replace-and-clear', 'owner', integration('ai'), 400, {'non_field_errors': ['Scegli se sostituire o rimuovere la chiave API.']},
    method='PUT', body={**AI, 'api_key': 'placeholder-ai-key', 'clear_secrets': ['api_key']})
integration_persist('ai', 1, {'enabled': True, 'model': 'fixture-model', 'cheap_model': 'fixture-cheap',
                              'base_url': 'https://llm.example.test/v1/', 'max_iterations': 20, 'max_results': 100000,
                              'query_timeout': 300, 'ws_rate_limit': 1, 'ws_timeout': 3600, 'history_cap': 2,
                              'api_key': {'$secret': 'placeholder-ai-key'}})
add('integration-ai-enable', 'owner', integration('ai'), 200, public_integration('ai'), method='PUT', body={
    **AI, 'api_key': 'placeholder-ai-key', 'base_url': ' https://llm.example.test/v1/ ', 'max_iterations': '20', 'max_results': 100000.0,
    'query_timeout': 300, 'ws_rate_limit': 1, 'ws_timeout': 3600, 'history_cap': 2}, effect=write())
add('admin-integrations-enabled', 'owner', ADMIN, 200, admin_body())
add('diagnostics-integrations-enabled', 'owner', DIAGNOSTICS, 200, diagnostics_body(), cache='no-store')
integration_persist('ai', 2, {'enabled': False, 'model': 'fixture-model', 'cheap_model': 'fixture-cheap',
                              'base_url': 'http://[::1]:8080/v1', 'max_iterations': 1, 'max_results': 1,
                              'query_timeout': 1, 'ws_rate_limit': 1000, 'ws_timeout': 1, 'history_cap': 1000,
                              'api_key': {'$secret': ''}})
add('integration-ai-disable-clear', 'owner', integration('ai'), 200, public_integration('ai'), method='PUT', body={
    **AI, 'revision': 1, 'enabled': False, 'base_url': 'http://[::1]:8080/v1', 'max_iterations': 1, 'max_results': 1,
    'query_timeout': 1, 'ws_rate_limit': 1000, 'ws_timeout': 1, 'history_cap': 1000, 'clear_secrets': ['api_key']}, effect=write())
add('admin-final', 'admin', ADMIN, 200, admin_body())
add('diagnostics-final', 'owner', DIAGNOSTICS, 200, diagnostics_body(), cache='no-store')

# ---------------------------------------------------------------------------
# admin/diagnostics POST runs. Before each run the case's setup replaces the
# stored leases (and, where stated, the email settings). The ten probes run
# with the scripted services; each result is {status, level, message,
# checked_at}, stored in CHECKS order with id/label/core/section, together
# with checked_at and the claimed lease (now + 45 s, '$lease45').
# ---------------------------------------------------------------------------
LEASE = '$lease45'
GENERIC = ('failed', 'connectivity', 'Verifica non riuscita. Controlla configurazione e disponibilità del servizio e riprova.')
HEALTH_FAILED = ('failed', 'connectivity', 'L’endpoint di salute non risponde con la versione attesa. Verifica URL, rete e reverse proxy.')
PASSED = {
    'public_url': ('passed', 'connectivity', 'URL pubblico raggiungibile con certificato HTTPS valido.'),
    'api': ('passed', 'connectivity', 'L’API risponde con la versione in esecuzione.'),
    'database': ('passed', 'connectivity', 'Il database risponde a una query di lettura. Nessun dato è stato modificato.'),
    'storage': ('passed', 'connectivity', 'Archivio accessibile in lettura. Scrittura e caricamento file non sono stati provati.'),
    'worker': ('passed', 'connectivity', 'Un worker risponde tramite il broker. Non sono stati eseguiti task applicativi.'),
    'scheduler': ('passed', 'functional', 'Il ciclo dello scheduler è attivo (segnale negli ultimi 90 secondi). L’esito dei task non è verificato.'),
    'renderer': ('passed', 'connectivity', 'Il renderer apre e chiude una pagina nel browser. La generazione di un documento PDF non è stata provata.'),
    'email': ('passed', 'configuration', 'Campi SMTP e mittente presenti. Connessione e invio non verificati: usa i test espliciti nella sezione Email.'),
    'updater': ('not_applicable', 'configuration', 'Disponibile nelle installazioni self-hosted in modalità produzione.'),
    'backups': ('not_applicable', 'configuration', 'Disponibile nelle installazioni self-hosted in modalità produzione.'),
}
NO_WORKER = ('failed', 'connectivity', 'Nessun worker risponde. Verifica worker e broker e riprova.')
NO_SCHEDULER = ('failed', 'connectivity', 'Segnale dello scheduler assente o scaduto. Avvia o riavvia il servizio beat e ripeti dopo un minuto.')
RENDERER_FAILED = ('failed', 'connectivity', 'Il renderer non supera il controllo di salute. Verifica il servizio PDF.')
TIMEOUT = ('failed', 'connectivity', 'Tempo massimo superato. Controlla la disponibilità del servizio e riprova.')
SMTP_PLAIN = ('warning', 'configuration', 'SMTP senza cifratura. Configura TLS o SSL prima di inviare credenziali o messaggi.')
SMTP_INCOMPLETE = ('warning', 'configuration', 'Nome utente e password SMTP incompleti. Completa entrambe le credenziali oppure rimuovile per un relay senza autenticazione.')
SMTP_MISSING = ('not_configured', 'configuration', 'Configura server SMTP e indirizzo mittente nella sezione Email.')
HEALTH_HEADERS = {'host': '$self', **{'user-agent': 'python-requests/2.34.2', 'accept-encoding': 'gzip, deflate', 'accept': '*/*',
                                     'connection': 'keep-alive'}}
HEALTH = {'method': 'GET', 'path': '/healthz', 'headers': HEALTH_HEADERS}
S3_LIST = {'method': 'GET', 'path': '/$bucket', 'query': {'list-type': '2', 'prefix': 'storage', 'max-keys': '1', 'encoding-type': 'url'},
           'signed': True}
LISTING = ('<?xml version="1.0" encoding="UTF-8"?>\n<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
           '<Name>contract</Name><Prefix>storage</Prefix><KeyCount>0</KeyCount><MaxKeys>1</MaxKeys><EncodingType>url</EncodingType>'
           '<IsTruncated>false</IsTruncated></ListBucketResult>')
DENIED_XML = ('<?xml version="1.0" encoding="UTF-8"?>\n<Error><Code>AccessDenied</Code><Message>Access Denied</Message>'
              '<RequestId>contract</RequestId></Error>')
# The ping kombu broadcasts (normalized by the adapter: '$uuid' for generated
# identifiers, '$expires' for time() + 2).
PING = {'channel': '/0.celery.pidbox', 'envelope_keys': ['body', 'content-encoding', 'content-type', 'headers', 'properties'],
        'content-encoding': 'utf-8', 'content-type': 'application/json', 'headers': {'clock': 1, 'expires': '$expires'},
        'properties': {'delivery_mode': 2, 'delivery_info': {'exchange': 'celery.pidbox', 'routing_key': ''}, 'priority': 0,
                       'body_encoding': 'base64', 'delivery_tag': '$uuid'},
        'body': {'method': 'ping', 'arguments': {}, 'destination': None, 'pattern': None, 'matcher': None, 'ticket': '$uuid',
                 'reply_to': {'exchange': 'reply.celery.pidbox', 'routing_key': '$uuid'}}}


def health_reply(status=200, body=None, raw=None, headers=None):
    value = {'status': status, 'headers': headers or {}}
    if raw is not None:
        value['raw_b64'] = base64.b64encode(raw.encode()).decode()
    else:
        value['raw_b64'] = base64.b64encode(json.dumps(body).encode()).decode()
        value['headers'] = {'Content-Type': 'application/json', **value['headers']}
    return value


HEALTHY = health_reply(body={'status': 'ok', 'version': 'v0.0.0'})
RENDERER_OK = health_reply(body={'status': 'ok'})
S3_OK = {'status': 200, 'headers': {'Content-Type': 'application/xml'}, 'raw_b64': base64.b64encode(LISTING.encode()).decode()}


def public(answer):
    return [{'connect': 'example.org:443', 'method': 'GET', 'path': '/api/healthz',
             'headers': {'host': 'example.org', **{k: v for k, v in HEALTH_HEADERS.items() if k != 'host'}}, 'reply': answer}]


def run(name, results, *, leases, public_reply=HEALTHY, api=HEALTHY, s3=S3_OK, worker='pong', heartbeat='fresh',
        renderer=RENDERER_OK, email_setup=None, race=False, actor='owner'):
    """One claimed run: the expected stored checks and response."""
    setup = {'leases': leases}
    if email_setup is not None:
        settings, password = email_setup
        setup.update(email_settings=settings, email_password=password)
        state['email_settings'] = settings
        state['email_password'] = {'$secret': password} if isinstance(password, str) else password
    results = {**PASSED, **results}
    checks = [{'status': results[key][0], 'level': results[key][1], 'message': results[key][2], 'checked_at': NOW,
               'id': key, 'label': label, 'core': core, 'section': section} for key, label, core, section in CHECKS]
    stored = state['diagnostic_results']
    if race:
        # Another save changes the email revision while the probes run: the
        # email result is not stored.
        state['email_revision'] += 1
        checks = [check for check in checks if check['id'] != 'email']
    stored['operation_leases'] = {**(leases or {}), 'diagnostics': LEASE}
    stored['checks'], stored['checked_at'] = checks, NOW
    add(name, actor, DIAGNOSTICS, 200, diagnostics_body(), method='POST', body={}, cache='no-store', effect=write(touched=False),
        setup=setup, outbound=public(public_reply),
        stubs={'api': {'replies': [api], 'expect': [HEALTH]}, 'renderer': {'replies': [renderer], 'expect': [HEALTH]},
               's3': {'replies': [s3], 'expect': [S3_LIST]}},
        worker={'mode': worker, 'expect': [PING]}, heartbeat=heartbeat, race=race)


RELAY = {'host': 'mail.example.test', 'port': 465, 'security': 'tls', 'username': 'relay', 'from_email': 'relay@example.test',
         'sender_name': 'Relay'}
# A past lease is claimable; other leases are kept.
run('diagnostics-run-passed', {}, leases={'diagnostics': 1.5, 'email_test': 2.5}, email_setup=(RELAY, 'placeholder secret 2'))
add('diagnostics-run-just-finished', 'owner', DIAGNOSTICS, 429,
    {'error': 'Diagnostica già in corso o appena terminata. Attendi fino a 45 secondi e riprova.'}, method='POST', body={}, retry='45')
add('diagnostics-after-run', 'owner', DIAGNOSTICS, 200, diagnostics_body(), cache='no-store')
run('diagnostics-run-failures', {'public_url': HEALTH_FAILED, 'api': HEALTH_FAILED, 'storage': GENERIC, 'worker': NO_WORKER,
                                 'scheduler': NO_SCHEDULER, 'renderer': RENDERER_FAILED, 'email': SMTP_PLAIN},
    leases={}, public_reply=health_reply(503, {'status': 'ok', 'version': 'v0.0.0'}),
    api=health_reply(body={'status': 'ok', 'version': 'v9.9.9'}),
    s3={'status': 403, 'headers': {'Content-Type': 'application/xml'}, 'raw_b64': base64.b64encode(DENIED_XML.encode()).decode()},
    worker='silent', heartbeat='stale', renderer=health_reply(body={'status': 'starting'}),
    email_setup=({**RELAY, 'security': 'none', 'username': ''}, ''))
# A health body that is not JSON, or JSON that is not an object, raises; a
# redirect is not followed; a reset connection raises; a worker answering
# something other than pong does not count; an unreadable saved password raises.
run('diagnostics-run-odd-answers', {'public_url': GENERIC, 'api': HEALTH_FAILED, 'storage': GENERIC, 'worker': NO_WORKER,
                                    'scheduler': NO_SCHEDULER, 'renderer': GENERIC, 'email': GENERIC},
    leases=None, public_reply=health_reply(raw='ok'), api=health_reply(302, raw='', headers={'Location': 'http://127.0.0.1:9/healthz'}),
    s3={'mode': 'reset'}, worker='pang', heartbeat=None, renderer=health_reply(body=[{'status': 'ok'}]),
    email_setup=(RELAY, {'$literal': 'not-a-fernet-token'}))
# The renderer keeps sending its body one byte a second: the probe's 10 s
# deadline ends it. An integer heartbeat (stored as plain digits) counts.
run('diagnostics-run-timeout', {'renderer': TIMEOUT, 'email': SMTP_INCOMPLETE}, leases={}, heartbeat='integer',
    renderer={'mode': 'trickle'}, email_setup=(RELAY, ''))
# The email revision changes while the probes run (the renderer stub bumps it
# when its request arrives): the email check is left out of the saved results.
run('diagnostics-run-email-changed', {}, leases={}, race=True, actor='admin', email_setup=(RELAY, 'placeholder secret 2'))
add('diagnostics-after-runs', 'owner', DIAGNOSTICS, 200, diagnostics_body(), cache='no-store')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'instance administration (admin, email, integrations, diagnostics, updates, restarts)',
                              'ids': IDS, 'instance': INSTANCE, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
