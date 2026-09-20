"""Publish only after all release images are available anonymously by digest."""
import hashlib
import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen

from distribution import unpack
from release_catalog import IMAGE_NAMES, REPOSITORY, image_version, read_json


def image_reference(name, tag):
    repository = f'{REPOSITORY.split("/")[0]}/assozeta-{name}'
    token = read_json(f'https://ghcr.io/token?service=ghcr.io&scope=repository:{repository}:pull')['token']
    request = Request(f'https://ghcr.io/v2/{repository}/manifests/{image_version(tag)}', method='HEAD', headers={
        'Authorization': f'Bearer {token}',
        'Accept': 'application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json',
    })
    with urlopen(request, timeout=30) as response:
        digest = response.headers['Docker-Content-Digest']
    if not digest or not digest.startswith('sha256:'):
        raise RuntimeError('Registry did not return an immutable image digest')
    return f'ghcr.io/{repository}@{digest}'


def build(tag, archive):
    content = Path(archive).read_bytes()
    return {
        'schema_version': 1, 'version': image_version(tag),
        'bundle_sha256': hashlib.sha256(content).hexdigest(),
        'images': {name: image_reference(name, tag) for name in IMAGE_NAMES},
        'files': {name: hashlib.sha256(value[0]).hexdigest() for name, value in unpack(content).items()},
    }


if __name__ == '__main__':
    Path(sys.argv[3]).write_text(json.dumps(build(sys.argv[1], sys.argv[2]), indent=2) + '\n')
