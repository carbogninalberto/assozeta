"""Transport manifest for distributing one manual capture across CI runners.

A transport manifest is the small, pure-data plan a capture batch publishes
before anything runs: which workflows are captured in this batch, which are
reused from evidence, and how the fresh ones are split across runners. CI
uploads it as an artifact, so it is deliberately plain data: no tokens, no
passwords, no environment values, no absolute paths and no browser input ever
belong in it.

Sharding is delegated to :mod:`manuale_capture_shards`, so a distributed plan
cuts the fresh workflows into contiguous registry-order blocks: every workflow
except the first of a block keeps the predecessor of the sequential run, the
heaviest block is as light as possible (classic linear partition) and no shard is
ever empty.

Usage::

    transport = build_transport(..., recipes=recipes, fresh=ids, reused=[], runners=4)
    Path('transport.json').write_text(dumps(transport))
    jobs = matrix(transport)['include']
"""
import hashlib
import importlib.util
import json
import re
from datetime import date
from pathlib import Path, PurePosixPath

_shards_spec = importlib.util.spec_from_file_location(
    'manuale_capture_shards', Path(__file__).with_name('manuale_capture_shards.py'))
capture_shards = importlib.util.module_from_spec(_shards_spec)
_shards_spec.loader.exec_module(capture_shards)

MAX_RUNNERS = 6
FORMAT = 1
WORKFLOW_ID = capture_shards.WORKFLOW_ID
CAPTURE_ID = re.compile(r'[0-9a-f]{16}')
REVISION = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})')
SHA256 = re.compile(r'[0-9a-f]{64}')
RELEASE = re.compile(r'[A-Za-z0-9._-]{1,64}')
RUN_ID = re.compile(r'[0-9]+')
REFERENCE_DATE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')
TRANSPORT_KEYS = frozenset({'format', 'run_id', 'run_attempt', 'capture_id', 'application_revision',
                            'manual_revision', 'release', 'reference_date', 'tooling_hashes',
                            'registry_sha256', 'fresh', 'reused', 'shards'})
SHARD_KEYS = frozenset({'index', 'workflows', 'artifact', 'diagnostics_artifact'})
ARTIFACT = 'manuale-capture-{capture}-s{index}'
DIAGNOSTICS_ARTIFACT = 'manuale-capture-diag-{capture}-s{index}-a{attempt}'


class TransportError(ValueError):
    """A transport manifest cannot be trusted."""


def registry_rows(recipes):
    """The registry facts a reused capture is only valid against, in registry order."""
    rows = []
    for recipe in recipes:
        if not isinstance(recipe, dict):
            raise TransportError('Invalid recipe registry entry')
        checkpoints = []
        for point in recipe.get('checkpoints') or ():
            if not isinstance(point, dict) or not isinstance(point.get('id'), str):
                raise TransportError(f'Invalid checkpoint in the recipe registry: {recipe.get("id")!r}')
            checkpoints.append(point['id'])
        rows.append([recipe.get('id'), recipe.get('fixture') or 'baseline', sorted(checkpoints)])
    return rows


def registry_sha256(recipes):
    """Digest of the workflow ids, fixtures and checkpoint ids of a recipe registry."""
    return hashlib.sha256(json.dumps(registry_rows(recipes), separators=(',', ':')).encode()).hexdigest()


def build_transport(*, run_id, run_attempt, capture_id, application_revision, manual_revision,
                    release, reference_date, tooling_hashes, recipes, fresh, reused, runners):
    """Build and strictly validate the transport manifest of one capture batch.

    ``fresh`` workflows are captured by this batch and are split across
    ``runners`` contiguous shards of the registry order; ``reused`` workflows
    keep evidence captured earlier. Together they must be exactly the recipe
    registry.
    """
    if type(runners) is not int or not 1 <= runners <= MAX_RUNNERS:
        raise TransportError(f'runners must be an integer from 1 to {MAX_RUNNERS}')
    ids = _registry_ids(recipes)
    fresh_ids = _registry_subset(fresh, ids, 'fresh')
    reused_ids = _registry_subset(reused, ids, 'reused')
    overlap = sorted(set(fresh_ids) & set(reused_ids))
    if overlap:
        raise TransportError('Workflows are both fresh and reused: ' + ', '.join(overlap))
    missing = [identifier for identifier in ids if identifier not in set(fresh_ids) | set(reused_ids)]
    if missing:
        raise TransportError('Workflows are neither fresh nor reused: ' + ', '.join(missing))
    count = min(runners, len(fresh_ids))
    try:
        plan = capture_shards.contiguous_partition(recipes, fresh_ids, count) if count else []
    except (ValueError, capture_shards.ShardError) as error:
        raise TransportError('Cannot partition the fresh workflows: ' + str(error)) from error
    built = {
        'format': FORMAT,
        'run_id': run_id,
        'run_attempt': run_attempt,
        'capture_id': capture_id,
        'application_revision': application_revision,
        'manual_revision': manual_revision,
        'release': release,
        'reference_date': reference_date,
        'tooling_hashes': dict(tooling_hashes) if isinstance(tooling_hashes, dict) else tooling_hashes,
        'registry_sha256': registry_sha256(recipes),
        'fresh': fresh_ids,
        'reused': reused_ids,
        'shards': [{'index': index, 'workflows': workflows,
                    'artifact': ARTIFACT.format(capture=capture_id, index=index),
                    'diagnostics_artifact': DIAGNOSTICS_ARTIFACT.format(
                        capture=capture_id, index=index, attempt=run_attempt)}
                   for index, workflows in enumerate(plan)],
    }
    return validate_transport(built, recipes=recipes)


