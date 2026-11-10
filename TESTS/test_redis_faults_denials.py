import unittest

from TESTS.adapters.redis_faults import _denials


class FakeClient:
    def __init__(self, entries):
        self.entries = entries

    def acl_log(self, count):
        return self.entries[:count]


def entry(command, count, client_id=7, username='default', reason='command'):
    return {'username': username, 'reason': reason, 'object': command, 'count': count,
            'client-info': {'id': client_id}}


class DenialCountTests(unittest.TestCase):
    def test_new_entry_is_counted_beside_an_older_entry_of_the_same_client(self):
        # Before Redis 7.2 entries have no id; a denial after the merge window
        # adds a second entry for the same client.
        before = _denials(FakeClient([entry('set', 5)]), 'SET')
        after = _denials(FakeClient([entry('set', 1), entry('set', 5)]), 'SET')
        self.assertEqual(after - before, 1)

    def test_only_default_user_command_denials_of_the_requested_command_count(self):
        client = FakeClient([entry('get', 2), entry('set', 3, username='other'),
                             entry('set', 4, reason='auth'), entry('SET', 1)])
        self.assertEqual(_denials(client, 'set'), 1)


if __name__ == '__main__':
    unittest.main()
