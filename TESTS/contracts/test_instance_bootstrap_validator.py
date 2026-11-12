"""Pure regressions for the bootstrap observation validator, not HTTP proof."""
import copy
import unittest

from TESTS.contracts.test_instance_bootstrap import validate_report


class BootstrapValidatorTests(unittest.TestCase):
    def report(self, body):
        return {'schema_version': 1, 'cases': {'example': {'status': 200, 'body': body,
            'database_unchanged': True, 'write_statements': 0, 'secret_free': True}}}

    def test_boolean_is_not_json_number(self):
        for expected, actual in [({'valid': True}, {'valid': 1}),
                                 ({'flags': [False]}, {'flags': [0]}),
                                 ({'number': 1}, {'number': True})]:
            with self.subTest(expected=expected):
                fixture = {'cases': {'example': {'expected': {'status': 200, 'body': expected}}}}
                with self.assertRaises(AssertionError):
                    validate_report(self.report(actual), fixture)
                validate_report(self.report(expected), fixture)

    def test_write_count_requires_integer_zero(self):
        fixture = {'cases': {'example': {'expected': {'status': 200, 'body': {'valid': True}}}}}
        for count in (False, None, '0', -1, 1):
            report = self.report({'valid': True})
            report['cases']['example']['write_statements'] = count
            with self.subTest(count=count), self.assertRaises(AssertionError):
                validate_report(report, fixture)

    def test_randomized_buffered_encoding_still_requires_real_smaller_gzip(self):
        fixture = {'cases': {'example': {'expected': {'status': 200, 'body': {'valid': True},
            'content_encoding_one_of': [None, 'gzip']}}}}
        report = self.report({'valid': True})
        actual = report['cases']['example']
        actual.update(headers={'Content-Encoding': 'gzip'}, gzip_decoded=True,
                      wire_body_bytes=50, decoded_body_bytes=100)
        validate_report(report, fixture)
        for changes in ({'gzip_decoded': False}, {'wire_body_bytes': 100},
                        {'headers': {'Content-Encoding': 'br'}}, {'wire_body_bytes': 0}):
            invalid = copy.deepcopy(report)
            invalid['cases']['example'].update(changes)
            with self.subTest(changes=changes), self.assertRaises(AssertionError):
                validate_report(invalid, fixture)


if __name__ == '__main__':
    unittest.main()
