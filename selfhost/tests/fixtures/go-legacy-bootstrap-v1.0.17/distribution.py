MAX_BUNDLE = 128 * 1024 * 1024


def managed_path(name):
    if not isinstance(name, str) or '\\' in name or '\x00' in name:
        return False
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or not path.parts or str(path) != name:
        return False
    return name in ('compose.yml', 'compose.updater.yml', '.env.example', '.env.dev.example') or path.parts[0] in ('bin', 'config', 'caddy', 'updater')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def unpack(content):
    files = {}
    with tarfile.open(fileobj=io.BytesIO(content), mode='r:gz') as archive:
        total = 0
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != 'selfhost':
                raise ReleaseError('Unsafe release archive path.')
            if member.isdir():
                continue
            if not member.isfile():
                raise ReleaseError('Release archives must not contain links or special files.')
            total += member.size
            if total > MAX_BUNDLE:
                raise ReleaseError('Unpacked release archive exceeds the supported size.')
            relative = str(PurePosixPath(*path.parts[1:]))
            if managed_path(relative):
                if relative in files:
                    raise ReleaseError('Duplicate release archive entry.')
                files[relative] = (archive.extractfile(member).read(), member.mode & 0o777)
    return files


def validate_manifest(manifest, tag):
    if not isinstance(manifest, dict) or manifest.get('schema_version') != 1 or manifest.get('version') != image_version(tag):
        raise ReleaseError('This release requires a different updater protocol.')
    if not isinstance(manifest.get('bundle_sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', manifest['bundle_sha256']):
        raise ReleaseError('Release bundle digest is missing.')
    images = manifest.get('images')
    if not isinstance(images, dict) or set(images) != set(IMAGE_NAMES):
        raise ReleaseError('Release must contain exactly the supported image references.')
    for name in IMAGE_NAMES:
        expected = rf'ghcr\.io/{re.escape(REPOSITORY.split("/")[0])}/assozeta-{name}@sha256:[a-f0-9]{{64}}'
        if not isinstance(images[name], str) or not re.fullmatch(expected, images[name]):
            raise ReleaseError('Release contains an invalid image reference.')
    if not isinstance(manifest.get('files'), dict) or not manifest['files']:
        raise ReleaseError('Release file manifest is missing.')
    for name, sha in manifest['files'].items():
        if not managed_path(name) or not isinstance(sha, str) or not re.fullmatch(r'[a-f0-9]{64}', sha):
            raise ReleaseError('Invalid managed file manifest.')
    return manifest
