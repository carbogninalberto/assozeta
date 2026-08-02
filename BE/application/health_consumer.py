"""
WebSocket Consumer for Health Checks

Provides real-time health status without authentication.

Protocol:
    Client -> Server:
        {"type": "check"}

    Server -> Client:
        {"type": "health", "status": "ok", "version": "v1.0.0"}
        {"type": "error", "message": "..."}
"""
import logging

from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings

logger = logging.getLogger(__name__)


class HealthConsumer(AsyncJsonWebsocketConsumer):
    """WebSocket consumer for health checks."""

    async def connect(self):
        """Accept connection and send initial health status."""
        await self.accept()
        # Send health status immediately on connect
        await self._send_health_status()
        logger.debug("Health WebSocket connected")

    async def disconnect(self, close_code):
        """Handle disconnection."""
        logger.debug(f"Health WebSocket disconnected: code={close_code}")

    async def receive_json(self, content):
        """Handle incoming messages."""
        msg_type = content.get('type')

        if msg_type == 'check':
            await self._send_health_status()
        else:
            await self.send_json({
                'type': 'error',
                'message': f'Unknown message type: {msg_type}'
            })

    async def _send_health_status(self):
        """Send current health status."""
        await self.send_json({
            'type': 'health',
            'status': 'ok',
            'msg': 'I am good',
            'version': settings.RUNNING_VERSION
        })
