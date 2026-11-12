"""Test-image-only release transport. Never copied into production images.

Real containers, backups, migrations and health checks run unchanged. Only the
upstream release service and registry pulls are replaced with local fixtures.
"""
import io
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
import urllib.request


def fixture_root():
    return Path(os.environ['ASSOZETA_TEST_FIXTURES'])


original_urlopen = urllib.request.urlopen


def urlopen(request, *args, **kwargs):
    url = request.full_url if hasattr(request, 'full_url') else request
    parsed = urlsplit(url)
    if parsed.netloc == 'api.github.com' and parsed.path.startswith('/repos/carbogninalberto/assozeta/releases'):
        catalog = json.loads((fixture_root() / 'releases.json').read_text())
        suffix = parsed.path.removeprefix('/repos/carbogninalberto/assozeta/releases')
        if suffix.startswith('/tags/'):
            response = next(item for item in catalog if item['tag_name'] == suffix.removeprefix('/tags/'))
        elif suffix:
            response = next(item for item in catalog if item['id'] == int(suffix[1:]))
        else:
            response = catalog
        return io.BytesIO(json.dumps(response).encode())
    if parsed.netloc == 'github.com' and parsed.path.startswith('/carbogninalberto/assozeta/releases/download/'):
        relative = parsed.path.removeprefix('/carbogninalberto/assozeta/releases/download/')
        return io.BytesIO((fixture_root() / relative).read_bytes())
    return original_urlopen(request, *args, **kwargs)


urllib.request.urlopen = urlopen

# Local fixture images have immutable Docker IDs rather than GHCR repository
# digests. Production validation remains covered by the distribution unit tests.
# This adapter exists solely in the disposable test controller image.
try:
    import distribution
except ModuleNotFoundError:
    pass  # The application image needs only the release-service fixture above.
else:
    original_validate_manifest = distribution.validate_manifest

    from contextvars import ContextVar
    from manifest_transport import validate_local_manifest, local_go_reference

    canonical_validation = ContextVar("fixture_canonical_validation", default=False)

    def validate_fixture_manifest(manifest, tag, **kwargs):
        allowed = set(json.loads((fixture_root() / 'images.json').read_text()).values())
        token = canonical_validation.set(True)
        try:
            return validate_local_manifest(manifest, tag, allowed, original_validate_manifest, **kwargs)
        finally:
            canonical_validation.reset(token)

    distribution.validate_manifest = validate_fixture_manifest

    # Engine verification reads the persisted actual fixture image ID directly.
    # Canonical validation inside the full validator must still call the original
    # reference validator, avoiding reinterpretation as a second fixture input.
    if hasattr(distribution, 'go_backend_reference'):
        original_go_reference = distribution.go_backend_reference

        def fixture_go_reference(manifest, **kwargs):
            if canonical_validation.get():
                return original_go_reference(manifest, **kwargs)
            allowed = set(json.loads((fixture_root() / 'images.json').read_text()).values())
            return local_go_reference(manifest, allowed, original_go_reference, **kwargs)

        distribution.go_backend_reference = fixture_go_reference
