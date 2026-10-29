"""Independent Linux proc transcripts; these controls never run Docker."""
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from TESTS.adapters.go import message_image_process as process


SERVER = 'b' * 64
HEADER4 = '  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode'
HEADER6 = HEADER4.replace('rem_address', 'remote_address')
# Fields 4..52, including PPID 0 and field 22 (starttime) 98765.
STAT = '1 (server) S ' + ' '.join(['0'] * 18 + ['98765'] + ['0'] * 30)
V4 = '  0: 00000000:1F40 00000000:0000 0A 00000000:00000000 00:00000000 00000000 10001 0 424242 1 0000000000000000 100 0 0 10 0'
V6 = '  0: 00000000000000000000000000000000:1F40 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 00000000 10001 0 424242 1 0000000000000000 100 0 0 10 0'


def sections(family='tcp6'):
    """Authored separately from collector/parsers, with realistic proc columns."""
    return {
        'stat_before': STAT, 'exe_before': '/app/server', 'exe_id_before': '2049:101',
        'sha_before': SERVER + '  /proc/1/exe',
        'fd_before': '0\t/dev/null\n1\tpipe:[123]\n2\tpipe:[124]\n3\tsocket:[424242]',
        'tcp_before': HEADER4 + ('\n' + V4 if family == 'tcp' else ''),
        'tcp6_before': HEADER6 + ('\n' + V6 if family == 'tcp6' else ''),
        'tcp_after': HEADER4 + ('\n' + V4 if family == 'tcp' else ''),
        'tcp6_after': HEADER6 + ('\n' + V6 if family == 'tcp6' else ''),
        'fd_after': '0\t/dev/null\n1\tpipe:[123]\n2\tpipe:[124]\n3\tsocket:[424242]',
        'sha_after': SERVER + '  /proc/1/exe', 'exe_id_after': '2049:101',
        'exe_after': '/app/server', 'stat_after': STAT,
    }


def transcript(values):
    return ''.join('@' + key + '\n' + value + '\n' for key, value in values.items()) + '@done\n'


