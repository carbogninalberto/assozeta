"""Portable authored expectations for manifest and collaborator transport locales.

This module deliberately imports only the Python standard library. Backend
adapters supply actual HTTP observations; expected values never come from a
running implementation.
"""
import json
from pathlib import Path
import re
import unittest


FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/read-route-locales.json'
SUPPORTED_LANGUAGE_COUNT = 99
AUTHORED_SELECTION_COUNT = 20
EXPLICIT_SELECTION_COUNT = 99
SELECTION_CASE_COUNT = 119
AUTHORED_HTTP_COUNT = 16
MATRIX_HTTP_COUNT = 594
HTTP_CASE_COUNT = 610

IDENTITIES = frozenset({'anonymous', 'owner', 'athlete', 'scoped-owner'})
MESSAGE_KINDS = ('credentials', 'permission', 'accept', 'not_found', 'method', 'media')
MATRIX_EXPECTATIONS = {
    'credentials': {
        'identity': 'anonymous',
        'route': 'collaborators-list',
        'method': 'GET',
        'path': '/collaborators/list',
        'status': 401,
    },
    'permission': {
        'identity': 'athlete',
        'route': 'collaborators-list',
        'method': 'GET',
        'path': '/collaborators/list',
        'status': 403,
    },
    'accept': {
        'identity': 'anonymous',
        'route': 'instance-manifest',
        'method': 'GET',
        'path': '/instance/manifest.json',
        'status': 406,
    },
    'not_found': {
        'identity': 'anonymous',
        'route': 'instance-manifest',
        'method': 'GET',
        'path': '/instance/manifest.json?format=xml',
        'status': 404,
    },
    'method': {
        'identity': 'anonymous',
        'route': 'instance-manifest',
        'method': 'POST',
        'path': '/instance/manifest.json',
        'status': 405,
    },
    'media': {
        'identity': 'scoped-owner',
        'route': 'collaborators-list',
        'method': 'POST',
        'path': '/collaborators/list',
        'status': 415,
    },
}
AUTHORED_SELECTION_NAMES = frozenset({
    'default-italian',
    'german',
    'spanish-argentina-exact',
    'unsupported-french-region-base',
    'traditional-chinese-alias',
    'quality-order',
    'zero-quality-still-winning',
    'unknown-before-supported',
    'wildcard-stops-selection',
    'invalid-quality-uses-default',
    'duplicate-header-values',
    'cookie-over-header',
    'duplicate-cookie-last-value',
    'invalid-cookie-falls-to-header',
    'latin1-nbsp-cookie-trim',
    'latin1-nbsp-quality-header',
    'latin1-nel-separator-header',
    'long-header-last-comma-before-500',
    'long-single-range-uses-default',
    'quoted-octal-cookie-control',
})
AUTHORED_HTTP_NAMES = frozenset({
    'manifest-german-format-not-found',
    'manifest-spanish-argentina-method',
    'manifest-traditional-chinese-accept-fallback',
    'manifest-vietnamese-method',
    'manifest-afrikaans-catalog-fallback',
    'manifest-brazilian-portuguese-method',
    'manifest-russian-method',
    'collaborator-english-credentials',
    'collaborator-german-permission',
    'collaborator-spanish-argentina-accept',
    'collaborator-french-format-not-found',
    'collaborator-japanese-method',
    'collaborator-french-scoped-media',
    'collaborator-traditional-chinese-permission',
    'collaborator-vietnamese-credentials-fallback',
    'collaborator-lithuanian-permission-fallback',
})
AUTHORED_HTTP_IDENTITIES = {
    'manifest-german-format-not-found': 'anonymous',
    'manifest-spanish-argentina-method': 'anonymous',
    'manifest-traditional-chinese-accept-fallback': 'anonymous',
    'manifest-vietnamese-method': 'anonymous',
    'manifest-afrikaans-catalog-fallback': 'anonymous',
    'manifest-brazilian-portuguese-method': 'anonymous',
    'manifest-russian-method': 'anonymous',
    'collaborator-english-credentials': 'anonymous',
    'collaborator-german-permission': 'athlete',
    'collaborator-spanish-argentina-accept': 'anonymous',
    'collaborator-french-format-not-found': 'owner',
    'collaborator-japanese-method': 'owner',
    'collaborator-french-scoped-media': 'scoped-owner',
    'collaborator-traditional-chinese-permission': 'athlete',
    'collaborator-vietnamese-credentials-fallback': 'anonymous',
    'collaborator-lithuanian-permission-fallback': 'athlete',
}
MATRIX_REQUEST_BODY = 'portable unsupported-media probe\n'
OWNED_SESSION_PLACEHOLDER = '__OWNED_SESSION__'
RUSSIAN_PERMISSION = (
    'У вас недостаточно прав для выполнения данного действия.'
)
RUSSIAN_NOT_FOUND = 'Страница не найдена.'


