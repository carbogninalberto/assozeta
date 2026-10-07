"""Explicit local installation, shared by the development reader and MCP.

Production still requires a published committed corpus. A development package
keeps its working-tree provenance and checks the live checkout on every read.
The active pointer is replaced only after the complete package is validated.
"""
import copy
import json
import re
import tempfile
from pathlib import Path
from urllib.parse import quote, urlsplit

from django.conf import settings

from .index import (EvidenceError, ManualIndex, digest, file_digest,
                    owned_path, promote, seal_index)


def development_enabled():
    return (settings.ASSOZETA_DEPLOYMENT_MODE == 'development'
            and not settings.MANUAL_RUN_ID and not settings.MANUAL_APPLICATION_REVISION)


def load_development_index(*, check_deleted=True):
    if not development_enabled():
        return None
    root = Path(settings.MANUAL_DEVELOPMENT_ROOT)
    pointer = root / 'active.json'
    if not pointer.exists():
        return None
    active = json.loads(pointer.read_text())
    identity = active.get('corpus_identity', '')
    if active.get('format') != 1 or not re.fullmatch(r'[0-9a-f]{64}', identity):
        raise EvidenceError('Invalid development manual installation')
    if not settings.MANUAL_SOURCE_ROOT:
        raise EvidenceError('Development manual requires the live source checkout')
    package = owned_path(root, 'packages/' + identity)
    index = ManualIndex.load_runtime(package / 'index.json')
    metadata = index.value['metadata']
    if (index.value['identity'] != identity or metadata.get('publication_status') != 'development-local'
            or not re.fullmatch(r'[0-9a-f]{64}', metadata.get('verified_corpus_identity', ''))):
        raise EvidenceError('Invalid development manual provenance')
    for relative in metadata.get('development_deleted_files', []) if check_deleted else []:
        if owned_path(settings.MANUAL_SOURCE_ROOT, relative).exists():
            raise EvidenceError('A deleted verification input has reappeared')
    index.asset_root = str(package)
    return index


def install_development_run(run_path):
    if not development_enabled() or not settings.MANUAL_SOURCE_ROOT:
        raise EvidenceError('Installation requires ordinary development mode with a live source checkout')
    code = Path(settings.MANUAL_SOURCE_ROOT).resolve()
    run = Path(run_path).resolve()
    if not run.is_relative_to(code / 'quality-reports' / 'manuale'):
        raise EvidenceError('Install only an owned local verification run')
    state = json.loads((run / 'run.json').read_text())
    original = ManualIndex.load(run / 'index.json')
    metadata = original.value['metadata']
    if (state.get('status') != 'evaluated' or state.get('run_id') != run.name
            or state['application_input']['revision'] != metadata['application_revision']):
        raise EvidenceError('Run is not completely verified')
    original.applicable(revision=metadata['application_revision'], release=settings.RUNNING_VERSION, code_root=code)
    original.verify_unchanged_dependencies(code)
    for name in ('render', 'evaluation', 'embedded-manual'):
        report = json.loads((run / (name + '.json')).read_text())
        if report.get('status') != 'passed':
            raise EvidenceError('Verification failed: ' + name)
        if name == 'evaluation' and report.get('corpus_identity') != original.value['identity']:
            raise EvidenceError('Evaluation belongs to a different corpus')
    value = copy.deepcopy(original.value)
    deleted = []
    for relative, expected in state['application_input'].get('uncommitted_files', {}).items():
        # Runtime implementation inputs are bound in addition to the workflow
        # sources/tooling already in the index. Editing maintainer prose or CI
        # notes does not invalidate instructions about unchanged application code.
        if not relative.startswith(('BE/', 'UI/')):
            continue
        source = owned_path(code, relative)
        if expected is None:
            if source.exists():
                raise EvidenceError('Deleted verification input is present: ' + relative)
            deleted.append(relative)
        else:
            if file_digest(source) != expected:
                raise EvidenceError('Checkout changed after verification: ' + relative)
            value['dependencies'][relative] = expected
    origin = settings.APP_URL.rstrip('/')
    parsed = urlsplit(origin)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.query or parsed.fragment:
        raise EvidenceError('Development application URL is invalid')
    value['metadata'].update(publication_status='development-local', manual_url=origin,
        verified_corpus_identity=original.value['identity'], source_run_id=state['run_id'],
        development_deleted_files=deleted)
    for chunk in value['chunks']:
        chunk['url'] = origin + '/#/manuale?section=' + quote(chunk['id'], safe='')
        for image in chunk['screenshots']:
            image['url'] = origin + '/api/manuale/assets/' + quote(image['path'])
    value = seal_index(value)
    root = Path(settings.MANUAL_DEVELOPMENT_ROOT)
    packages = root / 'packages'
    packages.mkdir(parents=True, exist_ok=True)
    package = owned_path(packages, value['identity'])
    # Stage all bytes before switching the pointer; failures leave the prior
    # installation available. A package can be repeated safely by its hash.
    with tempfile.TemporaryDirectory(dir=packages, prefix='.install-') as temporary:
        staged = Path(temporary) / 'package'
        staged.mkdir()
        images = {image['path']: image['sha256'] for chunk in value['chunks'] for image in chunk['screenshots']}
        for relative, expected in images.items():
            source = owned_path(run / 'manual', relative)
            contents = source.read_bytes()
            if source.suffix.lower() != '.png' or digest(contents) != expected:
                raise EvidenceError('Screenshot differs from verified capture: ' + relative)
            target = owned_path(staged, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(contents)
        promote(value, staged / 'index.json')
        if not package.exists():
            staged.rename(package)
        # Revalidate an existing package too, including every referenced image.
        installed = ManualIndex.load(package / 'index.json')
        if installed.value['identity'] != value['identity']:
            raise EvidenceError('Existing development package is corrupt')
        for relative, expected in images.items():
            if file_digest(owned_path(package, relative)) != expected:
                raise EvidenceError('Installed screenshot is corrupt')
        installed.applicable(revision=metadata['application_revision'], release=settings.RUNNING_VERSION, code_root=code)
        installed.verify_unchanged_dependencies(code)
        promote({'format': 1, 'corpus_identity': value['identity']}, root / 'active.json')
        # A successful verified installation replaces the reader-only draft.
        (root / 'preview' / 'active.json').unlink(missing_ok=True)
    return {'status': 'installed', 'corpus_identity': value['identity'], 'sections': len(value['chunks']),
            'screenshots': len(images), 'verified_corpus_identity': original.value['identity']}
