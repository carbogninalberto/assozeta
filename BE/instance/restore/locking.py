"""One OS lock shared by API, Celery and the self-host lifecycle CLI."""
import fcntl
from contextlib import contextmanager
from django.conf import settings
from django.core.files.uploadhandler import FileUploadHandler, StopUpload


class Busy(Exception):
    pass


@contextmanager
def operation_lock(exclusive=False, required=False):
    path = getattr(settings, 'DATA_RESTORE_LOCK_PATH', '')
    if not path:
        if required:
            raise Busy('Il volume di coordinamento del ripristino non è configurato. Aggiorna la distribuzione self-hosted.')
        yield
        return
    try:
        handle = open(path, 'a')
    except OSError as exc:
        raise Busy('Il volume di coordinamento non è disponibile.') from exc
    try:
        try:
            fcntl.flock(handle, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Busy('Un’altra operazione sui dati è in corso. Riprova tra poco.') from exc
        yield
    finally:
        handle.close()


def restore_pending():
    from instance.models import DataRestore
    return DataRestore.objects.filter(state__in=DataRestore.ACTIVE).exists()


class RestoreMaintenanceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == '/instance/admin/data-restore' and request.method == 'POST':
            request.upload_handlers.insert(0, LimitedRestoreUploadHandler(request))
        # Status, authentication and configuration reads remain usable during restore.
        safe_reads = {'/instance/access', '/instance/config', '/instance/status', '/profile/info', '/readyz', '/healthz'}
        if (request.method == 'OPTIONS' or request.path.startswith('/instance/admin/data-restore')
                or (request.method in ('GET', 'HEAD') and request.path in safe_reads)
                or request.path == '/oauth2/refresh-token'):
            return self.get_response(request)
        from django.http import JsonResponse
        try:
            with operation_lock():
                if request.method not in ('GET', 'HEAD') and getattr(settings, 'DATA_RESTORE_LOCK_PATH', '') and restore_pending():
                    raise Busy('Ripristino dei dati in corso. Le modifiche sono temporaneamente sospese.')
                return self.get_response(request)
        except Busy as exc:
            response = JsonResponse({'error': str(exc)}, status=503)
            response['Retry-After'] = '10'
            return response


def coordinated_sync(function):
    """Own the lease in the thread doing the work, even if its await is cancelled."""
    from functools import wraps
    @wraps(function)
    def wrapped(*args, **kwargs):
        with operation_lock():
            if getattr(settings, 'DATA_RESTORE_LOCK_PATH', '') and restore_pending():
                raise Busy('Ripristino dei dati in corso. Riprova al termine.')
            return function(*args, **kwargs)
    return wrapped


def coordinated_async(function):
    """Keep asynchronous chat writes/agent runs inside the same maintenance lease."""
    from functools import wraps
    @wraps(function)
    async def wrapped(self, *args, **kwargs):
        from channels.db import database_sync_to_async
        try:
            with operation_lock():
                if getattr(settings, 'DATA_RESTORE_LOCK_PATH', '') and await database_sync_to_async(restore_pending)():
                    raise Busy('Ripristino dei dati in corso. Riprova al termine.')
                return await function(self, *args, **kwargs)
        except Busy as exc:
            await self.send_json({'type': 'error', 'message': str(exc)})
            await self.send_json({'type': 'done'})
    return wrapped


class LimitedRestoreUploadHandler(FileUploadHandler):
    """Stop oversized uploads before Django writes the entire file to disk."""
    def __init__(self, request):
        super().__init__(request)
        self.received = 0

    def receive_data_chunk(self, raw_data, start):
        self.received += len(raw_data)
        if self.received > settings.DATA_RESTORE_MAX_UPLOAD_BYTES:
            raise StopUpload(connection_reset=True)
        return raw_data

    def file_complete(self, file_size):
        return None
