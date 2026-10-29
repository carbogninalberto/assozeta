#!/usr/bin/env python3
"""Per-workflow, content-addressed manual capture evidence.

Evidence identity is a deterministic fingerprint of every input that can change
what a browser workflow captures. It is deliberately separate from release
identity (application commit + manual revision + version): evidence may be
reused across compatible commits, but it always keeps its original producer,
commit and capture id, and a release binding is rebuilt for every release
identity. Nothing here is release proof, and the release loader never reads
this store.

Layout::

    <store>/index.json                                   manifest sha256 per entry
    <store>/workflows/<workflow_id>/<fingerprint>/manifest.json
    <store>/workflows/<workflow_id>/<fingerprint>/evidence.json
    <store>/workflows/<workflow_id>/<fingerprint>/report.json
    <store>/blobs/sha256/<hex>                           deduplicated screenshots

Manifests refer to screenshots as ``screenshots/<sha256>.png``; the bytes live
once in the shared blob pool, so a manifest-only copy of the store (index,
manifests and evidence records, without blobs) is enough to decide reuse.
"""
from dataclasses import dataclass, field
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / 'BE') not in sys.path:
    sys.path.insert(0, str(ROOT / 'BE'))
from application.manuale import index as manual_index  # noqa: E402
from application.manuale import reuse  # noqa: E402

FORMAT = 1
SCHEMA_VERSION = 1
PURPOSE = 'workflow-evidence-not-release'
BINDING_PURPOSE = 'release-binding-of-workflow-evidence'
HASH = re.compile(r'[a-f0-9]{64}\Z')
REVISION = re.compile(r'[a-f0-9]{40}\Z')
WORKFLOW_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,127}\Z')
SCREENSHOT = re.compile(r'screenshots/([a-f0-9]{64})\.png\Z')
MAX_JSON_BYTES = 32 * 1024 ** 2
MAX_BLOB_BYTES = 64 * 1024 ** 2

# Same capture contract the existing reuse gate enforces on original reports.
CAPTURE_ENVIRONMENT = {'capture_format': 'full-hd-v1', 'viewport': {'width': 1920, 'height': 1080},
                       'device_scale_factor': 1, 'locale': 'it-IT', 'timezone': 'Europe/Rome',
                       'theme': 'light'}
# Shared capture automation, fixtures and browser/runtime configuration. A
# change to any of these invalidates every workflow fingerprint.
# The shard planner/merger decides which isolated stack captures a workflow and
# how shard results are merged, so it is shared capture tooling as well.
SHARED_CAPTURE_TOOLING = tuple(dict.fromkeys((*reuse.CORE_TOOLING, *reuse.CONFIG_INPUTS,
                                              'docs/scripts/manuale_capture_shards.py')))
SHARED_CAPTURE_PREFIXES = ('selfhost/tests/browser/manuale/fixtures/',)
REGISTRY_SHARED_KEYS = ('format', 'shared_dependencies', 'non_capture_dependencies')

# Approved producers: the GitHub API run record is the provenance authority.
TRUSTED_WORKFLOWS = frozenset({'.github/workflows/manuale.yml', '.github/workflows/manuale-release.yml',
                               '.github/workflows/publish-images.yml'})
TRUSTED_EVENTS = frozenset({'push', 'release', 'schedule', 'workflow_dispatch'})
RELEASE_TAG = re.compile(r'refs/tags/v?\d+\.\d+\.\d+(?:[-+.][0-9A-Za-z.-]+)?\Z')
PRODUCER_KEYS = ('repository', 'workflow', 'run_id', 'ref', 'head_sha', 'event', 'conclusion')
ORIGIN_KEYS = ('application_revision', 'capture_id', 'reference_date', 'manual_revision')
MANIFEST_KEYS = {'format', 'purpose', 'workflow_id', 'fingerprint', 'schema_version', 'producer',
                 'origin', 'created_at', 'files'}


class EvidenceStoreError(ValueError):
    """Base class: every rejection is a ValueError for existing CLI gates."""


class MissingEvidence(EvidenceStoreError):
    pass


class CorruptEvidence(EvidenceStoreError):
    pass


class IncompatibleEvidence(EvidenceStoreError):
    pass


class UntrustedProducer(EvidenceStoreError):
    pass


@dataclass
class Entry:
    workflow_id: str
    fingerprint: str
    path: Path
    manifest: dict
    manifest_sha256: str
    evidence: dict
    verified: str  # 'manifest' (no screenshot bytes read) or 'full'
    blobs: dict = field(default_factory=dict)  # screenshot name -> blob path (full only)

    def provenance(self):
        return {'workflow_id': self.workflow_id, 'fingerprint': self.fingerprint,
                'manifest_sha256': self.manifest_sha256, 'schema_version': self.manifest['schema_version'],
                'producer': dict(self.manifest['producer']), 'origin': dict(self.manifest['origin']),
                'created_at': self.manifest['created_at'], 'verified': self.verified}


# --------------------------------------------------------------------------
# Hashing helpers (reuse the corpus digest/canonical definitions)
# --------------------------------------------------------------------------

digest = manual_index.digest
canonical = manual_index.canonical


def _file_sha256(path, *, maximum=None):
    hasher = hashlib.sha256()
    size = 0
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if maximum is not None and size > maximum:
                raise CorruptEvidence('Evidence file is unreasonably large: ' + Path(path).name)
            hasher.update(chunk)
    return hasher.hexdigest()


class _HashCache:
    """Memoize file digests within one process; keyed by inode metadata."""

    def __init__(self):
        self.values = {}

    def __call__(self, root, relative):
        try:
            path = reuse.owned(root, relative)
        except ValueError as exc:
            raise IncompatibleEvidence('Unowned fingerprint input: ' + relative) from exc
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        if not path.is_file():
            return None
        key = (str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns)
        if key not in self.values:
            self.values[key] = _file_sha256(path)
        return self.values[key]


_HASHES = _HashCache()


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise CorruptEvidence('Duplicate JSON key: ' + key)
        value[key] = item
    return value


