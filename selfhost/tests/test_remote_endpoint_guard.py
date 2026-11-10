"""Remote copy storage addresses: the endpoint as written, the resolved addresses and the pinned connection."""
import importlib.util
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest import mock

UPDATER = Path(__file__).resolve().parents[1] / 'updater'
sys.path[:0] = [str(UPDATER), str(UPDATER.parents[1] / 'BE/instance')]
import endpoint_guard as guard  # noqa: E402

spec = importlib.util.spec_from_file_location('remote_backups', UPDATER / 'remote_backups.py')
remote = importlib.util.module_from_spec(spec)
spec.loader.exec_module(remote)

try:
    import boto3  # noqa: F401
    import urllib3.util.connection  # noqa: F401
except ImportError:  # pragma: no cover - the runner image has py3-boto3
    boto3 = None


def resolver(*addresses):
    """getaddrinfo answering with fixed addresses; records each lookup."""
    calls = []

    def resolve(host, port, type=0):
        calls.append(host)
        out = []
        for address in addresses:
            family = socket.AF_INET6 if ':' in address else socket.AF_INET
            out.append((family, socket.SOCK_STREAM, 6, '', (address, port, 0, 0) if family == socket.AF_INET6 else (address, port)))
        return out
    resolve.calls = calls
    return resolve


class EndpointAsWrittenTests(unittest.TestCase):
    def test_same_address_rules_as_the_owner_page(self):
        for value in ('', 'https://s3.example.test', 'https://192.168.1.20:9000', 'http://minio.lan:9000', 'https://[2001:db8::5]',
                      'https://10.0.0.5/base', 'https://[::ffff:192.168.1.20]'):
            self.assertIsNone(guard.endpoint_problem(value), value)
        for value in ('https://169.254.169.254', 'https://127.0.0.1:9000', 'https://localhost', 'https://api.localhost.',
                      'https://metadata.google.internal', 'https://[::1]', 'https://[fd00:ec2::254]', 'https://[::ffff:169.254.169.254]',
                      'https://0.0.0.0', 'https://224.0.0.1', 'https://100.100.100.200', 'http://169.254.170.2/v2'):
            self.assertEqual(guard.endpoint_problem(value), guard.BLOCKED_MESSAGE, value)
        for value in ('https://2852039166', 'https://0xa9fea9fe', 'https://user:x@s3.example.test', 'ftp://s3.example.test',
                      'https://s3.example.test:0', 'https://[fe80::1%25en0]'):
            self.assertEqual(guard.endpoint_problem(value), guard.INVALID_MESSAGE, value)


class ResolvedAddressTests(unittest.TestCase):
    def test_a_public_name_resolving_to_the_instance_address_is_refused(self):
        with self.assertRaises(guard.BlockedAddress):
            guard.vetted_addresses('s3.example.test', 443, resolver('169.254.169.254'))

    def test_one_refused_address_refuses_the_name(self):
        for addresses in (('203.0.113.10', '127.0.0.1'), ('::ffff:169.254.169.254',), ('fd00:ec2::254',), ('0.0.0.0',),
                          ('fe80::1%eth0',), ('64:ff9b::a9fe:a9fe',), ('239.1.1.1',)):
            with self.assertRaises(guard.BlockedAddress, msg=addresses):
                guard.vetted_addresses('s3.example.test', 443, resolver(*addresses))

    def test_private_and_public_addresses_are_allowed(self):
        self.assertEqual(guard.vetted_addresses('minio.lan', 9000, resolver('192.168.1.20', '192.168.1.20')), ['192.168.1.20'])
        self.assertEqual(guard.vetted_addresses('s3.example.test', 443, resolver('203.0.113.10', '2001:db8::5')),
                         ['203.0.113.10', '2001:db8::5'])

    def test_names_of_this_machine_are_refused_without_a_lookup(self):
        resolve = resolver('203.0.113.10')
        for name in ('localhost', 'LOCALHOST.', 'minio.localhost', 'metadata'):
            with self.assertRaises(guard.BlockedAddress):
                guard.vetted_addresses(name, 443, resolve)
        self.assertEqual(resolve.calls, [])


class SavedEndpointTests(unittest.TestCase):
    def test_a_saved_endpoint_is_checked_again_when_used(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.updater').mkdir()
            (root / remote.CONFIG_FILE).write_text(json.dumps({
                'endpoint': 'https://169.254.169.254', 'bucket': 'copie', 'access_key': 'AKIAFIXTURE', 'secret_key': 'placeholder'}))
            result = remote.test_connection(root, {})
        self.assertEqual(result, {'ok': False, 'message': guard.BLOCKED_MESSAGE})
        with self.assertRaises(remote.RemoteError):
            remote.make_client({'endpoint': 'https://localhost:9000', 'access_key': 'a', 'secret_key': 'b'})


@unittest.skipIf(boto3 is None, 'boto3 is not installed')
class PinnedConnectionTests(unittest.TestCase):
    CONFIG = {'endpoint': 'https://s3.moving.example.test', 'region': 'eu-south-1', 'access_key': 'AKIAFIXTURE',
              'secret_key': 'placeholder', 'path_style': True}

    def call(self, resolve):
        """One request through a real boto3 client; returns the addresses it tried to connect to."""
        connected = []

        def create_connection(address, *args, **kwargs):
            connected.append(address)
            raise ConnectionRefusedError('stub: no storage here')
        client = remote.make_client(self.CONFIG, quick=True)
        with mock.patch.object(guard._Vetted, 'resolve', staticmethod(resolve)), \
                mock.patch('urllib3.util.connection.create_connection', side_effect=create_connection):
            try:
                client.list_objects_v2(Bucket='copie')
            except Exception as exc:
                return connected, remote.plain_error(exc)
        return connected, None

    def test_a_name_resolving_to_the_instance_address_is_never_connected(self):
        connected, message = self.call(resolver('169.254.169.254'))
        self.assertEqual((connected, message), ([], guard.BLOCKED_MESSAGE))

    def test_each_connection_goes_to_the_address_checked_for_it(self):
        answers = iter([['203.0.113.10'], ['169.254.169.254'], ['169.254.169.254']])
        lookups = []

        def changing_answers(host, port, type=0):
            lookups.append(host)
            return resolver(*next(answers))(host, port, type)
        connected, message = self.call(changing_answers)
        # The first connection uses the address it checked; when the name later
        # answers with the instance address, the retry is refused, never connected.
        self.assertEqual(connected, [('203.0.113.10', 443)])
        self.assertEqual(message, guard.BLOCKED_MESSAGE)
        self.assertTrue(all(host == 's3.moving.example.test' for host in lookups))

    def test_a_private_storage_is_connected_by_its_checked_address(self):
        connected, message = self.call(resolver('192.168.1.20'))
        self.assertEqual(set(connected), {('192.168.1.20', 443)})
        self.assertEqual(message, 'Storage non raggiungibile: controlla endpoint e rete.')

if __name__ == '__main__':
    unittest.main()
