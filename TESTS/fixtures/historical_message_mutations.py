"""Immutable, offline historical saved-message derivation (standard library only).

The ZIP adds owned messages and actual delivery children to the frozen base.
The separate foreign graph is parent-installed setup, never a second association
in the single-association import archive. Expected projections are authored in
the fixed JSON, independently of application models and exporters.
"""
from copy import deepcopy
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
from uuid import UUID
import zipfile


FIXTURE_PATH = Path(__file__).with_name('bakney-v1-message-mutations.json')
BASE_FIXTURE_PATH = Path(__file__).with_name('bakney-v1.json')
BASE_SHA256 = '0d0f5d58946de1306d93476261a6dc6fe09cb56c3b5e23b4d3dcd2c4e75f6713'
BASE_BYTES = 15420
BASE_REVISION = '0058de5909734bdd0f00867abbdb723649de4238'
OVERLAY_SHA256 = '03ac87a1f4209f024dfd92d77e549e8aef6dfc86c4324aca5c7800c9756a1df4'
ARCHIVE_SHA256 = 'e0a2e7ab8b277944980cf284fbc31fb166fba2bd02d902053be6c8dd7f2505ea'
CONTRACT_SHA256 = '4bd203906fbeb061bc43febab27671e14ffcb78b6def8af3f3d5f7ced4e425a3'
OVERLAY_ALGORITHM = (
    'sha256 of UTF-8 canonical JSON (sort_keys=true, ensure_ascii=false, '
    'separators comma/colon) over archive_overlay only'
)
SOURCE_SHA256 = {
    'communications/models.py': '138897672e8aa1185c238859dd1f6e2b39d4f6d44921a4f0a7168efecc346b65',
    'communications/serializers.py': '9e35179af12182558578b880489bbc77827b2cf3fbc877af596d3b2756ecec7f',
    'communications/views.py': '961b41ee8077f8bfabc0fa3d787ecef62223469d219fb3cc96a5c913c64bccbe',
    'application/services/export_service.py': '79d5a8c5df1160c101f7113ad8418bbd393ae250615335381f6d4b02e85032f2',
    'application/services/import_service.py': '5d91f2b366f80a5d807483fee4476236b00ae7db20fe87b7d49dad2eda4c07b2',
    'application/models/user_models.py': '43c96210095dc1444d1dfffb71b18e3676190badcbe9711223a8cf1173fe3a1c',
}
MESSAGE_SECTION = 'data/52_messages.json'
DELIVERY_SECTION = 'data/53_message_transactions.json'
SECTIONS = {MESSAGE_SECTION: 'Message', DELIVERY_SECTION: 'MessageTransaction'}
FIELDS = {
    'Message': {'message_id', 'sport_association_id', 'type', 'message', 'subject', 'created_at'},
    'MessageTransaction': {
        'message_transaction_id', 'message_id', 'recipient', 'sent_on',
        'delivered_on', 'opened_on', 'clicked_on', 'bounced_on',
        'bounced_reason', 'bounced_type', 'bounced_status',
    },
    'User': {'user_id', 'username', 'email', 'password', 'role', 'first_name',
             'last_name', 'is_active', 'deleted'},
    'SportAssociation': {'sport_association_id', 'user_id', 'denomination',
                         'tax_code', 'subscription_fee', 'membership_fee'},
}
PRIMARY_KEYS = {
    'Message': 'message_id', 'MessageTransaction': 'message_transaction_id',
    'User': 'user_id', 'SportAssociation': 'sport_association_id',
}
# All nonempty base sections, including the two retired records. This does not
# infer primary keys from current models or rewrite any base data.
BASE_PRIMARY_KEYS = {
    'BillingPlan': 'billing_plan_id', 'SportAssociation': 'sport_association_id',
    'User': 'user_id', 'Associate': 'associate_id', 'Subscription': 'subscription_id',
    'Payment': 'payment_id', 'PreviewAndCustomFeatures': 'preview_and_custom_features_id',
    'SportAssociationDocumentsArchive': 'sport_association_documents_archive_id',
    'Document': 'document_id',
}
NEW_IDS = {
    'retained_email', 'retained_post', 'retained_sms', 'delete_email',
    'retained_delivery', 'post_delivery', 'sms_delivery', 'delete_delivered',
    'delete_bounced', 'foreign_owner', 'foreign_association',
    'foreign_message', 'foreign_delivery',
}
TOP_LEVEL_KEYS = {
    'schema_version', 'scope', 'base_fixture', 'historical_source',
    'overlay_algorithm', 'overlay_sha256', 'derived_archive_sha256',
    'archive_overlay', 'expected_counts', 'ids', 'retired_policy',
    'foreign_sentinel', 'mutation', 'expected',
}
SKIPS = {'explicit_skip_records': {'BillingPlan': 1, 'PreviewAndCustomFeatures': 1}}