def validate_transport(transport, *, recipes=None):
    """Validate a transport manifest exactly; return it unchanged when it is sound.

    Every object has an exact key set, every scalar has an exact format, the
    fresh and reused workflows are unique registry-order lists, and the shards
    cover each fresh workflow exactly once under their canonical artifact names:
    concatenated, they are exactly the fresh list, so every shard is one
    contiguous registry-order block. With ``recipes`` the registry digest and the
    fresh-plus-reused registry must match too.
    """
    if not isinstance(transport, dict):
        raise TransportError('Transport manifest must be an object')
    _exact_keys(transport, TRANSPORT_KEYS, 'transport manifest')
    if type(transport['format']) is not int or transport['format'] != FORMAT:
        raise TransportError(f'Unsupported transport manifest format: {transport["format"]!r}')
    _matches(transport['run_id'], RUN_ID, 'run_id', 'a string of digits')
    if type(transport['run_attempt']) is not int or transport['run_attempt'] < 1:
        raise TransportError('run_attempt must be an integer greater than zero')
    _matches(transport['capture_id'], CAPTURE_ID, 'capture_id', '16 lowercase hexadecimal digits')
    for key in ('application_revision', 'manual_revision'):
        _matches(transport[key], REVISION, key, 'a 40 or 64 character lowercase hexadecimal revision')
    _matches(transport['release'], RELEASE, 'release', '1 to 64 characters of [A-Za-z0-9._-]')
    _reference_date(transport['reference_date'])
    _tooling_hashes(transport['tooling_hashes'])
    _matches(transport['registry_sha256'], SHA256, 'registry_sha256', 'a lowercase sha256 digest')
    fresh = _workflow_list(transport['fresh'], 'fresh')
    reused = _workflow_list(transport['reused'], 'reused')
    overlap = sorted(set(fresh) & set(reused))
    if overlap:
        raise TransportError('Workflows are both fresh and reused: ' + ', '.join(overlap))
    _shards(transport['shards'], fresh, transport['capture_id'], transport['run_attempt'])
    if recipes is not None:
        ids = _registry_ids(recipes)
        order = {identifier: position for position, identifier in enumerate(ids)}
        for label, values in (('fresh', fresh), ('reused', reused)):
            _in_registry_order(values, order, label)
        if set(fresh) | set(reused) != set(ids):
            raise TransportError('Fresh and reused workflows do not match the recipe registry')
        if transport['registry_sha256'] != registry_sha256(recipes):
            raise TransportError('Transport registry_sha256 does not match the recipe registry')
    if [identifier for shard in transport['shards'] for identifier in shard['workflows']] != fresh:
        # Each shard must be one contiguous block, so every workflow except the
        # first of a block keeps the predecessor of the sequential run.
        raise TransportError('Shard workflows must be contiguous registry-order blocks of the fresh workflows')
    return transport


def matrix(transport):
    """The GitHub Actions matrix derived from a validated transport manifest."""
    transport = validate_transport(transport)
    return {'include': [{'index': shard['index'],
                         'workflows': ','.join(shard['workflows']),
                         'artifact': shard['artifact']}
                        for shard in transport['shards']]}


def dumps(transport):
    """Serialise a validated transport manifest deterministically."""
    return json.dumps(validate_transport(transport), sort_keys=True, indent=2, ensure_ascii=False) + '\n'


def loads(text):
    """Parse and validate a transport manifest; the result is the same as :func:`dumps` wrote."""
    if not isinstance(text, str):
        raise TransportError('Transport manifest must be JSON text')
    try:
        value = json.loads(text)
    except ValueError as error:
        raise TransportError('Transport manifest is not valid JSON') from error
    return validate_transport(value)


def shard_workflows(transport, index):
    """The workflow ids of one shard, in registry order."""
    transport = validate_transport(transport)
    if type(index) is not int:
        raise TransportError('Shard index must be an integer')
    for shard in transport['shards']:
        if shard['index'] == index:
            return list(shard['workflows'])
    raise TransportError(f'Unknown shard index: {index}')


def _exact_keys(value, expected, label):
    if not isinstance(value, dict):
        raise TransportError(f'{label} must be an object')
    unknown = sorted(set(value) - expected)
    if unknown:
        raise TransportError(f'{label} has unknown keys: ' + ', '.join(unknown))
    missing = sorted(expected - set(value))
    if missing:
        raise TransportError(f'{label} is missing keys: ' + ', '.join(missing))


