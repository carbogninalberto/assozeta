"""The pairing contract checks the throttle delay against the actual request window."""
from email.message import Message
import json
import unittest
from unittest.mock import patch

from TESTS.contracts import test_sso_pairing_http as pairing


class RetryAfterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = pairing.load_source()
        cls.case = next(case for case in cls.source['cases'] if case['name'] == 'sso-pairing-admin-throttled')

    def check_reply(self, stamp, start, end, header, body_seconds):
        headers = Message()
        for name, value in self.source['secure_headers'].items():
            headers[name] = value
        headers['Allow'] = pairing.ALLOW[self.case['view']]
        headers['Content-Language'] = 'en'
        headers['Content-Type'] = 'application/json'
        if header is not None:
            headers['Retry-After'] = header
        unit = 'second' if body_seconds == 1 else 'seconds'
        body = {'detail': f'Request was throttled. Expected available in {body_seconds} {unit}.'}
        with patch.dict(pairing.RUNTIME, source=self.source), patch.object(pairing, 'Client') as client, \
                patch.object(pairing, 'time') as clock:
            clock.time.side_effect = [start, end]
            client.return_value.request.return_value = (429, json.dumps(body).encode(), headers)
            return pairing.assert_case(self.case, 'http://localhost', token=None, sessions={},
                                       throttle_stamp=stamp)

    def test_delay_follows_the_seed_and_request_times(self):
        for stamp, start, end, seconds in (
            (90, 100.97, 100.99, 50), (90, 101.01, 101.04, 49),
            (90, 100.99, 101.01, 49), (90, 100.99, 101.01, 50),
            (90.2, 101.03, 101.06, 50), (90, 149.1, 149.2, 1),
        ):
            with self.subTest(stamp=stamp, start=start, end=end, seconds=seconds):
                self.assertEqual(self.check_reply(stamp, start, end, str(seconds), seconds), 429)

    def test_header_is_canonical_in_range_and_agrees_with_body(self):
        for header, seconds in [('50', 50), ('48', 48), ('049', 49), (None, 49), ('49', 50)]:
            with self.subTest(header=header, seconds=seconds), self.assertRaises(pairing.ResponseFailure):
                self.check_reply(90, 101.01, 101.04, header, seconds)
