"""Author the instance setup HTTP sequence (instance-setup.md).

Pins BE/instance/views.py (InstanceSetupTokenValidateView, InstanceSetupView,
InstanceReconfigureView, InstanceLogoUploadView), administration.py
(OwnerLogoView) and InstanceConfiguration.save in Django's order: identity
(instance paths never impersonate), the permission (SetupTokenOrAuthenticated
reads the setup token only while no configuration exists; once configured it
is the primary association's owner or a superuser; IsInstanceOwner for
admin/logo), method dispatch, the parser, the serializer, then the writes.

The sequence runs in two phases on one fixture. Phase A has no
InstanceConfiguration: setup-token checks, logo uploads with the token,
configure validation, an import whose task has no result yet, then the fresh
setup that creates the configuration, its owner, the association, the two
default accounts and the onboarding row. The runner then signs a token for
the created owner ('setup_owner'). Phase B runs on the created
configuration: repeated configure, reconfigure validation and saves, and logo
uploads that update logo_path. The generator keeps a model of the
configuration row and of the stored logo objects; every case states the
complete expected storage listing and every write the complete managed
fields. The setup token is the run's placeholder ('$setup-token').
Run with ``python3 -I``.
"""
import copy
import hashlib
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('instance_setup_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
LOCATION = 'instance-setup-fixture'
TOKEN = {'X-Setup-Token': '$setup-token'}
WRONG = {'X-Setup-Token': 'not-the-setup-token'}
ANON = {'detail': 'Authentication credentials were not provided.'}
SETUP_DENIED = {'detail': 'Setup token missing or invalid.'}
OWNER_DENIED = {'detail': 'Only the instance owner or an administrator can administer this installation.'}
LEAVE = {'detail': 'Esci dall’impersonificazione per accedere all’amministrazione.'}
REQUIRED, NULL, BLANK = 'This field is required.', 'This field may not be null.', 'This field may not be blank.'
EMAIL_INVALID, URL_INVALID = 'Enter a valid email address.', 'Enter a valid URL.'
PATTERN, BOOLEAN = 'This value does not match the required pattern.', 'Must be a valid boolean.'
VALIDATE, CONFIGURE, RECONFIGURE = '/instance/setup-token/validate', '/instance/configure', '/instance/reconfigure'
LOGO, ADMIN_LOGO = '/instance/logo', '/instance/admin/logo'
CANONICAL = '/api/instance/logo.png'
LOGO_VERSION = '$logo-version'
MB5 = 5 * 1024 * 1024
DISPLAY = {'general': {'searchBar': True, 'cookieConsent': True, 'showDemoButton': False},
           'login': {'showSportAssociationCreation': False, 'allowOauthLogin': True, 'showFooterInfo': True,
                     'allowOnlyAthletes': False, 'showTestimonials': False, 'termsAndConditionsUrl': ''},
           'navbar': {'showAffiliateLink': False, 'showReview': False, 'showNotifications': True},
           'sidebar': {'showPlanUpgrades': False, 'showPrivacyPolicy': True, 'showTermsOfService': True, 'showPreferences': True}}
cases = []
storage = {}   # stored key (under AWS_LOCATION) -> object description
config = None  # the configuration row's managed fields once configured


def blob(fill, size):
    data = (fill * size)[:size].encode()
    return {'size': size, 'sha256': hashlib.sha256(data).hexdigest()}


def add(name, actor, path, status, response, *, method='POST', body=None, raw_body=None, content_type=None, headers=None,
        upload=None, effect=None):
    case = {'name': f'instance-setup-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect, 'storage': copy.deepcopy(storage)}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    if upload is not None:
        case['upload'] = upload
    cases.append(case)


def options(name, description, parses):
    return {'name': name, 'description': description, 'renders': ['application/json'], 'parses': parses}


JSON_ONLY = ['application/json']
DEFAULT_PARSERS = ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']
UPLOAD_PARSERS = ['multipart/form-data', 'application/x-www-form-urlencoded']
SETUP_OPTIONS = options('Instance Setup', 'POST /instance/configure\nInitial instance setup (only works once).\n'
                        'Protected by setup token during first-run setup wizard.', JSON_ONLY)
VALIDATE_OPTIONS = options('Instance Setup Token Validate', 'Validate the first-run setup token without changing instance state.',
                           DEFAULT_PARSERS)
RECONFIGURE_OPTIONS = options('Instance Reconfigure', 'PUT /instance/reconfigure\nUpdate instance configuration.\n'
                              'Requires authentication and admin privileges.', JSON_ONLY)
LOGO_OPTIONS = options('Instance Logo Upload', 'POST /instance/logo\nUpload instance logo.', UPLOAD_PARSERS)
OWNER_LOGO_OPTIONS = options('Owner Logo', '', UPLOAD_PARSERS)


def file_part(content_type, fill, size, *, name='file', filename='logo.bin'):
    return {'name': name, 'filename': filename, 'content_type': content_type, 'fill': fill, 'size': size}


def upload(*parts):
    return {'parts': list(parts)}


def store(content_type, fill, size, kind):
    """InstanceLogoUploadView: delete every instance/logo.* object, then save instance/logo.<ext>."""
    for ext in ('png', 'jpg', 'svg', 'webp'):
        storage.pop(f'instance/logo.{ext}', None)
    storage[f'instance/logo.{kind}'] = {**blob(fill, size), 'content_type': content_type, 'cache_control': 'max-age=86400'}


def config_effect(touched=True):
    return {'kind': 'config', 'fields': copy.deepcopy(config), 'touched': touched}


def setup_body(**overrides):
    body = {'domain': 'setup.example.test', 'oem': {'name': 'Istanza Setup'},
            'initialization': {'type': 'fresh', 'associationName': 'Associazione Setup', 'ownerEmail': 'owner@setup.example.test',
                               'ownerPassword': 'setup-password-1'}}
    body.update(overrides)
    return body


def invalid(errors):
    return {'success': False, 'errors': errors}


# ===========================================================================
# Phase A: no InstanceConfiguration.
# ===========================================================================

# --- setup-token/validate --------------------------------------------------
# DRF answers a permission denial without a successful authenticator with
# NotAuthenticated, so an anonymous request without the token is a 401.
add('validate-anonymous', 'anonymous', VALIDATE, 401, ANON)
add('validate-wrong-token', 'anonymous', VALIDATE, 401, ANON, headers=WRONG)
add('validate-owner-without-token', 'owner', VALIDATE, 403, SETUP_DENIED)
add('validate-admin-without-token', 'admin', VALIDATE, 403, SETUP_DENIED)
add('validate-token', 'anonymous', VALIDATE, 200, {'valid': True}, headers=TOKEN)
add('validate-owner-token', 'owner', VALIDATE, 200, {'valid': True}, headers=TOKEN)
add('validate-token-get', 'anonymous', VALIDATE, 405, {'detail': 'Method "GET" not allowed.'}, method='GET', headers=TOKEN)
add('validate-token-options', 'anonymous', VALIDATE, 200, VALIDATE_OPTIONS, method='OPTIONS', headers=TOKEN)
add('validate-anonymous-options', 'anonymous', VALIDATE, 401, ANON, method='OPTIONS')
add('validate-anonymous-get', 'anonymous', VALIDATE, 401, ANON, method='GET')
add('validate-owner-impersonation', 'owner', VALIDATE, 403, LEAVE, headers={**TOKEN, 'X-Impersonation-Id': '$session:admin_owner'})

# --- reconfigure before configuration -------------------------------------
add('reconfigure-anonymous', 'anonymous', RECONFIGURE, 401, ANON, method='PUT', body={}, headers=TOKEN)
add('reconfigure-unconfigured-owner', 'owner', RECONFIGURE, 404, {'success': False, 'error': 'Instance not configured'},
    method='PUT', body={'meta_title': 'x'})
add('reconfigure-unconfigured-admin', 'admin', RECONFIGURE, 404, {'success': False, 'error': 'Instance not configured'},
    method='PUT', raw_body='name=x', content_type='application/x-www-form-urlencoded')
add('reconfigure-owner-options', 'owner', RECONFIGURE, 200, RECONFIGURE_OPTIONS, method='OPTIONS')
add('reconfigure-owner-get', 'owner', RECONFIGURE, 405, {'detail': 'Method "GET" not allowed.'}, method='GET')

# --- logo uploads with the setup token ------------------------------------
add('logo-anonymous', 'anonymous', LOGO, 401, ANON, upload=upload(file_part('image/png', 'p', 64)))
add('logo-owner-without-token', 'owner', LOGO, 403, SETUP_DENIED, upload=upload(file_part('image/png', 'p', 64)))
add('logo-token-options', 'anonymous', LOGO, 200, LOGO_OPTIONS, method='OPTIONS', headers=TOKEN)
add('logo-token-get', 'anonymous', LOGO, 405, {'detail': 'Method "GET" not allowed.'}, method='GET', headers=TOKEN)
add('logo-token-json', 'anonymous', LOGO, 415, {'detail': 'Unsupported media type "application/json" in request.'},
    body={'file': 'x'}, headers=TOKEN)
add('logo-token-form', 'anonymous', LOGO, 400, {'success': False, 'error': 'No file provided'},
    raw_body='file=logo.png', content_type='application/x-www-form-urlencoded', headers=TOKEN)
add('logo-token-empty', 'anonymous', LOGO, 400, {'success': False, 'error': 'No file provided'}, headers=TOKEN)
add('logo-token-field-only', 'anonymous', LOGO, 400, {'success': False, 'error': 'No file provided'}, headers=TOKEN,
    upload=upload({'name': 'file', 'value': 'not-a-file'}))
add('logo-token-other-name', 'anonymous', LOGO, 400, {'success': False, 'error': 'No file provided'}, headers=TOKEN,
    upload=upload(file_part('image/png', 'p', 64, name='logo')))
add('logo-token-svg', 'anonymous', LOGO, 400, {'success': False, 'error': 'Invalid file type. Use PNG, JPG, or WebP.'},
    headers=TOKEN, upload=upload(file_part('image/svg+xml', 's', 64)))
add('logo-token-no-type', 'anonymous', LOGO, 400, {'success': False, 'error': 'Invalid file type. Use PNG, JPG, or WebP.'},
    headers=TOKEN, upload=upload(file_part(None, 's', 64)))
add('logo-token-too-large', 'anonymous', LOGO, 400, {'success': False, 'error': 'File too large. Maximum size is 5MB.'},
    headers=TOKEN, upload=upload(file_part('image/png', 'L', MB5 + 1)))
store('image/png', 'a', 4096, 'png')
add('logo-token-png', 'anonymous', LOGO, 200, {'success': True, 'logo_url': CANONICAL}, headers=TOKEN,
    upload=upload({'name': 'note', 'value': 'ignored'}, file_part('image/png', 'a', 4096, filename='brand.PNG')))
store('image/jpeg', 'b', 2048, 'jpg')
add('logo-token-jpeg-replaces', 'anonymous', LOGO, 200, {'success': True, 'logo_url': CANONICAL}, headers=TOKEN,
    upload=upload(file_part('image/jpeg', 'b', 2048, filename='brand.jpeg')))
# The content type is the part's lowercased media type, parameters dropped;
# exactly 5 MiB is accepted. The last 'file' part is the one saved.
store('image/webp', 'w', MB5, 'webp')
add('logo-token-webp-limit', 'anonymous', LOGO, 200, {'success': True, 'logo_url': CANONICAL}, headers=TOKEN,
    upload=upload(file_part('image/png', 'x', 10), file_part('IMAGE/WEBP; charset=binary', 'w', MB5)))

# --- configure validation --------------------------------------------------
add('configure-anonymous', 'anonymous', CONFIGURE, 401, ANON, body=setup_body())
add('configure-wrong-token', 'anonymous', CONFIGURE, 401, ANON, body=setup_body(), headers=WRONG)
add('configure-owner-without-token', 'owner', CONFIGURE, 403, SETUP_DENIED, body=setup_body())
add('configure-token-get', 'anonymous', CONFIGURE, 405, {'detail': 'Method "GET" not allowed.'}, method='GET', headers=TOKEN)
add('configure-token-options', 'anonymous', CONFIGURE, 200, SETUP_OPTIONS, method='OPTIONS', headers=TOKEN)
add('configure-token-form', 'anonymous', CONFIGURE, 415,
    {'detail': 'Unsupported media type "application/x-www-form-urlencoded" in request.'},
    raw_body='domain=setup.example.test', content_type='application/x-www-form-urlencoded', headers=TOKEN)
add('configure-empty', 'anonymous', CONFIGURE, 400, invalid({'domain': [REQUIRED], 'oem': [REQUIRED], 'initialization': [REQUIRED]}),
    body={}, headers=TOKEN)
add('configure-list', 'anonymous', CONFIGURE, 400,
    invalid({'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']}), body=[], headers=TOKEN)
add('configure-short-domain-and-nested-types', 'anonymous', CONFIGURE, 400, invalid({
    'domain': ['Domain must be at least 3 characters.'],
    'oem': {'non_field_errors': ['Invalid data. Expected a dictionary, but got str.']},
    'oauth': [NULL], 'initialization': {'type': [REQUIRED]}}),
    body={'domain': ' ab ', 'oem': 'brand', 'oauth': None, 'initialization': {}}, headers=TOKEN)
add('configure-field-rules', 'anonymous', CONFIGURE, 400, invalid({
    'domain': [BLANK],
    'oem': {'name': [BLANK], 'abbreviation': ['Ensure this field has no more than 50 characters.'],
            'primaryColor': [PATTERN], 'supportEmail': [EMAIL_INVALID], 'logo': ['Logo must be /api/instance/logo.png.']},
    'oauth': {'googleClientId': ['Ensure this field has no more than 255 characters.'], 'appleClientId': ['Not a valid string.']},
    'stripe': {'clientPortal': [URL_INVALID], 'publicKey': [NULL]},
    'initialization': {'type': ['"restore" is not a valid choice.']}}),
    body={'domain': '   ', 'oem': {'name': ' ', 'abbreviation': 'A' * 51, 'primaryColor': '#12345G', 'supportEmail': 'no-at',
                                   'logo': '/oem/other.png'},
          'oauth': {'googleClientId': 'g' * 256, 'appleClientId': {'id': 1}},
          'stripe': {'clientPortal': 'not a url', 'publicKey': None},
          'initialization': {'type': 'restore'}}, headers=TOKEN)
add('configure-long-domain', 'anonymous', CONFIGURE, 400,
    invalid({'domain': ['Ensure this field has no more than 255 characters.'], 'oem': [NULL]}),
    body={'domain': 'd' * 256, 'oem': None, 'initialization': {'type': 'fresh', 'associationName': 'A', 'ownerEmail': 'a@b.example',
                                                                   'ownerPassword': '12345678'}}, headers=TOKEN)
# InitializationInputSerializer.validate runs only without field errors, in
# its own order, one message at a time.
add('configure-fresh-missing-name', 'anonymous', CONFIGURE, 400,
    invalid({'initialization': {'associationName': ['Required for fresh initialization.']}}),
    body=setup_body(initialization={'type': 'fresh'}), headers=TOKEN)
add('configure-fresh-missing-email', 'anonymous', CONFIGURE, 400,
    invalid({'initialization': {'ownerEmail': ['Required for fresh initialization.']}}),
    body=setup_body(initialization={'type': 'fresh', 'associationName': 'A', 'ownerPassword': 'x'}), headers=TOKEN)
add('configure-fresh-missing-password', 'anonymous', CONFIGURE, 400,
    invalid({'initialization': {'ownerPassword': ['Required for fresh initialization.']}}),
    body=setup_body(initialization={'type': 'fresh', 'associationName': 'A', 'ownerEmail': 'a@b.example'}), headers=TOKEN)
add('configure-fresh-short-trimmed-password', 'anonymous', CONFIGURE, 400,
    invalid({'initialization': {'ownerPassword': ['Password must be at least 8 characters.']}}),
    body=setup_body(initialization={'type': 'fresh', 'associationName': 'A', 'ownerEmail': 'a@b.example',
                                    'ownerPassword': '  1234567  '}), headers=TOKEN)
add('configure-fresh-field-errors', 'anonymous', CONFIGURE, 400,
    invalid({'initialization': {'associationName': ['Ensure this field has no more than 150 characters.'],
                                'ownerEmail': [EMAIL_INVALID], 'ownerPassword': ['Ensure this field has no more than 128 characters.']}}),
    body=setup_body(initialization={'type': 'fresh', 'associationName': 'N' * 151, 'ownerEmail': 'owner@', 'ownerPassword': 'p' * 129}),
    headers=TOKEN)
add('configure-import-missing-task', 'anonymous', CONFIGURE, 400,
    invalid({'initialization': {'importTaskId': ['Required for import initialization.']}}),
    body=setup_body(initialization={'type': 'import'}), headers=TOKEN)
add('configure-import-blank-task', 'anonymous', CONFIGURE, 400, invalid({'initialization': {'importTaskId': [BLANK]}}),
    body=setup_body(initialization={'type': 'import', 'importTaskId': ' '}), headers=TOKEN)
# The configuration is created inside transaction.atomic before the import
# task is read; a task without a ready result raises and rolls it back.
add('configure-import-pending-task', 'anonymous', CONFIGURE, 500,
    {'success': False, 'error': 'Import task is still in progress. Please wait for completion.'},
    body=setup_body(initialization={'type': 'import', 'importTaskId': 'b3000000-0000-4000-8000-000000000001'}), headers=TOKEN)

# --- the fresh setup -------------------------------------------------------
OWNER_PASSWORD = 'setup-password-1'
SETUP = {
    'domain': '  Setup.Example.TEST ',
    'oem': {'name': ' Istanza Setup ', 'abbreviation': ' IS ', 'primaryColor': '#0a1B2c', 'supportEmail': ' help@setup.example.test ',
            'logo': None, 'ignored': True},
    'oauth': {'googleClientId': ' google-setup-id ', 'appleClientId': None},
    'stripe': {'publicKey': 'pk_test_placeholder', 'clientPortal': ''},
    'initialization': {'type': 'fresh', 'associationName': ' Associazione Setup ', 'ownerEmail': ' Owner.Setup@Setup.EXAMPLE.test ',
                       'ownerPassword': '  ' + OWNER_PASSWORD + '  ', 'importTaskId': 'ignored'},
}
config = {'domain': 'setup.example.test', 'name': 'Istanza Setup', 'abbreviation': 'IS', 'primary_color': '#0a1B2c',
          'support_email': 'help@setup.example.test', 'logo_path': '', 'setup_provenance': 'fresh', 'onboarding_completed': False,
          'display_settings': DISPLAY, 'google_client_id': 'google-setup-id', 'apple_client_id': '',
          'stripe_public_key': '', 'stripe_secret_key': '', 'stripe_webhook_secret': '', 'stripe_pricing_table': '',
          'stripe_client_portal': '', 'self_hosted': True, 'support_multiple_associations': False, 'is_reseller': False,
          'show_course_athletes': True, 'show_minor_exemption': True, 'show_tutorial': True, 'meta_title': '', 'meta_description': '',
          'email_settings': {}, 'email_password_encrypted': '', 'email_revision': 0, 'diagnostic_results': {},
          'integration_settings': {}}
add('configure-fresh', 'anonymous', CONFIGURE, 200,
    {'success': True, 'message': 'Istanza configurata con successo', 'association_id': '$new', 'redirect': '/login'},
    body=SETUP, headers=TOKEN, effect={'kind': 'configure', 'config': copy.deepcopy(config),
                                       'owner': {'email': 'Owner.Setup@setup.example.test', 'password': OWNER_PASSWORD},
                                       'association': {'denomination': 'Associazione Setup'}})

# ===========================================================================
# Phase B: configured; the primary association's owner is 'setup_owner'.
# ===========================================================================
ALREADY = {'success': False, 'error': 'Instance already configured. Use /instance/reconfigure to update.'}
add('configured-configure-token', 'anonymous', CONFIGURE, 401, ANON, body=setup_body(), headers=TOKEN)
add('configured-configure-owner', 'owner', CONFIGURE, 403, SETUP_DENIED, body=setup_body(), headers=TOKEN)
add('configured-configure-setup-owner', 'setup_owner', CONFIGURE, 400, ALREADY, body=setup_body())
add('configured-configure-admin-form', 'admin', CONFIGURE, 400, ALREADY, raw_body='domain=x', content_type='application/x-www-form-urlencoded')
add('configured-configure-setup-owner-options', 'setup_owner', CONFIGURE, 200, SETUP_OPTIONS, method='OPTIONS')
add('configured-validate-token', 'anonymous', VALIDATE, 401, ANON, headers=TOKEN)
add('configured-validate-owner-token', 'owner', VALIDATE, 403, SETUP_DENIED, headers=TOKEN)
add('configured-validate-collaborator', 'full_collaborator', VALIDATE, 403, SETUP_DENIED)
add('configured-validate-setup-owner', 'setup_owner', VALIDATE, 200, {'valid': True})
add('configured-validate-admin', 'admin', VALIDATE, 200, {'valid': True})

# --- reconfigure -----------------------------------------------------------
NOT_OWNER = {'success': False, 'error': 'Only the instance owner can reconfigure'}
add('reconfigure-owner', 'owner', RECONFIGURE, 403, NOT_OWNER, method='PUT', body={'meta_title': 'x'})
add('reconfigure-restricted-collaborator', 'restricted_collaborator', RECONFIGURE, 403, NOT_OWNER, method='PUT', body={})
add('reconfigure-athlete', 'athlete', RECONFIGURE, 403, NOT_OWNER, method='PUT', body={})
add('reconfigure-setup-owner-form', 'setup_owner', RECONFIGURE, 415,
    {'detail': 'Unsupported media type "application/x-www-form-urlencoded" in request.'}, method='PUT',
    raw_body='meta_title=x', content_type='application/x-www-form-urlencoded')
add('reconfigure-list', 'setup_owner', RECONFIGURE, 400,
    invalid({'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']}), method='PUT', body=['oem'])
