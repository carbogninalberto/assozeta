"""
Notification service layer for business logic.
"""
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.cache import cache

from notifications.models import UserNotificationsBroadcast

logger = logging.getLogger(__name__)

# Cache configuration
BROADCAST_CACHE_TTL = 300  # 5 minutes


class NotificationService:
    """Service class for notification-related business logic."""

    _manager = None

    @classmethod
    def get_manager(cls):
        """Get or create NotificationManager instance."""
        if cls._manager is None:
            from notifications import utils as nt
            cls._manager = nt.NotificationManager()
        return cls._manager

    @staticmethod
    def get_user_broadcasts(user):
        """
        Get list of broadcast names for a user including 'all_notifications'.

        Uses Django cache with 5-minute TTL to reduce database queries.

        Args:
            user: Django User instance

        Returns:
            list: List of broadcast names including 'all_notifications'
        """
        # Use user_id (primary key) instead of id
        user_pk = user.user_id if hasattr(user, 'user_id') else user.pk
        cache_key = f'user_broadcasts:{user_pk}'
        broadcasts = cache.get(cache_key)

        if broadcasts is None:
            # Cache miss - query database
            broadcasts = list(
                UserNotificationsBroadcast.objects
                .select_related('broadcast')
                .filter(user=user)
                .values_list('broadcast__broadcast_name', flat=True)
            )
            broadcasts.append('all_notifications')

            # Store in cache
            cache.set(cache_key, broadcasts, BROADCAST_CACHE_TTL)

        return broadcasts

    @staticmethod
    def invalidate_user_broadcasts_cache(user_id):
        """
        Invalidate cache for user's broadcasts.

        Call this when UserNotificationsBroadcast records are created/deleted.

        Args:
            user_id: User ID (integer or string)
        """
        cache_key = f'user_broadcasts:{user_id}'
        cache.delete(cache_key)

    @classmethod
    def send_notification(cls, user, messages, broadcast=None):
        """
        Send notification: store in Redis AND push via WebSocket.

        This is the main entry point for creating notifications. It:
        1. Validates/creates the broadcast channel
        2. Stores the notification in Redis
        3. Pushes to connected WebSocket clients

        Args:
            user: User instance to notify
            messages: List of message dicts [{"msg": "...", "type": "..."}]
            broadcast: Broadcast channel name (default: user-specific)

        Returns:
            bool: Success status
        """
        manager = cls.get_manager()

        # Default to user-specific broadcast
        if broadcast is None:
            broadcast = f"broadcast_{user.user_id}"

        # Ensure broadcast exists and user is subscribed
        manager.validate_broadcast(user, broadcast)

        # Store in Redis (existing logic)
        result = manager.add_notification_role_broadcast(messages, broadcast=broadcast)

        if result:
            # Push via WebSocket to connected clients
            cls._push_to_websocket(user, messages)

        return result

    @classmethod
    def _push_to_websocket(cls, user, messages):
        """
        Push notification to connected WebSocket clients.

        Sends to the user's notification group so all their connected
        devices/tabs receive the notification in real-time.

        Args:
            user: User instance
            messages: List of message dicts that were just stored
        """
        try:
            channel_layer = get_channel_layer()
            if channel_layer is None:
                logger.warning("Channel layer not available for WebSocket push")
                return

            # Get the stored notifications to include IDs and formatted data
            broadcasts = cls.get_user_broadcasts(user)
            all_notifications, _ = cls.get_manager().get_notification(
                str(user.user_id),
                broadcasts=broadcasts
            )

            if not all_notifications:
                return

            # Find the newly added notifications by matching message content
            for msg in messages:
                msg_text = msg.get('msg', '')
                for notif in all_notifications:
                    if notif.get('msg') == msg_text:
                        # Send to user-specific notification group
                        async_to_sync(channel_layer.group_send)(
                            f"notifications_user_{user.user_id}",
                            {
                                'type': 'notification_push',
                                'notification': notif
                            }
                        )
                        logger.debug(
                            f"Pushed notification to user {user.user_id}: "
                            f"{notif.get('id', 'unknown')}"
                        )
                        break

        except Exception as e:
            logger.error(f"Failed to push WebSocket notification: {e}", exc_info=True)
            # Don't raise - notification is stored, push failure is non-critical