"""Fixture completeness controls independent of any running backend."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from TESTS.adapters.django.message_reads import load_authored_manifest
from TESTS.contracts.test_message_reads import load_manifest


class MessageMatrixCompleteness(unittest.TestCase):
    def test_every_version_one_case_is_required_by_both_validators(self):
        fixture = load_authored_manifest()
        self.assertEqual(len(fixture['cases']), 30)
        for omitted in fixture['cases']:
            incomplete = copy.deepcopy(fixture)
            incomplete['cases'] = [case for case in incomplete['cases']
                                   if case['name'] != omitted['name']]
            for validator in (load_authored_manifest, load_manifest):
                with self.subTest(validator=validator.__name__, omitted=omitted['name']):
                    with tempfile.TemporaryDirectory() as directory:
                        path = Path(directory) / 'fixture.json'
                        path.write_text(json.dumps(incomplete))
                        with self.assertRaises(AssertionError):
                            validator(path=path)

    def test_version_one_rejects_an_unrecognized_additional_case(self):
        fixture = load_authored_manifest()
        extra = copy.deepcopy(fixture['cases'][0])
        extra['name'] = 'unrecognized-version-one-case'
        fixture['cases'].append(extra)
        for validator in (load_authored_manifest, load_manifest):
            with self.subTest(validator=validator.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'fixture.json'
                    path.write_text(json.dumps(fixture))
                    with self.assertRaises(AssertionError):
                        validator(path=path)


if __name__ == '__main__':
    unittest.main()
