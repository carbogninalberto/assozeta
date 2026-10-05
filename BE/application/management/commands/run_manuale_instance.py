"""Run the real ASGI application at a controlled date in an owned fixture."""
from datetime import date
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import BaseCommand

from application.management.commands.seed_manuale import assert_disposable


@contextmanager
def manual_capture_connections():
    """Close request connections even while application monotonic time is fixed."""
    from django.db import connections

    # ASGI requests use separate thread contexts. Frozen monotonic time never
    # reaches a positive connection age, so retaining connections leaks one per
    # request context. These settings apply only in this disposable command.
    configurations = list(connections.settings.values())
    previous = [(config, config['CONN_MAX_AGE']) for config in configurations]
    try:
        for config in configurations:
            config['CONN_MAX_AGE'] = 0
        connections.close_all()
        yield
    finally:
        connections.close_all()
        for config, max_age in previous:
            config['CONN_MAX_AGE'] = max_age


@contextmanager
def manual_capture_clock(reference):
    """Fix application time while signing real S3 requests at wall-clock time."""
    import datetime
    import botocore.auth
    from freezegun import freeze_time
    from freezegun.api import real_datetime

    # Botocore's SigV4 signer calls datetime.utcnow(), which freezegun freezes
    # even for ignored SDK modules. Give just that signer its own datetime
    # namespace; the storage client, request, signature and server stay real.
    signing_clock = SimpleNamespace(**vars(datetime))
    signing_clock.datetime = real_datetime
    with freeze_time(reference, real_asyncio=True):
        with patch.object(botocore.auth, 'datetime', signing_clock):
            yield


class Command(BaseCommand):
    help = 'Run an isolated manual instance with a fixed date; refuses non-fixture databases.'

    def add_arguments(self, parser):
        parser.add_argument('--reference-date', required=True)

    def handle(self, *args, **options):
        assert_disposable()
        reference = date.fromisoformat(options['reference_date'])
        import uvicorn
        with manual_capture_connections(), manual_capture_clock(reference.isoformat() + ' 12:00:00+00:00'):
            # WEB_CONCURRENCY from the normal dev environment otherwise spawns
            # fresh workers, which do not inherit this process's fixture clock.
            # Load Django's routes before entering the asynchronous server loop.
            from core.asgi import application
            uvicorn.run(application, host='0.0.0.0', port=8000, workers=1, log_level='warning')
