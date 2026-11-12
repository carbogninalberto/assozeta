"""Parent-only staff-board HTTP runner; imports are inert.

Seeds the shared identity fixture plus staff-board rows through the Django ORM,
runs the authored sequence against one live endpoint, and verifies every case
against the complete 128-model state, the historical cache and per-request
upstream attribution. A Django-only run is reference evidence, not parity.
"""
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path

from TESTS.contracts import test_staff_board_http as contract
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations

MODEL = 'communications.StaffBoardMessage'


SOURCES = ('TESTS/fixtures/staff_board_http.json', 'TESTS/contracts/test_staff_board_http.py',
           'TESTS/adapters/django/staff_board_http.py', 'BE/communications/views.py', 'BE/communications/staff_board.py',
           'BE/communications/serializers.py', 'BE/notifications/go_realtime.py', 'BE_GO/internal/handler/staff_board.go',
           'BE_GO/internal/service/staff_board.go', 'BE_GO/internal/repository/staff_board.go',
           'BE_GO/internal/dto/staff_board_input.go', 'BE_GO/internal/dto/staff_board_document.go',
           'BE_GO/internal/dto/staff_board_response.go')


def scalar(value):
    if isinstance(value, dict) and set(value) == {'type', 'value'} and value['type'] in ('uuid', 'datetime'):
        return value['value']
    return value


def rows(state):
    return {str(scalar(row['staff_board_message_id'])): {key: scalar(value) for key, value in row.items()}
            for row in state[MODEL]}


def seed_fixture(apps, private_work, raw):
    from django.db import transaction
    from communications.models import StaffBoardMessage
    source = contract.load_source()
    with transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        for identifier, values in source['seed'].items():
            contract.require(not StaffBoardMessage.objects.filter(pk=identifier).exists(), 'fixture.row-collision')
            StaffBoardMessage.objects.create(staff_board_message_id=identifier, sport_association_id=values['association'],
                author_id=values['author'], content=values['content'], document=values['document'], pinned=values['pinned'])
            moment = datetime.fromisoformat(values['at'].replace('Z', '+00:00'))
            StaffBoardMessage.objects.filter(pk=identifier).update(created_at=moment, updated_at=moment)
            fixture['created'].append((StaffBoardMessage, identifier))
        from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
        fixture['baseline_state'] = scopes.snapshot_state(apps)
        fixture['baseline_cache'] = snapshot_historical_read_cache(raw)
    return fixture


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from communications.models import StaffBoardMessage
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    mutations.require_private_database()
    private_work = Path(private_work)
    contract.require(not private_work.exists(), 'fixture.new-private-directory-required')
    before = scopes.snapshot_state(apps)
    events_before = [row[0] for row in outbox_events()]
    cache_before, sequences_before = snapshot_historical_read_cache(raw), mutations.sequence_snapshot()
    fixture = None
    receipt = cleanup_receipt if cleanup_receipt is not None else {}
    receipt.update(verified=False, results={})
    try:
        fixture = seed_fixture(apps, private_work, raw)
        yield fixture
    finally:
        def remove_rows():
            # Includes rows created by requests even when an assertion failed.
            StaffBoardMessage.objects.exclude(pk__in=list(rows(before))).delete()
            # Restoring sequences below would otherwise reuse outbox identities.
            from django.db import connection
            with connection.cursor() as cursor:
                cursor.execute('DELETE FROM public.assozeta_go_realtime_events WHERE NOT (id = ANY(%s))', [events_before])
            if fixture is not None:
                shared.delete_created(fixture)
            shared.assert_state_unchanged(before, scopes.snapshot_state(apps))
            return True
        receipt.update(scopes.cleanup_steps((
            ('rows', remove_rows), ('cache', lambda: scopes.restore_cache(raw, cache_before)),
            ('sequences', lambda: mutations.restore_sequences(sequences_before)),
            ('private_fixture', lambda: scopes.remove_private_fixture(private_work)),
        )))
        contract.require(receipt['verified'], 'cleanup.fixture-incomplete')


