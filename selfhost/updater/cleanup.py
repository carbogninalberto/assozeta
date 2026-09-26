"""Best-effort maintenance after a verified upgrade; never delete persistent data."""
import json
from pathlib import Path
import subprocess
import sys

from common import read_env


REPOSITORIES = {f'ghcr.io/carbogninalberto/assozeta-{name}'
                for name in ('backend', 'web', 'renderer', 'updater')}


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True, timeout=120)


def references(values):
    for name in ('BACKEND', 'WEB', 'RENDERER', 'UPDATER'):
        reference = values.get(f'ASSOZETA_{name}_REF')
        if not reference:
            repository = values.get(f'ASSOZETA_{name}_IMAGE',
                                    f'ghcr.io/carbogninalberto/assozeta-{name.lower()}')
            reference = f'{repository}:{values["ASSOZETA_VERSION"].removeprefix("v")}'
        yield reference


def protected_images(root, env_file):
    state = Path(root) / '.updater'
    if (state / 'pending-distribution').exists():
        raise RuntimeError('Recovery is pending; image cleanup skipped')
    environments = [Path(env_file)]
    history = state / 'distribution-history'
    snapshots = sorted(history.iterdir(), key=lambda path: int(path.name)) if history.exists() else []
    if not snapshots:
        raise RuntimeError('Previous release snapshot is missing; image cleanup skipped')
    environments.append(snapshots[-1] / 'environment')
    protected = set()
    # Resolve everything before deleting anything. Missing rollback metadata or
    # unavailable Docker inspection must fail closed, not weaken retention.
    for environment in environments:
        for reference in references(read_env(environment)):
            protected.add(json.loads(docker('image', 'inspect', reference))[0]['Id'])
    containers = docker('ps', '-aq').split()
    for container in containers:
        protected.add(json.loads(docker('container', 'inspect', container))[0]['Image'])
    return protected


def cleanup_images(root, env_file):
    protected = protected_images(root, env_file)
    identifiers = set(docker('image', 'ls', '-aq', '--no-trunc').split())
    for identifier in sorted(identifiers - protected):
        image = json.loads(docker('image', 'inspect', identifier))[0]
        refs = (image.get('RepoTags') or []) + (image.get('RepoDigests') or [])
        # Anonymous build layers and images shared with other repositories are
        # not identifiable as obsolete Assozeta releases.
        if not refs or any(ref.split('@')[0].split(':')[0] not in REPOSITORIES for ref in refs):
            continue
        for reference in refs:
            try:
                # Never force removal: Docker is the final guard against an
                # image that another container started using after inspection.
                print(docker('image', 'rm', reference), end='', flush=True)
            except subprocess.CalledProcessError:
                # Removing the last tag may also remove its digest reference.
                print('[assozeta] cleanup: image reference retained or already removed', flush=True)


def cleanup(root, env_file):
    for label, action in (
        ('obsolete Assozeta images', lambda: cleanup_images(root, env_file)),
        ('build cache older than seven days', lambda: print(
            docker('builder', 'prune', '--force', '--filter', 'until=168h'), end='', flush=True)),
    ):
        print(f'[assozeta] cleaning {label}', flush=True)
        try:
            action()
        except (OSError, ValueError, KeyError, IndexError, RuntimeError, subprocess.SubprocessError) as exc:
            print(f'[assozeta] cleanup warning ({label}): {type(exc).__name__}; upgrade remains successful', flush=True)


if __name__ == '__main__':
    cleanup(*sys.argv[1:])
