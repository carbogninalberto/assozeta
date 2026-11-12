"""The Django relay test must exercise the exact shipped Go outbox schema."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class GoRealtimeOutboxDDL(unittest.TestCase):
    def test_django_copy_matches_go_migration(self):
        tree = ast.parse((ROOT / 'BE/notifications/tests/test_go_realtime.py').read_text())
        copies = [node.value.value for node in tree.body if isinstance(node, ast.Assign)
                  and any(getattr(target, 'id', None) == 'OUTBOX_DDL' for target in node.targets)]
        self.assertEqual(copies, [(ROOT / 'BE_GO/migrations/000004_realtime_events.up.sql').read_text()])


if __name__ == '__main__':
    unittest.main()
