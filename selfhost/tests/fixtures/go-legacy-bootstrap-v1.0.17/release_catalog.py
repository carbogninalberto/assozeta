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
