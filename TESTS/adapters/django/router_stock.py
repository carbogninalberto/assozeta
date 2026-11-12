"""Shared parent-only effect checks for the DefaultRouter families; imports are inert.

single_model(...) builds the effect check of a viewset whose writes touch one
audited model (plus optional through tables): every other model must stay
unchanged, and an authored effect is one of
- None: the managed models unchanged;
- {'kind': 'add', 'actor', 'id' ('$new' or a fixed key), 'stored': {column: value}}:
  exactly one new row with those stored values (and the association), one
  create entry whose changes are auditlog's create diff of the stored row;
- {'kind': 'update', 'actor', 'id', 'stored', 'changes'}: the row's stored
  values, the authored [old, new] changes in one entry (none when empty);
- {'kind': 'delete', 'actor', 'id'}: the row removed, one delete entry with
  auditlog's delete diff of the row read before the request.
Create/update entries index through 'direct', deletes through
'changes_json.sport_association'.
"""
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import carnet_subscription_topup_http as topup
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed, scalar

SOURCES = ('TESTS/adapters/django/router_stock.py', 'TESTS/adapters/django/carnet_replace_http.py',
           'TESTS/adapters/django/carnet_subscription_topup_http.py')


def plain(value):
    return str(scalar(value)) if value is not None else None


def single_model(contract, *, label, load, pk, association='sport_association_id', through=(), audit_name=None):
    """label: 'app.Model' state key; load(): the model class; through: state keys of
    M2M through tables that change only with their owner's delete (rows whose
    `through_key` column is the deleted key)."""
    managed = (label, *[name for name, _ in through], harness.AUDIT, harness.INDEX)

    def prepare(source):
        model = load()
        return {str(row.pk): replace.deleted_changes(model, row) for row in model._base_manager.all()}

    def effect(case, before, after, source, context):
        harness.require_unmanaged_unchanged(contract, before, after, managed)
        spec = case['effect']
        if spec is None:
            contract.require(all(before[k] == after[k] for k in managed), 'state.no-effect')
            return
        model = load()
        name = audit_name or model.__name__
        ids = source['ids']
        own = ids['own_association']
        old, new = keyed(before[label], pk), keyed(after[label], pk)
        old = {plain(k): v for k, v in old.items()}
        new = {plain(k): v for k, v in new.items()}
        stored = lambda row: {column: (plain(row[column]) if column.endswith('_id') else row[column]) for column in spec['stored']}
        if spec['kind'] == 'add':
            keys = set(new) - set(old)
            contract.require(len(keys) == 1 and all(new[k] == old[k] for k in old), 'state.created')
            key = keys.pop()
            contract.require(spec['id'] == '$new' or key == spec['id'], 'state.created-id')
            contract.require(stored(new[key]) == spec['stored'] and plain(new[key][association]) == own, 'state.row')
            action, path = 0, 'direct'
            changes = topup.created_changes(model, model._base_manager.get(pk=key), {})
        elif spec['kind'] == 'update':
            key = spec['id']
            contract.require(set(new) == set(old) and stored(new[key]) == spec['stored']
                             and all(new[k] == old[k] for k in old if k != key), 'state.row')
            action, path, changes = 1, 'direct', spec['changes']
        else:
            key = spec['id']
            contract.require(set(new) == set(old) - {key}, 'state.deleted')
            action, path, changes = 2, 'changes_json.sport_association', context['prepared'][key]
        for name_, column in through:
            if spec['kind'] == 'delete':
                kept = [row for row in before[name_] if plain(row[column]) != key]
                contract.require(after[name_] == kept, 'state.through')
            else:
                contract.require(after[name_] == before[name_], 'state.through')
        added, indexes = harness.audit_delta(contract, before, after)
        contract.require(len(added) == (1 if changes else 0) and len(indexes) == len(added), 'audit.count')
        for event in added:
            harness.check_audit_event(contract, case, event, indexes, before=before, ids=ids, model=name, key=key,
                                      action=action, changes=changes, actor=spec['actor'], association=own, path=path, context=context)

    return effect, prepare
