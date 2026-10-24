"""Authored storage trust contract, independent of Django and Go implementations.

An explicitly empty legacy CA setting is distinct from an absent setting. It
disables certificate verification; a nonempty invalid value must never do so.
"""
import copy
import hashlib
import unittest

PAYLOAD = b'\x89PNG\r\n\x1a\nowned private CA fixture\x00'


def cases():
    rows = []
    def add(name, *, env=None, profile_ca=None, configured=False, profile=False,
            success=True, tls=True):
        rows.append({'name': name, 'environment': env or {},
                     'profile_ca': profile_ca, 'profile_ca_configured': configured,
                     'profile_credentials': profile, 'tls': tls,
                     'expected': {'initialized': True, 'credentials': True,
                                  'exists': success, 'read': success,
                                  'body_sha256': hashlib.sha256(PAYLOAD).hexdigest() if success else None}})
    add('absent-private-root-rejected', success=False)
    add('SSL_CERT_FILE-is-not-storage-trust', env={'SSL_CERT_FILE':'good'}, success=False)
    add('SSL_CERT_DIR-is-not-storage-trust', env={'SSL_CERT_DIR':'directory'}, success=False)
    for variable, label in [('AWS_CA_BUNDLE', 'aws'), ('REQUESTS_CA_BUNDLE', 'requests')]:
        for value, success in [('good', True), ('wrong', False), ('invalid', False), ('missing', False), ('empty', True)]:
            add(label+'-'+value, env={variable: value}, success=success)
    for value, success in [('good', True), ('wrong', False), ('invalid', False), ('missing', False), ('empty', True)]:
        add('shared-'+value, profile_ca=value, configured=True, success=success)
    add('aws-good-before-shared-and-requests', env={'AWS_CA_BUNDLE':'good','REQUESTS_CA_BUNDLE':'wrong'}, profile_ca='wrong', configured=True)
    add('aws-missing-before-shared-and-requests', env={'AWS_CA_BUNDLE':'missing','REQUESTS_CA_BUNDLE':'good'}, profile_ca='good', configured=True, success=False)
    add('aws-empty-before-shared-and-requests', env={'AWS_CA_BUNDLE':'empty','REQUESTS_CA_BUNDLE':'wrong'}, profile_ca='wrong', configured=True)
    add('shared-good-before-requests', env={'REQUESTS_CA_BUNDLE':'wrong'}, profile_ca='good', configured=True)
    add('shared-wrong-before-requests', env={'REQUESTS_CA_BUNDLE':'good'}, profile_ca='wrong', configured=True, success=False)
    add('shared-empty-before-requests', env={'REQUESTS_CA_BUNDLE':'wrong'}, profile_ca='empty', configured=True)
    add('named-profile-good', profile_ca='good', configured=True, profile=True)
    add('named-profile-missing', profile_ca='missing', configured=True, profile=True, success=False)
    for value in ('literal-dollar', 'literal-space', 'literal-false', 'tilde'):
        add(value, env={'AWS_CA_BUNDLE':value})
    add('dollar-not-expanded', env={'AWS_CA_BUNDLE':'unexpanded-dollar'}, success=False)
    add('trailing-space-not-trimmed', env={'AWS_CA_BUNDLE':'trailing-space'}, success=False)
    add('http-ignores-missing-bundle', env={'AWS_CA_BUNDLE':'missing'}, tls=False)
    add('http-profile-ignores-invalid-bundle', profile_ca='invalid', configured=True, profile=True, tls=False)
    return rows


def assert_result(case, actual):
    for key, expected in case['expected'].items():
        assert type(actual.get(key)) is type(expected), (case['name'], key, 'JSON type differs')
        assert actual[key] == expected, (case['name'], key, actual[key], expected)


class StorageTLSContractTests(unittest.TestCase):
    def test_names_and_security_boundaries(self):
        rows = cases()
        self.assertEqual(len(rows), len({row['name'] for row in rows}))
        by_name = {row['name']:row for row in rows}
        self.assertFalse(by_name['absent-private-root-rejected']['expected']['exists'])
        self.assertTrue(by_name['aws-empty-before-shared-and-requests']['expected']['exists'])
        self.assertFalse(by_name['aws-missing-before-shared-and-requests']['expected']['exists'])
        self.assertTrue(all(row['expected']['initialized'] for row in rows))

    def test_type_alias_is_rejected(self):
        case = cases()[0]
        actual = copy.deepcopy(case['expected']); actual['initialized'] = 1
        with self.assertRaises(AssertionError): assert_result(case, actual)


if __name__ == '__main__': unittest.main()
