"""Fetch a release corpus from a configured public repository, then promote it.

No user/tool argument selects this URL. Failed downloads retain the previous
valid index; applicability is checked again by the normal retrieval path.
"""
import fcntl
import json
import logging
import os
import re
import stat
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

from django.conf import settings

from .index import EvidenceError, ManualIndex, digest, owned_path, png_dimensions, promote

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_attempted = {}
MAX_BYTES = 64 * 1024 * 1024
MAX_IMAGE_BYTES = 16 * 1024 * 1024
ASSET_GRACE_SECONDS = 24 * 60 * 60


def validate_url(url):
    if not isinstance(url, str):
        raise EvidenceError('Manual download URL must be a string')
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' and not (settings.MANUAL_RUN_ID and parsed.scheme == 'http'
            and parsed.hostname in ('127.0.0.1', 'localhost'))
            or not parsed.netloc or parsed.username or parsed.password or parsed.fragment):
        raise EvidenceError('Manual downloads require a public HTTPS URL')
    return parsed


class ManualRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(request, response, code, message, headers, newurl)


urlopen = build_opener(ManualRedirectHandler()).open


def asset_cache_root():
    return Path(settings.MANUAL_INDEX_PATH).parent / 'assets'


def cached_asset_path(image):
    if not isinstance(image, dict):
        raise EvidenceError('Invalid manual screenshot record')
    expected = image.get('sha256', '')
    if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise EvidenceError('Manual screenshot requires a SHA-256 digest')
    return owned_path(asset_cache_root(), expected + '.png')


def install_image(image):
    """Content-addressed assets remain valid across corpus updates and restarts."""
    target = cached_asset_path(image)
    validate_url(image['url'])
    if not isinstance(image.get('path'), str) or not image['path'].endswith('.png'):
        raise EvidenceError('Manual screenshot must be a PNG')
    if target.exists():
        contents = target.read_bytes()
        if digest(contents) == image['sha256']:
            png_dimensions(contents)
            return
    with urlopen(image['url'], timeout=10) as response:
        contents = response.read(MAX_IMAGE_BYTES + 1)
    if len(contents) > MAX_IMAGE_BYTES or digest(contents) != image['sha256']:
        raise EvidenceError('Downloaded screenshot differs from verified evidence')
    png_dimensions(contents)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix='.manual-image-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def corpus_url():
    base = settings.MANUAL_CORPUS_BASE_URL.rstrip('/')
    if not base:
        return None
    parsed = validate_url(base)
    if parsed.query:
        raise EvidenceError('Manual corpus source must be a configured public HTTPS base URL')
    revision = settings.MANUAL_APPLICATION_REVISION
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise EvidenceError('Release corpus synchronization needs a committed application revision')
    if not settings.RUNNING_VERSION:
        raise EvidenceError('Release corpus synchronization needs a running release')
    return base + '/' + revision + '/' + quote(settings.RUNNING_VERSION, safe='') + '.json'


def _retire_previous_assets(current_hashes):
    """Start the grace period when an image leaves the active index, not at download."""
    try:
        previous = ManualIndex.load_runtime(settings.MANUAL_INDEX_PATH)
    except (OSError, ValueError, KeyError, TypeError):
        return
    now = time.time()
    for image in (image for chunk in previous.value['chunks'] for image in chunk['screenshots']):
        expected = image.get('sha256', '')
        if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
            continue
        if expected in current_hashes:
            continue
        path = asset_cache_root() / (expected + '.png')
        try:
            if stat.S_ISREG(path.lstat().st_mode):
                os.utime(path, (now, now), follow_symlinks=False)
        except FileNotFoundError:
            pass
        # Other filesystem errors abort before promotion, retaining the active
        # index rather than retiring a screenshot without its grace period.


def _prune_assets(current_hashes):
    """Only successful promotions collect old, unreferenced, owned PNG files."""
    root = asset_cache_root()
    cutoff = time.time() - ASSET_GRACE_SECONDS
    removed = 0
    try:
        for path in root.iterdir():
            if not re.fullmatch(r'[0-9a-f]{64}\.png', path.name) or path.stem in current_hashes:
                continue
            try:
                info = path.lstat()
                if stat.S_ISREG(info.st_mode) and info.st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                logger.warning('Manual screenshot cleanup deferred; active corpus retained.')
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning('Manual screenshot cleanup deferred; active corpus retained.')
    return removed


def synchronize():
    # API workers and the operator command share the persistent volume. Hold
    # an OS lock across downloads, promotion and collection so one publisher
    # cannot delete the screenshots another publisher is about to activate.
    if not corpus_url():
        raise EvidenceError('Manual corpus source is not configured')
    root = Path(settings.MANUAL_INDEX_PATH).parent
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.sync.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            return _synchronize_locked()
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _synchronize_locked():
    url = corpus_url()
    if not url:
        raise EvidenceError('Manual corpus source is not configured')
    with urlopen(url, timeout=5) as response:
        payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise EvidenceError('Manual corpus download exceeds its size limit')
    index = ManualIndex(json.loads(payload))
    metadata = index.value['metadata']
    if (not isinstance(metadata, dict)
            or metadata.get('publication_status') != 'published' or metadata.get('code_state') != 'committed'
            or metadata.get('manual_state') != 'committed' or not re.fullmatch(r'[0-9a-f]{40}', metadata.get('manual_revision', ''))):
        raise EvidenceError('Only committed, published manual evidence can enter a release corpus')
    index.check_version(revision=settings.MANUAL_APPLICATION_REVISION, release=settings.RUNNING_VERSION)
    # Reading may omit an outdated guide while retaining unrelated sections.
    # Refresh must reject an outdated candidate before replacing the previous
    # corpus; a filtered result alone does not validate the downloaded package.
    if index.stale_sections(settings.MANUAL_SOURCE_ROOT or None):
        raise EvidenceError('Downloaded manual corpus contains stale implementation evidence')
    images = {}
    for chunk in index.value['chunks']:
        for image in chunk['screenshots']:
            cached_asset_path(image)
            validate_url(image['url'])
            images.setdefault(image['sha256'], image)
    # The active index changes only after every referenced PNG is installed.
    # Interrupted downloads leave the previous index and its images intact.
    pending = list(images.values())
    with ThreadPoolExecutor(max_workers=8) as executor:
        # Submit one bounded batch at a time: a publisher outage must not queue
        # hundreds of additional ten-second failures before installation exits.
        for start in range(0, len(pending), 8):
            list(executor.map(install_image, pending[start:start + 8]))
    _retire_previous_assets(images)
    promote(index.value, settings.MANUAL_INDEX_PATH)
    pruned = _prune_assets(images)
    return {'status': 'updated', 'corpus_identity': index.value['identity'],
            'content_identity': index.value['content_identity'], 'manual_revision': metadata['manual_revision'],
            'screenshots': len(images), 'pruned_screenshots': pruned}


def refresh_if_due():
    if not settings.MANUAL_CORPUS_BASE_URL:
        return
    key = (settings.MANUAL_CORPUS_BASE_URL, settings.MANUAL_APPLICATION_REVISION,
           settings.RUNNING_VERSION, settings.MANUAL_INDEX_PATH)
    with _lock:
        now = time.monotonic()
        if now - _attempted.get(key, float('-inf')) < 300:
            return
        _attempted[key] = now
        try:
            synchronize()
        except (OSError, ValueError, KeyError, TypeError):
            # Deliberately omit private configuration/URLs from user output.
            logger.warning('Manual corpus refresh failed; retaining the last validated local corpus.')
