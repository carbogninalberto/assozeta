"""Opt-in, private test diagnostics. No SQL, network, or Django setup on import.

A query_start is a Python execute-wrapper observation, not proof of PostgreSQL
arrival or transaction identity. The parent must independently reconcile live PG
catalogs and the original HTTP response, and reject invalid/incomplete streams.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import json
import threading
from uuid import UUID


_REQUEST = ContextVar('django_native_query_request', default=None)
_QUERY = ContextVar('django_native_query_execution', default=None)
_REQUEST_GUARD = ContextVar('django_native_request_guard', default=None)
_QUERY_GUARD = ContextVar('django_native_query_guard', default=None)
_REJECTED = object()
_HEADER = b'x-query-observation-id'
# Cross-observer nesting must invalidate both without lock-order deadlocks.
_LOCK = threading.RLock()
_COUNTERS = (
    'request_starts', 'request_finishes', 'finish_calls', 'active_requests',
    'query_starts', 'query_ends', 'active_queries', 'active_bindings',
    'unattributed_queries', 'request_errors', 'query_errors',
    'connection_violations', 'post_close_events',
)


@dataclass(eq=False)
class _Request:
    owner: object
    request_id: str
    active: bool = True
    queries: int = 0
    response_started: bool = False
    response_finished: bool = False
    sends: int = 0
    error: bool = False
    guard: object = field(default_factory=object)
    guard_token: object = None


@dataclass(eq=False)
class _Query:
    owner: object
    query_id: str
    request: object
    connection: object
    native: object
    pid: object
    context: object
    active: bool = True
    binding_valid: bool = True
    guard: object = field(default_factory=object)
    guard_token: object = None


class RequestConnectionObserver:
    """Single-install observer with bounded events, bytes and active indexes.

    The parent supplies a fresh UUID per run; IDs are generated internally.
    Install before starting Django request threads. Stop/join services and drain
    DB operations before close. No begin/end/PID injection interface is public.
    """

    def __init__(self, run_id, *, capacity=4096, max_bytes=4 * 1024 * 1024,
                 max_active=128):
        if not isinstance(run_id, (str, UUID)):
            raise ValueError('run_id must be a UUID')
        parsed = str(UUID(str(run_id)))
        if parsed != str(run_id):
            raise ValueError('run_id must be a canonical UUID')
        if type(capacity) is not int or capacity < 4:
            raise ValueError('capacity must be an integer >= 4')
        if type(max_bytes) is not int or max_bytes < 8192:
            raise ValueError('max_bytes must be an integer >= 8192')
        if type(max_active) is not int or max_active < 1:
            raise ValueError('max_active must be a positive integer')
        self._run_id = parsed
        self._capacity = capacity
        self._max_bytes = max_bytes
        self._max_active = max_active
        self._lock = _LOCK
        self._events = []
        self._bytes = 2  # JSON array delimiters; each event reserves a comma.
        self._counts = dict.fromkeys(_COUNTERS, 0)
        self._requests = {}
        self._queries = {}
        self._bindings = {}
        self._active_sends = 0
        self._attached = []
        self._signal = None
        self._installed = False
        self._closed = False
        self._invalid = False
        self._reason = None
        self._overflow = False
        self._wrapper = self._execute
        self._receiver = self._connected
        self._emit('header', complete=False)

    @property
    def run_id(self):
        return self._run_id

    @property
    def capacity(self):
        return self._capacity

    @property
    def max_bytes(self):
        return self._max_bytes

    @property
    def max_active(self):
        return self._max_active

    def _event(self, kind, request_id=None, query_id=None, pid=None,
               error=False, complete=None, reason=None):
        event = dict(stream='query_connection', version=1, run_id=self.run_id,
                     sequence=len(self._events) + 1, type=kind,
                     request_id=request_id, query_id=query_id, error=bool(error),
                     complete=self._healthy() if complete is None else bool(complete),
                     **self._counts)
        if pid is not None:
            event['backend_pid'] = pid
        if reason is not None:
            event['reason'] = reason
        return event

    def _healthy(self):
        return not (self._invalid or self._counts['query_errors'] or
                    self._counts['request_errors'])

    def _append(self, event):
        size = len(json.dumps(event, separators=(',', ':'), ensure_ascii=True).encode('ascii')) + 1
        if len(self._events) >= self.capacity or self._bytes + size > self.max_bytes:
            self._invalid = self._overflow = True
            self._reason = self._reason or 'event_capacity'
            return False
        self._events.append(event)
        self._bytes += size
        return True

    def _emit(self, kind, request_id=None, query_id=None, pid=None,
              error=False, complete=None):
        event = self._event(kind, request_id, query_id, pid, error, complete)
        size = len(json.dumps(event, separators=(',', ':'), ensure_ascii=True).encode('ascii')) + 1
        # Reserve one invalidation and one footer, including post-close detection.
        if len(self._events) >= self.capacity - 2 or self._bytes + size > self.max_bytes - 4096:
            self._overflow = True
            self._invalidate('event_capacity')
            return
        self._append(event)

    def _invalidate(self, reason):
        if not self._invalid:
            self._invalid = True
            self._reason = reason
            self._append(self._event('invalidated', complete=False, reason=reason))

    def _post_close(self):
        if self._closed:
            self._counts['post_close_events'] += 1
            self._invalidate('post_close_event')
            return True
        return False

    @contextmanager
    def installed(self):
        """Lazily install only owned wrappers/signals; always try all cleanup."""
        with self._lock:
            if self._installed or self._closed:
                self._invalidate('installation_reused')
                raise RuntimeError('observer installation is single-use')
            self._installed = True
        context_failed = False
        try:
            from django.db import connections
            from django.db.backends.signals import connection_created

            with self._lock:
                self._signal = connection_created
                # Connect first so a new connection cannot fall between scan/signal.
                self._signal.connect(self._receiver, weak=False)
                for connection in connections.all():
                    self._attach(connection)
            yield self
        except BaseException:
            context_failed = True
            with self._lock:
                self._invalidate('installation_context_error')
            raise
        finally:
            try:
                self.close()
            except BaseException:
                # An installation/body failure takes precedence over cleanup
                # cancellation; close has already finalized invalid evidence.
                if not context_failed:
                    raise

    def _connected(self, sender, connection, **kwargs):
        del sender, kwargs
        self._attach(connection)

    def _attach(self, connection):
        with self._lock:
            if self._post_close():
                return
            if any(existing is connection for existing in self._attached):
                if sum(item is self._wrapper for item in connection.execute_wrappers) != 1:
                    self._invalidate('wrapper_registration_mismatch')
                return
            if len(self._attached) >= self.max_active:
                self._overflow = True
                self._invalidate('connection_capacity')
                return
            # Record ownership first so a partially successful append is cleaned.
            self._attached.append(connection)
            try:
                connection.execute_wrappers.append(self._wrapper)
            except BaseException:
                self._invalidate('wrapper_attach_failed')

    def _native(self, connection):
        """Only inspect the native object Django is actually about to execute on."""
        try:
            if connection.vendor != 'postgresql':
                return None, None
            native = connection.connection
            if native is None or native.closed:
                return None, None
            # psycopg2 and psycopg3 both expose info.backend_pid. No SQL or fallback
            # to a caller label, wrapper attribute, cached PID, or get_backend_pid.
            pid = native.info.backend_pid
            if type(pid) is not int or pid <= 0:
                return native, None
            return native, pid
        except BaseException:
            return None, None

    def _begin_query(self, context):
        with self._lock:
            self._counts['query_starts'] += 1
            self._counts['active_queries'] += 1
            current = _REQUEST.get()
            request = current if isinstance(current, _Request) else None
            if current is None:
                self._counts['unattributed_queries'] += 1
            if self._post_close():
                return None
            if not self._installed:
                self._invalidate('query_before_install')
            if current is _REJECTED:
                self._invalidate('untracked_http_query')
            if request is not None and (request.owner is not self or not request.active or
                    self._requests.get(request.request_id) is not request):
                self._invalidate('request_context_mismatch')
                if request.owner is not self:
                    with request.owner._lock:
                        request.owner._invalidate('request_context_mismatch')
                request = None
            nested = _QUERY.get()
            if nested is not None:
                self._invalidate('nested_query')
            if len(self._queries) + len(self._requests) >= self.max_active:
                self._overflow = True
                self._invalidate('active_capacity')
                return None
            connection = context.get('connection') if isinstance(context, dict) else None
            native, pid = self._native(connection)
            query_id = f'{self.run_id}-q{self._counts["query_starts"]}'
            query = _Query(self, query_id, request, connection, native, pid, context)
            query.guard_token = _QUERY_GUARD.set(query.guard)
            self._queries[query_id] = query
            if request is not None:
                request.queries += 1
            if not any(connection is attached for attached in self._attached):
                self._counts['connection_violations'] += 1
                self._invalidate('unregistered_connection')
                query.binding_valid = False
            if native is None or pid is None:
                self._counts['connection_violations'] += 1
                self._invalidate('native_binding_missing')
                query.binding_valid = False
            elif (id(native) in self._bindings or
                  any(active.pid == pid for active in self._bindings.values())):
                self._counts['connection_violations'] += 1
                self._invalidate('native_binding_overlap')
                query.binding_valid = False
            elif query.binding_valid:
                self._bindings[id(native)] = query
                self._counts['active_bindings'] += 1
            self._emit('query_start', request.request_id if request else None,
                       query_id, pid if query.binding_valid else None)
            return query

    def _end_query(self, query, context, error):
        with self._lock:
            self._counts['query_ends'] += 1
            self._counts['query_errors'] += int(error)
            self._post_close()
            if query is None:
                self._counts['active_queries'] -= 1
                return
            if (not query.active or self._queries.get(query.query_id) is not query or
                    query.owner is not self or _QUERY.get() is not query or
                    context is not query.context or _QUERY_GUARD.get() is not query.guard):
                self._invalidate('query_end_mismatch')
                return
            try:
                # Matching ContextVar values in a copied context are insufficient:
                # a Token can only reset in the exact context where it was set.
                _QUERY_GUARD.reset(query.guard_token)
            except (RuntimeError, ValueError):
                self._invalidate('query_end_context_mismatch')
                return
            query.guard_token = None
            query.active = False
            self._counts['active_queries'] -= 1
            del self._queries[query.query_id]
            request = query.request
            if request is not None:
                request.queries -= 1
                if not request.active or _REQUEST.get() is not request:
                    self._invalidate('query_request_lifetime_mismatch')
            connection = context.get('connection') if isinstance(context, dict) else None
            native, pid = self._native(connection)
            valid = (query.binding_valid and connection is query.connection and
                     native is query.native and pid == query.pid)
            if not valid:
                self._counts['connection_violations'] += 1
                self._invalidate('native_binding_changed')
            if query.native is not None and self._bindings.get(id(query.native)) is query:
                del self._bindings[id(query.native)]
                self._counts['active_bindings'] -= 1
            if not self._closed:
                self._emit('query_end', request.request_id if request else None,
                           query.query_id, query.pid if valid else None, error)
            # Copied/late contexts may retain this token; never retain native objects
            # or Django wrappers in a completed token.
            query.native = query.connection = query.request = query.context = None

    def _execute(self, execute, sql, params, many, context):
        query = self._begin_query(context)
        token = _QUERY.set(query)
        error = True
        try:
            result = execute(sql, params, many, context)
            error = False
            return result
        finally:
            try:
                self._end_query(query, context, error)
            finally:
                _QUERY.reset(token)

    def _begin_request(self, scope):
        with self._lock:
            self._counts['request_starts'] += 1
            self._counts['active_requests'] += 1
            request_id = f'{self.run_id}-{self._counts["request_starts"]}'
            if self._post_close():
                return request_id, None
            if not self._installed:
                self._invalidate('request_before_install')
            previous = _REQUEST.get()
            if previous is not None:
                self._invalidate('nested_request')
                if isinstance(previous, _Request) and previous.owner is not self:
                    with previous.owner._lock:
                        previous.owner._invalidate('nested_request')
            if any(name.lower() == _HEADER for name, _ in scope.get('headers', ())):
                self._invalidate('caller_observation_header')
            request = None
            if len(self._requests) + len(self._queries) < self.max_active:
                request = _Request(self, request_id)
                request.guard_token = _REQUEST_GUARD.set(request.guard)
                self._requests[request_id] = request
            else:
                self._overflow = True
                self._invalidate('active_capacity')
            self._emit('request_begin', request_id)
            return request_id, request

    def _begin_send(self, request):
        with self._lock:
            self._active_sends += 1
            self._post_close()
            if request is not None:
                if not request.active:
                    self._invalidate('response_send_after_return')
                if request.sends:
                    self._invalidate('response_send_overlap')
                request.sends += 1

    def _response(self, request, request_id, message):
        with self._lock:
            kind = message.get('type')
            if kind == 'http.response.start':
                headers = list(message.get('headers', ()))
                if any(name.lower() == _HEADER for name, _ in headers):
                    self._invalidate('application_observation_header')
                if request is not None:
                    if request.response_started or not request.active:
                        self._invalidate('response_start_mismatch')
                # A preexisting application value can never impersonate our ID.
                headers = [(name, value) for name, value in headers if name.lower() != _HEADER]
                headers.append((_HEADER, request_id.encode('ascii')))
                message = dict(message, headers=headers)
            elif kind == 'http.response.body' and request is not None:
                if not request.active or not request.response_started or request.response_finished:
                    self._invalidate('response_body_mismatch')
            return message

    def _request_error(self, request):
        # Count failed HTTP requests once, including a caught send failure or a
        # send that fails after application return. Never retain the exception.
        if request is None or not request.error:
            self._counts['request_errors'] += 1
            if request is not None:
                request.error = True

    def _end_send(self, request, kind, final_body, error):
        with self._lock:
            self._active_sends -= 1
            self._post_close()
            if error:
                self._request_error(request)
                self._invalidate('response_send_error')
            if request is not None:
                request.sends -= 1
                if not request.active:
                    self._invalidate('response_send_after_return')
                elif not error:
                    # Progress means downstream send returned successfully, not
                    # merely that the application attempted a response message.
                    if kind == 'http.response.start':
                        request.response_started = True
                    elif final_body:
                        request.response_finished = True

    def _end_request(self, request, request_id, error):
        with self._lock:
            self._counts['finish_calls'] += 1
            if error:
                self._request_error(request)
            self._post_close()
            if request is not None:
                if (not request.active or self._requests.get(request_id) is not request or
                        _REQUEST.get() is not request or request.owner is not self or
                        _REQUEST_GUARD.get() is not request.guard):
                    self._invalidate('request_finish_mismatch')
                    return
                try:
                    _REQUEST_GUARD.reset(request.guard_token)
                except (RuntimeError, ValueError):
                    self._invalidate('request_finish_context_mismatch')
                    return
                request.guard_token = None
                if request.queries:
                    self._invalidate('request_has_active_queries')
                if request.sends:
                    self._invalidate('request_has_active_sends')
                if not request.response_started or not request.response_finished:
                    self._invalidate('response_incomplete')
                error = error or request.error
                request.active = False
                del self._requests[request_id]
            self._counts['active_requests'] -= 1
            self._counts['request_finishes'] += 1
            if not self._closed:
                self._emit('request_finish', request_id, error=error)

    def wrap(self, application):
        """Observe every HTTP scope. Non-HTTP scopes have no HTTP SQL context."""
        async def observed(scope, receive, send):
            if scope.get('type') != 'http':
                token = _REQUEST.set(None)
                try:
                    return await application(scope, receive, send)
                finally:
                    _REQUEST.reset(token)
            request_id, request = self._begin_request(scope)
            token = _REQUEST.set(request if request is not None else _REJECTED)
            async def observed_send(message):
                self._begin_send(request)
                kind, final_body, send_error = None, False, True
                try:
                    outgoing = self._response(request, request_id, message)
                    kind = outgoing.get('type')
                    final_body = kind == 'http.response.body' and not outgoing.get('more_body', False)
                    await send(outgoing)
                    send_error = False
                finally:
                    self._end_send(request, kind, final_body, send_error)
            error = True
            try:
                result = await application(scope, receive, observed_send)
                error = False
                return result
            finally:
                try:
                    self._end_request(request, request_id, error)
                finally:
                    _REQUEST.reset(token)
        return observed

    def cursor(self):
        with self._lock:
            return len(self._events)

    def delta(self, cursor):
        with self._lock:
            if type(cursor) is not int or not 0 <= cursor <= len(self._events):
                self._invalidate('invalid_cursor')
                raise ValueError('cursor outside retained stream')
            return tuple(dict(event) for event in self._events[cursor:])

    def snapshot(self):
        with self._lock:
            drained = not (self._counts['active_requests'] or self._counts['active_queries'] or
                           self._active_sends)
            return dict(version=1, stream='query_connection', run_id=self.run_id,
                        events=tuple(dict(event) for event in self._events),
                        counters=dict(self._counts), valid=self._healthy(),
                        invalidated=self._invalid, reason=self._reason,
                        overflow=self._overflow, closed=self._closed,
                        complete=self._closed and drained and self._healthy(),
                        retained_bytes=self._bytes,
                        attached_connections=len(self._attached))

    def close(self):
        """Close once, require drain, attempt signal and every wrapper separately.

        Ordinary errors become sticky diagnostics. After all detach attempts,
        re-raise the first control exception; installed preserves any body error.
        Repeated close retries failed detachments but never emits a second footer.
        """
        with self._lock:
            control_error = None
            first = not self._closed
            self._closed = True
            if (self._counts['active_requests'] or self._counts['active_queries'] or
                    self._active_sends):
                self._invalidate('close_before_drain')
            if self._signal is not None:
                try:
                    if not self._signal.disconnect(self._receiver):
                        self._invalidate('signal_registration_missing')
                    self._signal = None
                except BaseException as error:
                    self._invalidate('signal_detach_failed')
                    if not isinstance(error, Exception):
                        control_error = error
            remaining = []
            for connection in self._attached:
                try:
                    wrappers = connection.execute_wrappers
                    count = sum(item is self._wrapper for item in wrappers)
                    if count != 1:
                        self._invalidate('wrapper_registration_mismatch')
                    # Remove by identity, preserving every unrelated wrapper/order.
                    for index in range(len(wrappers) - 1, -1, -1):
                        if wrappers[index] is self._wrapper:
                            del wrappers[index]
                except BaseException as error:
                    self._invalidate('wrapper_detach_failed')
                    remaining.append(connection)
                    if control_error is None and not isinstance(error, Exception):
                        control_error = error
            self._attached = remaining
            if first:
                self._append(self._event('footer'))
            snapshot = self.snapshot()
        if control_error is not None:
            raise control_error
        return snapshot
