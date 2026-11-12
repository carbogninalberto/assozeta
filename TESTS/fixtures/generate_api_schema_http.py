"""Author the schema/, schema/swagger-ui/ and schema/redoc/ HTTP sequence (api-schema.md).

drf-spectacular with SPECTACULAR_SETTINGS (SERVE_PUBLIC, AllowAny, the
project's ScopedJWTAuthentication, sidecar assets). DRF order: content
negotiation (?format, Accept; failures render with the first renderer), then
authentication and identity resolution, then method dispatch (GET/HEAD/OPTIONS,
405 otherwise), then the view. SpectacularAPIView renders YAML (vnd.oai.openapi,
application/yaml; ErrorDetail as {string, code}) or JSON (indent 4 unless the
accepted media type carries indent=1..8); the document's language is `lang`
when given, else LocaleMiddleware's; `version` becomes '1.0.0 (<version>)' and
part of the Content-Disposition filename. The HTML views render templates:
swagger-ui embeds a masked CSRF token (and sets the csrftoken cookie) and its
OPTIONS fails (500); redoc's OPTIONS renders the template without context;
their exceptions render as '<status> <reason>'. Without a trailing slash,
CommonMiddleware redirects (301).

Responses are either literal (authored here) or references into the documents
Django generated for BE_GO/internal/apischema (see the contract). Run with
``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('api_schema_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
SERVER = '<stable-server-error-page>'
YAML, YAML2 = 'application/vnd.oai.openapi', 'application/yaml'
JSON, JSON2 = 'application/vnd.oai.openapi+json', 'application/json'
HTML = 'text/html; charset=utf-8'
BROWSER = 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
ALLOW = 'GET, HEAD, OPTIONS'
cases = []


def add(name, path, status, response, *, actor='anonymous', method='GET', headers=None, content_type=None,
        allow=ALLOW, vary=('Cookie', 'origin', 'Accept-Language'), language='it', disposition=None, location=None,
        csrf=None):
    """vary: the Vary names before GZipMiddleware (the contract adds Accept-Encoding for bodies of 200+ bytes)."""
    cases.append({'name': f'api-schema-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
                  'status': status, 'response': response, 'content_type': content_type, 'allow': allow,
                  'vary': list(vary), 'content_language': language, 'disposition': disposition,
                  'location': location, 'csrf': csrf})


SCHEMA_VARY = ('Accept', 'Cookie', 'origin', 'Accept-Language')


def schema(name, status, response, *, content_type, query='', **options):
    add(name, '/schema/' + query, status, response, content_type=content_type, vary=SCHEMA_VARY, **options)


def document(form, lang, version='', indent=4, version_text=None):
    """version_text: the text Django emits for the info.version line(s) when it is not a plain substitution."""
    value = {'kind': 'document', 'format': form, 'lang': lang, 'version': version, 'indent': indent}
    if version_text is not None:
        value['version_text'] = version_text
    return value


def quoted_disposition(version, form):
    """Content-Disposition for a version that is not plain ASCII (urllib reads header bytes as latin-1)."""
    return f'inline; filename="Bakney Sport API ({version}).{form}"'


def yaml_disposition(version=''):
    return f'inline; filename="Bakney Sport API{f" ({version})" if version else ""}.yaml"'


def json_disposition(version=''):
    return f'inline; filename="Bakney Sport API{f" ({version})" if version else ""}.json"'


def yaml_detail(text, code):
    return f'detail:\n  string: {text}\n  code: {code}\n'


def json_detail(text, indent=4):
    return '{\n' + ' ' * indent + json.dumps('detail') + ': ' + json.dumps(text, ensure_ascii=False) + '\n}'


# --- schema/: formats and negotiation -------------------------------------------------------
# No Accept: */* -> the first renderer, YAML in the default language.
schema('yaml-default', 200, document('yaml', 'it'), content_type=YAML + '; charset=utf-8', disposition=yaml_disposition())
schema('yaml-application-yaml', 200, document('yaml', 'it'), content_type=YAML2 + '; charset=utf-8',
       headers={'Accept': YAML2}, disposition=yaml_disposition())
# A wildcard accept range is accepted with the client's parameters; Content-Type is still the
# renderer's media type (Response.rendered_content), the accepted one only reaches render().
schema('yaml-browser-accept', 200, document('yaml', 'it'), content_type=YAML + '; charset=utf-8',
       headers={'Accept': BROWSER}, disposition=yaml_disposition())
schema('json-accept-english', 200, document('json', 'en'), content_type=JSON2, headers={'Accept': JSON2, 'Accept-Language': 'en'},
       language='en', disposition=json_disposition())
schema('json-format-override', 200, document('json', 'it'), content_type=JSON, query='?format=json', disposition=json_disposition())
schema('yaml-format-with-wildcard', 200, document('yaml', 'it'), content_type=YAML + '; charset=utf-8', query='?format=yaml',
       headers={'Accept': JSON2 + ', */*;q=0.5'}, disposition=yaml_disposition())
schema('json-indent-two', 200, document('json', 'en', indent=2), content_type=JSON2,
       headers={'Accept': JSON2 + '; indent=2', 'Accept-Language': 'en'}, language='en', disposition=json_disposition())
schema('json-indent-zero-default', 200, document('json', 'en'), content_type=JSON,
       headers={'Accept': JSON + ';indent=0', 'Accept-Language': 'en'}, language='en', disposition=json_disposition())
schema('json-indent-clamped', 200, document('json', 'it', indent=8), content_type=JSON2,
       headers={'Accept': JSON2 + ';indent=12'}, disposition=json_disposition())
schema('json-wildcard-indent', 200, document('json', 'it', indent=1), content_type=JSON,
       headers={'Accept': 'application/*;indent=1'}, query='?format=json',
       disposition=json_disposition())

# --- schema/: languages and versions -------------------------------------------------------
schema('lang-german-json', 200, document('json', 'de'), content_type=JSON2, query='?lang=de',
       headers={'Accept': JSON2, 'Accept-Language': 'en'}, language='en', disposition=json_disposition())
schema('lang-chinese-traditional-yaml', 200, document('yaml', 'zh-hant'), content_type=YAML + '; charset=utf-8', query='?lang=zh-hant',
       disposition=yaml_disposition())
schema('lang-brazilian-json', 200, document('json', 'pt-br'), content_type=JSON2, query='?lang=pt-br', headers={'Accept': JSON2},
       disposition=json_disposition())
schema('lang-swiss-german', 200, document('json', 'de-ch'), content_type=JSON2, query='?lang=de-ch', headers={'Accept': JSON2},
       disposition=json_disposition())
schema('lang-serbian-latin', 200, document('yaml', 'sr-latn'), content_type=YAML + '; charset=utf-8', query='?lang=sr-latn',
       disposition=yaml_disposition())
# Unknown and upper-case codes: no catalogs, or the same catalogs with the default chained as fallback.
schema('lang-unknown', 200, document('json', 'xx'), content_type=JSON2, query='?lang=xx', headers={'Accept': JSON2},
       disposition=json_disposition())
schema('lang-upper-english', 200, document('json', 'EN'), content_type=JSON2, query='?lang=EN', headers={'Accept': JSON2},
       disposition=json_disposition())
# The last value wins; an empty lang keeps LocaleMiddleware's language.
schema('lang-last-value', 200, document('json', 'fr'), content_type=JSON2, query='?lang=de&lang=fr', headers={'Accept': JSON2},
       disposition=json_disposition())
schema('lang-empty-uses-header', 200, document('json', 'es'), content_type=JSON2, query='?lang=',
       headers={'Accept': JSON2, 'Accept-Language': 'es'}, language='es', disposition=json_disposition())
schema('accept-language-french-yaml', 200, document('yaml', 'fr'), content_type=YAML + '; charset=utf-8',
       headers={'Accept-Language': 'fr-CH, en;q=0.5'}, language='fr', disposition=yaml_disposition())
schema('language-cookie', 200, document('json', 'nl'), content_type=JSON2,
       headers={'Accept': JSON2, 'Accept-Language': 'en', 'Cookie': 'django_language=nl'}, language='nl', disposition=json_disposition())
schema('version-yaml', 200, document('yaml', 'it', version='v2'), content_type=YAML + '; charset=utf-8', query='?version=v2',
       disposition=yaml_disposition('v2'))
schema('version-json-with-lang', 200, document('json', 'de', version='1.2-rc_3'), content_type=JSON2,
       query='?version=1.2-rc_3&lang=de', headers={'Accept': JSON2}, disposition=json_disposition('1.2-rc_3'))
schema('version-empty', 200, document('json', 'it'), content_type=JSON2, query='?version=', headers={'Accept': JSON2},
       disposition=json_disposition())

# The version is emitted by the format's own renderer: PyYAML picks the scalar style and folds long lines.
schema('version-yaml-quoted', 200, document('yaml', 'it', version='x: y', version_text="  version: '1.0.0 (x: y)'\n"),
       content_type=YAML + '; charset=utf-8', query='?version=x%3A+y', disposition=yaml_disposition('x: y'))
schema('version-yaml-tab', 200, document('yaml', 'it', version='a\tb', version_text='  version: "1.0.0 (a\\tb)"\n'),
       content_type=YAML + '; charset=utf-8', query='?version=a%09b', disposition=yaml_disposition('a\tb'))
schema('version-yaml-single-quote-and-hash', 200, document('yaml', 'it', version="it's #1", version_text="  version: '1.0.0 (it''s #1)'\n"),
       content_type=YAML + '; charset=utf-8', query="?version=it%27s+%231", disposition=yaml_disposition("it's #1"))
WORDS = ('word ' * 30).strip()
schema('version-yaml-folded', 200, document('yaml', 'it', version=WORDS, version_text=(
    '  version: 1.0.0 (word word word word word word word word word word word word word\n'
    '    word word word word word word word word word word word word word word word word\n    word)\n')),
       content_type=YAML + '; charset=utf-8', query='?version=' + WORDS.replace(' ', '+'), disposition=yaml_disposition(WORDS))
schema('version-json-quote-and-backslash', 200, document('json', 'it', version='a"b\\c', version_text='        "version": "1.0.0 (a\\"b\\\\c)",\n'),
       content_type=JSON2, query='?version=a%22b%5Cc', headers={'Accept': JSON2}, disposition=json_disposition('a"b\\c'))
schema('version-json-non-ascii', 200, document('json', 'it', version='caf\u00e9', version_text='        "version": "1.0.0 (caf\u00e9)",\n'),
       content_type=JSON2, query='?version=caf%C3%A9', headers={'Accept': JSON2}, disposition=json_disposition('caf\u00e9'))
schema('version-yaml-non-latin', 200, document('yaml', 'it', version='\u65e5\u672c', version_text='  version: 1.0.0 (\u65e5\u672c)\n'),
       content_type=YAML + '; charset=utf-8', query='?version=%E6%97%A5%E6%9C%AC',
       disposition='=?utf-8?b?aW5saW5lOyBmaWxlbmFtZT0iQmFrbmV5IFNwb3J0IEFQSSAo5pel5pysKS55YW1sIg==?=')
add('version-line-break-server-error', '/schema/?version=a%0Ab', 500, SERVER, content_type='text/html; charset=utf-8',
    vary=('Cookie', 'origin', 'Accept-Language'), allow=None)
schema('lang-space-unknown', 200, document('json', 'de ch'), content_type=JSON2, query='?lang=de+ch', headers={'Accept': JSON2},
       disposition=json_disposition())
schema('lang-non-ascii', 200, document('yaml', 'd\u00e9'), content_type=YAML + '; charset=utf-8', query='?lang=d%C3%A9',
       disposition=yaml_disposition())
schema('lang-folds-to-ascii', 200, document('json', 'IT-\u0131'), content_type=JSON2, query='?lang=IT-%C4%B1',
       headers={'Accept': JSON2}, disposition=json_disposition())
schema('lang-sharp-s-territory', 200, document('json', 'de-\u00df'), content_type=JSON2, query='?lang=de-%C3%9F',
       headers={'Accept': JSON2}, disposition=json_disposition())

# Accept parameters go through email.message: quoted strings, comments, RFC 2231 and non-ASCII text.
schema('accept-indent-quoted', 200, document('json', 'it', indent=3), content_type=JSON2, headers={'Accept': JSON2 + '; indent="3"'},
       disposition=json_disposition())
schema('accept-indent-quoted-with-semicolon', 200, document('json', 'it', indent=5), content_type=JSON2,
       headers={'Accept': JSON2 + '; title="a;b"; indent=5'}, disposition=json_disposition())
schema('accept-indent-rfc2231', 200, document('json', 'it', indent=2), content_type=JSON2,
       headers={'Accept': JSON2 + "; indent*=utf-8''2"}, disposition=json_disposition())
schema('accept-indent-rfc2231-continued', 200, document('json', 'it', indent=6), content_type=JSON2,
       headers={'Accept': JSON2 + "; indent*0*=utf-8'en'%36; x=1"}, disposition=json_disposition())
add('accept-unknown-rfc2231-charset-server-error', '/schema/', 500, SERVER, content_type='text/html; charset=utf-8',
    headers={'Accept': JSON2 + "; indent*=bogus''3"}, vary=('Cookie', 'origin', 'Accept-Language'), allow=None)
schema('accept-comment-parameter-ignored', 200, document('json', 'it'), content_type=JSON2,
       headers={'Accept': JSON2 + '; x=(a)'}, disposition=json_disposition())
schema('accept-non-ascii-subtype-not-acceptable', 406, yaml_detail('Impossibile soddisfare l\'header "Accept" presente nella richiesta.',
                                                                'not_acceptable'),
       content_type=YAML + '; charset=utf-8', headers={'Accept': 'application/json\u00f6'})

# --- schema/: methods, HEAD, OPTIONS, encodings ---------------------------------------------
schema('head-yaml', 200, document('yaml', 'it'), content_type=YAML + '; charset=utf-8', method='HEAD', disposition=yaml_disposition())
schema('gzip-json', 200, document('json', 'en'), content_type=JSON2, headers={'Accept': JSON2, 'Accept-Language': 'en', 'Accept-Encoding': 'gzip'},
       language='en', disposition=json_disposition())
OPTIONS_IT_YAML = ('name: Spectacular Api\ndescription: |-\n  OpenApi3 schema for this API. Format can be selected via content negotiation.\n\n'
                   '  - YAML: application/vnd.oai.openapi\n  - JSON: application/vnd.oai.openapi+json\nrenders:\n'
                   '- application/vnd.oai.openapi\n- application/yaml\n- application/vnd.oai.openapi+json\n- application/json\nparses:\n'
                   '- application/json\n- application/x-www-form-urlencoded\n- multipart/form-data\n')
schema('options-yaml', 200, OPTIONS_IT_YAML, content_type=YAML + '; charset=utf-8', method='OPTIONS')
schema('options-json-german', 200, {'kind': 'options', 'format': 'json', 'language': 'de', 'indent': 4}, content_type=JSON2,
       method='OPTIONS', headers={'Accept': JSON2, 'Accept-Language': 'de'}, language='de')
schema('options-json-indent', 200, {'kind': 'options', 'format': 'json', 'language': 'en', 'indent': 2}, content_type=JSON2,
       method='OPTIONS', headers={'Accept': JSON2 + ';indent=2', 'Accept-Language': 'en'}, language='en')
schema('post-not-allowed-yaml', 405, yaml_detail('Metodo "POST" non consentito', 'method_not_allowed'),
       content_type=YAML + '; charset=utf-8', method='POST')
schema('delete-not-allowed-json', 405, json_detail('Method "DELETE" not allowed.'), content_type=JSON2, actor='owner', method='DELETE',
       headers={'Accept': JSON2, 'Accept-Language': 'en'}, language='en')
schema('format-unknown-not-found', 404, yaml_detail('Non trovato.', 'not_found'), content_type=YAML + '; charset=utf-8',
       query='?format=xml', headers={'Accept': JSON2})
schema('format-unknown-english', 404, yaml_detail('Not found.', 'not_found'), content_type=YAML + '; charset=utf-8',
       query='?format=html', headers={'Accept-Language': 'en'}, language='en')
schema('accept-html-not-acceptable', 406, yaml_detail('Impossibile soddisfare l\'header "Accept" presente nella richiesta.', 'not_acceptable'),
       content_type=YAML + '; charset=utf-8', headers={'Accept': 'text/html'})
schema('accept-image-not-acceptable', 406, yaml_detail('Could not satisfy the request Accept header.', 'not_acceptable'),
       content_type=YAML + '; charset=utf-8', headers={'Accept': 'image/png', 'Accept-Language': 'en'}, language='en')
# Negotiation precedes authentication.
schema('format-before-token', 404, yaml_detail('Not found.', 'not_found'), content_type=YAML + '; charset=utf-8', query='?format=xml',
       headers={'Authorization': 'Bearer not-a-token', 'Accept-Language': 'en'}, language='en')

# --- schema/: authentication and identity ---------------------------------------------------
schema('owner', 200, document('json', 'en'), content_type=JSON2, actor='owner', headers={'Accept': JSON2, 'Accept-Language': 'en'},
       language='en', disposition=json_disposition())
schema('full-collaborator', 200, document('yaml', 'it'), content_type=YAML + '; charset=utf-8', actor='full_collaborator',
       disposition=yaml_disposition())
schema('admin-impersonating-owner', 200, document('json', 'it'), content_type=JSON2, actor='admin',
       headers={'Accept': JSON2, 'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']}, disposition=json_disposition())
schema('athlete', 200, document('json', 'it'), content_type=JSON2, actor='athlete', headers={'Accept': JSON2}, disposition=json_disposition())
schema('anonymous-ignores-impersonation-headers', 200, document('json', 'it'), content_type=JSON2,
       headers={'Accept': JSON2, 'User-Id': IDS['owner'], 'X-Impersonation-Id': 'not-a-session'}, disposition=json_disposition())
ADMIN_TEXT = 'Seleziona un utente da impersonificare per accedere a questa sezione.'
schema('admin-without-session-yaml', 403, yaml_detail(ADMIN_TEXT, 'permission_denied'), content_type=YAML + '; charset=utf-8', actor='admin')
schema('admin-without-session-json', 403, json_detail(ADMIN_TEXT), content_type=JSON2, actor='admin', headers={'Accept': JSON2})
schema('restricted-collaborator', 403,
       "detail:\n  string: 'Access denied: No permission mapping for endpoint ''schema''. Contact administrator\n"
       "    to grant access.'\n  code: permission_denied\n", content_type=YAML + '; charset=utf-8', actor='restricted_collaborator')
schema('restricted-collaborator-post', 403,
       json_detail("Access denied: No permission mapping for endpoint 'schema'. Contact administrator to grant access."),
       content_type=JSON2, actor='restricted_collaborator', method='POST', headers={'Accept': JSON2})
schema('owner-scoped-athlete', 403, json_detail('Operazione non disponibile nel contesto dell’associazione.'), content_type=JSON2,
       actor='owner', headers={'Accept': JSON2, 'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
TOKEN_YAML = ('detail:\n  string: Given token not valid for any token type\n  code: token_not_valid\ncode:\n  string: token_not_valid\n'
              '  code: token_not_valid\nmessages:\n- token_class:\n    string: AccessToken\n    code: token_not_valid\n  token_type:\n'
              '    string: access\n    code: token_not_valid\n  message:\n    string: Token is invalid\n    code: token_not_valid\n')
TOKEN_JSON = ('{\n    "detail": "Given token not valid for any token type",\n    "code": "token_not_valid",\n    "messages": [\n'
              '        {\n            "token_class": "AccessToken",\n            "token_type": "access",\n'
              '            "message": "Token is invalid"\n        }\n    ]\n}')
schema('invalid-token-yaml', 401, TOKEN_YAML, content_type=YAML + '; charset=utf-8', headers={'Authorization': 'Bearer not-a-token'})
schema('invalid-token-json', 401, TOKEN_JSON, content_type=JSON2, headers={'Authorization': 'Bearer not-a-token', 'Accept': JSON2})
schema('bad-authorization-header', 401,
       '{\n    "detail": "Authorization header must contain two space-delimited values",\n    "code": "bad_authorization_header"\n}',
       content_type=JSON2, headers={'Authorization': 'Bearer', 'Accept': JSON2})
schema('other-scheme-anonymous', 200, document('json', 'it'), content_type=JSON2, headers={'Authorization': 'Basic abc', 'Accept': JSON2},
       disposition=json_disposition())

# --- schema/swagger-ui/ ---------------------------------------------------------------------
PAGE_VARY = ('Cookie', 'origin', 'Accept-Language')


def page(name, path, status, response, *, content_type=HTML, **options):
    add(name, path, status, response, content_type=content_type, vary=PAGE_VARY, **options)


SWAGGER, REDOC = '/schema/swagger-ui/', '/schema/redoc/'
page('swagger', SWAGGER, 200, {'kind': 'swagger', 'url': '/schema/'}, headers={'Accept': BROWSER}, csrf='new')
page('swagger-wildcard', SWAGGER, 200, {'kind': 'swagger', 'url': '/schema/'}, content_type=HTML,
     headers={'Accept': '*/*;q=0.8'}, csrf='new')
page('swagger-query', SWAGGER + '?lang=de&version=a-b&format=html', 200,
     {'kind': 'swagger', 'url': '/schema/?format=html&lang=de&version=a-b'}, headers={'Accept': 'text/html'}, csrf='new')
page('swagger-empty-query-values', SWAGGER + '?version=&lang=de%40euro', 200,
     {'kind': 'swagger', 'url': '/schema/?lang=de%40euro&version='}, csrf='new')
# DRF's reverse(request=...) keeps a non-empty ?format; set_query_parameters then drops blank values
# from the reversed URL but adds lang/version even when empty.
page('swagger-empty-format', SWAGGER + '?format=&lang=', 200, {'kind': 'swagger', 'url': '/schema/?lang='}, csrf='new')
# CsrfViewMiddleware keeps a well-formed secret, unmasks a legacy 64-character token and replaces anything else.
SECRET = 'aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bC1eF'
page('swagger-existing-csrf', SWAGGER, 200, {'kind': 'swagger', 'url': '/schema/'}, actor='owner',
     headers={'Accept': 'text/html', 'Cookie': f'csrftoken={SECRET}'}, csrf=SECRET)
MASK = 'Zyxwvutsrqponmlkjihgfedcba987654'
CHARS = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
LEGACY = MASK + ''.join(CHARS[(CHARS.index(s) + CHARS.index(m)) % 62] for s, m in zip(SECRET, MASK))
page('swagger-legacy-csrf', SWAGGER, 200, {'kind': 'swagger', 'url': '/schema/'},
     headers={'Accept': 'text/html', 'Cookie': f'csrftoken={LEGACY}'}, csrf=SECRET)
page('swagger-malformed-csrf', SWAGGER, 200, {'kind': 'swagger', 'url': '/schema/'},
     headers={'Accept': 'text/html', 'Cookie': 'csrftoken=short!'}, csrf='new')
page('swagger-query-outside-safe-charset', SWAGGER + '?lang=d%C3%A9&version=a%26b%20c', 200,
     {'kind': 'swagger', 'url': '/schema/?lang=d%C3%A9&version=a%26b+c'}, headers={'Accept': 'text/html'}, csrf='new')
page('swagger-head', SWAGGER, 200, {'kind': 'swagger', 'url': '/schema/'}, method='HEAD', headers={'Accept': 'text/html'}, csrf='new')
page('swagger-options-server-error', SWAGGER, 500, SERVER, method='OPTIONS', headers={'Accept': 'text/html'}, allow=None)
page('swagger-json-not-acceptable', SWAGGER, 406, '406 Not Acceptable', headers={'Accept': JSON2})
page('swagger-format-not-found', SWAGGER + '?format=json', 404, '404 Not Found', headers={'Accept': 'text/html'})
page('swagger-post-not-allowed', SWAGGER, 405, '405 Method Not Allowed', method='POST', headers={'Accept': 'text/html'})
page('swagger-invalid-token', SWAGGER, 401, '401 Unauthorized', headers={'Accept': 'text/html', 'Authorization': 'Bearer not-a-token'})
page('swagger-admin-without-session', SWAGGER, 403, '403 Forbidden', actor='admin', headers={'Accept': 'text/html'})
page('swagger-restricted-collaborator', SWAGGER, 403, '403 Forbidden', actor='restricted_collaborator', headers={'Accept': 'text/html'})

# --- schema/redoc/ --------------------------------------------------------------------------
page('redoc', REDOC, 200, {'kind': 'redoc', 'url': '/schema/'}, headers={'Accept': BROWSER})
page('redoc-query', REDOC + '?lang=pt-br&version=2', 200, {'kind': 'redoc', 'url': '/schema/?lang=pt-br&version=2'},
     actor='owner', headers={'Accept': 'text/html', 'Accept-Language': 'en'}, language='en')
page('redoc-query-outside-safe-charset', REDOC + '?lang=%E6%97%A5&version=a%3Cb%3E%22', 200,
     {'kind': 'redoc', 'url': '/schema/?lang=%E6%97%A5&version=a%3Cb%3E%22'}, headers={'Accept': 'text/html'})
page('redoc-head', REDOC, 200, {'kind': 'redoc', 'url': '/schema/'}, method='HEAD')
page('redoc-options', REDOC, 200, {'kind': 'redoc_options'}, method='OPTIONS', headers={'Accept': 'text/html'})
page('redoc-json-not-acceptable', REDOC, 406, '406 Not Acceptable', headers={'Accept': JSON2})
page('redoc-put-not-allowed', REDOC, 405, '405 Method Not Allowed', actor='owner', method='PUT', headers={'Accept': 'text/html'})
page('redoc-owner-scoped-athlete', REDOC, 403, '403 Forbidden', actor='owner',
     headers={'Accept': 'text/html', 'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})

# --- APPEND_SLASH ---------------------------------------------------------------------------
for name, path, location, method in (('schema', '/schema', '/schema/', 'GET'),
                                     ('swagger-query', '/schema/swagger-ui?lang=de&x=a%20b', '/schema/swagger-ui/?lang=de&x=a%20b', 'GET'),
                                     ('redoc-post', '/schema/redoc', '/schema/redoc/', 'POST')):
    add(f'append-slash-{name}', path, 301, '<empty>', method=method, content_type=HTML, allow=None, location=location)

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'drf-spectacular schema, swagger-ui and redoc', 'ids': IDS, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
