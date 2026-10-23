"""Portable authored contracts for manual tools; imports no application code."""
import json
import hashlib
import math
import os
from pathlib import Path
import unittest

FIXTURE = Path(__file__).resolve().parents[1]/'fixtures/manual-tools.json'
PUBLIC_KEYS = set(('id page section title text status url screenshots scenario_ids page_title language intent '
                  'reader page_description page_order section_order embedded_url version application_revision '
                  'manual_revision features audience corpus_identity code_state publication_status score').split())


def validate_report(report, fixture):
    assert report.get('schema_version') == 1
    assert set(report.get('cases', {})) == set(fixture['cases']), 'Tool case set differs; no skips'
    for name, spec in fixture['cases'].items():
        actual = report['cases'][name]
        result = actual['result']
        assert actual['database_unchanged'] and actual['files_unchanged'], name+': mutated durable state'
        assert actual['writes'] == 0 and actual['source_canary_absent'], name+': unexpected write or private source'
        assert actual['selects'] <= spec.get('max_selects', 6), name+': SQL budget exceeded'
        for key, expected in spec['expected'].items():
            value = actual['ids'] if key=='ids' else sorted(result) if key=='keys' else result.get(key)
            assert value == expected, f'{name}.{key}: expected {expected!r}, observed {value!r}'
        for section in result.get('results', []) + ([result['section']] if isinstance(result.get('section'), dict) else []):
            assert set(section) == PUBLIC_KEYS, name+': complete public projection differs'
            assert section['corpus_identity'] == actual['fixture_identity'], name+': invalid fixture identity'
            assert section['version'] == 'manual-tool-fixture'
            assert section['application_revision'] == 'a'*40 and section['manual_revision'] == 'c'*40
            assert section['url'].startswith('https://manual.example.test/guide#'), name+': tool URL was replaced by reader URL'
            assert section['embedded_url'].startswith('/#/manuale?section=guide%23')
            if 'search_population' in spec:
                # Every authored candidate has exactly two occurrences of the
                # sole vocabulary word. The one-dimensional unit model yields
                # cosine=1; derive the expected score without backend imports.
                count = spec['search_population']
                bm25 = math.log(1 + .5 / (count + .5)) * 2 * 2.2 / (2 + 1.2)
                score = round(.75 * (.7 * bm25 / (1 + bm25) + .3) + .25, 6)
                assert section['score'] == score, name+': search score differs'
            else:
                assert section['score'] is None, name+': section read acquired a search score'


class ManualToolContracts(unittest.TestCase):
    def test_actual_function_oracle(self):
        path = os.environ.get('TESTS_MANUAL_TOOL_REPORT')
        self.assertTrue(path, 'Actual tool adapter report required')
        report = json.loads(Path(path).read_text())
        self.assertIs(report.get('success'), True)
        self.assertIs(report.get('private_services_stopped'), True)
        self.assertEqual(report.get('fixture_sha256'), hashlib.sha256(FIXTURE.read_bytes()).hexdigest())
        validate_report(report, json.loads(FIXTURE.read_text()))


if __name__ == '__main__':
    unittest.main()
