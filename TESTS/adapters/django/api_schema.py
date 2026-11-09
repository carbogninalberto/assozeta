"""Generate, or check, the OpenAPI schema data that the Go server embeds.

drf-spectacular builds `schema/` at request time from the URL configuration and
the active translation catalogs, so the document only changes when the Django
code or its installed packages change. Go serves the documents generated here
from BE_GO/internal/apischema:

  python TESTS/adapters/django/api_schema.py --write   # regenerate after a Django change
  python TESTS/adapters/django/api_schema.py --check   # CI drift check: fail if stale

Every document comes from Django's own views through the test client (no
database, no network). The language of a document is decided by the catalog
files gettext selects for the requested language (`lang` or LocaleMiddleware),
so documents are grouped by that selection: the "catalog key". Candidates that
share a key must render identical documents; the check enforces it.
"""
import argparse
import contextlib
import difflib
import gettext
import json
import locale
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / 'BE_GO/internal/apischema'
SCHEMA = '/schema/'
YAML, JSON = 'application/vnd.oai.openapi', 'application/vnd.oai.openapi+json'
CSRF_PLACEHOLDER, URL_PLACEHOLDER = '{{csrf_token}}', '{{schema_url}}'


def setup_django():
    sys.path[:0] = [str(ROOT / 'BE'), str(ROOT)]
    tools_env = {name: value for name, value in os.environ.items() if name in ('PATH', 'HOME', 'TMPDIR')}
    import environ
    os.environ.clear()
    os.environ.update(tools_env)
    environ.Env.read_env(str(ROOT / 'selfhost/.env.example'))
    os.environ.update(DEBUG='False', JWT_SECRET_KEY_B64='', JWT_PUBLIC_KEY_B64='', JWT_SECRET_KEY='schema-only',
                      JWT_PUBLIC_KEY='schema-only', DJANGO_SETTINGS_MODULE='core.settings', DBHOST='127.0.0.1',
                      DBPORT='1', STORE_ON_DISK='True', ALLOWED_HOSTS='testserver')
    import django
    from django.db.backends.base.base import BaseDatabaseWrapper
    stack = contextlib.ExitStack()
    stack.enter_context(patch.object(BaseDatabaseWrapper, 'ensure_connection',
                                     side_effect=RuntimeError('Schema generation prohibits database access')))
    stack.enter_context(patch('environ.Env.read_env'))
    import logging
    import warnings
    with open(os.devnull, 'w') as quiet, contextlib.redirect_stderr(quiet):
        django.setup()
    # drf-spectacular reports undocumented views on stderr while generating; the
    # documents themselves are the output that matters here.
    logging.disable(logging.CRITICAL)
    warnings.simplefilter('ignore')
    return stack


def locale_directories():
    """DjangoTranslation's catalog directories, in its merge order."""
    from django.apps import apps
    from django.conf import settings
    directories = [os.path.join(os.path.dirname(sys.modules[settings.__module__].__file__), 'locale')]
    directories += [os.path.join(config.path, 'locale') for config in reversed(list(apps.get_app_configs()))]
    directories += [str(path) for path in reversed(settings.LOCALE_PATHS)]
    result = []
    for directory in directories:
        names = sorted(name for name in (os.listdir(directory) if os.path.isdir(directory) else ())
                       if os.path.exists(os.path.join(directory, name, 'LC_MESSAGES', 'django.mo')))
        for relative in ('', '.', '..'):
            # gettext joins "", "." and ".." expansions; the Go port relies on them selecting nothing.
            assert not os.path.exists(os.path.join(directory, relative, 'LC_MESSAGES', 'django.mo')), directory
        if names:
            result.append((directory, names))
    return result


def catalog_key(language, directories):
    """The catalogs DjangoTranslation(language) loads, and whether it falls back."""
    from django.conf import settings
    from django.utils.translation.trans_real import to_locale
    selected = []
    for directory, _names in directories:
        found = gettext.find('django', directory, [to_locale(language)], all=True)
        selected.append(','.join(Path(path).parents[1].name for path in found))
    fallback = not (language == settings.LANGUAGE_CODE or language.startswith('en'))
    return ('1' if fallback else '0') + '|' + '|'.join(selected)