def _read_json_bytes(path, label):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise MissingEvidence('Missing evidence ' + label)
    if path.stat().st_size > MAX_JSON_BYTES:
        raise CorruptEvidence('Evidence JSON is unreasonably large: ' + label)
    contents = path.read_bytes()
    try:
        return contents, json.loads(contents, object_pairs_hook=_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CorruptEvidence('Invalid evidence JSON: ' + label) from exc


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()


def _safe_relative(relative, label):
    if not isinstance(relative, str) or not relative or '\\' in relative or '\0' in relative:
        raise CorruptEvidence('Unsafe ' + label + ' path: ' + repr(relative))
    path = PurePosixPath(relative)
    if path.is_absolute() or str(path) != relative or any(part in ('', '.', '..') for part in path.parts):
        raise CorruptEvidence('Unsafe ' + label + ' path: ' + repr(relative))
    return relative


def _check_workflow_id(workflow_id):
    if not isinstance(workflow_id, str) or not WORKFLOW_ID.fullmatch(workflow_id):
        raise IncompatibleEvidence('Invalid workflow identifier: ' + repr(workflow_id))
    return workflow_id


def _check_fingerprint(fingerprint):
    if not isinstance(fingerprint, str) or not HASH.fullmatch(fingerprint):
        raise IncompatibleEvidence('Invalid workflow fingerprint')
    return fingerprint


# --------------------------------------------------------------------------
# Fingerprint
# --------------------------------------------------------------------------

def _registry(root):
    try:
        value, recipes = reuse.registry(root)
    except ValueError as exc:
        raise IncompatibleEvidence('Invalid manual recipe registry: ' + str(exc)) from exc
    return value, recipes


def _recipe(root, workflow_id):
    value, recipes = _registry(root)
    if workflow_id not in recipes:
        raise IncompatibleEvidence('Unknown workflow: ' + workflow_id)
    return value, recipes[workflow_id]


def _fallback_workflow_dependencies(root, workflow_id):
    """FALLBACK until ``manuale_impact.workflow_dependencies`` is integrated.

    Same signature as the impact module. It is a conservative superset of the
    recipe's reviewed file lists (``script``, ``dependencies``, ``sources``):
    it uses the existing reuse gate's dependency contract (registered BE/UI
    production files under the recipe and shared prefixes, plus the literal
    import closure of the workflow tooling), so a store hit is never broader
    than what the retained-evidence gate would accept.
    """
    value, recipe = _recipe(root, workflow_id)
    try:
        known, production, tooling = reuse.dependency_contract(root, value, recipe)
    except ValueError as exc:
        raise IncompatibleEvidence('Workflow dependency contract unavailable: ' + str(exc)) from exc
    declared = {recipe['script'], *recipe.get('sources', [])}
    declared.update(item for item in recipe.get('dependencies', []) if not item.endswith('/'))
    return sorted((known | production | tooling | declared) - {'docs/manuale/recipes.json'})


def _impact_module():
    path = ROOT / 'docs/scripts/manuale_impact.py'
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location('manuale_impact_for_evidence', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def workflow_dependencies(root, workflow_id):
    """Delegate to the impact module when it provides the shared interface."""
    module = _impact_module()
    function = getattr(module, 'workflow_dependencies', None) if module else None
    # A tree without an impact map (e.g. an older snapshot) uses the broader
    # registry-contract superset, never a narrower guess.
    if function is None or not (Path(root) / 'docs/manuale/impact-map.json').is_file():
        return _fallback_workflow_dependencies(Path(root), workflow_id)
    # Failures in the precise analysis must not degrade to a narrower fallback.
    dependencies = function(Path(root), workflow_id)
    if not isinstance(dependencies, (list, tuple)) or not all(isinstance(item, str) for item in dependencies):
        raise IncompatibleEvidence('Impact module returned invalid workflow dependencies')
    return sorted(set(dependencies))


def _shared_tooling_paths(root):
    paths = set(SHARED_CAPTURE_TOOLING)
    for prefix in SHARED_CAPTURE_PREFIXES:
        base = Path(root) / prefix
        if base.is_dir() and not base.is_symlink():
            for file in base.rglob('*'):
                if file.is_file() and not file.is_symlink():
                    paths.add(file.relative_to(root).as_posix())
    return sorted(paths)


def workflow_manual_revision(root, workflow_id):
    """Manual mapping identity for one workflow: its pages and section mapping.

    Captures do not depend on prose; reviewed section text is still bound at
    compile time by the editorial gates. Callers wanting strict whole-manual
    identity may pass the manual commit to ``workflow_fingerprint`` instead.
    """
    root = Path(root)
    _value, recipe = _recipe(root, workflow_id)
    page_map = {}
    try:
        mapped = json.loads(reuse.owned(root, 'docs/manuale/page-map.json').read_text())
        page_map = {item['path']: item for item in mapped.get('pages', [])
                    if isinstance(item, dict) and item.get('path') in recipe.get('pages', [])}
    except FileNotFoundError:
        page_map = {}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise IncompatibleEvidence('Invalid manual page map') from exc
    return digest(canonical({'pages': recipe.get('pages', []),
                             'publication_sections': recipe.get('publication_sections'),
                             'capture_prefix': recipe.get('capture_prefix'), 'page_map': page_map}))


def fingerprint_inputs(root, workflow_id, *, manual_revision, schema_version, reference_date=None):
    """The canonical document whose sha256 is the workflow fingerprint."""
    root = Path(root).resolve()
    _check_workflow_id(workflow_id)
    if not isinstance(manual_revision, str) or not manual_revision:
        raise IncompatibleEvidence('A manual revision is required for a workflow fingerprint')
    if type(schema_version) is not int or schema_version < 1:
        raise IncompatibleEvidence('Invalid evidence schema version')
    value, recipe = _recipe(root, workflow_id)
    dependencies = set(workflow_dependencies(root, workflow_id))
    # The literal import closure of the workflow's capture tooling, exactly as
    # the retained-evidence gate binds it (e.g. shared content modules).
    try:
        dependencies |= reuse.closure(root, recipe)
    except ValueError as exc:
        raise IncompatibleEvidence('Workflow tooling closure unavailable: ' + str(exc)) from exc
    dependencies = sorted(dependencies - {'docs/manuale/recipes.json'})
    for relative in dependencies:
        _safe_relative(relative, 'dependency')
    try:
        fixture = reuse.fixture_version(root)
    except ValueError as exc:
        raise IncompatibleEvidence('Unknown manual fixture version') from exc
    return {
        'format': FORMAT, 'purpose': PURPOSE, 'schema_version': schema_version,
        'workflow': {'id': workflow_id, 'recipe': recipe},
        'registry': {key: value.get(key) for key in REGISTRY_SHARED_KEYS},
        'dependencies': {relative: _HASHES(root, relative) for relative in dependencies},
        'shared_tooling': {relative: _HASHES(root, relative) for relative in _shared_tooling_paths(root)},
        'fixtures': {'profile': recipe.get('fixture', 'baseline'), 'version': fixture},
        'browser_environment': {**CAPTURE_ENVIRONMENT, 'reference_date': reference_date},
        'manual': {'revision': manual_revision},
    }


def workflow_fingerprint(root, workflow_id, *, manual_revision, schema_version, reference_date=None):
    """sha256 hex over every input that can change the captured behavior."""
    return digest(canonical(fingerprint_inputs(root, workflow_id, manual_revision=manual_revision,
                                               schema_version=schema_version, reference_date=reference_date)))


# --------------------------------------------------------------------------
# Producer trust
# --------------------------------------------------------------------------

def producer_rejection(producer, *, repository=None, default_branch='main'):
    """Static trust rule for an authenticated producer record; None if trusted."""
    if not isinstance(producer, dict):
        return 'producer record is missing'
    if producer.get('fork'):
        return 'producer comes from a fork'
    if repository is not None and producer.get('repository') != repository:
        return 'producer repository is untrusted'
    if not isinstance(producer.get('repository'), str) or '/' not in producer['repository']:
        return 'producer repository is missing'
    event = producer.get('event')
    if isinstance(event, str) and event.startswith('pull_request'):
        return 'pull request artifacts are never a trusted baseline'
    if event not in TRUSTED_EVENTS:
        return 'producer event is not approved'
    if producer.get('workflow') not in TRUSTED_WORKFLOWS:
        return 'producer workflow is not approved'
    ref = producer.get('ref')
    if ref != 'refs/heads/' + default_branch and not (isinstance(ref, str) and RELEASE_TAG.fullmatch(ref)):
        return 'producer ref is not a trusted branch or release tag'
    if producer.get('conclusion') != 'success':
        return 'producer did not conclude successfully'
    if not isinstance(producer.get('head_sha'), str) or not REVISION.fullmatch(producer['head_sha']):
        return 'producer commit is invalid'
    if not isinstance(producer.get('run_id'), str) or not producer['run_id'].isdigit():
        return 'producer run id is invalid'
    return None


def producer_from_github_run(run):
    """Normalize a GitHub API ``actions/runs/<id>`` record to the producer shape."""
    repository = (run.get('repository') or {}).get('full_name')
    head_repository = (run.get('head_repository') or {}).get('full_name')
    branch = run.get('head_branch') or ''
    ref = run.get('ref') or ('refs/tags/' + branch if run.get('event') == 'release' else 'refs/heads/' + branch)
    status = run.get('conclusion') if run.get('status') == 'completed' else run.get('status')
    record = {'repository': repository, 'workflow': (run.get('path') or '').split('@', 1)[0],
              'run_id': str(run.get('id', '')), 'ref': ref, 'head_sha': run.get('head_sha'),
              'event': run.get('event'), 'conclusion': status}
    if head_repository != repository:
        record['fork'] = True
    return record


def producer_from_environment(environment=None):
    """Producer claim of the current GitHub job; trusted only after API lookup."""
    environment = os.environ if environment is None else environment
    workflow_ref = environment.get('GITHUB_WORKFLOW_REF', '')
    repository = environment.get('GITHUB_REPOSITORY', '')
    workflow = workflow_ref.split('@', 1)[0]
    if repository and workflow.startswith(repository + '/'):
        workflow = workflow[len(repository) + 1:]
    return {'repository': repository, 'workflow': workflow, 'run_id': environment.get('GITHUB_RUN_ID', ''),
            'ref': environment.get('GITHUB_REF', ''), 'head_sha': environment.get('GITHUB_SHA', ''),
            'event': environment.get('GITHUB_EVENT_NAME', ''),
            # Publication happens only after every verification gate passed;
            # verify() still requires the authenticated run to have succeeded.
            'conclusion': 'success'}


def gh_json(endpoint):
    return json.loads(subprocess.check_output(['gh', 'api', endpoint]))


def github_trusted_producers(repository, run_ids, *, api=gh_json, default_branch='main'):
    """Authenticate producer runs with the GitHub API; return trusted records only."""
    trusted, rejected = {}, {}
    for run_id in sorted({str(item) for item in run_ids if str(item).isdigit()}):
        try:
            record = producer_from_github_run(api(f'repos/{repository}/actions/runs/{run_id}'))
        except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
            rejected[run_id] = 'producer lookup failed (' + type(exc).__name__ + ')'
            continue
        reason = producer_rejection(record, repository=repository, default_branch=default_branch)
        if reason or record['run_id'] != run_id:
            rejected[run_id] = reason or 'producer run id mismatch'
        else:
            trusted[run_id] = record
    return trusted, rejected


def _check_producer(producer, trusted_producers, *, repository=None, default_branch='main'):
    if not isinstance(producer, dict) or set(producer) != set(PRODUCER_KEYS):
        raise CorruptEvidence('Evidence manifest has an invalid producer record')
    reason = producer_rejection(producer, repository=repository, default_branch=default_branch)
    if reason:
        raise UntrustedProducer(reason)
    authenticated = (trusted_producers or {}).get(producer['run_id'])
    if authenticated is None:
        raise UntrustedProducer('producer run is not an authenticated trusted producer')
    reason = producer_rejection(authenticated, repository=repository, default_branch=default_branch)
    if reason:
        raise UntrustedProducer('authenticated producer: ' + reason)
    for key in PRODUCER_KEYS:
        if authenticated.get(key) != producer[key]:
            raise UntrustedProducer('producer claim differs from its authenticated run: ' + key)


# --------------------------------------------------------------------------
# Store access
# --------------------------------------------------------------------------

def _entry_dir(store, workflow_id, fingerprint):
    return Path(store) / 'workflows' / _check_workflow_id(workflow_id) / _check_fingerprint(fingerprint)


def _blob_path(store, sha):
    if not HASH.fullmatch(sha):
        raise CorruptEvidence('Invalid blob digest')
    return Path(store) / 'blobs' / 'sha256' / sha


def _no_symlinks(store, path):
    current = Path(store)
    if current.is_symlink():
        raise CorruptEvidence('The evidence store must not be a symlink')
    for part in Path(path).relative_to(current).parts:
        current = current / part
        if current.is_symlink():
            raise CorruptEvidence('Symlink inside the evidence store')


def read_index(store):
    path = Path(store) / 'index.json'
    if not path.exists():
        return {'format': FORMAT, 'entries': {}}
    _bytes, value = _read_json_bytes(path, 'store index')
    if (not isinstance(value, dict) or value.get('format') != FORMAT
            or not isinstance(value.get('entries'), dict)):
        raise CorruptEvidence('Invalid evidence store index')
    return value


class _Lock:
    def __init__(self, store):
        self.path = Path(store) / '.lock'

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open('a')
        fcntl.flock(self.stream, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.stream, fcntl.LOCK_UN)
        self.stream.close()


def _atomic_write(path, contents):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '.')
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _write_blob(store, contents):
    sha = digest(contents)
    if len(contents) > MAX_BLOB_BYTES:
        raise CorruptEvidence('Screenshot is unreasonably large')
    manual_index.png_dimensions(contents)
    path = _blob_path(store, sha)
    if path.is_file() and not path.is_symlink() and _file_sha256(path) == sha:
        return sha
    if path.is_symlink():
        path.unlink()
    _atomic_write(path, contents)
    return sha


def _validate_origin(origin):
    if not isinstance(origin, dict) or set(origin) != set(ORIGIN_KEYS):
        raise CorruptEvidence('Evidence origin must record exactly ' + ', '.join(ORIGIN_KEYS))
    if not isinstance(origin['application_revision'], str) or not REVISION.fullmatch(origin['application_revision']):
        raise CorruptEvidence('Evidence origin has an invalid application revision')
    for key in ('capture_id', 'reference_date', 'manual_revision'):
        if not isinstance(origin[key], str) or not origin[key]:
            raise CorruptEvidence('Evidence origin is missing ' + key)
    return origin


def _report_captures(report):
    """Run-relative capture files the scenario report claims, with their hashes."""
    files = {}
    if not isinstance(report.get('screenshots'), list) or not report['screenshots']:
        raise CorruptEvidence('Scenario report has no screenshots')
    for frame in report['screenshots']:
        if not isinstance(frame, dict) or not isinstance(frame.get('master'), dict):
            raise CorruptEvidence('Scenario report has an invalid screenshot record')
        relative = _safe_relative(frame.get('path'), 'screenshot')
        if not relative.startswith('images/') or not relative.endswith('.png'):
            raise CorruptEvidence('Screenshot is outside the owned image prefix: ' + relative)
        records = [('captures/' + relative, frame.get('sha256')), (frame['master'].get('path'), frame['master'].get('sha256'))]
        if frame.get('stability'):
            records.append((frame['stability'].get('path'), frame['stability'].get('sha256')))
        for path, sha in records:
            _safe_relative(path, 'capture')
            if not path.startswith(('captures/images/', 'masters/images/', 'masters/repeats/images/')):
                raise CorruptEvidence('Unowned capture path: ' + path)
            if not isinstance(sha, str) or not HASH.fullmatch(sha):
                raise CorruptEvidence('Screenshot record has no sha256: ' + path)
            if files.get(path, sha) != sha:
                raise CorruptEvidence('Conflicting screenshot hashes: ' + path)
            files[path] = sha
    return files


def publish(store, *, workflow_id, fingerprint, schema_version, producer, origin, report_bytes,
            screenshots, scenario=None, sections=(), editorial_bindings=None, inputs=None, created_at=None,
            editorial=None, origin_release=None):
    """Atomically publish one workflow evidence entry and record it in the index.

    ``screenshots`` maps run-relative capture paths (``captures/…``,
    ``masters/…``) to bytes or files; every one is checked against the report.
    The entry never contains release identity. ``editorial`` (the reviewed
    section bindings the existing reuse gate checks) and ``origin_release`` (the
    producer run's release label, recorded as origin only) let a consumer admit
    the entry through the same per-recipe gate as a whole-run baseline.
    """
    store = Path(store)
    _check_workflow_id(workflow_id)
    _check_fingerprint(fingerprint)
    if type(schema_version) is not int or schema_version < 1:
        raise IncompatibleEvidence('Invalid evidence schema version')
    if not isinstance(producer, dict) or set(producer) != set(PRODUCER_KEYS):
        raise CorruptEvidence('Producer must record exactly ' + ', '.join(PRODUCER_KEYS))
    producer = {key: str(producer[key]) for key in PRODUCER_KEYS}
    origin = _validate_origin(dict(origin))
    try:
        report = json.loads(report_bytes, object_pairs_hook=_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CorruptEvidence('Scenario report is not JSON') from exc
    if (report.get('id') != workflow_id or report.get('status') != 'passed' or report.get('backend') != 'real'
            or report.get('application_revision') != origin['application_revision']
            or report.get('capture_id') != origin['capture_id']
            or report.get('reference_date') != origin['reference_date']):
        raise IncompatibleEvidence('Only a passed real capture report for its origin can be published')
    if any(report.get(key) != value for key, value in CAPTURE_ENVIRONMENT.items()):
        raise IncompatibleEvidence('Scenario report capture environment differs from the contract')
    claimed = _report_captures(report)
    if set(screenshots) != set(claimed):
        raise IncompatibleEvidence('Screenshot files differ from the scenario report')
    store.mkdir(parents=True, exist_ok=True)
    captures, files = {}, {}
    for relative, source in sorted(screenshots.items()):
        contents = source if isinstance(source, bytes) else Path(source).read_bytes()
        sha = _write_blob(store, contents)
        if sha != claimed[relative]:
            raise CorruptEvidence('Screenshot bytes differ from the scenario report: ' + relative)
        captures[relative] = sha
        files['screenshots/' + sha + '.png'] = sha
    evidence = {'format': FORMAT, 'workflow_id': workflow_id,
                'report': {'path': workflow_id + '.json', 'sha256': digest(report_bytes)},
                'captures': captures, 'scenario': scenario, 'sections': list(sections),
                'editorial_bindings': editorial_bindings or {},
                'inputs_sha256': digest(canonical(inputs)) if inputs is not None else None,
                'inputs': inputs, 'editorial': editorial, 'origin_release': origin_release}
    evidence_bytes = _json_bytes(evidence)
    files['evidence.json'] = digest(evidence_bytes)
    files['report.json'] = digest(report_bytes)
    manifest = {'format': FORMAT, 'purpose': PURPOSE, 'workflow_id': workflow_id, 'fingerprint': fingerprint,
                'schema_version': schema_version, 'producer': producer, 'origin': origin,
                'created_at': created_at or time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'files': dict(sorted(files.items()))}
    manifest_bytes = _json_bytes(manifest)
    target = _entry_dir(store, workflow_id, fingerprint)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=target.parent, prefix='.staging-'))
    try:
        (staging / 'report.json').write_bytes(report_bytes)
        (staging / 'evidence.json').write_bytes(evidence_bytes)
        (staging / 'manifest.json').write_bytes(manifest_bytes)
        with _Lock(store):
            retired = None
            if target.exists() or target.is_symlink():
                retired = target.with_name('.retired-' + target.name + '-' + os.urandom(4).hex())
                target.rename(retired)
            staging.rename(target)
            index = read_index(store)
            index['entries'][workflow_id + '/' + fingerprint] = {
                'manifest_sha256': digest(manifest_bytes), 'producer_run_id': producer['run_id'],
                'origin_application_revision': origin['application_revision']}
            index['entries'] = dict(sorted(index['entries'].items()))
            _atomic_write(store / 'index.json', _json_bytes(index))
            if retired is not None:
                shutil.rmtree(retired, ignore_errors=True)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    # Integrity only: trust is judged by every consumer, never by the publisher.
    return _verify(store, workflow_id, fingerprint, schema_version=schema_version, trust=None)


