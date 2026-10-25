"""Exercise the browser checkpoint protocol with real files and a child process."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

from TESTS.adapters.django.collaborator_list_browser import (
    CHECKPOINTS, DjangoObserver, assert_server_binary_binding, drive_browser,
    require_server_digest,
)


class CheckpointProtocolTests(unittest.TestCase):
    def test_real_child_completes_all_ordered_checkpoints(self):
        child = """
import json, pathlib, sys, time
root=pathlib.Path(sys.argv[1])
for number, (actor, action) in enumerate(json.loads(sys.argv[2]), 1):
    pending=root/f'{number}.pending'
    pending.write_text(json.dumps(dict(id=number, actor=actor, action=action)))
    pending.rename(root/f'{number}.json')
    ack=root/f'{number}.ack.json'
    deadline=time.monotonic()+10
    while not ack.exists():
        assert time.monotonic()<deadline
        time.sleep(.01)
    assert json.loads(ack.read_text())['success'] is True
"""
        with tempfile.TemporaryDirectory(prefix='az-browser-checkpoints-') as directory:
            root=Path(directory)
            observed=[]
            process=subprocess.Popen([sys.executable, '-c', child, str(root), json.dumps(CHECKPOINTS)])
            try:
                def checkpoint(value):
                    observed.append((value['actor'], value['action']))
                    return {'verified': True}
                self.assertEqual(drive_browser(process, root, checkpoint, timeout=15), 11)
                self.assertEqual(process.wait(timeout=5), 0)
                self.assertEqual(tuple(observed), CHECKPOINTS)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)

    def test_early_child_exit_is_not_success(self):
        with tempfile.TemporaryDirectory(prefix='az-browser-checkpoints-') as directory:
            process=subprocess.Popen([sys.executable, '-c', 'pass'])
            process.wait(timeout=5)
            with self.assertRaisesRegex(AssertionError, 'before all checkpoints'):
                drive_browser(process, Path(directory), lambda value: {}, timeout=1)


class CheckpointRejectionTests(unittest.TestCase):
    def test_out_of_order_checkpoint_does_not_run_callback(self):
        with tempfile.TemporaryDirectory(prefix='az-browser-checkpoints-') as directory:
            root = Path(directory)
            (root / '1.json').write_text(json.dumps(
                {'id': 1, 'actor': 'restricted', 'action': 'login'}))
            callback = Mock()
            with self.assertRaisesRegex(AssertionError, 'sequence differs'):
                drive_browser(Mock(poll=lambda: None), root, callback, timeout=1)
            callback.assert_not_called()
            self.assertFalse((root / '1.ack.json').exists())

    def test_failed_durable_check_writes_negative_ack(self):
        with tempfile.TemporaryDirectory(prefix='az-browser-checkpoints-') as directory:
            root = Path(directory)
            (root / '1.json').write_text(json.dumps(
                {'id': 1, 'actor': 'owner', 'action': 'login'}))
            callback = Mock(side_effect=AssertionError('state changed'))
            with self.assertRaisesRegex(AssertionError, 'state changed'):
                drive_browser(Mock(poll=lambda: None), root, callback, timeout=1)
            self.assertEqual(json.loads((root / '1.ack.json').read_text()),
                             {'success': False, 'error_type': 'AssertionError'})


class ObserverRouteTests(unittest.TestCase):
    def test_only_explicit_websocket_dependencies_are_accepted(self):
        async def application(scope, receive, send):
            await send({'type': 'websocket.accept'})

        async def sink(message):
            pass

        async def exercise():
            observer = DjangoObserver(application)
            observer.begin('allowed')
            for path in ('/ws/health/', '/ws/notifications/'):
                await observer({'type': 'websocket', 'path': path}, None, sink)
            observer.assert_no_unknown('allowed')
            observer.begin('unknown')
            await observer({'type': 'websocket', 'path': '/ws/unlisted/'}, None, sink)
            with self.assertRaisesRegex(AssertionError, 'Unlisted Django dependency'):
                observer.assert_no_unknown('unknown')
        asyncio.run(exercise())


class ServerBinaryBindingTests(unittest.TestCase):
    def test_receipt_requires_exact_sha256_before_service_setup(self):
        digest = 'a' * 64
        self.assertEqual(require_server_digest(digest), digest)
        for invalid in (None, '', 'a' * 63, 'a' * 65, 'A' * 64, 'sha256:' + digest, 123):
            with self.subTest(invalid=invalid), self.assertRaises(AssertionError):
                require_server_digest(invalid)

    def test_running_server_must_match_receipt_digest(self):
        expected = 'a' * 64
        runtime = {'metadata': {'server_sha256': expected}}
        self.assertEqual(assert_server_binary_binding(runtime, expected), expected)
        for altered in ({}, {'metadata': {}},
                        {'metadata': {'server_sha256': 'invalid'}},
                        {'metadata': {'server_sha256': 'b' * 64}}):
            with self.subTest(runtime=altered), self.assertRaises(AssertionError):
                assert_server_binary_binding(altered, expected)