def _matches(value, pattern, label, expectation):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise TransportError(f'{label} must be {expectation}')


def _reference_date(value):
    _matches(value, REFERENCE_DATE, 'reference_date', 'a YYYY-MM-DD date')
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise TransportError('reference_date must be a real calendar date') from error


def _tooling_hashes(value):
    if not isinstance(value, dict):
        raise TransportError('tooling_hashes must be an object')
    for path, digest in value.items():
        if not isinstance(path, str) or not path:
            raise TransportError('tooling_hashes keys must be relative posix paths')
        if path.startswith('/') or '\\' in path or path != PurePosixPath(path).as_posix():
            raise TransportError('tooling_hashes path is not a normalised relative posix path: ' + path)
        if '..' in path:
            raise TransportError('tooling_hashes path escapes the repository: ' + path)
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise TransportError('tooling_hashes digest is not sha256: ' + path)


def _workflow_list(values, label):
    if not isinstance(values, list):
        raise TransportError(f'{label} workflows must be a list of workflow ids')
    for identifier in values:
        if not isinstance(identifier, str) or not WORKFLOW_ID.fullmatch(identifier):
            raise TransportError(f'Invalid workflow id in {label}: {identifier!r}')
    if len(set(values)) != len(values):
        raise TransportError(f'Duplicate workflow ids in {label}')
    return values


def _in_registry_order(values, order, label):
    unknown = [identifier for identifier in values if identifier not in order]
    if unknown:
        raise TransportError(f'Unknown workflows in {label}: ' + ', '.join(unknown))
    positions = [order[identifier] for identifier in values]
    if positions != sorted(positions):
        raise TransportError(f'{label} workflows are not in registry order')


def _shards(shards, fresh, capture_id, run_attempt):
    if not isinstance(shards, list):
        raise TransportError('shards must be a list of shard objects')
    if not fresh and shards:
        raise TransportError('Shards are declared although no workflow is fresh')
    if fresh and not shards:
        raise TransportError('Fresh workflows are not assigned to any shard')
    owners = {}
    for position, shard in enumerate(shards):
        _exact_keys(shard, SHARD_KEYS, f'transport shard {position}')
        index = shard['index']
        if type(index) is not int:
            raise TransportError(f'Shard {position} index must be an integer')
        if index != position:
            raise TransportError('Shard indexes must be exactly 0..n-1 in order')
        workflows = shard['workflows']
        if not isinstance(workflows, list) or not workflows:
            raise TransportError(f'Shard {index} has no workflows')
        for identifier in workflows:
            if not isinstance(identifier, str) or not WORKFLOW_ID.fullmatch(identifier):
                raise TransportError(f'Invalid workflow id in shard {index}: {identifier!r}')
            if identifier not in fresh:
                raise TransportError(f'Shard {index} runs a workflow that is not fresh: {identifier}')
            if identifier in owners:
                raise TransportError(f'Workflow is assigned to several shards: {identifier}')
            owners[identifier] = index
        artifact = ARTIFACT.format(capture=capture_id, index=index)
        if shard['artifact'] != artifact:
            raise TransportError(f'Shard {index} artifact must be exactly {artifact}')
        diagnostics = DIAGNOSTICS_ARTIFACT.format(capture=capture_id, index=index, attempt=run_attempt)
        if shard['diagnostics_artifact'] != diagnostics:
            raise TransportError(f'Shard {index} diagnostics_artifact must be exactly {diagnostics}')
    missing = [identifier for identifier in fresh if identifier not in owners]
    if missing:
        raise TransportError('Fresh workflows are not assigned to any shard: ' + ', '.join(missing))


def _registry_ids(recipes):
    if not isinstance(recipes, (list, tuple)):
        raise TransportError('Recipe registry must be a list of recipes')
    ids = []
    for recipe in recipes:
        if not isinstance(recipe, dict) or not isinstance(recipe.get('id'), str) \
                or not WORKFLOW_ID.fullmatch(recipe['id']):
            raise TransportError('Invalid recipe registry entry')
        ids.append(recipe['id'])
    if len(set(ids)) != len(ids):
        raise TransportError('Duplicate workflow ids in the recipe registry')
    return ids


def _registry_subset(values, ids, label):
    if isinstance(values, str) or not isinstance(values, (list, tuple, set, frozenset)):
        raise TransportError(f'{label} workflows must be a list of workflow ids')
    values = list(values)
    for identifier in values:
        if not isinstance(identifier, str) or not WORKFLOW_ID.fullmatch(identifier):
            raise TransportError(f'Invalid workflow id in {label}: {identifier!r}')
    if len(set(values)) != len(values):
        raise TransportError(f'Duplicate workflow ids in {label}')
    known = set(ids)
    unknown = sorted(set(values) - known)
    if unknown:
        raise TransportError(f'Unknown workflows in {label}: ' + ', '.join(unknown))
    return [identifier for identifier in ids if identifier in set(values)]