def verify(store, workflow_id, fingerprint, *, trusted_producers, schema_version=SCHEMA_VERSION,
           manifest_only=False, repository=None, reference_date=None, default_branch='main'):
    """Return a verified Entry or raise. Never returns partially checked evidence.

    ``manifest_only`` verifies index, manifest, provenance and evidence records
    without reading screenshot blobs; it is a reuse decision, not admission.
    """
    return _verify(store, workflow_id, fingerprint, schema_version=schema_version,
                   manifest_only=manifest_only, reference_date=reference_date,
                   trust={'trusted_producers': trusted_producers, 'repository': repository,
                          'default_branch': default_branch})


def _verify(store, workflow_id, fingerprint, *, schema_version, trust, manifest_only=False, reference_date=None):
    store = Path(store)
    target = _entry_dir(store, workflow_id, fingerprint)
    if not store.is_dir() or not target.is_dir():
        raise MissingEvidence('No evidence for ' + workflow_id + ' at this fingerprint')
    _no_symlinks(store, target)
    recorded = read_index(store)['entries'].get(workflow_id + '/' + fingerprint)
    if not isinstance(recorded, dict) or not HASH.fullmatch(str(recorded.get('manifest_sha256', ''))):
        raise CorruptEvidence('Evidence entry is not recorded in the store index')
    manifest_bytes, manifest = _read_json_bytes(target / 'manifest.json', 'manifest.json')
    manifest_sha = digest(manifest_bytes)
    if manifest_sha != recorded['manifest_sha256']:
        raise CorruptEvidence('Evidence manifest differs from its indexed sha256')
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_KEYS:
        raise CorruptEvidence('Evidence manifest has an invalid structure')
    if manifest['format'] != FORMAT or manifest['purpose'] != PURPOSE:
        raise IncompatibleEvidence('Unsupported evidence manifest format')
    if manifest['schema_version'] != schema_version:
        raise IncompatibleEvidence('Evidence schema version differs: ' + str(manifest['schema_version']))
    if manifest['workflow_id'] != workflow_id or manifest['fingerprint'] != fingerprint:
        raise CorruptEvidence('Evidence manifest identity differs from its location')
    origin = _validate_origin(manifest['origin'])
    if reference_date is not None and origin['reference_date'] != reference_date:
        raise IncompatibleEvidence('Evidence reference date differs')
    if trust is not None:
        _check_producer(manifest['producer'], trust['trusted_producers'], repository=trust['repository'],
                        default_branch=trust['default_branch'])
        if manifest['producer']['head_sha'] != origin['application_revision']:
            raise UntrustedProducer('Evidence origin commit differs from its producer commit')
    files = manifest['files']
    if not isinstance(files, dict) or {'evidence.json', 'report.json'} - set(files):
        raise CorruptEvidence('Evidence manifest is missing its evidence records')
    screenshots = {}
    for name, sha in files.items():
        if not isinstance(sha, str) or not HASH.fullmatch(sha):
            raise CorruptEvidence('Evidence manifest has an invalid hash for ' + str(name))
        if name in ('evidence.json', 'report.json'):
            continue
        match = SCREENSHOT.fullmatch(name) if isinstance(name, str) else None
        if not match or match[1] != sha:
            raise CorruptEvidence('Evidence manifest has an unowned file: ' + repr(name))
        screenshots[name] = sha
    actual = {path.name for path in target.iterdir()}
    if actual != {'manifest.json', 'evidence.json', 'report.json'}:
        raise CorruptEvidence('Evidence entry contains missing or unexpected files')
    evidence_bytes, evidence = _read_json_bytes(target / 'evidence.json', 'evidence.json')
    report_bytes, report = _read_json_bytes(target / 'report.json', 'report.json')
    if digest(evidence_bytes) != files['evidence.json'] or digest(report_bytes) != files['report.json']:
        raise CorruptEvidence('Evidence record bytes differ from the manifest')
    if (not isinstance(evidence, dict) or evidence.get('format') != FORMAT
            or evidence.get('workflow_id') != workflow_id
            or evidence.get('report') != {'path': workflow_id + '.json', 'sha256': files['report.json']}
            or not isinstance(evidence.get('captures'), dict)):
        raise CorruptEvidence('Evidence record is not bound to its manifest')
    if (report.get('id') != workflow_id or report.get('status') != 'passed' or report.get('backend') != 'real'
            or report.get('application_revision') != origin['application_revision']
            or report.get('capture_id') != origin['capture_id']
            or report.get('reference_date') != origin['reference_date']):
        raise CorruptEvidence('Scenario report differs from the evidence origin')
    claimed = _report_captures(report)
    if evidence['captures'] != claimed or set(screenshots) != {'screenshots/' + sha + '.png' for sha in claimed.values()}:
        raise CorruptEvidence('Evidence screenshot inventory differs from the scenario report')
    blobs = {}
    if not manifest_only:
        for name, sha in screenshots.items():
            path = _blob_path(store, sha)
            if path.is_symlink() or not path.is_file():
                raise MissingEvidence('Screenshot blob is missing: ' + name)
            if _file_sha256(path, maximum=MAX_BLOB_BYTES) != sha:
                raise CorruptEvidence('Screenshot blob is corrupted: ' + name)
            with path.open('rb') as stream:
                manual_index.png_dimensions(stream.read(24))
            blobs[name] = path
    return Entry(workflow_id, fingerprint, target, manifest, manifest_sha, evidence,
                 'manifest' if manifest_only else 'full', blobs)


