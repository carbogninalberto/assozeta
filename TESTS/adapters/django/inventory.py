"""Resolve the supported production URL tree without accessing a database.

Run with the backend Python environment. Uses synthetic example configuration,
never developer .env credentials. Output is discovery, not parity evidence.
"""
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'BE'), str(ROOT)]


def methods(callback):
    cls = getattr(callback, 'cls', None) or getattr(callback, 'view_class', None)
    if cls:
        names = set(name.upper() for name in cls.http_method_names if hasattr(cls, name))
        if 'GET' in names and 'head' in cls.http_method_names:
            names.add('HEAD')
        return sorted(names)
    current = callback
    while current:
        closure = inspect.getclosurevars(current).nonlocals if inspect.isfunction(current) else {}
        for key in ('request_method_list', 'allowed_methods'):
            if key in closure:
                return sorted(closure[key])
        current = getattr(current, '__wrapped__', None)
    return None


def qualified(value):
    if hasattr(value, '__qualname__'):
        return value.__module__ + '.' + value.__qualname__
    if hasattr(value, 'operator_class'):
        operands = [qualified(getattr(value, name)) for name in ('op1_class', 'op2_class') if hasattr(value, name)]
        return qualified(value.operator_class) + '(' + ', '.join(operands) + ')'
    raise TypeError('Unrecognized permission expression: ' + type(value).__name__)


def routes(patterns, prefix=''):
    from django.urls import URLResolver
    for pattern in patterns:
        path = prefix + str(pattern.pattern)
        if isinstance(pattern, URLResolver):
            yield from routes(pattern.url_patterns, path)
            continue
        callback = pattern.callback
        cls = getattr(callback, 'cls', None) or getattr(callback, 'view_class', None)
        target = cls or inspect.unwrap(callback)
        filename = inspect.getsourcefile(target)
        source = Path(filename) if filename else None
        local = source and source.is_relative_to(ROOT)
        yield {'upstream_path': '/' + path, 'name': pattern.name,
               'methods': methods(callback),
               'django': target.__module__ + '.' + target.__qualname__,
               'file': str(source.relative_to(ROOT)) if local else None,
               'sha256': hashlib.sha256(source.read_bytes()).hexdigest() if local else None,
               'authentication_classes': [qualified(c) for c in getattr(cls, 'authentication_classes', ())],
               'permission_classes': [qualified(c) for c in getattr(cls, 'permission_classes', ())],
               'status': 'uncharacterized'}


def main():
    import environ
    # Remove inherited configuration before reading only the committed example.
    # This command runs in its own process; no caller environment is modified.
    os.environ.clear()
    environ.Env.read_env(str(ROOT / 'selfhost/.env.example'))
    os.environ.update(DEBUG='False', JWT_SECRET_KEY_B64='', JWT_PUBLIC_KEY_B64='',
                      JWT_SECRET_KEY='inventory-only', JWT_PUBLIC_KEY='inventory-only',
                      DJANGO_SETTINGS_MODULE='core.settings', DBHOST='127.0.0.1', DBPORT='1',
                      STORE_ON_DISK='True', MANUAL_INDEX_PATH='/nonexistent/inventory-only.json')
    import django
    from django.db.backends.base.base import BaseDatabaseWrapper
    with patch.object(BaseDatabaseWrapper, 'ensure_connection', side_effect=RuntimeError('Inventory must never connect to a database')), patch('environ.Env.read_env'):
        django.setup()
        from django.urls import get_resolver
        from django.apps import apps
        from core.celery import app
        app.autodiscover_tasks(force=True)
        from application import routing
        from application.chat import routing as chat_routing
        from notifications import routing as notification_routing
        from django.contrib.auth.hashers import get_hashers
        from application.mcp_server.server import TOOL_DEFINITIONS
        result = {
            'format': 1, 'configuration': 'production URL tree, committed example, database access prohibited',
            'routes': list(routes(get_resolver().url_patterns)),
            'websockets': list(routes(routing.websocket_urlpatterns + chat_routing.websocket_urlpatterns + notification_routing.websocket_urlpatterns)),
            'tasks': sorted(name for name in app.tasks if not name.startswith('celery.')),
            'password_algorithms': [hasher.algorithm for hasher in get_hashers()],
            'mcp_tools': TOOL_DEFINITIONS,
            'effective_static_schedule': {name: {'task': spec['task'], 'schedule': str(spec['schedule']),
                                                'arguments': spec.get('args', ())}
                                          for name, spec in sorted(app.conf.beat_schedule.items())},
            'models': [{'label': model._meta.label, 'table': model._meta.db_table,
                        'managed': model._meta.managed,
                        'fields': [{'name': f.name, 'column': f.column, 'type': f.get_internal_type(),
                                    'null': f.null, 'primary_key': f.primary_key,
                                    'max_digits': getattr(f, 'max_digits', None),
                                    'decimal_places': getattr(f, 'decimal_places', None)}
                                   for f in model._meta.local_fields],
                        'many_to_many': [{'name': f.name, 'table': f.remote_field.through._meta.db_table}
                                         for f in model._meta.local_many_to_many]}
                       for model in apps.get_models()],
        }
    destination = ROOT / 'docs/architecture/go-migration/django-runtime.json'
    destination.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({key: len(result[key]) for key in ('routes', 'websockets', 'tasks', 'models')}))


if __name__ == '__main__':
    main()
