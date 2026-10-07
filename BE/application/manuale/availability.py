"""Cheap, offline discovery for manual navigation and the manual-only assistant.

A configured publisher may supply the corpus lazily on the first read. Merely
reporting feature availability must never download evidence or expose its URL.
Retrieval still enforces all source, publication and asset checks.
"""
from django.conf import settings

from .development import load_development_index
from .index import ManualIndex
from .preview import load_preview


def manual_available():
    if settings.MANUAL_CORPUS_BASE_URL.strip():
        return True
    try:
        if load_preview() is not None or load_development_index() is not None:
            return True
        index = ManualIndex.load_runtime(settings.MANUAL_INDEX_PATH)
        index.check_version(revision=settings.MANUAL_APPLICATION_REVISION, release=settings.RUNNING_VERSION)
        metadata = index.value['metadata']
        return bool(settings.MANUAL_RUN_ID or (
            metadata.get('publication_status', 'published') == 'published'
            and metadata.get('code_state', 'committed') == 'committed'))
    except (OSError, ValueError, KeyError, TypeError):
        return False