def lookup(store, workflow_id, fingerprint, *, trusted_producers, **options):
    """Return a verified Entry, or None on a miss (cold fallback). Rejections raise."""
    if store is None or not Path(store).is_dir():
        return None
    if not _entry_dir(store, workflow_id, fingerprint).exists():
        return None
    return verify(store, workflow_id, fingerprint, trusted_producers=trusted_producers, **options)


def materialize(entry, run):
    """Copy fully verified evidence into a run directory at its original paths.

    Original report bytes, revision and capture id are preserved; the returned
    provenance record must accompany the files so they are never presented as
    fresh captures of the target commit.
    """
    if not isinstance(entry, Entry) or entry.verified != 'full':
        raise IncompatibleEvidence('Only fully verified evidence can be materialized')
    run = Path(run)
    report_bytes = (entry.path / 'report.json').read_bytes()
    if digest(report_bytes) != entry.manifest['files']['report.json']:
        raise CorruptEvidence('Scenario report changed after verification')
    planned = {entry.evidence['report']['path']: report_bytes}
    for relative, sha in entry.evidence['captures'].items():
        contents = entry.blobs['screenshots/' + sha + '.png'].read_bytes()
        if digest(contents) != sha:
            raise CorruptEvidence('Screenshot blob changed after verification: ' + relative)
        planned[relative] = contents
    manifest_copy = 'evidence-store/' + entry.workflow_id + '.manifest.json'
    planned[manifest_copy] = (entry.path / 'manifest.json').read_bytes()
    if digest(planned[manifest_copy]) != entry.manifest_sha256:
        raise CorruptEvidence('Evidence manifest changed after verification')
    evidence_copy = 'evidence-store/' + entry.workflow_id + '.evidence.json'
    planned[evidence_copy] = (entry.path / 'evidence.json').read_bytes()
    if digest(planned[evidence_copy]) != entry.manifest['files']['evidence.json']:
        raise CorruptEvidence('Evidence record changed after verification')
    for relative, contents in planned.items():
        target = reuse.owned(run, _safe_relative(relative, 'run'))
        if target.exists() and target.read_bytes() != contents:
            raise IncompatibleEvidence('Materialized evidence collides with different run bytes: ' + relative)
    for relative, contents in planned.items():
        _atomic_write(reuse.owned(run, relative), contents)
    report = json.loads(report_bytes)
    for frame in report['screenshots']:
        manual_index.verify_capture_frame(run, frame)
    return {**entry.provenance(), 'report_path': entry.evidence['report']['path'],
            'report_sha256': entry.evidence['report']['sha256'], 'manifest_path': manifest_copy,
            'evidence_path': evidence_copy, 'evidence_sha256': entry.manifest['files']['evidence.json']}