def _within(value, window):
    moment = datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    return window[0] - 0.001 <= moment <= window[1] + 0.001


def assert_effect(case, before, after, bindings, window):
    """Only the authored staff-board change may occur; all other models are equal."""
    others = lambda state: {key: value for key, value in state.items() if key != MODEL}
    contract.require(others(before) == others(after), 'state.other-models')
    old, new = rows(before), rows(after)
    effect = case['effect']
    if effect['kind'] == 'none':
        contract.require(old == new, 'state.no-effect')
        return
    if effect['kind'] == 'create':
        added = set(new) - set(old)
        contract.require(len(added) == 1 and set(old) <= set(new), 'state.one-created')
        identifier = added.pop()
        contract.require(bindings.get(effect['label']) == identifier, 'state.created-matches-response')
        row = new[identifier]
        for key, value in effect['values'].items():
            contract.require(row[key] == value, 'state.created-' + key)
        contract.require(_within(row['created_at'], window) and _within(row['updated_at'], window), 'state.created-times')
        contract.require({key: value for key, value in old.items()} == {k: v for k, v in new.items() if k != identifier},
                         'state.create-only')
        return
    identifier = effect['row']
    if effect['kind'] == 'delete':
        contract.require(identifier in old and identifier not in new, 'state.deleted')
        contract.require({k: v for k, v in old.items() if k != identifier} == new, 'state.delete-only')
        return
    contract.require(identifier in old and identifier in new, 'state.update-target')
    expected = dict(old[identifier], **effect['values'])
    expected['updated_at'] = new[identifier]['updated_at']
    contract.require(_within(new[identifier]['updated_at'], window), 'state.updated-time')
    contract.require(new[identifier] == expected, 'state.update-fields')
    contract.require({k: v for k, v in old.items() if k != identifier} == {k: v for k, v in new.items() if k != identifier},
                     'state.update-only')


def outbox_events():
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute("""SELECT id, envelope_version, kind, target_id::text, delivered_at IS NULL AND failed_code IS NULL
            FROM public.assozeta_go_realtime_events ORDER BY id""")
        return cursor.fetchall()


def assert_realtime(case, backend, before, after):
    """Go writes append exactly one pending invalidation that the Django relay
    delivers to the staff-board group; Django writes and failures append none."""
    from asgiref.sync import async_to_sync
    from channels.layers import InMemoryChannelLayer
    from communications.staff_board import staff_board_group
    from notifications import go_realtime
    added = after[len(before):]
    contract.require(after[:len(before)] == before, 'realtime.history')
    if backend != 'go' or case['effect']['kind'] == 'none':
        contract.require(not added, 'realtime.unexpected-event')
        return False
    association = contract.load_source()['ids']['own_association']
    contract.require(len(added) == 1 and added[0][1:] == (1, 'staff_board_changed', association, True), 'realtime.event')
    layer = InMemoryChannelLayer()
    channel = async_to_sync(layer.new_channel)()
    async_to_sync(layer.group_add)(staff_board_group(association), channel)
    contract.require(go_realtime.relay_once(layer=layer) == {'delivered': 1, 'retry': 0, 'failed': 0}, 'realtime.relay')
    contract.require(async_to_sync(layer.receive)(channel) == {'type': 'staff_board_changed'}, 'realtime.delivered')
    return True


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    """Run every authored case in order; raise on the first failed check."""
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    shared.assert_state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
    bindings, results = {}, []
    known = set(source['seed'])
    relayed = 0
    for case in source['cases']:
        before = scopes.snapshot_state(apps)
        events_before = outbox_events()
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            status, window = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'],
                                                  bindings=bindings, known_ids=known)
            after = scopes.snapshot_state(apps)
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            assert_effect(case, before, after, bindings, window)
            relayed += assert_realtime(case, backend, events_before, outbox_events())
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results,
            'authored_cases': len(source['cases']), 'realtime_events_relayed': relayed}
