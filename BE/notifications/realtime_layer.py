"""Channels Redis layer that also publishes every group message for Go sockets.

Django keeps its normal Channels delivery unchanged. After each successful
``group_send`` the same group name and message are published as a version-1
JSON envelope on one Redis pub/sub channel, so a Go process serving the same
WebSocket consumers can deliver them to its own connections. Go reads only
this envelope, never the Channels msgpack encoding. Publication is best effort
and at most once, like Channels group delivery itself: a failure is logged and
never fails the sender.
"""
import json
import logging

from channels_redis.core import RedisChannelLayer

logger = logging.getLogger(__name__)

REALTIME_CHANNEL = 'assozeta:realtime:v1'
ENVELOPE_VERSION = 1


def envelope(group, message):
    return json.dumps({'version': ENVELOPE_VERSION, 'group': group, 'message': message})


class GoMirroredRedisChannelLayer(RedisChannelLayer):
    async def group_send(self, group, message):
        await super().group_send(group, message)
        try:
            await self.connection(0).publish(REALTIME_CHANNEL, envelope(group, message))
        except Exception:
            logger.warning('Unable to mirror a group message for Go WebSockets', exc_info=True)
