"""Settings of the Django prefork worker that celery_consumer_process.py, celery_consumer_email_process.py and
celery_consumer_workflow_process.py compare the Go consumer against (production starts ``celery -A core worker``
without ``-P``: prefork). consumer_restore_settings (private broker, the ``celery`` default queue, django-db results)
plus the scenario's routes and settings from CONSUMER_PREFORK_ORACLE (JSON ``{"routes", "settings", "core"}``):
``settings`` are Django settings, ``core`` are attributes of the core.settings module, which the tasks also read
directly (``from core import settings``)."""
import json as _json
import os as _os

import core.settings as _core_settings
from core.task_routing import go_task_routes as _go_task_routes
from TESTS.adapters.go.consumer_restore_settings import *  # noqa: F401,F403

_oracle = _json.loads(_os.environ.get('CONSUMER_PREFORK_ORACLE') or 'null')
if not isinstance(_oracle, dict) or not _oracle.get('routes') or not _oracle.get('settings'):
    raise RuntimeError('CONSUMER_PREFORK_ORACLE must name the routes and the settings')
CELERY_TASK_ROUTES = _go_task_routes(_oracle['routes'])
for _name, _value in _oracle['settings'].items():
    globals()[_name] = _value
for _name, _value in (_oracle.get('core') or {}).items():
    setattr(_core_settings, _name, _value)
