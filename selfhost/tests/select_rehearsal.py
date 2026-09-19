"""Select real published versions for the post-publication quality workflow."""
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'BE/instance'))
from release_catalog import API, fetch_releases, read_json, version_tuple


def select(source, target, commit):
    if not re.fullmatch(r'[a-f0-9]{40}', commit or ''):
        raise ValueError('Expected a full release commit SHA')
    if source or target:
        if version_tuple(source) is None or version_tuple(target) is None or version_tuple(source) >= version_tuple(target):
            raise ValueError('Provide exact stable source/target versions in ascending order')
        return source, target, commit
    releases = fetch_releases()
    selected = next((release for release in releases if release['artifacts_ready'] and
                     read_json(f'{API}/commits/{release["tag"]}')['sha'] == commit), None)
    if selected is None:
        raise ValueError('No complete stable release matches the published commit')
    for release in releases:
        if version_tuple(release['tag']) >= version_tuple(selected['tag']):
            continue
        raw = read_json(f'{API}/releases/{release["id"]}')
        bundle = f'assozeta-selfhost-{release["tag"]}.tar.gz'
        if any(asset.get('name') == bundle and asset.get('state') == 'uploaded' and asset.get('size', 0) > 0
               for asset in raw.get('assets', [])):
            return release['tag'], selected['tag'], commit
    raise ValueError('No older published self-host bundle is available for the rehearsal')


if __name__ == '__main__':
    source, target, commit = select(os.environ.get('SOURCE_VERSION', ''), os.environ.get('TARGET_VERSION', ''), os.environ['RELEASE_COMMIT'])
    with Path(os.environ['GITHUB_OUTPUT']).open('a') as output:
        output.write(f'source={source}\ntarget={target}\ncommit={commit}\n')