class ProcessControls(unittest.TestCase):
    def make_guard(self, values=None):
        self.values = sections() if values is None else values
        self.identity = {'pid': 321, 'instance': 456, 'running': True}
        self.backend = Mock(side_effect=lambda: dict(self.identity))
        self.docker = Mock(side_effect=lambda *args, **kw:
            SimpleNamespace(returncode=0, stdout=transcript(self.values)))
        return process.executing_server_guard(self.backend, docker=self.docker,
            container='owned-server', expected_server_sha256=SERVER)

    def test_ipv4_and_ipv6_executing_server_and_exact_evidence(self):
        for family, address in [('tcp', '00000000'), ('tcp6', '0' * 32)]:
            with self.subTest(family=family):
                guard = self.make_guard(sections(family))
                self.assertEqual(guard(), self.identity)
                expected = {'pid': 1, 'starttime_ticks': 98765,
                    'executable': {'path': '/app/server', 'sha256': SERVER,
                                   'device': 2049, 'inode': 101},
                    'port': 8000, 'listeners': [{'family': family,
                        'local_address_hex': address, 'local_port': 8000,
                        'inode': 424242, 'fds': [3]}]}
                self.assertEqual(guard.evidence, expected)
                self.assertEqual(guard.probe(), expected)

    def test_regular_guard_calls_do_not_exec_and_evidence_is_defensive_copy(self):
        guard = self.make_guard()
        for _ in range(119):
            self.assertTrue(guard()['running'])
        self.docker.assert_called_once()
        evidence = guard.evidence
        evidence['executable']['sha256'] = 'f' * 64
        evidence['listeners'].clear()
        self.assertEqual(guard.probe()['executable']['sha256'], SERVER)
        self.assertEqual(self.docker.call_count, 2)
        args, kwargs = self.docker.call_args
        self.assertEqual(args[:5], ('exec', 'owned-server', 'sh', '-c', process.PROBE_SCRIPT))
        self.assertEqual(len(args), 5)
        self.assertEqual(kwargs, {'check': False, 'timeout': 10})
        self.assertNotIn('--user', args)
        self.assertNotIn(SERVER, process.PROBE_SCRIPT)
        self.assertNotIn('owned-server', process.PROBE_SCRIPT)

    def test_old_container_identity_guard_alone_misses_exec_of_another_binary(self):
        guard = self.make_guard()
        self.values['sha_before'] = self.values['sha_after'] = 'f' * 64 + '  /proc/1/exe'
        self.assertTrue(self.backend()['running'])  # Original identity is unchanged.
        self.assert_failure_latches(guard)

    def assert_failure_latches(self, guard):
        with self.assertRaisesRegex(AssertionError, '^image.executing-server$'):
            guard.probe()
        self.assertEqual(guard(), {'pid': 321, 'instance': 456, 'running': False})
        self.values = sections()
        with self.assertRaises(AssertionError):
            guard.probe()
        with self.assertRaises(AssertionError):
            _ = guard.evidence

    def test_wrong_process_socket_even_with_exact_executable_is_rejected(self):
        for change in ('foreign', 'missing', 'nonlisten', 'wrongport', 'extra-foreign'):
            values = sections()
            if change == 'foreign':
                values['fd_before'] = values['fd_after'] = '3\tsocket:[999999]'
            if change == 'missing':
                values['tcp6_before'] = values['tcp6_after'] = HEADER6
            if change == 'nonlisten':
                values['tcp6_before'] = values['tcp6_after'] = HEADER6 + '\n' + V6.replace(' 0A ', ' 01 ')
            if change == 'wrongport':
                values['tcp6_before'] = values['tcp6_after'] = HEADER6 + '\n' + V6.replace(':1F40', ':1F41')
            if change == 'extra-foreign':
                values['tcp_before'] = values['tcp_after'] = HEADER4 + '\n' + V4.replace('424242', '999999')
            with self.subTest(change=change), self.assertRaises(AssertionError):
                self.make_guard(values)

    def test_malformed_missing_or_racing_proc_evidence_fails_closed(self):
        mutations = [
            ('sha_before', 'x' * 64 + '  /proc/1/exe'),
            ('sha_after', 'f' * 64 + '  /proc/1/exe'),
            ('sha_before', SERVER + '  /app/server'),
            ('exe_before', '/app/worker'), ('exe_after', '/app/server (deleted)'),
            ('exe_id_after', '2049:102'), ('exe_id_before', 'malformed'),
            ('stat_before', STAT.replace('1 (server)', '2 (server)')),
            ('stat_after', STAT.replace('98765', '98766')), ('stat_before', '1 (server) S 0'),
            ('stat_before', STAT.replace(') S ', ') Z ')),
            ('fd_after', '3\tsocket:[999999]'), ('fd_after', '4\tsocket:[424242]'),
            ('fd_before', '3\tsocket:[broken]'), ('fd_before', '3\tsocket:[424242]\n3\t/dev/null'),
            ('tcp6_before', HEADER6 + '\n0: broken'), ('tcp_before', ''),
            ('tcp6_after', HEADER6),
            ('tcp6_before', HEADER6 + '\n' + V6 + '\n' + V6),
            ('tcp6_before', HEADER6 + '\n' + V6.replace(' 424242 ', ' nope ')),
        ]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                guard = self.make_guard()
                self.values[key] = value
                self.assert_failure_latches(guard)
        guard = self.make_guard()
        del self.values['stat_after']
        self.assert_failure_latches(guard)

    def test_backend_death_identity_change_or_exception_latches_without_more_exec(self):
        for key, value in [('pid', 322), ('instance', 457), ('running', False), ('running', 1)]:
            guard = self.make_guard()
            self.identity[key] = value
            with self.subTest(key=key, value=value):
                self.assertFalse(guard()['running'])
                self.docker.assert_called_once()
                self.identity = {'pid': 321, 'instance': 456, 'running': True}
                self.assertFalse(guard()['running'])
        guard = self.make_guard()
        self.backend.side_effect = PermissionError('private-value')
        self.assertFalse(guard()['running'])

    def test_backend_identity_race_during_exec_is_rejected(self):
        guard = self.make_guard()
        def raced(*args, **kwargs):
            self.identity['instance'] += 1
            return SimpleNamespace(returncode=0, stdout=transcript(self.values))
        self.docker.side_effect = raced
        self.assert_failure_latches(guard)

    def test_permission_timeout_bad_result_and_bounded_output_are_sanitized(self):
        for failure in [PermissionError('private-value'), RuntimeError('private-value'),
                        IndexError('private-value'),
                        subprocess.TimeoutExpired('private-value', 10)]:
            guard = self.make_guard()
            self.docker.side_effect = failure
            with self.subTest(failure=type(failure).__name__):
                self.assert_failure_latches(guard)
        for result in [SimpleNamespace(returncode=1, stdout='private-value'),
                       SimpleNamespace(returncode=0, stdout=''),
                       SimpleNamespace(returncode=0, stdout=b'not text'),
                       SimpleNamespace(returncode=0, stdout='x' * 1_200_001),
                       SimpleNamespace(returncode=0, stdout=transcript(sections()) + '@done\n')]:
            guard = self.make_guard()
            self.docker.side_effect = None
            self.docker.return_value = result
            self.assert_failure_latches(guard)

    def test_cancellation_propagates_and_invalidates_evidence(self):
        guard = self.make_guard()
        self.docker.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            guard.probe()
        self.assertFalse(guard()['running'])
        with self.assertRaises(AssertionError):
            _ = guard.evidence

    def test_nonlistener_fd_churn_and_proc_stat_comm_parentheses_are_supported(self):
        values = sections()
        values['stat_before'] = values['stat_after'] = STAT.replace('(server)', '(server (go))')
        values['fd_after'] += '\n9\tsocket:[555555]'
        self.assertTrue(self.make_guard(values)()['running'])

    def test_owned_dual_family_listeners_and_duplicate_descriptors(self):
        values = sections()
        for when in ('before', 'after'):
            values['tcp_' + when] += '\n' + V4.replace('424242', '424243')
            values['fd_' + when] += '\n5\tsocket:[424242]\n6\tsocket:[424243]'
        evidence = self.make_guard(values).evidence
        self.assertEqual([row['family'] for row in evidence['listeners']], ['tcp', 'tcp6'])
        self.assertEqual([row['fds'] for row in evidence['listeners']], [[6], [3, 5]])

    def test_replacement_between_explicit_probes_is_rejected(self):
        for key, old, new in [('stat', '98765', '98766'),
                              ('exe_id', '2049:101', '2049:102'),
                              ('tcp6', '424242', '424243')]:
            guard = self.make_guard()
            for when in ('before', 'after'):
                self.values[key + '_' + when] = self.values[key + '_' + when].replace(old, new)
                if key == 'tcp6':
                    self.values['fd_' + when] = self.values['fd_' + when].replace(old, new)
            with self.subTest(key=key):
                self.assert_failure_latches(guard)

    def test_section_truncation_at_newline_and_malformed_framing_are_rejected(self):
        for output in [
                transcript(sections()).replace('@tcp_before\n', '@tcp6_before\n', 1),
                transcript(sections()).replace('@done\n', ''),
                transcript(sections()).replace('/app/server', '/app/server\r', 1),
                transcript(sections()).replace('@tcp_before\n', '@tcp_before\n' + ' ' * 262144 + '\n', 1)]:
            guard = self.make_guard()
            self.docker.side_effect = None
            self.docker.return_value = SimpleNamespace(returncode=0, stdout=output)
            self.assert_failure_latches(guard)

    def test_backend_malformed_initial_protocol_rejected_without_exec(self):
        for value in [None, {}, {'pid': True, 'instance': 2, 'running': True},
                      {'pid': 1, 'instance': 0, 'running': True},
                      {'pid': 1, 'instance': 2, 'running': 1},
                      {'pid': 1, 'instance': 2, 'running': True, 'extra': 0}]:
            docker = Mock()
            with self.subTest(value=value), self.assertRaises(AssertionError):
                process.executing_server_guard(Mock(return_value=value), docker=docker,
                    container='owned', expected_server_sha256=SERVER)
            docker.assert_not_called()

    def test_bad_api_inputs_rejected_before_docker(self):
        for name in ('', '-foreign', 'name;echo injected', 'has space', None):
            docker = Mock()
            with self.subTest(name=name), self.assertRaises(ValueError):
                process.executing_server_guard(Mock(), docker=docker, container=name,
                                               expected_server_sha256=SERVER)
            docker.assert_not_called()
        for digest in ('', 'sha256:' + SERVER, 'F' * 64, None):
            docker = Mock()
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                process.executing_server_guard(Mock(), docker=docker, container='owned',
                                               expected_server_sha256=digest)
            docker.assert_not_called()


if __name__ == '__main__':
    unittest.main()
