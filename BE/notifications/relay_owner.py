import logging
import os
from contextlib import contextmanager
from threading import Event, Thread
from uuid import uuid4

from django.conf import settings
from django.db import close_old_connections, connection

from core import outbox

logger = logging.getLogger(__name__)


@contextmanager
def registered_relay():
    consumer = f'django-relay-{uuid4()}'
    stop = Event()
    interval = float(os.environ.get('ASSOZETA_HANDOFF_HEARTBEAT_SECONDS',
                                    getattr(settings, 'ASSOZETA_HANDOFF_HEARTBEAT_SECONDS', 1.0)))
    if interval <= 0:
        raise ValueError('relay heartbeat interval must be positive')

    def pulse():
        if not outbox.installed():
            return
        with connection.cursor() as cursor:
            # The claim transaction holds ownership FOR SHARE. Heartbeats
            # must keep advancing even when a freeze queues behind that lock.
            cursor.execute("""INSERT INTO public.assozeta_handoff_consumers AS c (consumer_id,runtime,role)
                VALUES (%s,'django','relay') ON CONFLICT (consumer_id) DO UPDATE SET
                heartbeat_at=clock_timestamp(), heartbeat_seq=c.heartbeat_seq+1""", [consumer])
            cursor.execute("""SELECT admission_operation_id FROM public.assozeta_go_job_ownership
                WHERE singleton=1 AND runtime='django' AND admission='frozen'""")
            operation = cursor.fetchone()
            if operation:
                cursor.execute("SELECT public.assozeta_handoff_acknowledge_freeze(%s,%s)", [consumer, operation[0]])

    def loop():
        try:
            while not stop.wait(interval):
                try:
                    close_old_connections()
                    pulse()
                except Exception:
                    logger.warning('Realtime relay heartbeat unavailable')
                    connection.close()
        finally:
            connection.close()

    pulse()
    thread = Thread(target=loop, name='relay-heartbeat', daemon=True)
    thread.start()
    try:
        yield consumer
    finally:
        stop.set()
        thread.join()
