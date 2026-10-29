"""Deterministic historical read-route fixture derivation; standard library only.

The base archive is immutable. This module applies an explicit append-only
overlay and recomputes every manifest count without importing a backend,
database model, or current exporter.
"""
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import zipfile


FIXTURE_PATH = Path(__file__).with_name('bakney-v1-read-routes.json')
BASE_FIXTURE_PATH = Path(__file__).with_name('bakney-v1.json')
BASE_SHA256 = '0d0f5d58946de1306d93476261a6dc6fe09cb56c3b5e23b4d3dcd2c4e75f6713'
BASE_BYTES = 15420
BASE_REVISION = '0058de5909734bdd0f00867abbdb723649de4238'
OVERLAY_ALGORITHM = (
    'sha256 of UTF-8 canonical JSON (sort_keys=true, ensure_ascii=false, '
    'separators comma/colon) over archive_overlay only'
)
SOURCE_SHA256 = {
    'application/models/user_models.py': '43c96210095dc1444d1dfffb71b18e3676190badcbe9711223a8cf1173fe3a1c',
    'application/serializers/collaborators_serializers.py': 'a79281391ff5d528e8e61fb693c47ac9f3711d181a1765a178f659495c3ec09f',
    'application/views/collaborator_views.py': 'd3c179f8408cb6eae45a9795d2d9a14c88696870b51e7e0582c650276622c5bb',
    'application/services/export_service.py': '79d5a8c5df1160c101f7113ad8418bbd393ae250615335381f6d4b02e85032f2',
    'application/services/import_service.py': '5d91f2b366f80a5d807483fee4476236b00ae7db20fe87b7d49dad2eda4c07b2',
    'instance/views.py': 'f013fb282baa21d9dbefe0532a25a63ebeadd42d5d9fba64a645362169119779',
    'instance/models.py': 'f6ec902a7a1d66b4ea228fa1275209898b895707d05be351efe9ed7caab0fc7c',
}
BASELINE_MODEL_COUNT = 67
SECTION_ENTRY_COUNT = 68
OVERLAY_SECTIONS = {
    'data/02_users.json': 'User',
    'data/05_collaboration_invites.json': 'CollaborationInvites',
}
PRIMARY_KEYS = {
    'User': 'user_id',
    'CollaborationInvites': 'collaboration_invite_id',
}
TOP_LEVEL_KEYS = {
    'schema_version',
    'scope',
    'base_fixture',
    'historical_source',
    'overlay_algorithm',
    'overlay_sha256',
    'derived_archive_sha256',
    'archive_overlay',
    'expected_counts',
    'ids',
    'retired_policy',
    'setup',
    'http',
}
BASE_IDS = {
    'owner',
    'association',
    'person',
    'payment',
    'subscription',
    'document',
}


class FixtureError(ValueError):
    """The fixed fixture or derived archive no longer has its declared shape."""


def _require(condition, message):
    if not condition:
        raise FixtureError(message)


def _read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def canonical_bytes(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(',', ':')
    ).encode('utf-8')


def overlay_digest(overlay):
    return hashlib.sha256(canonical_bytes(overlay)).hexdigest()


def fixture_path():
    """Return the canonical fixture or the explicitly documented test override."""
    return Path(os.environ.get('TESTS_HISTORICAL_READ_FIXTURE', FIXTURE_PATH))


def load_base():
    raw = BASE_FIXTURE_PATH.read_bytes()
    _require(len(raw) == BASE_BYTES, f'base fixture byte count changed: {len(raw)}')
    _require(hashlib.sha256(raw).hexdigest() == BASE_SHA256, 'base fixture SHA-256 changed')
    return json.loads(raw)


def _manifest_sections(base):
    manifest = base.get('entries', {}).get('manifest.json')
    _require(isinstance(manifest, dict), 'base fixture has no manifest')
    specifications = manifest.get('models_exported')
    _require(isinstance(specifications, list), 'manifest.models_exported must be a list')
    _require(
        len(specifications) == BASELINE_MODEL_COUNT,
        f'base manifest must have {BASELINE_MODEL_COUNT} model sections',
    )
    names = [spec.get('name') for spec in specifications]
    files = [spec.get('file') for spec in specifications]
    _require(len(set(names)) == len(names), 'base manifest model names are not unique')
    _require(len(set(files)) == len(files), 'base manifest section files are not unique')
    return specifications


def _validate_record_fields(record, allowed, section):
    _require(isinstance(record, dict), f'{section} records must be objects')
    unknown = set(record) - set(allowed)
    _require(not unknown, f'{section} has unknown authored fields: {sorted(unknown)}')