add('reconfigure-field-rules', 'setup_owner', RECONFIGURE, 400, invalid({
    'oem': {'name': [BLANK], 'primaryColor': [PATTERN]},
    'oauth': {'googleClientId': ['Ensure this field has no more than 255 characters.']},
    'stripe': {'clientPortal': [URL_INVALID]},
    'display_settings': [NULL], 'meta_title': ['Ensure this field has no more than 255 characters.'],
    'meta_description': ['Not a valid string.'], 'show_tutorial': [BOOLEAN], 'show_minor_exemption': [NULL]}),
    method='PUT', body={'oem': {'name': '', 'primaryColor': 'red'}, 'oauth': {'googleClientId': 'g' * 256},
                        'stripe': {'clientPortal': 'ftp//nowhere'}, 'display_settings': None, 'meta_title': 'm' * 256,
                        'meta_description': ['x'], 'show_tutorial': 'maybe', 'show_minor_exemption': None})
add('reconfigure-oem-not-dict', 'setup_owner', RECONFIGURE, 400,
    invalid({'oem': {'non_field_errors': ['Invalid data. Expected a dictionary, but got int.']}, 'oauth': [NULL]}),
    method='PUT', body={'oem': 5, 'oauth': None})
# An empty body still saves updated_at (update_fields always holds it).
add('reconfigure-empty', 'setup_owner', RECONFIGURE, 200, {'success': True, 'message': 'Configurazione aggiornata'},
    method='PUT', body={}, effect=config_effect())