# --------------------------------------------------------------------------
# Store maintenance: carry verified entries forward, merge a run's candidate
# --------------------------------------------------------------------------

def _copy_entry(target_store, entry):
    """Copy one fully verified entry (bytes unchanged) and index its manifest."""
    if not isinstance(entry, Entry) or entry.verified != 'full':
        raise IncompatibleEvidence('Only fully verified evidence can be copied')
    target_store = Path(target_store)
    for name, path in entry.blobs.items():
        if _write_blob(target_store, path.read_bytes()) != SCREENSHOT.fullmatch(name)[1]:
            raise CorruptEvidence('Screenshot blob changed while copying: ' + name)
    target = _entry_dir(target_store, entry.workflow_id, entry.fingerprint)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=target.parent, prefix='.staging-'))
    try:
        for name in ('manifest.json', 'evidence.json', 'report.json'):
            (staging / name).write_bytes((entry.path / name).read_bytes())
        if digest((staging / 'manifest.json').read_bytes()) != entry.manifest_sha256:
            raise CorruptEvidence('Evidence manifest changed while copying')
        with _Lock(target_store):
            if target.exists():
                shutil.rmtree(target)
            staging.rename(target)
            index = read_index(target_store)
            index['entries'][entry.workflow_id + '/' + entry.fingerprint] = {
                'manifest_sha256': entry.manifest_sha256, 'producer_run_id': entry.manifest['producer']['run_id'],
                'origin_application_revision': entry.manifest['origin']['application_revision']}
            index['entries'] = dict(sorted(index['entries'].items()))
            _atomic_write(target_store / 'index.json', _json_bytes(index))
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return _verify(target_store, entry.workflow_id, entry.fingerprint, schema_version=entry.manifest['schema_version'],
                   trust=None)


