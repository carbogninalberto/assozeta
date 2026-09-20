"""Scheduler liveness evidence, written by normal service operation, never by diagnostics."""
import threading
import time
from django.core.cache import cache
from django_celery_beat.schedulers import DatabaseScheduler


class InstanceScheduler(DatabaseScheduler):
    @staticmethod
    def publish_heartbeat(timestamp):
        try:
            cache.set('instance:scheduler:heartbeat', timestamp, timeout=90)
        except Exception:
            pass

    def tick(self, *args, **kwargs):
        interval = super().tick(*args, **kwargs)
        # Monitoring must never delay scheduled work if cache/DNS stalls. Only
        # one daemon publisher can exist; a stuck publisher means stale evidence,
        # not an accumulating thread pool or a blocked scheduler. Record tick time,
        # not eventual write time, so a delayed write cannot claim fresh liveness.
        publisher = getattr(self, '_heartbeat_publisher', None)
        if publisher is None or not publisher.is_alive():
            self._heartbeat_publisher = threading.Thread(
                target=self.publish_heartbeat, args=(time.time(),), daemon=True,
                name='instance-scheduler-heartbeat',
            )
            try:
                self._heartbeat_publisher.start()
            except RuntimeError:
                # Resource pressure in monitoring must not stop the scheduler.
                pass
        return min(interval, 30)
