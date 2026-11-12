"""Offline fixture controls; these tests do not prove an actual historical import."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from TESTS.fixtures import historical_message_mutations as fixture


BASE = Path(__file__).parent / 'fixtures' / 'bakney-v1.json'
MESSAGE = 'data/52_messages.json'
DELIVERY = 'data/53_message_transactions.json'
BASE_SHA = '0d0f5d58946de1306d93476261a6dc6fe09cb56c3b5e23b4d3dcd2c4e75f6713'


class HistoricalMessageFixtureTests(unittest.TestCase):
    def setUp(self):
        self.contract = fixture.load_contract()

    def records(self, contract, section):
        return next(op['records'] for op in contract['archive_overlay']['operations']
                    if op['section'] == section)

    def reject(self, change, pattern):
        contract = deepcopy(self.contract)
        change(contract)
        # Re-sign the overlay so these cases exercise semantic validation first.
        contract['overlay_sha256'] = hashlib.sha256(
            fixture.canonical_bytes(contract['archive_overlay'])).hexdigest()
        with self.assertRaisesRegex(fixture.FixtureError, pattern):
            fixture.validate_contract(contract)

    def test_base_bytes_and_all_records_are_immutable(self):
        before = BASE.read_bytes()
        base = json.loads(before)
        archive = fixture.derived_archive()
        self.assertEqual(len(before), 15420)
        self.assertEqual(hashlib.sha256(before).hexdigest(), BASE_SHA)
        self.assertEqual(set(archive), set(base))
        self.assertEqual(set(archive['entries']), set(base['entries']))
        for section, rows in base['entries'].items():
            if section == 'manifest.json':
                continue
            self.assertEqual(archive['entries'][section][:len(rows)], rows)
            if section not in (MESSAGE, DELIVERY):
                self.assertEqual(archive['entries'][section], rows)
        self.assertEqual(archive['text_files'], base['text_files'])
        self.assertEqual(archive['ids'], base['ids'])
        before_manifest = deepcopy(base['entries']['manifest.json'])
        after_manifest = deepcopy(archive['entries']['manifest.json'])
        for manifest in (before_manifest, after_manifest):
            for spec in manifest['models_exported']:
                spec.pop('count')
        self.assertEqual(after_manifest, before_manifest)
        self.assertEqual(BASE.read_bytes(), before)

    def test_complete_manifest_and_exact_skip_counts(self):
        archive = fixture.derived_archive()
        specs = archive['entries']['manifest.json']['models_exported']
        self.assertEqual(len(specs), 67)
        self.assertEqual(len(archive['entries']), 68)
        self.assertEqual(len({s['name'] for s in specs}), 67)
        self.assertEqual(len({s['file'] for s in specs}), 67)
        self.assertEqual(set(self.contract['expected_counts']), {s['name'] for s in specs})
        for spec in specs:
            self.assertEqual(spec['count'], len(archive['entries'][spec['file']]))
            self.assertEqual(spec['count'], self.contract['expected_counts'][spec['name']])
        counts = {s['name']: s['count'] for s in specs}
        self.assertEqual((counts['Message'], counts['MessageTransaction']), (4, 5))
        self.assertEqual((counts['SportAssociation'], counts['User']), (1, 1))
        self.assertEqual(self.contract['retired_policy'], {
            'explicit_skip_records': {'BillingPlan': 1, 'PreviewAndCustomFeatures': 1}})

    def test_independently_authored_values_and_children(self):
        rows = {r['message_id']: r for r in fixture.derived_archive()['entries'][MESSAGE]}
        ids = self.contract['ids']
        self.assertEqual(rows[ids['retained_email']], {
            'message_id': ids['retained_email'], 'sport_association_id': ids['association'],
            'type': 'EMAIL', 'message': 'Avviso <storico> & "salvato" — attività',
            'subject': '  Stagione <2026> & "quote"  ',
            'created_at': '2026-08-01T09:00:00+00:00'})
        self.assertEqual(rows[ids['retained_post']]['type'], 'INSIDE_APP')
        self.assertIsNone(rows[ids['retained_post']]['subject'])
        self.assertIsNone(rows[ids['retained_post']]['created_at'])
        self.assertEqual(rows[ids['retained_sms']]['type'], 'SMS')
        self.assertEqual(rows[ids['retained_sms']]['subject'], '')
        self.assertEqual(rows[ids['retained_sms']]['message'], '')
        self.assertTrue(all(isinstance(r['message'], str) for r in rows.values()))
        children = fixture.derived_archive()['entries'][DELIVERY]
        target = [r for r in children if r['message_id'] == ids['delete_email']]
        self.assertEqual({r['message_transaction_id'] for r in target},
                         {ids['delete_delivered'], ids['delete_bounced']})
        self.assertEqual(len(target), 2)
        self.assertEqual({r['message_id'] for r in children}, set(rows))
        post = next(r for r in children if r['message_id'] == ids['retained_post'])
        self.assertEqual(post['recipient'], ids['association'])
        self.assertNotIn('created_at', post)
        bounced = next(r for r in target if r['bounced_on'])
        self.assertEqual((bounced['bounced_reason'], bounced['bounced_type'], bounced['bounced_status']),
                         ('Synthetic mailbox unavailable', 'Permanent', '5.1.1'))
        self.assertIsNone(bounced['delivered_on'])
        delivered = next(r for r in target if r['delivered_on'])
        self.assertEqual(delivered['bounced_reason'], '')
        self.assertIsNone(delivered['bounced_type'])

    def test_expected_before_and_after_delete_are_explicit_and_fresh(self):
        initial = fixture.expected_projection()
        self.assertEqual(initial['acceptance_scope'], 'current-importer-characterization')
        self.assertIs(initial['historical_timestamp_preservation_verified'], False)
        self.assertEqual(initial['timestamp_preservation_requirement'], 'exact-source-values')
        after = fixture.expected_projection('after_delete')
        ids = self.contract['ids']
        self.assertEqual(initial['counts'], {'Message': 4, 'MessageTransaction': 5})
        self.assertEqual(after['counts'], {'Message': 3, 'MessageTransaction': 3})
        self.assertEqual(initial['timestamp_policy'], {
            'Message.created_at': 'import_window',
            'MessageTransaction.sent_on': 'import_window',
            'foreign_sentinel': 'parent_setup_baseline'})
        self.assertEqual(after['timestamp_policy'], {
            'Message.created_at': 'unchanged_from_import',
            'MessageTransaction.sent_on': 'unchanged_from_import',
            'foreign_sentinel': 'parent_setup_baseline'})
        self.assertEqual(initial['messages'], fixture.derived_archive()['entries'][MESSAGE])
        self.assertEqual(initial['deliveries'], fixture.derived_archive()['entries'][DELIVERY])
        self.assertEqual({r['message_id'] for r in initial['messages']} -
                         {r['message_id'] for r in after['messages']}, {ids['delete_email']})
        self.assertEqual({r['message_transaction_id'] for r in initial['deliveries']} -
                         {r['message_transaction_id'] for r in after['deliveries']},
                         {ids['delete_delivered'], ids['delete_bounced']})
        self.assertEqual(after['messages'], [r for r in initial['messages']
                         if r['message_id'] != ids['delete_email']])
        initial['messages'].clear()
        self.assertEqual(len(fixture.expected_projection()['messages']), 4)
        with self.assertRaisesRegex(fixture.FixtureError, 'stage'):
            fixture.expected_projection('unknown')

    def test_foreign_sentinel_is_complete_separate_graph(self):
        sentinel = fixture.foreign_sentinel()
        ids = self.contract['ids']
        self.assertEqual(set(sentinel), {'User', 'SportAssociation', 'Message', 'MessageTransaction'})
        self.assertTrue(all(len(rows) == 1 for rows in sentinel.values()))
        owner, association, message, child = (sentinel[k][0] for k in
            ('User', 'SportAssociation', 'Message', 'MessageTransaction'))
        self.assertEqual(owner['user_id'], ids['foreign_owner'])
        self.assertEqual(owner['role'], 1)
        self.assertEqual(association['user_id'], owner['user_id'])
        self.assertEqual(association['sport_association_id'], ids['foreign_association'])
        self.assertEqual(message['sport_association_id'], association['sport_association_id'])
        self.assertEqual(child['message_id'], message['message_id'])
        self.assertEqual((message['message_id'], child['message_transaction_id']),
                         (ids['foreign_message'], ids['foreign_delivery']))
        initial = fixture.expected_projection()
        self.assertEqual(initial['foreign_sentinel'], sentinel)
        self.assertEqual(initial['combined_counts'], {'Message': 5, 'MessageTransaction': 6})
        self.assertEqual(fixture.expected_projection('after_delete')['foreign_sentinel'], sentinel)
        self.assertEqual(fixture.expected_projection('after_delete')['combined_counts'],
                         {'Message': 4, 'MessageTransaction': 4})
        self.assertNotIn(ids['foreign_message'],
                         {r['message_id'] for r in initial['messages']})
        sentinel['Message'].clear()
        self.assertEqual(len(fixture.foreign_sentinel()['Message']), 1)

    def test_deterministic_zip_metadata_and_complete_contents(self):
        raw = fixture.archive_bytes()
        self.assertEqual(raw, fixture.archive_bytes(fixture.derived_archive()))
        self.assertEqual(hashlib.sha256(raw).hexdigest(), self.contract['derived_archive_sha256'])
        archive = fixture.derived_archive()
        with zipfile.ZipFile(io.BytesIO(raw)) as zipped:
            self.assertEqual(len(zipped.namelist()), 69)
            self.assertEqual(zipped.namelist(), [*archive['entries'], *archive['text_files']])
            for info in zipped.infolist():
                self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
                self.assertEqual(info.create_system, 3)
                self.assertEqual(info.external_attr >> 16, 0o100644)
                self.assertEqual((info.extra, info.comment, info.flag_bits), (b'', b'', 0))
            for section, value in archive['entries'].items():
                self.assertEqual(json.loads(zipped.read(section)), value)
            for name, value in archive['text_files'].items():
                self.assertEqual(zipped.read(name), value.encode())

    def test_bad_base_is_rejected_even_when_json_is_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'base.json'
            path.write_bytes(BASE.read_bytes() + b' ')
            with patch.object(fixture, 'BASE_FIXTURE_PATH', path):
                with self.assertRaisesRegex(fixture.FixtureError, 'base fixture'):
                    fixture.load_contract()

    def test_source_base_overlay_and_archive_binding(self):
        for path, value, pattern in [
            (('historical_source', 'revision'), 'untrusted', 'historical source'),
            (('historical_source', 'files_sha256'), {}, 'historical source'),
            (('base_fixture', 'sha256'), '0' * 64, 'base fixture'),
            (('overlay_sha256',), '0' * 64, 'overlay SHA'),
            (('derived_archive_sha256',), '0' * 64, 'archive SHA'),
        ]:
            with self.subTest(path=path):
                contract = deepcopy(self.contract)
                target = contract
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.assertRaisesRegex(fixture.FixtureError, pattern):
                    fixture.validate_contract(contract)

    def test_duplicates_and_base_id_collision_rejected(self):
        self.reject(lambda c: self.records(c, MESSAGE).append(deepcopy(self.records(c, MESSAGE)[0])),
                    'duplicate')
        self.reject(lambda c: self.records(c, DELIVERY)[1].update(
            message_transaction_id=self.records(c, DELIVERY)[0]['message_transaction_id']), 'duplicate')
        self.reject(lambda c: self.records(c, MESSAGE)[0].update(message_id=c['ids']['owner']),
                    'identity|collision')

    def test_dangling_and_foreign_swaps_rejected(self):
        for change in [
            lambda c: self.records(c, DELIVERY)[0].update(message_id='00000000-0000-4000-8000-000000000001'),
            lambda c: self.records(c, DELIVERY)[0].update(message_id=c['ids']['foreign_message']),
            lambda c: self.records(c, MESSAGE)[0].update(sport_association_id=c['ids']['foreign_association']),
            lambda c: c['foreign_sentinel']['SportAssociation'][0].update(user_id=c['ids']['owner']),
            lambda c: c['foreign_sentinel']['MessageTransaction'][0].update(message_id=c['ids']['delete_email']),
            lambda c: self.records(c, DELIVERY)[2].update(message_id=c['ids']['retained_email']),
        ]:
            with self.subTest(change=change):
                self.reject(change, 'relationship|foreign|identity|projection|children')

    def test_null_empty_and_historical_fields_are_not_coerced(self):
        self.reject(lambda c: self.records(c, MESSAGE)[0].update(message=None), 'message.*string')
        self.reject(lambda c: self.records(c, MESSAGE)[0].update(type='POST'), 'type')
        self.reject(lambda c: self.records(c, MESSAGE)[0].update(subject=[]), 'subject')
        self.reject(lambda c: self.records(c, MESSAGE)[0].update(created_at='yesterday'), 'timestamp')
        self.reject(lambda c: self.records(c, MESSAGE)[0].update(created_at='2026-08-01T09:00:00'), 'timestamp')
        self.reject(lambda c: self.records(c, DELIVERY)[0].update(sent_on=None), 'timestamp')
        self.reject(lambda c: self.records(c, DELIVERY)[0].update(created_at='2026-08-01T00:00:00Z'), 'fields')
        self.reject(lambda c: self.records(c, MESSAGE)[0].pop('subject'), 'fields')
        self.reject(lambda c: self.records(c, MESSAGE)[1].update(subject=''), 'projection')

    def test_unknown_malformed_or_missing_sections_rejected(self):
        mutations = [
            lambda c: c.update(extra={}),
            lambda c: c['archive_overlay'].update(extra={}),
            lambda c: c['archive_overlay']['operations'][0].update(section='data/unknown.json'),
            lambda c: c['archive_overlay']['operations'][0].update(op='replace'),
            lambda c: c['archive_overlay']['operations'].pop(),
            lambda c: c['archive_overlay']['operations'].append(deepcopy(c['archive_overlay']['operations'][0])),
            lambda c: c['archive_overlay']['operations'][0].update(records={}),
            lambda c: c.update(schema_version=True),
            lambda c: c.update(expected=[]),
            lambda c: c['expected_counts'].update(Unknown=0),
            lambda c: c['expected_counts'].pop('Group'),
            lambda c: c['expected_counts'].update(Group=False),
            lambda c: c['retired_policy']['explicit_skip_records'].update(Unknown=1),
            lambda c: c['retired_policy']['explicit_skip_records'].update(BillingPlan=0),
            lambda c: c['retired_policy']['explicit_skip_records'].update(BillingPlan=True),
            lambda c: c['foreign_sentinel'].update(Unknown=[]),
            lambda c: c['foreign_sentinel']['User'].clear(),
            lambda c: c['expected']['imported']['timestamp_policy'].update(
                {'Message.created_at': 'ignore'}),
        ]
        for change in mutations:
            with self.subTest(change=change):
                self.reject(change, '.')

    def test_projection_and_content_tampering_rejected(self):
        self.reject(lambda c: c['expected']['after_delete']['counts'].update(Message=4), 'projection')
        self.reject(lambda c: self.records(c, MESSAGE)[0].update(message='tampered'), 'projection')
        self.reject(lambda c: c['expected']['imported']['messages'][0].update(subject='tampered'), 'projection')
        # Matching expected rows cannot turn this immutable specimen into a new fixture.
        contract = deepcopy(self.contract)
        self.records(contract, MESSAGE)[0]['message'] = 'coordinated tamper'
        contract['expected']['imported']['messages'][0]['message'] = 'coordinated tamper'
        contract['expected']['after_delete']['messages'][0]['message'] = 'coordinated tamper'
        contract['overlay_sha256'] = hashlib.sha256(fixture.canonical_bytes(contract['archive_overlay'])).hexdigest()
        with self.assertRaisesRegex(fixture.FixtureError, 'fixed|pinned|SHA'):
            fixture.validate_contract(contract)

    def test_malformed_nested_shapes_fail_as_fixture_errors(self):
        for key in ('archive_overlay', 'expected', 'expected_counts', 'ids',
                    'foreign_sentinel', 'mutation', 'historical_source', 'base_fixture'):
            for value in (None, [], 7, True, 'invalid'):
                with self.subTest(key=key, value=value):
                    contract = deepcopy(self.contract)
                    contract[key] = value
                    with self.assertRaises(fixture.FixtureError):
                        fixture.validate_contract(contract)

    def test_explicit_local_load_and_detached_returns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixed.json'
            path.write_bytes(fixture.FIXTURE_PATH.read_bytes())
            self.assertEqual(fixture.load_contract(path), self.contract)
        validated = fixture.validate_contract(self.contract)
        validated['expected']['imported']['messages'].clear()
        self.assertEqual(len(self.contract['expected']['imported']['messages']), 4)
        first = fixture.derived_archive(self.contract)
        first['entries'][MESSAGE].clear()
        self.assertEqual(len(fixture.derived_archive(self.contract)['entries'][MESSAGE]), 4)

    def test_archive_argument_is_validated_instead_of_packaging_arbitrary_data(self):
        for change in [
            lambda a: a['entries'].update({'data/unknown.json': []}),
            lambda a: a['entries']['manifest.json']['models_exported'][0].update(count=0),
            lambda a: a['entries']['manifest.json']['models_exported'][0].update(count=True),
            lambda a: a['entries'][DELIVERY][0].update(message_id=self.contract['ids']['foreign_message']),
            lambda a: a['entries'].update({MESSAGE: {}}),
            lambda a: a['text_files'].clear(),
        ]:
            archive = fixture.derived_archive()
            change(archive)
            with self.assertRaises(fixture.FixtureError):
                fixture.archive_bytes(archive)

    def test_file_loader_rejects_invalid_json_and_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.json'
            for raw in ('[]', '{', '{"schema_version":1,"schema_version":1}',
                        '{"schema_version":NaN}'):
                path.write_text(raw)
                with self.assertRaises(fixture.FixtureError):
                    fixture.load_contract(path)

    def test_import_and_use_are_stdlib_only_without_network_or_app_imports(self):
        code = '''
import sys

def deny(event, args):
    if event.startswith('socket.') or event in ('subprocess.Popen', 'os.system'):
        raise AssertionError('external action: ' + event)
    if event == 'import' and any(args[0] == prefix or args[0].startswith(prefix + '.')
        for prefix in ('application', 'communications', 'django', 'requests', 'TESTS.adapters')):
        raise AssertionError('application import: ' + args[0])
sys.addaudithook(deny)
from TESTS.fixtures import historical_message_mutations as fixture
fixture.load_contract()
fixture.archive_bytes()
fixture.expected_projection('after_delete')
fixture.foreign_sentinel()
assert not any(name.startswith(('django', 'application', 'communications')) for name in sys.modules)
'''
        result = subprocess.run([sys.executable, '-S', '-c', code], cwd=BASE.parents[2],
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