def candidate_languages(directories):
    """Language codes covering every reachable catalog key.

    A key depends only on the language, territory and modifier gettext expands
    from the normalized locale, filtered by the catalogs that exist. So every
    (language, territory, modifier) seen in a catalog directory, plus a code
    without a territory, covers every key; the upper-case form flips only the
    fallback flag.
    """
    from django.conf import settings
    names = {name for _directory, found in directories for name in found}
    territories, modifiers = {}, {}
    for name in names:
        base, _, modifier = name.partition('@')
        language, _, territory = base.partition('_')
        territories.setdefault(language, {''}).add(territory)
        modifiers.setdefault(language, {''}).add(modifier)
    result = set(code for code, _ in settings.LANGUAGES) | names
    for language in territories:
        for territory in territories[language]:
            for modifier in modifiers[language]:
                code = language + ('_' + territory if territory else '') + ('@' + modifier if modifier else '')
                result |= {code, code.upper()[:len(language)] + code[len(language):]}
    result |= {'xx', 'XX', 'enx', 'english', 'C', 'posix', settings.LANGUAGE_CODE}
    return sorted(result)


EXTRA_KEY_SAMPLES = ('de-ch', 'DE-CH', 'de_CH.UTF-8', 'de_DE.ISO-8859-1@euro', 'de@euro', 'sr-latn', 'sr_RS@latin',
                     'sr-Latn-RS', 'zh-hans', 'zh-Hant-TW', 'zh_TW', 'pt-br', 'pt', 'no', 'nb-no', 'iw', 'in', 'en-us',
                     'EN', 'english_uk', 'it_IT', 'IT', 'it-ch', 'es-419', 'fr:utf8', 'ca@valencia', 'be@latin',
                     'uz@cyrillic', 'ku', 'c.utf8', 'POSIX', 'universal', 'x', 'a.b.c', '@', '_', '-', '.', 'de_', 'de-',
                     'ja_JP.eucJP', 'ko.euckr', 'el_GR@euro', 'deutsch', 'french', 'nynorsk', 'bokmal')


def lines(text):
    return text.splitlines(keepends=True)


def patch_ops(base, other):
    matcher = difflib.SequenceMatcher(None, base, other, autojunk=False)
    return [[i1, i2, other[j1:j2]] for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != 'equal']


def apply_ops(base, ops):
    result, position = [], 0
    for start, end, replacement in ops:
        result += base[position:start] + replacement
        position = end
    return ''.join(result + base[position:])


def render_corpus():
    """Renderer conformance cases for the Go YAML/JSON error renderers."""
    from django.conf import settings
    from django.utils import translation
    from drf_spectacular.renderers import OpenApiJsonRenderer, OpenApiYamlRenderer
    from rest_framework import exceptions
    from rest_framework.exceptions import ErrorDetail
    texts = ['Seleziona un utente da impersonificare per accedere a questa sezione.',
             'Operazione non disponibile nel contesto dell’associazione.',
             'Esci dall’impersonificazione per accedere all’amministrazione.',
             "Access denied: No permission mapping for endpoint 'schema'. Contact administrator to grant access.",
             "Access denied: No permission mapping for endpoint 'schema/swagger-ui'. Contact administrator to grant access.",
             'Missing permission: association.courses.read', 'Given token not valid for any token type',
             'True', 'yes', 'null', '~', '1', '1.5', '0x1F', '2024-01-01', '', ' lead', 'trail ', '- dash', '? q', '# c',
             'a: b', 'a #b', 'tab\there', 'quote\'s', '"double"', '[x]', '{x}', '&a', '*a', '!t', '%p', '@a', '`b',
             'é ’ « »\xa0nbsp', 'line\nbreak', 'x' * 120, ('word ' * 30).strip(), 'ctrl\x01', '\u2028sep', '\ufeffbom',
             'a,b', 'a: ', ':a', '-', '---', '...', 'x' * 70 + ' ' + 'y' * 30]
    fixed = len(texts)
    for code, _name in settings.LANGUAGES:
        with translation.override(code):
            for exception in (exceptions.NotFound(), exceptions.NotAcceptable(), exceptions.MethodNotAllowed('POST'),
                              exceptions.MethodNotAllowed('DELETE'), exceptions.NotAuthenticated(),
                              exceptions.PermissionDenied()):
                texts.append(str(exception.detail))
    corpus, seen = [], set()
    yaml_renderer, json_renderer = OpenApiYamlRenderer(), OpenApiJsonRenderer()
    for index, text in enumerate(texts):
        if text in seen:
            continue
        seen.add(text)
        detail = lambda code: ErrorDetail(text, code=code)
        token = lambda value: ErrorDetail(value, code='token_not_valid')
        shapes = [{'detail': detail('some_code')}]
        if index < fixed:
            # The shapes of SimpleJWT's DetailDictMixin and of the impersonation 403.
            shapes += [{'detail': detail('user_not_found'), 'code': ErrorDetail('user_not_found', code='user_not_found')},
                       {'detail': token(text), 'code': token('token_not_valid'),
                        'messages': [{'token_class': token('AccessToken'), 'token_type': token('access'), 'message': token(text)}]},
                       {'detail': detail('permission_denied'), 'impersonation_invalid': ErrorDetail('True', code='permission_denied')}]
        for shape, data in enumerate(shapes):
            corpus.append({'text': text, 'shape': shape, 'yaml': yaml_renderer.render(data).decode(),
                           'json': json_renderer.render(data, JSON, {}).decode()})
    return corpus


