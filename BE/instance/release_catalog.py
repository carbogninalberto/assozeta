"""Canonical release metadata, shared with the isolated updater (stdlib only)."""
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

REPOSITORY = 'carbogninalberto/assozeta'
API = f'https://api.github.com/repos/{REPOSITORY}'
RELEASES_URL = f'https://github.com/{REPOSITORY}/releases'
STABLE_VERSION = re.compile(r'^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$')
IMAGE_NAMES = ('backend', 'web', 'renderer', 'updater')


class ReleaseError(Exception):
    pass


def version_tuple(value):
    match = STABLE_VERSION.fullmatch(str(value or ''))
    return tuple(map(int, match.groups())) if match else None


def image_version(tag):
    version = version_tuple(tag)
    if version is None:
        raise ReleaseError('A stable release version is required.')
    return '.'.join(map(str, version))


def read_json(url, headers=None):
    request = Request(url, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'Assozeta-Self-Instance',
        'X-GitHub-Api-Version': '2022-11-28',
        **(headers or {}),
    })
    try:
        with urlopen(request, timeout=15) as response:
            return json.load(response)
    except HTTPError as exc:
        if exc.code in (403, 429):
            raise ReleaseError('Release service rate limit reached. Please try again later.') from exc
        raise ReleaseError(f'Release service returned HTTP {exc.code}.') from exc
    except (URLError, TimeoutError, ValueError, OSError) as exc:
        raise ReleaseError('Release service is unavailable. Please try again later.') from exc


def normalize_release(raw):
    tag = raw.get('tag_name', '')
    if raw.get('draft') or raw.get('prerelease') or not raw.get('published_at') or not version_tuple(tag):
        return None
    bundle_name = f'assozeta-selfhost-{tag}.tar.gz'
    expected = (bundle_name, 'assozeta-update.json')
    assets = {item.get('name'): item for item in raw.get('assets', [])}
    ready = all(assets.get(name, {}).get('state') == 'uploaded' and assets[name].get('size', 0) > 0 for name in expected)
    return {
        'id': raw['id'], 'tag': tag, 'version': image_version(tag),
        'name': raw.get('name') or tag, 'published_at': raw['published_at'],
        'notes': raw.get('body') or '',
        'url': f'{RELEASES_URL}/tag/{quote(tag, safe="")}',
        'artifacts_ready': ready,
        'unavailable_reason': None if ready else 'The complete update distribution has not been published yet.',
    }


def fetch_releases():
    """Read every upstream page; fail explicitly instead of returning partial history."""
    releases = {}
    page = 1
    while True:
        raw_page = read_json(f'{API}/releases?per_page=100&page={page}')
        if not isinstance(raw_page, list):
            raise ReleaseError('Invalid release catalog response.')
        for raw in raw_page:
            release = normalize_release(raw)
            if release:
                releases[release['id']] = release
        if len(raw_page) < 100:
            break
        page += 1
    return sorted(releases.values(), key=lambda item: version_tuple(item['tag']), reverse=True)


def fetch_release(release_id):
    if not isinstance(release_id, int) or isinstance(release_id, bool) or release_id < 1:
        raise ReleaseError('Invalid release identifier.')
    release = normalize_release(read_json(f'{API}/releases/{release_id}'))
    if not release:
        raise ReleaseError('The selected release is not a published stable release.')
    return release


def release_summary(releases, running, page=1, page_size=10):
    current = version_tuple(running)
    latest = releases[0] if releases else None
    target = version_tuple(latest['tag']) if latest else None
    relation = 'unknown' if current is None else (
        'unavailable' if target is None else 'behind' if current < target else 'ahead' if current > target else 'current'
    )
    pending = [item for item in releases if current is not None and version_tuple(item['tag']) > current]
    return {
        'repository': REPOSITORY, 'latest': latest, 'relation': relation,
        'pending': pending, 'history': releases[(page - 1) * page_size:page * page_size],
        'page': page, 'next_page': page + 1 if len(releases) > page * page_size else None,
        'total': len(releases),
    }
