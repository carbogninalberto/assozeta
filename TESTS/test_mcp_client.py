"""Check the subprocess oracle cannot accept truncated or unsolicited output."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

from TESTS.mcp_client import StdioClient


class StdioClientTests(unittest.TestCase):
    def client(self, code):
        return StdioClient([sys.executable, '-u', '-c', code], os.environ.copy(),
                           subprocess.DEVNULL, Path(__file__).parent)

    def test_clean_eof_after_response(self):
        client = self.client('import sys; print("{}"); sys.stdin.buffer.read()')
        self.assertEqual(client.receive(), {})
        self.assertEqual(client.close(), 0)

    def test_output_after_eof_is_not_silently_discarded(self):
        client = self.client('import sys; sys.stdin.buffer.read(); print("{}")')
        with self.assertRaisesRegex(RuntimeError, 'Unexpected.*stdout'):
            client.close()

    def test_buffered_output_is_not_silently_discarded(self):
        client = self.client('import sys; print("{}\\n{}"); sys.stdin.buffer.read()')
        self.assertEqual(client.receive(), {})
        with self.assertRaisesRegex(RuntimeError, 'Unexpected.*stdout'):
            client.close()

    def test_exit_before_eof_is_not_clean_eof_evidence(self):
        client = self.client('pass')
        client.process.wait(timeout=5)
        with self.assertRaisesRegex(RuntimeError, 'exited before EOF'):
            client.close()


if __name__ == '__main__':
    unittest.main()