NEGOTIATION_ACCEPTS = (
    None, '', '*/*', 'application/json', 'application/yaml', YAML, JSON, 'text/html', 'application/*', 'text/*',
    '*/*;q=0.8', 'application/json; indent=2', 'application/json;indent=8', 'application/json; indent=0',
    'application/json; indent=12', 'application/json; indent=-1', 'application/json; indent=x', 'application/json; indent= 3',
    'Application/JSON', 'APPLICATION/YAML;Q=0.5', 'application/json, application/yaml', 'application/yaml, application/json',
    'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8', 'image/png', 'application/xml', '*/json',
    'application/json;q=0', 'application/json; charset=utf-8', JSON + '; indent=1', '*/*; indent=2', 'application/*; indent=6',
    'text/html; q=1', 'application/json;', 'application/json; foo', ' application/json ', 'application/json,', ',',
    'application/json;indent=+2', 'application/json;indent=1_0', 'application/json;indent=4_', 'json', 'application',
    'text/html;level=1', 'TEXT/HTML', 'text/plain, */*', 'application/json;Indent=3;q=0.5')
NEGOTIATION_FORMATS = (None, '', 'json', 'yaml', 'html', 'xml', 'JSON')


def negotiation_corpus():
    """DRF content negotiation outcomes for the schema view and the HTML pages."""
    from django.http import Http404
    from django.test import RequestFactory
    from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
    from rest_framework.exceptions import NotAcceptable
    from rest_framework.negotiation import DefaultContentNegotiation
    from rest_framework.request import Request
    corpus = []
    for view, classes in (('schema', SpectacularAPIView.renderer_classes), ('page', SpectacularSwaggerView.renderer_classes)):
        renderers = [renderer() for renderer in classes]
        for accept in NEGOTIATION_ACCEPTS:
            for format_value in NEGOTIATION_FORMATS:
                query = {} if format_value is None else {'format': format_value}
                headers = {} if accept is None else {'HTTP_ACCEPT': accept}
                request = Request(RequestFactory().get(SCHEMA, query, **headers))
                case = {'view': view, 'accept': accept, 'format': format_value}
                try:
                    renderer, media_type = DefaultContentNegotiation().select_renderer(request, renderers)
                except Http404:
                    case['outcome'] = 404
                except NotAcceptable:
                    case['outcome'] = 406
                else:
                    case.update(outcome=200, renderer=renderers.index(renderer), media_type=media_type)
                    if hasattr(renderer, 'get_indent'):
                        case['indent'] = renderer.get_indent(media_type, {})
                corpus.append(case)
    return corpus


