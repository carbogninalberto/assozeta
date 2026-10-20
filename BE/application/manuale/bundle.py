"""Read the manual shipped in the same immutable image as the application.

Image deployment is activation: no shared-volume mutation or network request is
needed, and rollback restores the previous package with the previous image.
"""
import json
import re
from pathlib import Path

from .index import EvidenceError, ManualIndex, file_digest, owned_path, png_dimensions


def load_bundle(root, *, revision, release, required=False, verify_assets=False):
    root = Path(root)
    if not root.exists():
        if required:
            raise EvidenceError('Required release manual is missing')
        return None
    # An empty source placeholder is normal in development/nonrelease images.
    if not required and not any(path.name != '.gitkeep' for path in root.iterdir()):
        return None
    index_path = owned_path(root, 'index.json')
    receipt = json.loads(owned_path(root, 'bundle.json').read_text())
    index = ManualIndex.load_runtime(index_path)
    metadata = index.value['metadata']
    if not hasattr(index, '_bundle_file_sha256'):
        index._bundle_file_sha256 = file_digest(index_path)
    index.check_version(revision=revision, release=release)
    if (receipt.get('format') != 1 or receipt.get('distribution') != 'immutable-backend-image'
            or receipt.get('index_sha256') != index._bundle_file_sha256
            or receipt.get('corpus_identity') != index.value['identity']
            or receipt.get('application_revision') != revision or receipt.get('release') != release
            or receipt.get('manual_revision') != metadata.get('manual_revision')
            or not re.fullmatch(r'[0-9a-f]{40}', metadata.get('manual_revision', ''))
            or metadata.get('publication_status') != 'release-bundled'
            or metadata.get('code_state') != 'committed'
            or metadata.get('manual_state') != 'generated-from-committed'
            or receipt.get('verified_corpus_identity') != metadata.get('verification', {}).get('corpus_identity')
            or not re.fullmatch(r'[0-9a-f]{64}', receipt.get('verified_corpus_identity', ''))
            or not index.value['chunks']):
        raise EvidenceError('Invalid release manual provenance')
    if verify_assets:
        images = {image['path']: image['sha256'] for chunk in index.value['chunks']
                  for image in chunk['screenshots']}
        for relative, expected in images.items():
            path = owned_path(root, relative)
            if not relative.endswith('.png') or file_digest(path) != expected:
                raise EvidenceError('Bundled screenshot differs from verified evidence')
            png_dimensions(path.read_bytes())
    index.asset_root = str(root)
    index.asset_content_addressed = False
    return index


def load_installed_bundle(*, verify_assets=False):
    from django.conf import settings
    return load_bundle(settings.MANUAL_BUNDLE_PATH,
        revision=settings.MANUAL_APPLICATION_REVISION, release=settings.RUNNING_VERSION,
        required=settings.MANUAL_BUNDLE_REQUIRED, verify_assets=verify_assets)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--release', required=True)
    parser.add_argument('--required', choices=('true', 'false'), default='false')
    args = parser.parse_args()
    load_bundle(args.root, revision=args.revision, release=args.release,
                required=args.required == 'true', verify_assets=True)


if __name__ == '__main__':
    main()
