"""Content-addressed cache for disposable test binaries.

With caching enabled, a compiled artifact is stored under a key derived from the
source tree, the toolchain and the build command, then copied into the run's
private directory. With caching disabled every call runs the build exactly as
before, without hashing or extra subprocesses.
"""
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
SKIP_DIRECTORIES = frozenset({'.git', '__pycache__'})
ENV_KEYS = ('GOFLAGS', 'CGO_ENABLED', 'CC', 'CFLAGS', 'LDFLAGS', 'PKG_CONFIG_PATH')
GO_ENV_KEYS = ('GOOS', 'GOARCH', 'CGO_ENABLED', 'GOFLAGS', 'GOEXPERIMENT', 'GOAMD64', 'GOARM64')

_DIGESTS = {}


def cache_directory():
    """Return the configured cache directory, or ``None`` when caching is off."""
    try:
        from TESTS.adapters.django import shared_postgres
        active = shared_postgres.active()
        binaries = getattr(active, 'binaries')
    except (ImportError, AttributeError):
        return None
    return None if binaries is None else Path(binaries)


def tree_digest(root):
    """Return a sha256 hex digest over the regular files below ``root``."""
    root = Path(root)
    entries = []
    for directory, names, files in os.walk(root):
        names[:] = sorted(name for name in names if name not in SKIP_DIRECTORIES)
        current = Path(directory)
        for name in files:
            path = current / name
            try:
                info = path.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            entries.append((path.relative_to(root).as_posix(), path, info))
    entries.sort(key=lambda entry: entry[0])
    signature = (len(entries),
                 max((entry[2].st_mtime_ns for entry in entries), default=0),
                 sum(entry[2].st_size for entry in entries))
    memo_key = (str(root), signature)
    cached = _DIGESTS.get(memo_key)
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    for relative, path, _ in entries:
        file_digest = hashlib.sha256()
        with path.open('rb') as handle:
            for block in iter(lambda: handle.read(1 << 20), b''):
                file_digest.update(block)
        digest.update(relative.encode('utf-8') + b'\0' + file_digest.hexdigest().encode('ascii') + b'\n')
    value = digest.hexdigest()
    _DIGESTS[memo_key] = value
    return value


def _toolchain_identity(kind, env):
    if kind == 'go':
        version = subprocess.run(['go', 'version'], cwd=ROOT / 'BE_GO', env=env, timeout=60,
                                 capture_output=True, text=True, check=True).stdout
        settings = subprocess.run(['go', 'env', *GO_ENV_KEYS], cwd=ROOT / 'BE_GO', env=env, timeout=60,
                                  capture_output=True, text=True, check=True).stdout
        return version + '\0' + settings
    if kind == 'cc':
        return subprocess.run(['cc', '--version'], env=env, timeout=60,
                              capture_output=True, text=True, check=True).stdout
    return ''


def build_key(kind, command, env, extra=''):
    """Return the cache key for one build invocation."""
    digest = hashlib.sha256()

    def feed(*values):
        for value in values:
            digest.update(str(value).encode('utf-8'))
            digest.update(b'\0')

    feed('binary-cache-1', kind)
    digest.update('\0'.join(command).encode('utf-8'))
    digest.update(b'\0')
    feed(tree_digest(ROOT / 'BE_GO'))
    feed(_toolchain_identity(kind, env))
    for key in ENV_KEYS:
        feed(key, env.get(key, ''))
    feed(extra)
    return digest.hexdigest()


def _touch(directory):
    try:
        os.utime(directory)
    except OSError:
        pass


def cached_build(kind, output, command, env, build, extra=''):
    """Run ``build`` or reuse a cached copy of its ``output``."""
    output = Path(output)
    cache = cache_directory()
    if cache is None:
        build()
        return
    entry = None
    try:
        entry = cache / build_key(kind, command, env, extra) / output.name
        if entry.is_file():
            shutil.copy2(entry, output)
            _touch(entry.parent)
            return
    except Exception:
        build()
        return
    build()
    try:
        directory = entry.parent
        directory.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix='.tmp-', dir=directory)
        os.close(handle)
        try:
            shutil.copy2(output, temporary)
            os.replace(temporary, entry)
        except Exception:
            Path(temporary).unlink(missing_ok=True)
            raise
        prune(cache, keep=12)
    except Exception:
        pass


def prune(cache, keep=12):
    """Remove the least recently used key directories beyond ``keep``.

    Several worktrees share one cache and each uses three entries, so the
    default keeps more than a single tree's builds.
    """
    cache = Path(cache)
    try:
        entries = [path for path in cache.iterdir() if path.is_dir()]
    except OSError:
        return

    def modified(path):
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return 0

    entries.sort(key=modified, reverse=True)
    for stale in entries[keep:]:
        shutil.rmtree(stale, ignore_errors=True)