def merge_candidate(candidate, store, *, run_id):
    """Merge this run's freshly published entries into the store.

    Integrity is verified in full; the producer must be exactly this run (its
    trust is established later, by the GitHub API, once the run succeeded).
    """
    merged, rejected = [], []
    if candidate is None or not Path(candidate).is_dir():
        return {'merged': merged, 'rejected': rejected}
    for key in sorted(read_index(candidate)['entries']):
        workflow_id, _, fingerprint = key.partition('/')
        try:
            entry = _verify(candidate, workflow_id, fingerprint, schema_version=SCHEMA_VERSION, trust=None)
            if entry.manifest['producer']['run_id'] != str(run_id):
                raise UntrustedProducer('candidate entry was produced by another run')
            reason = producer_rejection({**entry.manifest['producer']})
            if reason:
                raise UntrustedProducer(reason)
            _copy_entry(store, entry)
            merged.append(key)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            rejected.append({'entry': key, 'reason': type(exc).__name__ + ': ' + str(exc)})
    return {'merged': merged, 'rejected': rejected}


def compact(store, application, *, trusted_producers, reference_date, repository=None, default_branch='main'):
    """Keep only fully verified trusted entries at the current fingerprints.

    Entries for other fingerprints, failing verification or from untrusted
    producers are dropped, and unreferenced blobs are removed, so the store
    stays bounded to one entry per workflow.
    """
    store = Path(store)
    kept, dropped = [], []
    if not store.is_dir():
        return {'kept': kept, 'dropped': dropped}
    _value, recipes = _registry(application)
    current = {}
    for identifier in recipes:
        try:
            current[identifier] = workflow_fingerprint(
                application, identifier, manual_revision=workflow_manual_revision(application, identifier),
                schema_version=SCHEMA_VERSION, reference_date=reference_date)
        except ValueError:
            continue
    with _Lock(store):
        index = read_index(store)
        referenced = set()
        for key in sorted(index['entries']):
            workflow_id, _, fingerprint = key.partition('/')
            try:
                if current.get(workflow_id) != fingerprint:
                    raise IncompatibleEvidence('not the current fingerprint of this workflow')
                entry = verify(store, workflow_id, fingerprint, trusted_producers=trusted_producers,
                               repository=repository, reference_date=reference_date, default_branch=default_branch)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                dropped.append({'entry': key, 'reason': type(exc).__name__ + ': ' + str(exc)})
                del index['entries'][key]
                continue
            referenced.update(SCREENSHOT.fullmatch(name)[1] for name in entry.blobs)
            kept.append(key)
        _atomic_write(store / 'index.json', _json_bytes(index))
        live = {tuple(key.split('/', 1)) for key in index['entries']}
        workflows = store / 'workflows'
        if workflows.is_dir():
            for directory in sorted(workflows.glob('*/*')):
                if directory.is_dir() and (directory.parent.name, directory.name) not in live:
                    shutil.rmtree(directory)
            for directory in sorted(workflows.iterdir()):
                if directory.is_dir() and not any(directory.iterdir()):
                    directory.rmdir()
        blobs = store / 'blobs' / 'sha256'
        if blobs.is_dir():
            for blob in blobs.iterdir():
                if blob.name not in referenced:
                    blob.unlink()
    return {'kept': kept, 'dropped': dropped}


# --------------------------------------------------------------------------
# Consumer gate: admit a store-materialized selection into a target run
# --------------------------------------------------------------------------

SELECTION_FILE = 'evidence-store-selection.json'
SELECTION_PURPOSE = 'reused-workflow-evidence-not-fresh-capture'
ORIGIN_MANIFEST_PURPOSE = 'evidence-store-origin'
# The authenticated producer runs (the `store-producers` record: GitHub API
# lookups) a selection was materialized with. Without it nothing is admitted.
TRUSTED_PRODUCERS_FILE = 'trusted-producers.json'


def trusted_producers_record(trusted, *, repository):
    """The `store-producers` record shape, for a selection's trusted-producers.json."""
    return {'format': FORMAT, 'repository': repository, 'trusted': dict(trusted or {}), 'rejected': {}}


def selection_trust(selection_dir, selection, *, repository=None):
    """(trusted producers, repository) of a selection; raises when it carries no authenticated record.

    A selection directory assembled by hand (or copied without the record that
    materialize wrote next to it) is never trusted: admission needs the same
    authenticated producer records the store planner used, bound by sha256 to
    the selection file.
    """
    path = Path(selection_dir) / TRUSTED_PRODUCERS_FILE
    if path.is_symlink() or not path.is_file():
        raise UntrustedProducer('selection has no authenticated trusted-producers record')
    record_bytes, record = _read_json_bytes(path, TRUSTED_PRODUCERS_FILE)
    if selection.get('trusted_producers_sha256') != digest(record_bytes):
        raise CorruptEvidence('trusted-producers record differs from the one bound to the selection')
    if (not isinstance(record, dict) or record.get('format') != FORMAT or not isinstance(record.get('trusted'), dict)
            or not isinstance(record.get('repository'), str) or '/' not in record['repository']):
        raise UntrustedProducer('trusted-producers record is malformed')
    if repository is not None and record['repository'] != repository:
        raise UntrustedProducer('trusted-producers record belongs to another repository')
    return record['trusted'], record['repository']


