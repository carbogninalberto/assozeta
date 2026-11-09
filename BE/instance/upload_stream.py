"""Stream large request bodies to a view without buffering them.

Django's ASGI handler reads the whole body into a temporary file before the
view runs. For the backup upload (several GB) the wrapper below gives Django
an empty body and lets the view read the real one, chunk by chunk, so it can
forward each chunk to the runner as it arrives.
"""
import asyncio

from asgiref.sync import async_to_sync

STREAMED_PATHS = frozenset({'/instance/admin/backups/upload'})
BODY_RECEIVE = 'assozeta.body_receive'
CHUNK = 1024 * 1024


class UploadAborted(Exception):
    pass


def stream_uploads(app):
    async def wrapper(scope, receive, send):
        if scope['type'] != 'http' or scope.get('method') != 'POST' or scope.get('path') not in STREAMED_PATHS:
            return await app(scope, receive, send)
        delivered = False
        never = asyncio.Event()

        async def empty_body():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': b'', 'more_body': False}
            # Django's disconnect listener waits here; the view reads the real stream.
            await never.wait()

        return await app({**scope, BODY_RECEIVE: receive}, empty_body, send)
    return wrapper


def body_chunks(request, length):
    """Yield exactly `length` body bytes; raise UploadAborted if they do not arrive."""
    django_request = getattr(request, '_request', request)
    receive = getattr(django_request, 'scope', {}).get(BODY_RECEIVE)
    remaining = length
    if receive is not None:
        receive_sync = async_to_sync(receive)
        while remaining > 0:
            message = receive_sync()
            if message.get('type') != 'http.request':
                raise UploadAborted()
            body = message.get('body', b'')
            if len(body) > remaining:
                raise UploadAborted()
            if body:
                remaining -= len(body)
                yield body
            if remaining and not message.get('more_body', False):
                raise UploadAborted()
        return
    while remaining > 0:
        chunk = django_request.read(min(CHUNK, remaining))
        if not chunk:
            raise UploadAborted()
        remaining -= len(chunk)
        yield chunk