# Partial nested data: only the given OEM keys change; any oauth data sets
# both client ids (a missing one becomes ''); stripe data changes nothing.
# A falsy display_settings is replaced with the defaults by
# InstanceConfiguration.save.
config.update(primary_color='#ABCDEF', meta_title='Titolo', meta_description='Descrizione', show_tutorial=False,
              show_course_athletes=False, google_client_id='', apple_client_id='apple-setup-id', display_settings=DISPLAY)
add('reconfigure-setup-owner', 'setup_owner', RECONFIGURE, 200, {'success': True, 'message': 'Configurazione aggiornata'},
    method='PUT', body={'oem': {'primaryColor': '#ABCDEF'}, 'oauth': {'appleClientId': ' apple-setup-id '},
                        'stripe': {'secretKey': 'sk_test_placeholder'}, 'display_settings': {}, 'meta_title': ' Titolo ',
                        'meta_description': ' Descrizione ', 'show_tutorial': 'false', 'show_course_athletes': 0,
                        'domain': 'ignored.example.test', 'self_hosted': False}, effect=config_effect())
CUSTOM_DISPLAY = {'login': {'allowOauthLogin': False}, 'extra': [1, 'two']}
config.update(name='Istanza Admin', abbreviation='', support_email='', display_settings=CUSTOM_DISPLAY, show_minor_exemption=False,
              google_client_id='', apple_client_id='')