def store_origin_manifest(manifest, evidence, origin_release):
    """Single-workflow origin manifest in the shape the per-recipe reuse gate reads.

    It embeds the verified store manifest and evidence record, so later gates
    (retained-plan validation, the corpus index, export) can re-check the hash
    chain store manifest -> evidence/report without the store itself.
    """
    identifier = manifest['workflow_id']
    pages = {}
    for item in evidence.get('sections') or []:
        if not isinstance(item, dict) or not isinstance(item.get('page'), str):
            raise CorruptEvidence('Evidence section record is invalid: ' + identifier)
        section = {key: value for key, value in item.items() if key != 'page'}
        pages.setdefault(item['page'], []).append(section)
    return {'format': FORMAT, 'purpose': ORIGIN_MANIFEST_PURPOSE,
            'metadata': {'application_revision': manifest['origin']['application_revision'],
                         'release': origin_release, 'manual_revision': manifest['origin']['manual_revision']},
            'scenarios': [evidence['scenario']],
            'pages': [{'path': path, 'sections': sections} for path, sections in sorted(pages.items())],
            'evidence_store': {'manifest': manifest, 'manifest_sha256': digest(_json_bytes(manifest)),
                               'evidence_sha256': manifest['files']['evidence.json']}}


def _admit_one(state, selection_dir, item, recipes, registry_value, *, repository, default_branch, trusted):
    identifier = _check_workflow_id(item.get('workflow_id'))
    if identifier not in recipes:
        raise IncompatibleEvidence('workflow is not in the current registry')
    recipe = recipes[identifier]
    manifest_path = 'evidence-store/' + identifier + '.manifest.json'
    evidence_path = 'evidence-store/' + identifier + '.evidence.json'
    report_path = identifier + '.json'
    if item.get('manifest_path') != manifest_path or item.get('evidence_path') != evidence_path \
            or item.get('report_path') != report_path:
        raise CorruptEvidence('selection paths differ from the store layout')
    manifest_bytes, manifest = _read_json_bytes(reuse.owned(selection_dir, manifest_path), manifest_path)
    if digest(manifest_bytes) != item.get('manifest_sha256') or _json_bytes(manifest) != manifest_bytes:
        raise CorruptEvidence('materialized manifest differs from its recorded sha256')
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_KEYS or manifest['purpose'] != PURPOSE \
            or manifest['format'] != FORMAT or manifest['schema_version'] != SCHEMA_VERSION:
        raise IncompatibleEvidence('materialized manifest is not a current evidence manifest')
    if manifest['workflow_id'] != identifier or manifest['fingerprint'] != item.get('fingerprint'):
        raise CorruptEvidence('materialized manifest identity differs from the selection')
    origin = _validate_origin(manifest['origin'])
    if item.get('origin') != origin or item.get('producer') != manifest['producer']:
        raise CorruptEvidence('selection provenance differs from the materialized manifest')
    # The producer claim must equal its authenticated GitHub run record (looked
    # up when the store was planned and materialized) and pass the trust rules.
    _check_producer(manifest['producer'], trusted, repository=repository, default_branch=default_branch)
    if manifest['producer']['head_sha'] != origin['application_revision']:
        raise UntrustedProducer('evidence origin commit differs from its producer commit')
    if origin['reference_date'] != state['reference_date']:
        raise IncompatibleEvidence('evidence reference date differs from the target run')
    evidence_bytes, evidence = _read_json_bytes(reuse.owned(selection_dir, evidence_path), evidence_path)
    report_bytes, report = _read_json_bytes(reuse.owned(selection_dir, report_path), report_path)
    if digest(evidence_bytes) != manifest['files'].get('evidence.json') \
            or digest(report_bytes) != manifest['files'].get('report.json') \
            or item.get('report_sha256') != manifest['files'].get('report.json'):
        raise CorruptEvidence('materialized evidence or report bytes differ from the manifest')
    if evidence.get('report') != {'path': report_path, 'sha256': manifest['files']['report.json']}:
        raise CorruptEvidence('evidence record is not bound to its report')
    claimed = _report_captures(report)
    if evidence.get('captures') != claimed:
        raise CorruptEvidence('evidence screenshot inventory differs from the report')
    for relative, sha in claimed.items():
        path = reuse.owned(selection_dir, relative)
        if path.is_symlink() or not path.is_file() or _file_sha256(path, maximum=MAX_BLOB_BYTES) != sha:
            raise CorruptEvidence('materialized screenshot differs from the manifest: ' + relative)
    # Evidence identity: the fingerprint is recomputed from the target checkout.
    current_inputs = fingerprint_inputs(state['application'], identifier,
                                        manual_revision=workflow_manual_revision(state['application'], identifier),
                                        schema_version=SCHEMA_VERSION, reference_date=state['reference_date'])
    fingerprint = digest(canonical(current_inputs))
    if fingerprint != manifest['fingerprint'] or evidence.get('inputs_sha256') != fingerprint \
            or evidence.get('inputs') != current_inputs:
        raise IncompatibleEvidence('workflow fingerprint differs from the target checkout')
    # The tooling closure and every recorded source must be covered by the
    # fingerprint; anything outside it is unknown and fails closed. Prefix-listed
    # production files outside the fingerprint are the ones the precise impact
    # closure proves unreachable: they are bound at their current bytes only.
    known, production, tooling = reuse.dependency_contract(state['application'], registry_value, recipe)
    hashes = report.get('source_hashes') or {}
    reuse.verify_snapshot(state['application'], state['application_input'], known | set(hashes))
    covered = {**current_inputs['dependencies'], **current_inputs['shared_tooling']}
    dependencies = {}
    for relative in sorted(production | tooling | set(hashes)):
        current = reuse.file_hash(state['application'], relative)
        required = relative in tooling or relative in hashes or relative in covered
        if required and covered.get(relative) != current:
            raise IncompatibleEvidence('dependency is not covered by the workflow fingerprint: ' + relative)
        dependencies[relative] = current
    scenario = evidence.get('scenario')
    if (not isinstance(scenario, dict) or scenario.get('id') != identifier or scenario.get('status') != 'passed'
            or scenario.get('report_path') != report_path
            or scenario.get('report_sha256') != manifest['files']['report.json']
            or scenario.get('application_revision') != origin['application_revision']
            or scenario.get('source_hashes') != hashes or 'reuse' in scenario):
        raise CorruptEvidence('evidence scenario binding is not an original passed capture')
    editorial = evidence.get('editorial')
    origin_release = evidence.get('origin_release')
    if not isinstance(editorial, list) or not isinstance(origin_release, str) or not origin_release:
        raise IncompatibleEvidence('evidence has no reviewed section binding; capture fresh')
    reuse_origin = {'run_id': 'github-actions-run-' + manifest['producer']['run_id'],
                    'application_revision': origin['application_revision'], 'release': origin_release,
                    'capture_id': origin['capture_id'], 'manual_revision': origin['manual_revision']}
    # Same report and editorial gates as a whole-run baseline (captures are
    # read from the materialized selection directory).
    reuse.check_report({**state, 'run': str(selection_dir)}, recipe, report, reuse_origin)
    editorial = json.loads(json.dumps(editorial))
    materialization = {'format': 1, 'capture_id': origin['capture_id'], 'scenario_id': identifier,
                       'report_sha256': manifest['files']['report.json']}
    for section in editorial:
        if not isinstance(section, dict) or section.get('materialization') != materialization:
            raise CorruptEvidence('reviewed section is not bound to this capture')
    reuse.bind_editorial_updates(state, editorial, report)
    reuse.check_editorial(state, editorial)
    origin_manifest = store_origin_manifest(manifest, evidence, origin_release)
    origin_manifest_bytes = _json_bytes(origin_manifest)
    entry = {'origin': reuse_origin,
             'origin_manifest': {'path': f'evidence-origin/{identifier}.manifest.json',
                                 'sha256': digest(origin_manifest_bytes)},
             'report_path': report_path, 'report_sha256': manifest['files']['report.json'],
             'recipe_contract_sha256': digest(canonical({'recipe': recipe,
                                                         'shared_dependencies': registry_value['shared_dependencies']})),
             'dependency_hashes': dependencies, 'production_paths': sorted(production),
             'tooling_paths': sorted(tooling), 'sections': editorial,
             'source': 'evidence-store',
             'evidence_store': {'workflow_id': identifier, 'fingerprint': manifest['fingerprint'],
                                'manifest_sha256': digest(manifest_bytes),
                                'evidence_sha256': manifest['files']['evidence.json'],
                                'schema_version': manifest['schema_version'],
                                'producer': dict(manifest['producer'])}}
    files = [(report_path, report_path)] + [(relative, relative) for relative in sorted(claimed)]
    return {'entry': entry, 'root': str(selection_dir), 'files': files,
            'origin_manifest_bytes': origin_manifest_bytes}