class FixtureError(ValueError):
    """The fixed fixture, provenance, relationship, or archive binding differs."""


def _require(condition, message):
    if not condition:
        raise FixtureError(message)


def _keys(value, keys, label):
    _require(isinstance(value, dict) and set(value) == set(keys),
             f'{label} has unknown or missing fields/sections')


def canonical_bytes(value):
    """Encode the documented canonical JSON form without application encoders."""
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError) as exc:
        raise FixtureError(f'value cannot be canonical fixture JSON: {exc}') from exc


def _digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, f'duplicate JSON key: {key}')
        result[key] = value
    return result


def _decode(raw):
    try:
        return json.loads(raw, object_pairs_hook=_unique_object,
                          parse_constant=lambda value: _invalid_constant(value))
    except (ValueError, UnicodeError) as exc:
        raise FixtureError(f'invalid fixture JSON: {exc}') from exc


def _invalid_constant(value):
    raise FixtureError(f'invalid JSON constant: {value}')


def _load_base():
    raw = BASE_FIXTURE_PATH.read_bytes()
    _require(len(raw) == BASE_BYTES, 'base fixture byte count changed')
    _require(hashlib.sha256(raw).hexdigest() == BASE_SHA256, 'base fixture SHA-256 changed')
    return _decode(raw)


def _uuid(value, label):
    try:
        valid = isinstance(value, str) and str(UUID(value)) == value
    except ValueError:
        valid = False
    _require(valid, f'{label} must be a canonical UUID string')


def _string(value, label, *, nullable=False, maximum=None):
    if nullable and value is None:
        return
    _require(isinstance(value, str), f'{label} must be a string')
    _require(maximum is None or len(value) <= maximum, f'{label} exceeds historical length')


def _timestamp(value, label, *, nullable=False):
    if nullable and value is None:
        return
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else None
        valid = parsed is not None and parsed.utcoffset() is not None
    except ValueError:
        valid = False
    _require(valid, f'{label} must be an explicit timestamp with timezone')


def _records(rows, model):
    _require(isinstance(rows, list) and rows, f'{model} records must be a nonempty list')
    seen = set()
    for row in rows:
        _keys(row, FIELDS[model], model)
        pk = row[PRIMARY_KEYS[model]]
        _uuid(pk, f'{model} identity')
        _require(pk not in seen, f'duplicate {model} identity')
        seen.add(pk)
        if model == 'Message':
            _uuid(row['sport_association_id'], 'Message association')
            _require(row['type'] in ('EMAIL', 'INSIDE_APP', 'SMS'), 'unsupported historical message type')
            _string(row['message'], 'Message.message')
            _string(row['subject'], 'Message.subject', nullable=True, maximum=255)
            _timestamp(row['created_at'], 'Message.created_at timestamp', nullable=True)
        elif model == 'MessageTransaction':
            _uuid(row['message_id'], 'MessageTransaction message')
            _string(row['recipient'], 'MessageTransaction.recipient', maximum=255)
            _timestamp(row['sent_on'], 'MessageTransaction.sent_on timestamp')
            for field in ('delivered_on', 'opened_on', 'clicked_on', 'bounced_on'):
                _timestamp(row[field], f'MessageTransaction.{field} timestamp', nullable=True)
            for field in ('bounced_reason', 'bounced_type', 'bounced_status'):
                _string(row[field], f'MessageTransaction.{field}', nullable=True, maximum=255)
        elif model == 'User':
            for field in ('username', 'email', 'password', 'first_name', 'last_name'):
                _string(row[field], f'foreign User.{field}')
            _require(type(row['role']) is int and row['role'] == 1, 'foreign owner role must be 1')
            _require(row['is_active'] is True and row['deleted'] is False,
                     'foreign owner must be active and retained')
        elif model == 'SportAssociation':
            _uuid(row['user_id'], 'foreign association owner')
            _string(row['denomination'], 'foreign denomination', maximum=150)
            _string(row['tax_code'], 'foreign tax_code', maximum=11)
            _string(row['subscription_fee'], 'foreign subscription_fee')
            _string(row['membership_fee'], 'foreign membership_fee', nullable=True)
    return seen


def _derive(base, contract):
    derived = deepcopy(base)
    for operation in contract['archive_overlay']['operations']:
        derived['entries'][operation['section']].extend(deepcopy(operation['records']))
    for spec in derived['entries']['manifest.json']['models_exported']:
        spec['count'] = len(derived['entries'][spec['file']])
    return derived


def _zip_bytes(archive):
    target = io.BytesIO()
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED) as output:
        for name, content in [
            *((name, canonical_bytes(value)) for name, value in archive['entries'].items()),
            *((name, value.encode('utf-8')) for name, value in archive['text_files'].items()),
        ]:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            output.writestr(info, content)
    return target.getvalue()