def fixture():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


def _header(headers, name):
    if headers is None:
        return None
    lowered = name.lower()
    matches = [
        value
        for key, value in headers.items()
        if isinstance(key, str) and key.lower() == lowered
    ]
    if len(matches) > 1:
        raise AssertionError(f'duplicate case-insensitive header {name!r}')
    return matches[0] if matches else None


def _assert_strict(test, actual, expected, message):
    """Compare JSON-like values without Python's bool/int equality coercion."""
    test.assertIs(type(actual), type(expected), message)
    if isinstance(expected, dict):
        test.assertEqual(set(actual), set(expected), message)
        for key in expected:
            _assert_strict(test, actual[key], expected[key], f'{message}: {key}')
    elif isinstance(expected, list):
        test.assertEqual(len(actual), len(expected), message)
        for index, (actual_item, expected_item) in enumerate(zip(actual, expected)):
            _assert_strict(test, actual_item, expected_item, f'{message}: [{index}]')
    else:
        test.assertEqual(actual, expected, message)


def assert_http_case(test, case, status, response, headers):
    """Apply one authored HTTP expectation to an adapter observation."""
    _assert_strict(test, status, case['status'], f"{case['name']}: status")
    _assert_strict(test, response, case['body'], f"{case['name']}: body")
    language = _header(headers, 'Content-Language')
    _assert_strict(test, language, case['language'], f"{case['name']}: Content-Language")


def assert_selection_report(test, report):
    """Apply an adapter-supplied {case name: normalized language} report."""
    expected = {case['name']: case['language'] for case in fixture()['selection_cases']}
    _assert_strict(test, report, expected, 'All authored locale-selection cases are mandatory')


def _case_names(cases):
    return [case['name'] for case in cases]


