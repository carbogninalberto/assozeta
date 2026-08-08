"""
Opt-in context helpers for mocking storage, background tasks, and mail.

These are NOT applied globally. Import and use them as context managers
inside individual test methods when you need to isolate external calls.

Usage::

    from application.tests.fixtures.mocks import storage_mock_context

    def test_something(self):
        with storage_mock_context():
            # storage calls are no-ops
            ...
"""
from contextlib import contextmanager
from unittest.mock import patch, MagicMock


@contextmanager
def storage_mock_context():
    """Mock Django's default storage so uploads/downloads are no-ops."""
    storage_mock = MagicMock()
    storage_mock.save.return_value = 'fake/storage/key.png'
    storage_mock.open.return_value.__enter__.return_value.read.return_value = b'fake-image'
    storage_mock.url.return_value = 'https://fake.storage.example/fake-key.png'
    with patch('django.core.files.storage.default_storage', storage_mock):
        yield storage_mock


@contextmanager
def task_mock_context():
    """Mock Celery's apply_async / delay so no real tasks are dispatched."""
    task_mock = MagicMock()
    with patch('celery.app.task.Task.apply_async', task_mock), \
         patch('celery.app.task.Task.delay', task_mock):
        yield task_mock


@contextmanager
def mail_mock_context():
    """Mock Django's send_mail / mail_admins so no real emails are sent."""
    with patch('django.core.mail.send_mail') as send_mail_mock, \
         patch('django.core.mail.mail_admins') as mail_admins_mock:
        yield {'send_mail': send_mail_mock, 'mail_admins': mail_admins_mock}
