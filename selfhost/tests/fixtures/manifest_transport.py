"""Map allowlisted local image IDs for validation without weakening its schema."""
from copy import deepcopy


def validate_local_manifest(manifest, tag, allowed, validator, **kwargs):
    canonical = deepcopy(manifest)

    def reference(actual, name):
        if not isinstance(actual, str) or actual not in allowed:
            raise ValueError('Fixture manifest references an image outside this test')
        return f'ghcr.io/carbogninalberto/assozeta-{name}@sha256:{"a" * 64}'

    images = canonical.get('images')
    if isinstance(images, dict):
        canonical['images'] = {name: reference(value, name) for name, value in images.items()}
    extensions = canonical.get('extensions')
    if isinstance(extensions, dict):
        golang = extensions.get('go_backend')
        if isinstance(golang, dict) and 'image' in golang:
            golang['image'] = reference(golang['image'], 'backend-go')
    validator(canonical, tag, **kwargs)
    return manifest


def local_go_reference(manifest, allowed, validator, **kwargs):
    result = []
    def validate_reference(canonical, _tag):
        result.append(validator(canonical, **kwargs))
    validate_local_manifest(manifest, '', allowed, validate_reference)
    return manifest['extensions']['go_backend']['image'] if result[0] is not None else None