def _validate(contract, base):
    _keys(contract, TOP_LEVEL_KEYS, 'fixture')
    _require(type(contract['schema_version']) is int and contract['schema_version'] == 1,
             'schema_version must be integer 1')
    _string(contract['scope'], 'scope')
    _require(contract['base_fixture'] == {
        'path': 'TESTS/fixtures/bakney-v1.json', 'bytes': BASE_BYTES,
        'sha256': BASE_SHA256, 'model_count': 67, 'section_entry_count': 68,
    }, 'base fixture provenance differs')
    _require(contract['historical_source'] == {
        'repository': 'carbogninalberto/django-bakney-sport',
        'revision': BASE_REVISION, 'files_sha256': SOURCE_SHA256,
    }, 'pinned historical source differs')
    _require(contract['overlay_algorithm'] == OVERLAY_ALGORITHM, 'overlay algorithm differs')
    # Canonical bytes distinguish booleans from numeric counts as well as extras.
    _require(canonical_bytes(contract['retired_policy']) == canonical_bytes(SKIPS),
             'retired skip allowlist/counts differ')
    specs = base['entries']['manifest.json']['models_exported']
    _require(len(specs) == 67 and len(base['entries']) == 68, 'base manifest section count differs')
    _keys(contract['expected_counts'], {spec['name'] for spec in specs}, 'expected_counts')
    _keys(contract['ids'], set(base['ids']) | NEW_IDS, 'identity inventory')
    ids = contract['ids']
    for name, value in ids.items():
        _uuid(value, f'{name} identity')
    _require(len(set(ids.values())) == len(ids), 'duplicate identity inventory')
    _require(all(ids[name] == value for name, value in base['ids'].items()), 'base identity changed')

    overlay = contract['archive_overlay']
    _keys(overlay, {'operations'}, 'archive_overlay')
    operations = overlay['operations']
    _require(isinstance(operations, list) and len(operations) == 2,
             'overlay must contain exactly two message/delivery sections')
    sections = {}
    for operation in operations:
        _keys(operation, {'op', 'section', 'records'}, 'overlay operation')
        _require(operation['op'] == 'append', 'overlay only supports append operations')
        section = operation['section']
        _require(isinstance(section, str) and section in SECTIONS, 'unknown overlay section')
        _require(section not in sections, 'duplicate overlay section')
        _records(operation['records'], SECTIONS[section])
        sections[section] = operation['records']
    _require(list(sections) == list(SECTIONS), 'overlay sections missing or reordered')

    foreign = contract['foreign_sentinel']
    _keys(foreign, PRIMARY_KEYS, 'foreign sentinel')
    for model, rows in foreign.items():
        _records(rows, model)
        _require(len(rows) == 1, f'foreign sentinel must have one {model}')
    messages, deliveries = sections[MESSAGE_SECTION], sections[DELIVERY_SECTION]
    message_ids = {row['message_id'] for row in messages}
    delivery_ids = {row['message_transaction_id'] for row in deliveries}
    _require(message_ids == {ids[name] for name in (
        'retained_email', 'retained_post', 'retained_sms', 'delete_email')},
        'message identity inventory differs or collides')
    _require(delivery_ids == {ids[name] for name in (
        'retained_delivery', 'post_delivery', 'sms_delivery', 'delete_delivered', 'delete_bounced')},
        'delivery identity inventory differs or collides')
    _require(all(row['sport_association_id'] == ids['association'] for row in messages),
             'message relationship crosses foreign association')
    _require(all(row['message_id'] in message_ids for row in deliveries),
             'delivery relationship is dangling or foreign')
    _require({row['message_id'] for row in deliveries} == message_ids,
             'each historical message must have actual delivery children')
    _require(foreign['User'][0]['user_id'] == ids['foreign_owner'] and
             foreign['SportAssociation'][0]['sport_association_id'] == ids['foreign_association'] and
             foreign['SportAssociation'][0]['user_id'] == ids['foreign_owner'] and
             foreign['Message'][0]['message_id'] == ids['foreign_message'] and
             foreign['Message'][0]['sport_association_id'] == ids['foreign_association'] and
             foreign['MessageTransaction'][0]['message_transaction_id'] == ids['foreign_delivery'] and
             foreign['MessageTransaction'][0]['message_id'] == ids['foreign_message'],
             'foreign sentinel identity/relationship differs')
    new_pks = [row[PRIMARY_KEYS[model]] for model, rows in foreign.items() for row in rows]
    new_pks.extend(message_ids | delivery_ids)
    base_pks = {row[BASE_PRIMARY_KEYS[spec['name']]] for spec in specs
                for row in base['entries'][spec['file']]}
    _require(len(set(new_pks)) == len(new_pks) and not (set(new_pks) & base_pks),
             'new identity collision with base or foreign sentinel')

    _require(contract['mutation'] == {
        'delete_message_id': ids['delete_email'],
        'delete_delivery_ids': [ids['delete_delivered'], ids['delete_bounced']],
    }, 'mutation identity projection differs')
    target_children = {r['message_transaction_id'] for r in deliveries
                       if r['message_id'] == ids['delete_email']}
    _require(target_children == {ids['delete_delivered'], ids['delete_bounced']},
             'delete target relationship must retain exactly two actual deliveries')
    _keys(contract['expected'], {'imported', 'after_delete'}, 'expected projection')
    for stage, count, child_count in [('imported', 4, 5), ('after_delete', 3, 3)]:
        projection = contract['expected'][stage]
        _keys(projection, {'counts', 'combined_counts', 'messages', 'deliveries',
                          'foreign_sentinel', 'timestamp_policy'},
              f'{stage} projection')
        _require(projection['timestamp_policy'] == {
            'Message.created_at': 'import_window' if stage == 'imported' else 'unchanged_from_import',
            'MessageTransaction.sent_on': 'import_window' if stage == 'imported' else 'unchanged_from_import',
            'foreign_sentinel': 'parent_setup_baseline',
        }, 'timestamp projection policy differs')
        _require(canonical_bytes(projection['counts']) == canonical_bytes({
            'Message': count, 'MessageTransaction': child_count}), 'projection counts differ')
        _require(canonical_bytes(projection['combined_counts']) == canonical_bytes({
            'Message': count + 1, 'MessageTransaction': child_count + 1}),
            'combined projection counts differ')
        wanted_messages = messages if stage == 'imported' else [
            row for row in messages if row['message_id'] != ids['delete_email']]
        wanted_deliveries = deliveries if stage == 'imported' else [
            row for row in deliveries if row['message_id'] != ids['delete_email']]
        _require(projection['messages'] == wanted_messages and
                 projection['deliveries'] == wanted_deliveries and
                 projection['foreign_sentinel'] == foreign, 'explicit field/identity projection differs')

    derived = _derive(base, contract)
    for spec in derived['entries']['manifest.json']['models_exported']:
        expected = contract['expected_counts'][spec['name']]
        _require(type(expected) is int and expected == spec['count'],
                 f'{spec["name"]} manifest/expected count differs')
    _require(contract['overlay_sha256'] == _digest(overlay) == OVERLAY_SHA256,
             'fixed overlay SHA-256 differs')
    _require(contract['derived_archive_sha256'] == hashlib.sha256(_zip_bytes(derived)).hexdigest()
             == ARCHIVE_SHA256, 'fixed derived archive SHA-256 differs')
    _require(_digest(contract) == CONTRACT_SHA256, 'fixed contract SHA-256 differs')
    return derived