def generate():
    from django.conf import settings
    from django.test import Client
    from django.utils.translation.trans_real import check_for_language
    client = Client(raise_request_exception=True)
    directories = locale_directories()
    # The Go key port relies on this: codesets and modifiers never select a catalog.
    assert not any('.' in name or '@' in name for _directory, names in directories for name in names), \
        'catalog names with a codeset or modifier need them in the Go key port'

    def document(language, media):
        response = client.get(SCHEMA, {'lang': language}, HTTP_ACCEPT=media)
        assert response.status_code == 200 and response['Content-Type'].startswith(media), (language, media)
        return response.content.decode()

    # Group every candidate by catalog key; candidates sharing a key must agree.
    groups, keys = {}, {}
    for language in candidate_languages(directories):
        key = catalog_key(language, directories)
        keys[language] = key
        json_document = document(language, JSON)
        if key not in groups:
            groups[key] = {'json': json_document, 'yaml': document(language, YAML), 'language': language}
        else:
            assert groups[key]['json'] == json_document, f'catalog key {key!r} renders two documents ({language})'
    base_key = catalog_key(settings.LANGUAGE_CODE, directories)
    base = {form: lines(groups[base_key][form]) for form in ('yaml', 'json')}
    version_lines = {'yaml': base['yaml'].index('  version: 1.0.0\n'), 'json': base['json'].index('        "version": "1.0.0",\n')}
    assert all(base[form].count(base[form][version_lines[form]]) == 1 for form in base), 'version line must be unique'

    variants, variant_of = [], {}
    distinct = {}
    for key in sorted(groups):
        documents = (groups[key]['yaml'], groups[key]['json'])
        if documents not in distinct:
            ops = {form: patch_ops(base[form], lines(groups[key][form])) for form in ('yaml', 'json')}
            for form in ops:
                assert apply_ops(base[form], ops[form]) == groups[key][form]
                for start, end, _ in ops[form]:
                    assert not (start <= version_lines[form] < max(end, start + 1)), 'a translation touches info.version'
            distinct[documents] = len(variants)
            variants.append(ops)
        variant_of[key] = distinct[documents]

    # OPTIONS bodies and the redoc/swagger pages depend on LocaleMiddleware's language only.
    options = {}
    for code in sorted({code for code, _ in settings.LANGUAGES if check_for_language(code)} | {settings.LANGUAGE_CODE}):
        bodies = {}
        for form, media in (('yaml', YAML), ('json', JSON)):
            response = client.options(SCHEMA, HTTP_ACCEPT=media, HTTP_ACCEPT_LANGUAGE=code)
            assert response.status_code == 200 and response['Content-Language'] == code, code
            bodies[form] = response.content.decode()
        options[code] = bodies

    swagger = client.get(SCHEMA + 'swagger-ui/', HTTP_ACCEPT='text/html').content.decode()
    redoc = client.get(SCHEMA + 'redoc/', HTTP_ACCEPT='text/html').content.decode()
    redoc_options = client.options(SCHEMA + 'redoc/', HTTP_ACCEPT='text/html').content.decode()
    token_line = 'request.headers["X-CSRFTOKEN"] = "'
    start = swagger.index(token_line) + len(token_line)
    assert swagger.count(token_line) == 1 and swagger[start + 64] == '"'
    swagger = swagger[:start] + CSRF_PLACEHOLDER + swagger[start + 64:]
    assert swagger.count('url: "/schema/",') == 1 and redoc.count('spec-url="/schema/"') == 1
    swagger = swagger.replace('url: "/schema/",', 'url: "' + URL_PLACEHOLDER + '",')
    redoc = redoc.replace('spec-url="/schema/"', 'spec-url="' + URL_PLACEHOLDER + '"')

    alias = dict(sorted(locale.locale_alias.items()))
    samples = {language: catalog_key(language, directories) for language in sorted(set(keys) | set(EXTRA_KEY_SAMPLES))}
    catalog = {
        'comment': 'Generated by TESTS/adapters/django/api_schema.py --write; do not edit.',
        'language_code': settings.LANGUAGE_CODE,
        'title': settings.SPECTACULAR_SETTINGS['TITLE'],
        'version': settings.SPECTACULAR_SETTINGS['VERSION'],
        'version_lines': version_lines,
        'directories': [names for _directory, names in directories],
        'locale_alias': alias,
        'variant_of': dict(sorted(variant_of.items())),
        'variants': variants,
        'options': options,
        'pages': {'swagger': swagger, 'redoc': redoc, 'redoc_options': redoc_options},
    }
    files = {
        'schema.yaml': ''.join(base['yaml']),
        'schema.json': ''.join(base['json']),
        'catalog.json': json.dumps(catalog, ensure_ascii=False, indent=1) + '\n',
        'testdata/catalog_keys.json': json.dumps(samples, ensure_ascii=False, indent=1) + '\n',
        'testdata/render_corpus.json': json.dumps(render_corpus(), ensure_ascii=False, indent=1) + '\n',
        'testdata/negotiation_corpus.json': json.dumps(negotiation_corpus(), ensure_ascii=False, indent=1) + '\n',
    }
    summary = {'candidates': len(keys), 'catalog_keys': len(groups), 'variants': len(variants), 'options_languages': len(options)}
    return files, summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--write', action='store_true')
    mode.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    with setup_django():
        files, summary = generate()
    stale = [name for name, content in files.items()
             if not (OUTPUT / name).exists() or (OUTPUT / name).read_text() != content]
    if args.write:
        for name in stale:
            (OUTPUT / name).parent.mkdir(parents=True, exist_ok=True)
            (OUTPUT / name).write_text(files[name])
    print(json.dumps({'success': args.write or not stale, 'stale': stale, **summary}))
    if args.check and stale:
        print('The Go OpenAPI schema data is stale: run python TESTS/adapters/django/api_schema.py --write', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
