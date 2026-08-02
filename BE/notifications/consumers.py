"""
WebSocket Consumer for Real-Time Notifications

Handles:
- Fetching notification history
- Marking notifications as read
- Receiving real-time push notifications

Protocol:
    Client -> Server:
        {"type": "fetch"}
        {"type": "mark_read", "id": "uuid"}
        {"type": "mark_all_read"}

    Server -> Client:
        {"type": "notifications", "data": [...], "unread": N}
        {"type": "notification_push", "notification": {...}}
        {"type": "read_confirmed", "id": "uuid"}
        {"type": "all_read_confirmed"}
        {"type": "error", "message": "..."}
"""
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth.models import AnonymousUser

from notifications.services import NotificationService

logger = logging.getLogger(__name__)


class NotificationConsumer(AsyncJsonWebsocketConsumer):
    """WebSocket consumer for notifications."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = None
        self.user_group = None
        self.broadcast_groups = []

    async def connect(self):
        """Handle WebSocket connection."""
        self.user = self.scope.get('user')

        # Reject unauthenticated connections
        if isinstance(self.user, AnonymousUser) or not self.user:
            logger.warning("WebSocket connection rejected: Not authenticated")
            await self.close(code=4001)
            return

        # Accept connection
        await self.accept()

        # Join user-specific notification group
        self.user_group = f"notifications_user_{self.user.user_id}"
        await self.channel_layer.group_add(self.user_group, self.channel_name)

        # Join broadcast groups the user is subscribed to
        broadcasts = await self._get_user_broadcasts()
        for broadcast in broadcasts:
            group_name = f"notifications_broadcast_{broadcast}"
            self.broadcast_groups.append(group_name)
            await self.channel_layer.group_add(group_name, self.channel_name)

        logger.info(
            f"WebSocket connected: user={self.user.user_id}, "
            f"broadcasts={len(self.broadcast_groups)}"
        )

    async def disconnect(self, close_code):
        """Handle WebSocket disconnection."""
        # Leave user group
        if self.user_group:
            await self.channel_layer.group_discard(self.user_group, self.channel_name)

        # Leave broadcast groups
        for group in self.broadcast_groups:
            await self.channel_layer.group_discard(group, self.channel_name)

        logger.info(
            f"WebSocket disconnected: user={getattr(self.user, 'user_id', 'unknown')}, "
            f"code={close_code}"
        )

    async def receive_json(self, content):
        """Handle incoming WebSocket messages."""
        msg_type = content.get('type')

        try:
            if msg_type == 'fetch':
                await self._handle_fetch()
            elif msg_type == 'mark_read':
                notification_id = content.get('id')
                if notification_id:
                    await self._handle_mark_read(notification_id)
                else:
                    await self._send_error("Missing 'id' for mark_read")
            elif msg_type == 'mark_all_read':
                await self._handle_mark_all_read()
            else:
                await self._send_error(f"Unknown message type: {msg_type}")
        except Exception as e:
            logger.error(f"Error handling WebSocket message: {e}", exc_info=True)
            await self._send_error("Internal server error")

    async def _handle_fetch(self):
        """Fetch all notifications for the user."""
        broadcasts = await self._get_user_broadcasts()
        notifications, unread = await self._get_notifications(broadcasts)

        await self.send_json({
            'type': 'notifications',
            'data': notifications if notifications else [],
            'unread': unread if unread else 0
        })

    async def _handle_mark_read(self, notification_id):
        """Mark a single notification as read."""
        broadcasts = await self._get_user_broadcasts()
        await self._read_notification(notification_id, broadcasts)

        await self.send_json({
            'type': 'read_confirmed',
            'id': notification_id
        })

    async def _handle_mark_all_read(self):
        """Mark all notifications as read."""
        broadcasts = await self._get_user_broadcasts()
        await self._read_all_notifications(broadcasts)

        await self.send_json({
            'type': 'all_read_confirmed'
        })

    async def _send_error(self, message):
        """Send error message to client."""
        await self.send_json({
            'type': 'error',
            'message': message
        })

    # Channel layer event handlers (for push notifications)
    async def notification_push(self, event):
        """
        Handle push notification from channel layer.
        Called when NotificationService.send_notification() pushes to the group.
        """
        await self.send_json({
            'type': 'notification_push',
            'notification': event['notification']
        })

    # Database operations (wrapped with database_sync_to_async)
    @database_sync_to_async
    def _get_user_broadcasts(self):
        """Get list of broadcasts user is subscribed to."""
        return NotificationService.get_user_broadcasts(self.user)

    @database_sync_to_async
    def _get_notifications(self, broadcasts):
        """Get notifications for user across broadcasts."""
        return NotificationService.get_manager().get_notification(
            str(self.user.user_id),
            broadcasts=broadcasts
        )

    @database_sync_to_async
    def _read_notification(self, notification_id, broadcasts):
        """Mark a notification as read."""
        return NotificationService.get_manager().read_notification(
            str(self.user.user_id),
            notification_id,
            broadcasts=broadcasts
        )

    @database_sync_to_async
    def _read_all_notifications(self, broadcasts):
        """Mark all notifications as read."""
        return NotificationService.get_manager().read_all_notification(
            str(self.user.user_id),
            broadcasts=broadcasts
        )