def admit_selection(state, selection_dir, *, exclude=(), repository=None, default_branch='main'):
    """Admit a store-materialized selection as explicit per-workflow provenance.

    The selection must carry the authenticated trusted-producers record it was
    materialized with (``selection_trust``); otherwise nothing is admitted.
    Every hash is re-verified (selection -> manifest -> evidence/report ->
    screenshots), the fingerprint is recomputed from the target checkout and
    the same report/editorial gates as a whole-run baseline apply. Anything
    rejected is captured fresh; nothing here weakens the corpus gates, which
    re-validate the resulting retained-evidence plan independently.
    """
    selection_dir = Path(selection_dir).resolve()
    _bytes, selection = _read_json_bytes(selection_dir / SELECTION_FILE, SELECTION_FILE)
    if (not isinstance(selection, dict) or selection.get('format') != FORMAT
            or selection.get('purpose') != SELECTION_PURPOSE or selection.get('schema_version') != SCHEMA_VERSION
            or not isinstance(selection.get('materialized'), list)):
        raise IncompatibleEvidence('Not a current evidence-store selection')
    trusted, repository = selection_trust(selection_dir, selection, repository=repository)
    registry_value, recipes = _registry(state['application'])
    admitted, rejected = {}, []
    for item in selection['materialized']:
        identifier = item.get('workflow_id') if isinstance(item, dict) else None
        if identifier in exclude:
            rejected.append({'workflow_id': identifier, 'reason': 'selected for fresh capture'})
            continue
        if identifier in admitted:
            raise CorruptEvidence('Duplicate workflow in the evidence-store selection')
        try:
            admitted[identifier] = _admit_one(state, selection_dir, item, recipes, registry_value,
                                              repository=repository, default_branch=default_branch,
                                              trusted=trusted)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            rejected.append({'workflow_id': identifier, 'reason': type(exc).__name__ + ': ' + str(exc)})
    return {'admitted': admitted, 'rejected': rejected}


# --------------------------------------------------------------------------
# Release identity (never stored, never reused across identities)
# --------------------------------------------------------------------------

def release_identity(*, application_revision, manual_revision, release):
    if not isinstance(application_revision, str) or not REVISION.fullmatch(application_revision):
        raise IncompatibleEvidence('Release identity needs an exact application commit')
    if not isinstance(manual_revision, str) or not manual_revision:
        raise IncompatibleEvidence('Release identity needs a manual revision')
    if not isinstance(release, str) or not release.strip():
        raise IncompatibleEvidence('Release identity needs a version')
    return {'application_revision': application_revision, 'manual_revision': manual_revision, 'release': release}


def release_binding(evidence, *, application_revision, manual_revision, release, sources=None):
    """Bind fresh and reused evidence to one release identity, explicitly.

    ``evidence`` is a list of provenance records (``Entry.provenance()`` for
    reused evidence, or a record with ``fresh: True`` for this run's captures).
    """
    identity = release_identity(application_revision=application_revision,
                                manual_revision=manual_revision, release=release)
    records = []
    for item in evidence:
        origin = item.get('origin', {})
        fresh = origin.get('application_revision') == application_revision and bool(item.get('fresh'))
        records.append({'workflow_id': item['workflow_id'], 'fingerprint': item['fingerprint'],
                        'manifest_sha256': item.get('manifest_sha256'),
                        'origin_application_revision': origin.get('application_revision'),
                        'origin_capture_id': origin.get('capture_id'),
                        'producer_run_id': item.get('producer', {}).get('run_id'),
                        'provenance': 'fresh' if fresh else 'reused'})
    records.sort(key=lambda record: record['workflow_id'])
    if len({record['workflow_id'] for record in records}) != len(records):
        raise IncompatibleEvidence('Duplicate workflow evidence in a release binding')
    body = {'format': FORMAT, 'purpose': BINDING_PURPOSE, 'release_identity': identity,
            'release_identity_sha256': digest(canonical(identity)), 'evidence': records}
    if sources is not None:
        body['sources'] = sources
    return {**body, 'binding_sha256': digest(canonical(body))}


def verify_release_binding(binding, *, application_revision, manual_revision, release):
    identity = release_identity(application_revision=application_revision,
                                manual_revision=manual_revision, release=release)
    if not isinstance(binding, dict) or binding.get('purpose') != BINDING_PURPOSE or binding.get('format') != FORMAT:
        raise IncompatibleEvidence('Not a release binding')
    body = {key: value for key, value in binding.items() if key != 'binding_sha256'}
    if binding.get('binding_sha256') != digest(canonical(body)):
        raise CorruptEvidence('Release binding bytes changed')
    if binding.get('release_identity') != identity or binding.get('release_identity_sha256') != digest(canonical(identity)):
        raise IncompatibleEvidence('Release binding belongs to a different release identity')
    return binding
