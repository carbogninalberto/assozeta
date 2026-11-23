import select

from django.core.management.base import BaseCommand
from django.db import connection

from notifications.go_realtime import relay_once
from notifications.relay_owner import registered_relay


class Command(BaseCommand):
    help = 'Relay Go realtime outbox events into Channels groups until stopped.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Process pending events once and exit')
        parser.add_argument('--poll-seconds', type=float, default=2.0)

    def handle(self, *args, once=False, poll_seconds=2.0, **options):
        with registered_relay() as consumer:
            self.run_relay(consumer, once, poll_seconds)

    def run_relay(self, consumer, once, poll_seconds):
        if once:
            self.stdout.write(str(relay_once(consumer=consumer)))
            return
        # LISTEN shortens latency; polling still covers missed notifications.
        connection.ensure_connection()
        raw = connection.connection
        raw.autocommit = True
        with raw.cursor() as cursor:
            cursor.execute('LISTEN assozeta_go_realtime')
        while True:
            # Drain while making progress; a retry waits for the next interval.
            while True:
                counts = relay_once(consumer=consumer)
                if counts['retry'] or not (counts['delivered'] or counts['failed']):
                    break
            if select.select([raw], [], [], poll_seconds) != ([], [], []):
                raw.poll()
                raw.notifies.clear()
