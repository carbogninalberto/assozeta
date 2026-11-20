import unittest
from email.mime.text import MIMEText
from email.policy import compat32

from TESTS.adapters.go.celery_consumer_process import normalize_unit


class SmtpTranscriptTests(unittest.TestCase):
    def test_random_ids_on_opposite_sides_of_the_header_limit(self):
        def transcript(length):
            mail = MIMEText('Message')
            mail['Message-ID'] = '<' + '1' * length + '@example.test>'
            return mail.as_bytes(policy=compat32.clone(linesep='\r\n')).decode()

        short, long = transcript(51), transcript(52)
        self.assertIn('Message-ID: <', short)
        self.assertIn('Message-ID: \r\n <', long)
        self.assertEqual(normalize_unit(short), normalize_unit(long))
        with self.assertRaises(AssertionError):
            normalize_unit(long.replace('Message-ID: \r\n ', 'Message-ID: '))