class ReadRouteLocaleFixtureTests(unittest.TestCase):
    def test_exact_case_counts_and_mandatory_identities(self):
        data = fixture()
        self.assertIs(type(data['schema_version']), int)
        self.assertEqual(data['schema_version'], 1)
        self.assertEqual(type(data['identity_enum']), list)
        self.assertEqual(set(data['identity_enum']), IDENTITIES)
        self.assertEqual(len(data['identity_enum']), len(IDENTITIES))
        self.assertEqual(len(data['supported_languages']), SUPPORTED_LANGUAGE_COUNT)
        self.assertEqual(len(set(data['supported_languages'])), SUPPORTED_LANGUAGE_COUNT)
        self.assertEqual(len(data['selection_cases']), SELECTION_CASE_COUNT)
        self.assertEqual(len(data['http_cases']), HTTP_CASE_COUNT)
        self.assertEqual(len(AUTHORED_SELECTION_NAMES), AUTHORED_SELECTION_COUNT)
        self.assertEqual(len(AUTHORED_HTTP_NAMES), AUTHORED_HTTP_COUNT)

        selection_names = _case_names(data['selection_cases'])
        http_names = _case_names(data['http_cases'])
        self.assertEqual(len(selection_names), len(set(selection_names)),
                         'Selection case names must identify observations')
        self.assertEqual(len(http_names), len(set(http_names)),
                         'HTTP case names must identify observations')

        languages = data['supported_languages']
        expected_selection_names = AUTHORED_SELECTION_NAMES | {
            f'supported-language-{language}' for language in languages
        }
        expected_http_names = AUTHORED_HTTP_NAMES | {
            f'matrix-{language}-{kind}'
            for language in languages
            for kind in MESSAGE_KINDS
        }
        self.assertEqual(set(selection_names), expected_selection_names)
        self.assertEqual(set(http_names), expected_http_names)

        for case in data['http_cases']:
            name = case['name']
            self.assertIn('identity', case, name)
            self.assertIs(type(case['identity']), str, name)
            self.assertIn(case['identity'], IDENTITIES, name)

    def test_selection_expectations_cover_each_supported_language(self):
        data = fixture()
        explicit = {
            case['name']: case
            for case in data['selection_cases']
            if case['name'].startswith('supported-language-')
        }
        self.assertEqual(len(explicit), EXPLICIT_SELECTION_COUNT)
        for language in data['supported_languages']:
            case = explicit[f'supported-language-{language}']
            self.assertEqual(case['headers'], {'Accept-Language': language})
            self.assertIs(type(case['language']), str)
            self.assertEqual(case['language'], language)

        cases = {case['name']: case for case in data['selection_cases']}
        self.assertEqual(cases['spanish-argentina-exact']['language'], 'es-ar')
        self.assertEqual(cases['unsupported-french-region-base']['language'], 'fr')
        self.assertEqual(cases['traditional-chinese-alias']['language'], 'zh-hant')
        self.assertEqual(cases['zero-quality-still-winning']['language'], 'de')
        self.assertEqual(cases['wildcard-stops-selection']['language'], 'it')
        self.assertEqual(cases['duplicate-cookie-last-value']['language'], 'es-ar')
        self.assertEqual(cases['long-single-range-uses-default']['language'], 'it')

        latin1_wire = {
            'latin1-nbsp-quality-header': (
                'Accept-Language', b'de\xa0;\xa0q=0.8,ja', 'ja',
            ),
            'latin1-nel-separator-header': (
                'Accept-Language', b'de\x85,\x85ja', 'de',
            ),
            'latin1-nbsp-cookie-trim': (
                'Cookie', b'django_language\xa0=\xa0ja\xa0', 'ja',
            ),
        }
        for name, (header, wire, language) in latin1_wire.items():
            case = cases[name]
            self.assertEqual(case['headers'][header].encode('latin-1'), wire, name)
            self.assertEqual(case['language'], language, name)
        self.assertEqual(
            cases['quoted-octal-cookie-control']['headers']['Cookie'],
            'django_language="\\152\\141"',
        )
        self.assertEqual(cases['quoted-octal-cookie-control']['language'], 'ja')

        max_length = data['source']['accept_language_max_length']
        long_header = cases['long-header-last-comma-before-500']['headers'][
            'Accept-Language'
        ]
        self.assertGreater(len(long_header), max_length)
        self.assertEqual(long_header.rfind(','), len('de'))
        self.assertLess(long_header.rfind(','), max_length)
        self.assertEqual(cases['long-header-last-comma-before-500']['language'], 'de')

        single_range = cases['long-single-range-uses-default']['headers'][
            'Accept-Language'
        ]
        self.assertGreater(len(single_range), max_length)
        self.assertNotIn(',', single_range)

    def test_matrix_has_six_explicit_actor_and_route_cases_per_language(self):
        data = fixture()
        cases = {case['name']: case for case in data['http_cases']}
        matrix = [
            case for case in data['http_cases']
            if case['name'].startswith('matrix-')
        ]
        self.assertEqual(len(matrix), MATRIX_HTTP_COUNT)

        for language in data['supported_languages']:
            for kind in MESSAGE_KINDS:
                case = cases[f'matrix-{language}-{kind}']
                expectation = MATRIX_EXPECTATIONS[kind]
                self.assertEqual(case['identity'], expectation['identity'], case['name'])
                self.assertIn(case['identity'], IDENTITIES, case['name'])
                self.assertEqual(case['route'], expectation['route'], case['name'])
                self.assertEqual(case['method'], expectation['method'], case['name'])
                self.assertEqual(case['path'], expectation['path'], case['name'])
                self.assertEqual(case['status'], expectation['status'], case['name'])
                self.assertEqual(case['language'], language, case['name'])
                self.assertEqual(_header(case['headers'], 'Accept-Language'),
                                 language, case['name'])
                self.assertEqual(_header(case['headers'], 'Content-Language'),
                                 None, case['name'])

                if kind == 'accept':
                    self.assertEqual(_header(case['headers'], 'Accept'), 'text/html')
                if kind == 'media':
                    self.assertEqual(_header(case['headers'], 'Content-Type'), 'text/plain')
                    self.assertEqual(_header(case['headers'], 'X-Impersonation-Id'),
                                     OWNED_SESSION_PLACEHOLDER)
                    self.assertEqual(case['request_body'], MATRIX_REQUEST_BODY)
                else:
                    self.assertNotIn('request_body', case, case['name'])

    def test_authored_http_expectations_and_request_inputs_are_preserved(self):
        cases = {
            case['name']: case
            for case in fixture()['http_cases']
            if case['name'] in AUTHORED_HTTP_NAMES
        }
        self.assertEqual(len(cases), AUTHORED_HTTP_COUNT)
        self.assertEqual(cases['manifest-german-format-not-found']['body']['detail'],
                         'Nicht gefunden.')
        self.assertEqual(cases['manifest-spanish-argentina-method']['body']['detail'],
                         'Método "POST" no permitido.')
        self.assertEqual(cases['manifest-traditional-chinese-accept-fallback']['body']['detail'],
                         "Impossibile soddisfare l'header \"Accept\" presente nella richiesta.")
        self.assertEqual(cases['collaborator-french-scoped-media']['body']['detail'],
                         'Type de média «\u00a0text/plain\u00a0» non supporté.')
        self.assertEqual(cases['collaborator-lithuanian-permission-fallback']['body']['detail'],
                         "Non hai l'autorizzazione per eseguire questa azione.")

        media = cases['collaborator-french-scoped-media']
        self.assertEqual(media['identity'], 'scoped-owner')
        self.assertEqual(_header(media['headers'], 'X-Impersonation-Id'),
                         OWNED_SESSION_PLACEHOLDER)
        self.assertEqual(media['request_body'], MATRIX_REQUEST_BODY)
        for name, case in cases.items():
            self.assertEqual(case['identity'], AUTHORED_HTTP_IDENTITIES[name], name)
            if case['status'] != 415:
                self.assertNotIn('request_body', case, name)

    def test_russian_gettext_alias_merge_has_independent_expectations(self):
        cases = {case['name']: case for case in fixture()['http_cases']}
        self.assertEqual(cases['matrix-ru-permission']['body']['detail'],
                         RUSSIAN_PERMISSION)
        self.assertEqual(cases['matrix-ru-not_found']['body']['detail'],
                         RUSSIAN_NOT_FOUND)

    def test_fixture_records_static_catalog_provenance(self):
        source = fixture()['source']
        self.assertEqual(source['django']['version'], '5.2.18')
        self.assertEqual(source['django_rest_framework']['version'], '3.17.2')
        self.assertEqual(source['django']['license'], 'BSD-3-Clause')
        self.assertEqual(source['django_rest_framework']['license'], 'BSD-3-Clause')
        self.assertEqual(source['django_asgi_header_decoding'], 'latin1')
        self.assertIn('U+0085', source['python_whitespace'])
        self.assertIn('U+00A0', source['python_whitespace'])
        self.assertEqual(source['license_notice'], 'BE_GO/THIRD_PARTY_NOTICES.md')
        digest_pattern = re.compile(r'^[0-9a-f]{64}$')
        for package in ('django', 'django_rest_framework'):
            self.assertGreater(source[package]['catalog_file_count'], 0)
            self.assertRegex(source[package]['catalog_manifest_sha256'], digest_pattern)
        self.assertIn('acceptance reads only JSON', source['expectation_mode'])
        self.assertGreaterEqual(len(source['fallback_rules']), 4)
        self.assertTrue(any('ru_RU' in rule for rule in source['fallback_rules']))

    def test_content_language_lookup_accepts_header_case_variants(self):
        self.assertEqual(_header({'content-language': 'de'}, 'Content-Language'), 'de')
        self.assertEqual(_header({'CONTENT-LANGUAGE': 'ja'}, 'Content-Language'), 'ja')
        self.assertIsNone(_header({}, 'Content-Language'))
        with self.assertRaisesRegex(AssertionError, 'duplicate'):
            _header({'Content-Language': 'de', 'content-language': 'fr'}, 'Content-Language')

    def test_strict_comparison_rejects_python_type_coercion(self):
        with self.assertRaises(AssertionError):
            _assert_strict(self, True, 1, 'bool is not int')
        with self.assertRaises(AssertionError):
            _assert_strict(self, 1.0, 1, 'float is not int')
        _assert_strict(self, {'detail': 'ok'}, {'detail': 'ok'}, 'same JSON shape')


if __name__ == '__main__':
    unittest.main()
