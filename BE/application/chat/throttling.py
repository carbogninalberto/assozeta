import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)


class WebSocketThrottle:
    """Cache-backed rate limiter for WebSocket messages."""

    def __init__(self, user_id: str, max_messages: int = 10, window: int = 60):
        self.key = f'ws_throttle:{user_id}'
        self.max_messages = max_messages
        self.window = window

    def is_allowed(self) -> bool:
        """Check if the user is allowed to send a message (atomic)."""
        try:
            count = cache.incr(self.key)
        except ValueError:
            # Key doesn't exist yet - create it atomically
            cache.set(self.key, 1, timeout=self.window)
            return True
        return count <= self.max_messages

    def reset(self):
        """Reset the throttle counter."""
        cache.delete(self.key)


class ConcurrencyGuard:
    """Cache-backed guard to limit concurrent WebSocket sessions per user."""

    def __init__(self, user_id: str, max_sessions: int = 1):
        self.key = f'ws_concurrent:{user_id}'
        self.max_sessions = max_sessions
        self._acquired = False

    def acquire(self) -> bool:
        """Try to acquire a session slot (atomic)."""
        try:
            count = cache.incr(self.key)
        except ValueError:
            # Key doesn't exist - create it
            cache.set(self.key, 1, timeout=300)
            self._acquired = True
            return True

        if count > self.max_sessions:
            # Over limit - undo the increment
            cache.decr(self.key)
            return False

        self._acquired = True
        return True

    def release(self):
        """Release the session slot."""
        if not self._acquired:
            return
        self._acquired = False
        try:
            val = cache.decr(self.key)
            if val <= 0:
                cache.delete(self.key)
        except ValueError:
            cache.delete(self.key)