def _validate_overlay(contract, base, sections):
    overlay = contract.get('archive_overlay')
    _require(isinstance(overlay, dict), 'archive_overlay must be an object')
    _require(set(overlay) == {'operations', 'authored_fields', 'omitted_field_policy'},
             'archive_overlay has unknown or missing sections')
    operations = overlay.get('operations')
    _require(isinstance(operations, list), 'archive_overlay.operations must be a list')
    seen_sections = set()
    section_files = {spec['file']: spec['name'] for spec in sections}
    authored = overlay.get('authored_fields')
    _require(isinstance(authored, dict), 'authored_fields must be an object')
    _require(set(authored) == set(OVERLAY_SECTIONS.values()),
             'authored_fields must list exactly User and CollaborationInvites')

    for operation in operations:
        _require(isinstance(operation, dict), 'overlay operation must be an object')
        _require(set(operation) == {'op', 'section', 'records'},
                 'overlay operation has unknown or missing keys')
        _require(operation['op'] == 'append', 'only explicit append operations are supported')
        section = operation['section']
        _require(section in OVERLAY_SECTIONS, f'unsupported overlay section: {section!r}')
        _require(section not in seen_sections, f'duplicate overlay operation for {section}')
        _require(section_files.get(section) == OVERLAY_SECTIONS[section],
                 f'overlay model name differs from historical manifest for {section}')
        _require(section in base['entries'], f'overlay section is absent from base: {section}')
        records = operation['records']
        _require(isinstance(records, list) and records, f'{section} records must be nonempty')
        seen_sections.add(section)
        model = OVERLAY_SECTIONS[section]
        pk_field = PRIMARY_KEYS[model]
        ids = []
        for record in records:
            _validate_record_fields(record, authored[model], section)
            _require(pk_field in record, f'{section} record has no {pk_field}')
            _require(isinstance(record[pk_field], str), f'{pk_field} must be a string')
            ids.append(record[pk_field])
        _require(len(set(ids)) == len(ids), f'duplicate primary keys in {section}')

    _require(seen_sections == set(OVERLAY_SECTIONS),
             'overlay must explicitly append both User and CollaborationInvites')
    users = next(item['records'] for item in operations
                 if item['section'] == 'data/02_users.json')
    invites = next(item['records'] for item in operations
                   if item['section'] == 'data/05_collaboration_invites.json')
    owner = base['ids']['owner']
    for user in users:
        _require(user['role'] == 3 and type(user['role']) is int,
                 'overlay users must be integer collaborator roles')
        _require(user['connected_user_id'] == owner,
                 'overlay user must be connected to the base owner')
        _require(type(user['is_active']) is bool and type(user['deleted']) is bool,
                 'overlay user active/deleted flags must be booleans')
        _require(type(user['collaborator_role']) is int,
                 'overlay collaborator_role must be an integer')
    for invite in invites:
        _require(invite['user_id'] == owner,
                 'overlay invitation must belong to the base owner')
        _require(type(invite['accepted']) is bool,
                 'overlay invitation accepted flag must be a boolean')
        _require(type(invite['collaborator_role']) is int,
                 'overlay invitation collaborator_role must be an integer')


def _apply_overlay(base, contract):
    derived = deepcopy(base)
    specifications = _manifest_sections(derived)
    section_spec = {spec['file']: spec for spec in specifications}
    for operation in contract['archive_overlay']['operations']:
        derived['entries'][operation['section']].extend(deepcopy(operation['records']))

    _require(
        [spec['name'] for spec in derived['entries']['manifest.json']['models_exported']]
        == [spec['name'] for spec in specifications],
        'model section order changed',
    )
    _require(
        [spec['file'] for spec in derived['entries']['manifest.json']['models_exported']]
        == [spec['file'] for spec in specifications],
        'model section file names changed',
    )
    for spec in specifications:
        section = spec['file']
        records = derived['entries'].get(section)
        _require(isinstance(records, list), f'manifest section is not a list: {section}')
        spec['count'] = len(records)
        _require(type(spec['count']) is int, f'manifest count is not an integer: {section}')

    _require(set(derived['entries']) == set(base['entries']),
             'overlay added or removed an archive section')
    for section, records in base['entries'].items():
        if section == 'manifest.json':
            continue
        if section not in OVERLAY_SECTIONS:
            _require(derived['entries'][section] == records,
                     f'non-overlay section changed: {section}')
        else:
            _require(derived['entries'][section][:len(records)] == records,
                     f'base records changed or reordered: {section}')
    _require(derived.get('text_files') == base.get('text_files'),
             'archive text files changed')

    for model, expected in contract['expected_counts'].items():
        spec = next((item for item in specifications if item['name'] == model), None)
        _require(spec is not None, f'expected count names unknown model: {model}')
        _require(type(expected) is int and expected == spec['count'],
                 f'{model} expected count {expected!r} != manifest {spec["count"]!r}')
    for spec in specifications:
        _require(spec['count'] == len(derived['entries'][spec['file']]),
                 f'{spec["name"]} manifest count mismatches its records')
    _require(section_spec['data/02_users.json']['count'] == 4,
             'derived User count must be 4')
    _require(section_spec['data/05_collaboration_invites.json']['count'] == 3,
             'derived CollaborationInvites count must be 3')
    return derived