def validate_contract(contract):
    """Validate a fixed contract and return a detached copy; never trust re-signed edits."""
    _validate(contract, _load_base())
    return deepcopy(contract)


def load_contract(path=None):
    """Read and validate the fixed specimen, optionally from an explicit local path."""
    return validate_contract(_decode(Path(FIXTURE_PATH if path is None else path).read_bytes()))


def derived_archive(contract=None):
    """Return complete base-shaped archive data; preserve every base row and ID."""
    contract = load_contract() if contract is None else contract
    return _validate(contract, _load_base())


def archive_bytes(archive=None):
    """Return deterministic importer ZIP bytes, rejecting altered supplied archives."""
    fixed = derived_archive()
    if archive is not None:
        _require(archive == fixed and canonical_bytes(archive) == canonical_bytes(fixed),
                 'supplied archive differs from fixed historical derivation')
    return _zip_bytes(fixed)


def expected_projection(stage='imported', contract=None):
    """Return explicit field/identity/count expectations, including separate sentinel.

    Main counts exclude the parent-installed foreign graph. Combined counts only
    apply after the parent installs that graph. after_delete describes successful
    removal of delete_email and its two children, not observed runtime behavior.
    Rows contain fixed source values. timestamp_policy characterizes the current
    importer's auto_now_add overwrite; it does not authorize normalization for
    historical-preservation acceptance. Compare source timestamps separately and
    retain a preservation gap when the importer changes them.
    """
    contract = load_contract() if contract is None else validate_contract(contract)
    _require(isinstance(stage, str) and stage in contract['expected'], 'unknown projection stage')
    result = deepcopy(contract['expected'][stage])
    result.update(acceptance_scope='current-importer-characterization',
                  historical_timestamp_preservation_verified=False,
                  timestamp_preservation_requirement='exact-source-values')
    return result


def foreign_sentinel(contract=None):
    """Return parent-owned setup rows in dependency order; excluded from the ZIP."""
    contract = load_contract() if contract is None else validate_contract(contract)
    return deepcopy(contract['foreign_sentinel'])
