"""Offline composition guards; packaged volume behavior is a parent-run gate."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPOOL = '/var/cache/assozeta-message-list'


def check_composition(case, compose, dockerfile, server):
    # Scope all Compose assertions to api-go; inherited migration commands own
    # a different volume/environment list and never serve message LIST.
    block = re.search(r'^  api-go:.*?\n(?=^  [a-z]|\Z)', compose,
                      re.M | re.S)
    case.assertIsNotNone(block, 'api-go service missing')
    api = block.group()
    case.assertRegex(api, rf'(?m)^      GO_MESSAGE_LIST_SPOOL_DIR: {re.escape(SPOOL)}$')
    case.assertRegex(api, rf'(?m)^      - {re.escape(SPOOL)}$')  # Anonymous disk volume.
    case.assertRegex(api, r'(?m)^    user: "10001:10001"$')
    case.assertRegex(api, r'(?m)^    read_only: true$')
    case.assertRegex(api, r'(?m)^      - /tmp:rw,noexec,nosuid,size=64m$')
    case.assertRegex(api, r'(?m)^      - no-new-privileges:true$')
    case.assertNotRegex(api, r'(?m)^\s+TMPDIR:')
    # Check image ownership, not a runtime root/chown workaround or image VOLUME.
    case.assertIn(f'RUN mkdir -p {SPOOL} && \\\n'
                  f'    chown 10001:10001 {SPOOL} && chmod 0700 {SPOOL}', dockerfile)
    case.assertRegex(dockerfile, r'(?m)^USER 10001:10001$')
    case.assertNotRegex(dockerfile, r'(?m)^VOLUME\b')
    case.assertIn('handler.WithMessageListSpoolDirectory(cfg.MessageListSpoolDirectory)', server)


class MessageListSpoolComposition(unittest.TestCase):
    def setUp(self):
        self.compose = (ROOT / 'selfhost/compose.yml').read_text()
        self.dockerfile = (ROOT / 'BE_GO/Dockerfile').read_text()
        self.server = (ROOT / 'BE_GO/cmd/server/main.go').read_text()

    def test_dedicated_disk_spool_and_security_controls(self):
        check_composition(self, self.compose, self.dockerfile, self.server)

    def test_guards_reject_deployment_regressions(self):
        cases = {
            'missing disk volume': (self.compose.replace(f'      - {SPOOL}\n', ''), self.dockerfile, self.server),
            'uses tmpfs': (self.compose.replace(f'GO_MESSAGE_LIST_SPOOL_DIR: {SPOOL}', 'GO_MESSAGE_LIST_SPOOL_DIR: /tmp'), self.dockerfile, self.server),
            'writable root': (self.compose.replace('    read_only: true', '    read_only: false'), self.dockerfile, self.server),
            'root user': (self.compose.replace('    user: "10001:10001"', '    user: "0:0"'), self.dockerfile, self.server),
            'larger tmpfs': (self.compose.replace('size=64m', 'size=256m'), self.dockerfile, self.server),
            'wrong volume owner': (self.compose, self.dockerfile.replace(f'chown 10001:10001 {SPOOL}', f'chown 0:0 {SPOOL}'), self.server),
            'wrong directory mode': (self.compose, self.dockerfile.replace(f'chmod 0700 {SPOOL}', f'chmod 0777 {SPOOL}'), self.server),
            'missing server wiring': (self.compose, self.dockerfile, self.server.replace('handler.WithMessageListSpoolDirectory(cfg.MessageListSpoolDirectory)', '')),
        }
        for name, sources in cases.items():
            with self.subTest(name=name), self.assertRaises(AssertionError):
                check_composition(self, *sources)


if __name__ == '__main__':
    unittest.main()
