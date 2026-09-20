"""Quality automation selection, host routing and evidence contracts."""
import json
import os
from pathlib import Path
import tempfile
import socket
import subprocess
import threading
import unittest
from unittest.mock import patch

import quality_support
import select_rehearsal


class QualityTests(unittest.TestCase):
    def test_readiness_rejects_a_server_that_accepts_but_never_responds(self):
        root = Path(__file__).resolve().parents[2]
        # Exercise the real lifecycle functions without dispatching a command
        # against an installation. The HTTP peer accepts and then stalls.
        script = (root / 'selfhost/bin/assozeta').read_text().split('\ncommand=${1:-help}\n')[0]
        script += '\nREADINESS_ATTEMPTS=1\nREADINESS_DELAY=1\nwait_url_ready web "$1"\n'
        received = threading.Event()
        release = threading.Event()
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            listener.settimeout(20)
            def stalled_peer():
                with listener.accept()[0] as connection:
                    connection.recv(8192)
                    received.set()
                    release.wait(20)
            peer = threading.Thread(target=stalled_peer, daemon=True)
            peer.start()
            try:
                result = subprocess.run(['sh', '-c', script, 'readiness-test',
                    f'http://127.0.0.1:{listener.getsockname()[1]}/healthz'],
                    env={**os.environ, 'ASSOZETA_INSTALL_ROOT': str(root / 'selfhost'),
                        'NO_PROXY': '127.0.0.1', 'no_proxy': '127.0.0.1'},
                    capture_output=True, text=True, timeout=20)
                self.assertTrue(received.is_set(), 'The readiness request never reached the test server')
                self.assertEqual(result.returncode, 1)
                self.assertIn('readiness check failed after 1 attempts', result.stdout)
            finally:
                release.set()
                peer.join(timeout=2)

    def test_linux_uses_docker_gateway_instead_of_desktop_dns(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(quality_support.sys, 'platform', 'linux'), patch.object(
                quality_support.subprocess, 'check_output', return_value='[{"IPAM":{"Config":[{"Gateway":"172.17.0.1"}]}}]'):
            self.assertEqual(quality_support.docker_host(), '172.17.0.1')

    def test_desktop_remains_supported(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(quality_support.sys, 'platform', 'darwin'):
            self.assertEqual(quality_support.docker_host(), 'host.docker.internal')

    def test_host_override_cannot_inject_seed_code_or_shell_arguments(self):
        for host in ('host;touch /tmp/x', "host'", 'http://host', 'host\nother', '../host'):
            with self.subTest(host=host), patch.dict(os.environ, {'ASSOZETA_TEST_HOST': host}):
                with self.assertRaises(ValueError):
                    quality_support.docker_host()

    def test_manual_versions_and_revision_are_strict(self):
        commit = 'a' * 40
        self.assertEqual(select_rehearsal.select('v1.0.2', '2.0.1', commit), ('v1.0.2', '2.0.1', commit))
        for source, target, revision in [('main', '2.0.1', commit), ('2.0.1', '1.0.2', commit),
                                         ('1.0.2', '1.0.2', commit), ('1.0.2', '2.0.1', 'HEAD'),
                                         ('1.0.2\ninjected=value', '2.0.1', commit)]:
            with self.subTest(source=source, target=target, revision=revision), self.assertRaises(ValueError):
                select_rehearsal.select(source, target, revision)

    def test_automatic_selection_binds_commit_and_requires_source_bundle(self):
        commit = 'a' * 40
        releases = [dict(id=i, tag=f'v1.0.{i}', artifacts_ready=i > 2) for i in (4, 3, 2, 1)]
        def fetch(url):
            if '/commits/' in url:
                return {'sha': commit if url.endswith('v1.0.3') else 'b' * 40}
            if url.endswith('/2'):
                return {'assets': []}
            if url.endswith('/1'):
                return {'assets': [{'name': 'assozeta-selfhost-v1.0.1.tar.gz', 'state': 'uploaded', 'size': 100}]}
            raise AssertionError(url)
        with patch.object(select_rehearsal, 'fetch_releases', return_value=releases), patch.object(select_rehearsal, 'read_json', side_effect=fetch):
            self.assertEqual(select_rehearsal.select('', '', commit), ('v1.0.1', 'v1.0.3', commit))

    def test_no_complete_matching_release_fails_closed(self):
        with patch.object(select_rehearsal, 'fetch_releases', return_value=[{'tag': 'v1.0.3', 'artifacts_ready': False}]):
            with self.assertRaisesRegex(ValueError, 'No complete stable release'):
                select_rehearsal.select('', '', 'a' * 40)

    def test_cleanup_failure_prevents_success_evidence(self):
        with patch.object(quality_support.subprocess, 'check_output', return_value='remaining-container\n'):
            with self.assertRaisesRegex(RuntimeError, 'containers remain'):
                quality_support.assert_project_removed('assozeta-quality-test')
        with patch.object(quality_support.subprocess, 'check_output', side_effect=['', '', 'remaining-volume\n']):
            with self.assertRaisesRegex(RuntimeError, 'volumes remain'):
                quality_support.assert_project_removed('assozeta-quality-test')

    def test_evidence_reports_dirty_tree_instead_of_claiming_exact_commit(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'ASSOZETA_QUALITY_REPORT_DIR': directory}), patch.object(
                quality_support.subprocess, 'check_output', side_effect=['a' * 40 + '\n', ' M changed.py\n']):
            quality_support.write_evidence('legacy-upgrade', {'browser': False, 'images': {'web': 'sha256:123'}})
            result = json.loads((Path(directory) / 'legacy-upgrade.json').read_text())
            self.assertFalse(result['working_tree_clean'])
            self.assertFalse(result['browser'])
            self.assertEqual(result['commit'], 'a' * 40)
            self.assertNotIn('environment', result)


if __name__ == '__main__':
    unittest.main()
