"""Offline validation of the parent-only, explicitly selected Docker endpoint."""
import errno
import os
from pathlib import Path
import socket
import stat
import tempfile
import traceback
import unittest
from unittest.mock import patch

from TESTS.adapters.go import local_docker_endpoint as endpoint


class LocalDockerEnvironmentTests(unittest.TestCase):
    def assert_code(self, path, code):
        with self.assertRaises(ValueError) as caught:
            endpoint.local_docker_environment({}, path)
        self.assertEqual(caught.exception.args, (code,))
        self.assertIsNone(caught.exception.__context__)
        return caught.exception

    def test_default_removes_endpoint_tls_and_proxy_overrides(self):
        overrides = (
            'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY',
            'DOCKER_CERT_PATH', 'DOCKER_TLS_CERTDIR', 'DOCKER_SSH_COMMAND',
            'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
            'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy',
            'DOCKER_HTTP_PROXY', 'DOCKER_HTTPS_PROXY', 'DOCKER_NO_PROXY',
        )
        original = {key: 'synthetic-private-value' for key in overrides}
        original.update(DOCKER_CONFIG='/synthetic/private-config', PATH='/tools',
                        DOCKER_API_VERSION='1.45', HOME='/synthetic/private-home')
        before = original.copy()
        result = endpoint.local_docker_environment(original, None)
        self.assertEqual(result, {
            'DOCKER_CONFIG': '/synthetic/private-config', 'PATH': '/tools',
            'DOCKER_API_VERSION': '1.45', 'HOME': '/synthetic/private-home',
        })
        self.assertEqual(original, before)
        self.assertIsNot(result, original)

    def test_default_does_not_read_ambient_environment_or_filesystem(self):
        with patch.dict(os.environ, {
            'DOCKER_HOST': 'tcp://synthetic-secret:2375',
            'DOCKER_CONTEXT': 'synthetic-ambient-context',
            'DOCKER_CONFIG': '/synthetic/ambient-config',
            'HTTPS_PROXY': 'synthetic-ambient-secret',
        }, clear=True), patch.object(endpoint.os.path, 'realpath') as resolve, \
                patch.object(endpoint.os, 'stat') as inspect:
            self.assertEqual(endpoint.local_docker_environment({}, None), {})
            resolve.assert_not_called()
            inspect.assert_not_called()

    def test_rejects_relative_transport_control_character_and_non_string_inputs(self):
        cases = (
            '', '.', 'docker.sock', '~/docker.sock', '../docker.sock',
            'unix:///tmp/docker.sock', 'tcp://localhost:2375',
            'ssh://user@host', 'http://localhost:2375', 'https://localhost:2376',
            'npipe:////./pipe/docker_engine', '//host/docker.sock',
            '/tmp/synthetic-secret\x00.sock', '/tmp/synthetic-secret\n.sock',
            '/tmp/synthetic-secret\r.sock', 42, b'/tmp/docker.sock',
            Path('/tmp/docker.sock'),
        )
        for path in cases:
            with self.subTest(path=path), \
                    patch.object(endpoint.os.path, 'realpath') as resolve, \
                    patch.object(endpoint.os, 'stat') as inspect:
                self.assert_code(path, 'INVALID_DOCKER_SOCKET_PATH')
                resolve.assert_not_called()
                inspect.assert_not_called()

    def test_missing_path_and_dangling_symlink_are_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / 'missing.sock'
            link = Path(directory) / 'dangling.sock'
            link.symlink_to(missing)
            for path in (missing, link):
                with self.subTest(kind=path.name):
                    self.assert_code(str(path), 'DOCKER_SOCKET_UNAVAILABLE')

    def test_regular_file_directory_and_symlink_to_file_are_not_sockets(self):
        with tempfile.TemporaryDirectory() as directory:
            regular = Path(directory) / 'regular'
            regular.touch()
            link = Path(directory) / 'linked'
            link.symlink_to(regular)
            for path in (regular, Path(directory), link):
                with self.subTest(kind=path.name):
                    self.assert_code(str(path), 'DOCKER_SOCKET_NOT_SOCKET')

    def test_symlink_loop_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / 'first', Path(directory) / 'second'
            first.symlink_to(second)
            second.symlink_to(first)
            self.assert_code(str(first), 'DOCKER_SOCKET_UNAVAILABLE')

    def test_other_filesystem_types_are_not_sockets(self):
        for mode in (stat.S_IFIFO, stat.S_IFCHR, stat.S_IFBLK):
            with self.subTest(mode=mode), \
                    patch.object(endpoint.os.path, 'realpath', return_value='/synthetic/socket'), \
                    patch.object(endpoint.os, 'stat', return_value=os.stat_result(
                        (mode | 0o600, 0, 0, 0, 0, 0, 0, 0, 0, 0))):
                self.assert_code('/synthetic/socket', 'DOCKER_SOCKET_NOT_SOCKET')

    def test_stat_boundary_accepts_socket_and_resolves_normal_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / 'socket'
            target.touch()  # The socket file type is supplied at the stat boundary.
            alias = root / 'alias'
            alias.symlink_to('socket')
            parent_alias = root / 'parent-alias'
            parent_alias.symlink_to(root, target_is_directory=True)
            for path in (target, alias, parent_alias / 'socket'):
                with self.subTest(kind=path.name), patch.object(
                    endpoint.os, 'stat', return_value=os.stat_result(
                        (stat.S_IFSOCK | 0o600, 0, 0, 0, 0, 0, 0, 0, 0, 0))
                ) as inspect:
                    original = {
                        'DOCKER_HOST': 'ssh://synthetic-secret',
                        'DOCKER_CONTEXT': 'ambient', 'DOCKER_TLS_VERIFY': '1',
                        'https_proxy': 'synthetic-secret',
                        'DOCKER_CONFIG': '/synthetic/private-config', 'PATH': '/tools',
                    }
                    before = original.copy()
                    result = endpoint.local_docker_environment(original, str(path))
                    self.assertEqual(result, {
                        'DOCKER_HOST': 'unix://' + str(target),
                        'DOCKER_CONFIG': '/synthetic/private-config', 'PATH': '/tools',
                    })
                    self.assertEqual(original, before)
                    self.assertIsNot(result, original)
                    inspect.assert_called_once_with(str(target))

    def test_filesystem_failures_never_echo_path_or_underlying_error(self):
        secret = 'synthetic-sensitive-marker'
        failures = (
            PermissionError(errno.EACCES, secret, '/synthetic/' + secret),
            OSError(errno.ELOOP, secret, '/synthetic/' + secret),
            RuntimeError(secret),
            UnicodeEncodeError('utf-8', secret, 0, 1, secret),
        )
        for boundary in ('resolve', 'stat'):
            for failure in failures:
                with self.subTest(boundary=boundary, failure=type(failure).__name__), \
                        patch.object(endpoint.os.path, 'realpath',
                                     return_value='/synthetic/' + secret,
                                     side_effect=failure if boundary == 'resolve' else None), \
                        patch.object(endpoint.os, 'stat',
                                     side_effect=failure if boundary == 'stat' else None):
                    error = self.assert_code('/synthetic/' + secret,
                                             'DOCKER_SOCKET_UNAVAILABLE')
                    self.assertNotIn(secret, ''.join(traceback.format_exception(error)))

    def test_resolved_control_character_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'synthetic-secret\n.sock'
            target.touch()
            alias = Path(directory) / 'alias.sock'
            alias.symlink_to(target)
            self.assert_code(str(alias), 'INVALID_DOCKER_SOCKET_PATH')

    @unittest.skipUnless(hasattr(socket, 'AF_UNIX'), 'AF_UNIX unavailable')
    def test_real_bound_unix_socket_and_its_symlink(self):
        # No listener, connection or daemon: bind only creates a temporary socket inode.
        with tempfile.TemporaryDirectory(prefix='de-', dir='/tmp') as directory:
            target = Path(directory).resolve() / 'socket'
            alias = target.with_name('alias')
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as bound:
                try:
                    bound.bind(str(target))
                except OSError as error:
                    if error.errno in (errno.EACCES, errno.EPERM):
                        self.skipTest('sandbox denies temporary AF_UNIX bind; mocked stat covered')
                    raise
                alias.symlink_to('socket')
                for path in (target, alias):
                    with self.subTest(kind=path.name):
                        self.assertEqual(endpoint.local_docker_environment({}, str(path)),
                                         {'DOCKER_HOST': 'unix://' + str(target)})


if __name__ == '__main__':
    unittest.main()