add('reconfigure-admin', 'admin', RECONFIGURE, 200, {'success': True, 'message': 'Configurazione aggiornata'}, method='PUT',
    body={'oem': {'name': 'Istanza Admin', 'abbreviation': '', 'supportEmail': ''}, 'oauth': {'googleClientId': None},
          'display_settings': CUSTOM_DISPLAY, 'show_minor_exemption': 'off'}, effect=config_effect())
add('reconfigure-anonymous-options', 'anonymous', RECONFIGURE, 401, ANON, method='OPTIONS')
add('reconfigure-collaborator-options', 'full_collaborator', RECONFIGURE, 200, RECONFIGURE_OPTIONS, method='OPTIONS')

# --- logo uploads on the configured instance -------------------------------
add('configured-logo-token', 'anonymous', LOGO, 401, ANON, headers=TOKEN, upload=upload(file_part('image/png', 'p', 64)))
add('configured-logo-owner', 'owner', LOGO, 403, SETUP_DENIED, upload=upload(file_part('image/png', 'p', 64)))
add('configured-logo-setup-owner-invalid', 'setup_owner', LOGO, 400,
    {'success': False, 'error': 'Invalid file type. Use PNG, JPG, or WebP.'}, upload=upload(file_part('image/gif', 'g', 64)))
