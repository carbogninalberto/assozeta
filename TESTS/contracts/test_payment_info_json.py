"""JSON comparison regressions; no live runtime or fixture is required."""
import unittest

from TESTS.contracts.test_payment_info import (assert_json_equal, decode_response, validate_fixture,
                                              MATRIX_VERSION, MATRIX_CASE_COUNT, MATRIX_SENTINELS)


class PaymentInfoJSONTests(unittest.TestCase):
    def test_portable_matrix_rejects_truncation_duplicates_and_wrong_version(self):
        cases = [{'name': name} for name in sorted(MATRIX_SENTINELS)]
        cases += [{'name': f'authored-case-{i}'} for i in range(MATRIX_CASE_COUNT - len(cases))]
        fixture = {'matrix_version': MATRIX_VERSION, 'cases': cases}
        self.assertIs(validate_fixture(self, fixture), cases)
        for invalid in ({**fixture, 'cases': cases[:-1]},
                        {**fixture, 'cases': [cases[1], *cases[1:]]},
                        {**fixture, 'matrix_version': 'old-matrix'},
                        {**fixture, 'cases': [{'name': f'wrong-{i}'} for i in range(MATRIX_CASE_COUNT)]}):
            with self.assertRaises(AssertionError):
                validate_fixture(self, invalid)

    def test_html_suffix_after_1000_bytes_is_available_to_exclusions(self):
        content = b'<html>Server Error' + b' ' * 1200 + b'Foreign course</html>'
        decoded = decode_response(content)
        self.assertEqual(decoded, content.decode())
        self.assertIn('Foreign course', decoded)

    def test_boolean_and_number_are_distinct_in_both_directions(self):
        for actual, expected in ((False, 0), (0, False), (True, 1), (1.0, True)):
            with self.subTest(actual=actual, expected=expected), self.assertRaises(AssertionError):
                assert_json_equal(self, actual, expected)

    def test_nested_alias_reports_the_changed_field(self):
        expected = {'data': {'course': [None, {'enabled': False, 'fee': 12.3}]}}
        actual = {'data': {'course': [None, {'enabled': 0, 'fee': 12.3}]}}
        with self.assertRaisesRegex(AssertionError, r'\$\.data\.course\[1\]\.enabled: JSON type'):
            assert_json_equal(self, actual, expected)

    def test_mixed_json_preserves_numbers_and_ignores_object_key_order(self):
        expected = {'data': [False, True, None, '1', {'fee': 1, 'amount': '12.30'}]}
        actual = {'data': [False, True, None, '1', {'amount': '12.30', 'fee': 1.0}]}
        assert_json_equal(self, actual, expected)

    def test_structure_and_string_number_changes_are_rejected(self):
        for actual, expected in (({'a': 1}, {'a': 1, 'b': None}), ([1], [1, None]), ('1', 1)):
            with self.subTest(actual=actual, expected=expected), self.assertRaises(AssertionError):
                assert_json_equal(self, actual, expected)
