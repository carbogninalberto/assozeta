# This will make sure the app is always imported when
# Django starts so that shared_task will use this app.
from .celery import app as celery_app  # noqa

# Phase 3 job handoff: durable admission of every task publication, claim fencing and the worker
# heartbeat. Installed here, next to the app import, so core/celery.py (catalogued beat schedule)
# stays unchanged.
from celery.signals import worker_init as _worker_init, worker_ready as _worker_ready  # noqa: E402
from . import handoff as _handoff  # noqa: E402

_handoff.install_producer()
_worker_init.connect(_handoff.worker_identity)
_worker_ready.connect(_handoff.start_heartbeat)

__all__ = ('celery_app',)