store('image/png', 'c', 1500, 'png')
config.update(logo_path=CANONICAL)
add('configured-logo-setup-owner', 'setup_owner', LOGO, 200, {'success': True, 'logo_url': LOGO_VERSION},
    upload=upload(file_part('image/png', 'c', 1500)), effect=config_effect())
add('owner-logo-anonymous', 'anonymous', ADMIN_LOGO, 401, ANON, headers=TOKEN, upload=upload(file_part('image/png', 'p', 64)))
add('owner-logo-owner', 'owner', ADMIN_LOGO, 403, OWNER_DENIED, upload=upload(file_part('image/png', 'p', 64)))
add('owner-logo-collaborator', 'full_collaborator', ADMIN_LOGO, 403, OWNER_DENIED, upload=upload(file_part('image/png', 'p', 64)))
add('owner-logo-options', 'setup_owner', ADMIN_LOGO, 200, OWNER_LOGO_OPTIONS, method='OPTIONS')
add('owner-logo-get', 'setup_owner', ADMIN_LOGO, 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('owner-logo-json', 'setup_owner', ADMIN_LOGO, 415, {'detail': 'Unsupported media type "application/json" in request.'}, body={})
add('owner-logo-too-large', 'admin', ADMIN_LOGO, 400, {'success': False, 'error': 'File too large. Maximum size is 5MB.'},
    upload=upload(file_part('image/jpeg', 'L', MB5 + 1)))
store('image/jpeg', 'd', 3000, 'jpg')
add('owner-logo-setup-owner', 'setup_owner', ADMIN_LOGO, 200, {'success': True, 'logo_url': LOGO_VERSION},
    upload=upload(file_part('image/jpeg', 'd', 3000)), effect=config_effect())
store('image/webp', 'e', 700, 'webp')
add('owner-logo-admin', 'admin', ADMIN_LOGO, 200, {'success': True, 'logo_url': LOGO_VERSION},
    upload=upload(file_part('image/webp', 'e', 700)), effect=config_effect())

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'instance setup, reconfigure, setup-token validation and logo uploads',
                              'ids': IDS, 'location': LOCATION, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