def load_contract():
    """Load and validate the overlay contract without applying it."""
    contract = _read_json(fixture_path())
    _require(set(contract) == TOP_LEVEL_KEYS,
             f'fixture has unknown or missing top-level keys: '
             f'{sorted(set(contract) ^ TOP_LEVEL_KEYS)}')
    _require(type(contract['schema_version']) is int and contract['schema_version'] == 1,
             'fixture schema_version must be integer 1')
    base = load_base()
    sections = _manifest_sections(base)
    _require(set(base['entries']) == {
        *[spec['file'] for spec in sections], 'manifest.json'
    }, 'base entries differ from manifest sections plus manifest.json')
    _require(len(base['entries']) == SECTION_ENTRY_COUNT,
             f'base section entry count must be {SECTION_ENTRY_COUNT}')

    base_control = contract['base_fixture']
    _require(base_control == {
        'path': 'TESTS/fixtures/bakney-v1.json',
        'bytes': BASE_BYTES,
        'sha256': BASE_SHA256,
        'model_count': BASELINE_MODEL_COUNT,
        'section_entry_count': SECTION_ENTRY_COUNT,
    }, 'base fixture provenance/control differs')
    source = contract['historical_source']
    _require(source.get('repository') == 'carbogninalberto/django-bakney-sport',
             'historical repository differs')
    _require(source.get('revision') == BASE_REVISION, 'historical revision differs')
    _require(source.get('files_sha256') == SOURCE_SHA256,
             'pinned historical source hashes differ')
    _require(contract['overlay_algorithm'] == OVERLAY_ALGORITHM,
             'overlay canonicalization algorithm changed')
    digest = overlay_digest(contract['archive_overlay'])
    _require(contract['overlay_sha256'] == digest,
             f'overlay SHA-256 changed: expected {contract["overlay_sha256"]}, got {digest}')

    _require(set(contract['ids']) == BASE_IDS | {
        'active_collaborator',
        'inactive_collaborator',
        'deleted_collaborator',
        'unknown_user',
        'accepted_invite',
        'pending_invite',
        'expired_invite',
    }, 'fixture ID inventory differs')
    for name, value in base['ids'].items():
        _require(contract['ids'][name] == value, f'base ID changed: {name}')
    _require(len(set(contract['ids'].values())) == len(contract['ids']),
             'fixture IDs are not unique')
    _require(contract['ids']['unknown_user'] not in {
        record[PRIMARY_KEYS[OVERLAY_SECTIONS[section]]]
        for operation in contract['archive_overlay']['operations']
        for record in operation['records']
        for section in [operation['section']]
    }, 'unknown-user contrast ID must not be persisted in the overlay')

    _validate_overlay(contract, base, sections)
    retired = contract['retired_policy']['explicit_skip_records']
    _require(retired == {'BillingPlan': 1, 'PreviewAndCustomFeatures': 1},
             'retired section policy changed')
    derived = _apply_overlay(base, contract)
    archive_digest = hashlib.sha256(_zip_bytes(derived)).hexdigest()
    _require(contract['derived_archive_sha256'] == archive_digest,
             f'derived archive SHA-256 changed: '
             f'expected {contract["derived_archive_sha256"]}, got {archive_digest}')
    return contract


def derived_archive():
    """Return a fresh base archive with the fixed overlay and derived counts."""
    contract = load_contract()
    return _apply_overlay(load_base(), contract)


def historical():
    """Return the fully derived fixture in the existing archive fixture shape.

    The result retains the base ``account`` plus all base entries and text
    files. ``read_routes`` carries the fixed source, setup, retired-policy, ID
    and HTTP expectations; ``archive_bytes`` ignores that non-archive key. The
    archive fixture has no ``setup_token``; the disposable runtime supplies it.
    """
    contract = load_contract()
    archive = _apply_overlay(load_base(), contract)
    archive['read_routes'] = {
        key: deepcopy(contract[key])
        for key in (
            'base_fixture',
            'historical_source',
            'overlay_sha256',
            'derived_archive_sha256',
            'ids',
            'retired_policy',
            'setup',
            'http',
        )
    }
    return archive


def _zip_bytes(archive):
    target = io.BytesIO()
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED) as output:
        for name, content in archive['entries'].items():
            value = canonical_bytes(content)
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            output.writestr(info, value)
        for name, content in archive.get('text_files', {}).items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            output.writestr(info, content.encode('utf-8'))
    return target.getvalue()


def archive_bytes(archive=None):
    """Package the derived fixture deterministically, with no backend imports."""
    if archive is None:
        archive = historical()
        return _zip_bytes(archive)
    _manifest_sections(archive)
    return _zip_bytes(archive)


def contract_sha256():
    """Expose the deterministic derived ZIP digest for fixture controls."""
    return hashlib.sha256(archive_bytes()).hexdigest()
