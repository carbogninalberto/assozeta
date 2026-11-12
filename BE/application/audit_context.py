"""Explicit atomic mutations whose audit-index writes must succeed."""
from contextlib import contextmanager
from contextvars import ContextVar

from django.db import transaction


_strict_audit_indexes = ContextVar('strict_audit_indexes', default=False)


def audit_index_errors_are_fatal():
    return _strict_audit_indexes.get()


@contextmanager
def atomic_audit_mutation():
    """Rollback the mutation and audit if index creation raises any exception.

    Legacy signal callers remain best effort. Context-local tokens preserve an
    enclosing strict mutation and prevent unrelated requests inheriting its mode.
    """
    token = _strict_audit_indexes.set(True)
    try:
        with transaction.atomic():
            yield
    finally:
        _strict_audit_indexes.reset(token)
