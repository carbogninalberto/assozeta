"""Shared state and payload helpers for association export progress."""

from copy import deepcopy
from typing import Any, Dict, Optional

from django.core.cache import cache
from django.utils import timezone


EXPORT_ACTIVE_CACHE_TIMEOUT = 2 * 60 * 60
EXPORT_TASK_CACHE_TIMEOUT = 24 * 60 * 60
# A sleeping/offline browser may reconnect hours after the worker finishes.
# Keep the terminal recovery record as long as task ownership metadata.
EXPORT_LAST_CACHE_TIMEOUT = 24 * 60 * 60


def export_active_cache_key(sport_association_id) -> str:
    return f'association-export-active:{sport_association_id}'


def export_task_cache_key(task_id) -> str:
    return f'association-export-task:{task_id}'


def export_last_cache_key(sport_association_id) -> str:
    return f'association-export-last:{sport_association_id}'


def normalize_progress(progress: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    progress = progress or {}
    try:
        percent = int(progress.get('percent', 0))
    except (TypeError, ValueError):
        percent = 0
    return {
        'percent': max(0, min(100, percent)),
        'phase': progress.get('phase') or 'queued',
        'label': progress.get('label') or 'Export in attesa',
        'completed': progress.get('completed'),
        'total': progress.get('total'),
        **({
            key: value for key, value in progress.items()
            if key not in {'percent', 'phase', 'label', 'completed', 'total'}
        }),
    }


def build_export_snapshot(
    *, task_id: str, sport_association_id: str, user_id: str,
    status: str = 'PENDING', progress: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized = normalize_progress(progress)
    return {
        'task_id': str(task_id),
        'sport_association_id': str(sport_association_id),
        'user_id': str(user_id),
        'status': status,
        'ready': status in {'SUCCESS', 'FAILURE'},
        'estimate': f"{normalized['percent']}%",
        'progress': normalized,
        'updated_at': timezone.now().isoformat(),
    }


def get_active_export(sport_association_id) -> Optional[Dict[str, Any]]:
    value = cache.get(export_active_cache_key(sport_association_id))
    return deepcopy(value) if isinstance(value, dict) else None


def set_active_export(snapshot: Dict[str, Any]) -> None:
    cache.set(
        export_active_cache_key(snapshot['sport_association_id']),
        deepcopy(snapshot),
        timeout=EXPORT_ACTIVE_CACHE_TIMEOUT,
    )


def get_last_export(sport_association_id) -> Optional[Dict[str, Any]]:
    value = cache.get(export_last_cache_key(sport_association_id))
    return deepcopy(value) if isinstance(value, dict) else None


def set_last_export(snapshot: Dict[str, Any]) -> None:
    """Retain a terminal snapshot briefly so reconnecting clients converge."""
    cache.set(
        export_last_cache_key(snapshot['sport_association_id']),
        deepcopy(snapshot),
        timeout=EXPORT_LAST_CACHE_TIMEOUT,
    )


def clear_active_export(sport_association_id, task_id: Optional[str] = None) -> bool:
    key = export_active_cache_key(sport_association_id)
    if task_id is not None:
        current = cache.get(key)
        if isinstance(current, dict) and current.get('task_id') != str(task_id):
            return False
    cache.delete(key)
    return True
