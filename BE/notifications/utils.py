import json
import logging

from core.settings import REDIS_HOST, REDIS_PORT, REDIS_SSLAUTH, REDIS_USERNAME, REDIS_PASSWORD, REDIS_SSLCERT
import redis
from time import time, ctime
import uuid

from notifications.models import NotificationsBroadcast, UserNotificationsBroadcast

logger = logging.getLogger(__name__)

# Notification configuration constants
NOTIFICATION_TTL_DAYS = 30
NOTIFICATION_TTL_SECONDS = 60 * 60 * 24 * NOTIFICATION_TTL_DAYS
DEFAULT_NOTIFICATION_TYPE = "info"


# Create Redis connection pool at module level for reuse
# Redis 5.x+ uses different SSL parameter names
REDIS_POOL_KWARGS = {
    'host': REDIS_HOST,
    'port': REDIS_PORT,
    'password': REDIS_PASSWORD,
    'username': REDIS_USERNAME,
    'max_connections': 50,
    'socket_connect_timeout': 5,
    'socket_timeout': 5,
    'decode_responses': False
}

# Add SSL parameters if SSL is enabled
if REDIS_SSLAUTH: # pragma: no cover
    REDIS_POOL_KWARGS['connection_class'] = redis.SSLConnection
    if REDIS_SSLCERT:
        REDIS_POOL_KWARGS['ssl_certfile'] = REDIS_SSLCERT

REDIS_POOL = redis.ConnectionPool(**REDIS_POOL_KWARGS)


class NotificationManager:
    """
    Single Notification is a dictionary that looks like:
    {
        "id": "hash_number",
        "msg": "some notification text",
        "users": ["user1", "user2", ...],
        "expires": "1604663627"
    }
    users field collect the user that saw the notification,
    expires is used to set when to remove notification. (usually last 30 days)
    """
    def __init__(self):
        self.r = redis.Redis(connection_pool=REDIS_POOL)

        # these are called "ROLE_BROADCASTS"
        self.broadcasts = ["all_notifications"]
        # self.broadcasts.append(NotificationsBroadcast.all().)
        self.n_notifications = 10

        for broadcast in self.broadcasts:
            try:
                val = self.r.get(broadcast)
                if val is None: # pragma: no cover
                    self.r.set(broadcast, json.dumps({
                        "messages": ""
                    }))
                brd = NotificationsBroadcast.objects.filter(
                    broadcast_name=broadcast
                )
                if len(brd) == 0:
                    brd = NotificationsBroadcast.objects.create(
                        broadcast_name=broadcast
                    )
                    brd.save()
            except Exception as e: # pragma: no cover
                logger.warning(f"Failed to create broadcast '{broadcast}' in database, setting default value: {e}")
                self.r.set(broadcast,  json.dumps({
                    "messages": ""
                }))

    def add_broadcast(self, broadcast):
        try:
            self.broadcasts.append(broadcast)
            val = self.r.get(broadcast)
            if val is None: # pragma: no cover
                self.r.set(broadcast,  json.dumps({
                "messages": ""
            }))
            brd = NotificationsBroadcast.objects.create(
                broadcast_name=broadcast
            )
            brd.save()
        except Exception as e: # pragma: no cover
            logger.warning(f"Failed to add broadcast '{broadcast}' to database, setting default value: {e}")
            self.r.set(broadcast,  json.dumps({
                "messages": ""
            }))

    def parse_notification(self, broadcast):
        notifications_encoded = self.r.get(broadcast)
        if notifications_encoded is None: # pragma: no cover
            notifications_encoded = "{\"messages\": \"\"}"
        # notifications = notifications_encoded.decode('utf8').replace("'", '"')
        notifications = json.loads(notifications_encoded)   # notifications_encoded.decode('utf8').replace("'", '"'))

        if notifications['messages'] == "":
            notifications = []
        else:
            # notifications = ast.literal_eval(notifications['messages'])
            notifications = notifications['messages']
        return notifications

    def validate_broadcast(self, user, broadcast):
        brd = NotificationsBroadcast.objects.filter(broadcast_name=broadcast).last()
        if brd is None:
            brd = NotificationsBroadcast.objects.create(
                broadcast_name=broadcast
            )
            brd.save()
        if len(UserNotificationsBroadcast.objects.filter(broadcast=brd, user=user)) == 0:
            user_brd = UserNotificationsBroadcast.objects.create(
                broadcast=brd,
                user=user
            )
            user_brd.save()
            self.r.set(broadcast, json.dumps({
                "messages": ""
            }))
        if broadcast not in self.broadcasts:
            self.broadcasts.append(broadcast)

    def update_broadcast(self, broadcast, value, mode='overwrite'):
        try:
            if mode == 'overwrite': # pragma: no cover
                self.r.set(broadcast, json.dumps({
                    "messages": value
                }))
        except Exception as e: # pragma: no cover
            logger.error(f"Failed to update broadcast '{broadcast}': {e}", exc_info=True)
            return False
        return True

    def add_notification_role_broadcast(self, messages, broadcast='all_notifications', append=True):
        """
        I get all notifications, add the new notification and update value.
        :param broadcast: a role broadcast
        :param messages: a list [] of notifications
        :param append: append notification to end
        :return: True if everything is ok, False otherwise
        """

        if broadcast is None:
            self.add_broadcast(broadcast)

        if broadcast in self.broadcasts:
            if append:
                notifications = self.parse_notification(broadcast)

                if messages is not None:
                    for message in messages:
                        notification_type = DEFAULT_NOTIFICATION_TYPE
                        if "type" in message.keys():
                            notification_type = message["type"]
                        message_obj = {
                            "id": str(uuid.uuid1()),
                            "type": notification_type,
                            "msg": message['msg'],
                            "users": [],
                            "expires": int(time()) + NOTIFICATION_TTL_SECONDS
                        }
                        notifications.append(message_obj)
                    # update redis
                    self.update_broadcast(broadcast, notifications)
                else:
                    return False
            else:
                self.r.set(broadcast,  json.dumps({
                    "messages": messages
                }))
        else:
            return False
        return True

    def read_notification(self, user_key, notification_key, broadcasts=None):
        """
        find the notification and check that user see the content.
        :param user_key:
        :param notification_key:
        :param broadcasts:
        :return:
        """
        if broadcasts is None:
            for broadcast in self.broadcasts:
                self.read_notification_by_broadcast(user_key, notification_key, broadcast)
        else:
            for broadcast in broadcasts:
                self.read_notification_by_broadcast(user_key, notification_key, broadcast)
        return True

    def read_notification_by_broadcast(self, user_key, notification_key, broadcast):
        """
        Read the notification given a certain broadcast
        """
        notifications = self.parse_notification(broadcast)

        for i in range(len(notifications)):
            notification = dict(notifications[i])
            # if the key is not present in the user field means that it has not been read
            if user_key not in notification['users'] and notification['id'] == notification_key:
                notifications[i]['users'].append(user_key)
                self.r.set(broadcast, json.dumps({
                    "messages": notifications
                }))
                break

    def read_all_notification(self, user_key, broadcasts=None):
        """
        Read all notifications
        :param broadcasts:
        :param user_key:
        :return:
        """
        if broadcasts is None:
            broadcasts = ['all_notifications']
        try:
            # I check all the broadcasts to find notifications
            for broadcast in broadcasts:
                notifications = self.parse_notification(broadcast)

                for i in range(len(notifications)):
                    notification = dict(notifications[i])
                    # if the key is not present in the user field means that it has not been read
                    if user_key not in notification['users']:
                        notifications[i]['users'].append(user_key)
                        self.update_broadcast(broadcast, notifications)
            return True
        except Exception as e:
            logger.error(f"Failed to mark all notifications as read for user '{user_key}': {e}", exc_info=True)
            return False

    def _build_notification_dict(self, notification, user_key):
        """
        Build notification dictionary with read status.

        Args:
            notification: Raw notification dict from Redis
            user_key: User ID string

        Returns:
            dict: Formatted notification with read status
        """
        notification_type = notification.get("type", DEFAULT_NOTIFICATION_TYPE)
        is_read = user_key in notification['users']
        issued_timestamp = notification['expires'] - NOTIFICATION_TTL_SECONDS

        return {
            "id": notification['id'],
            "type": notification_type,
            "read": is_read,
            "msg": notification['msg'],
            "issued": issued_timestamp,
            "date": ctime(issued_timestamp)
        }

    def get_notification(self, user_key, broadcasts=None):
        """
        Get notifications for a user across specified broadcasts.

        Args:
            user_key: User ID string
            broadcasts: List of broadcast names (default: ['all_notifications'])

        Returns:
            tuple: (sorted notifications list, unread count)
        """
        if broadcasts is None:
            broadcasts = ['all_notifications']

        results = []
        unread = 0

        for broadcast in broadcasts:
            notifications = self.parse_notification(broadcast)

            for notification in notifications:
                notification_dict = self._build_notification_dict(dict(notification), user_key)
                results.append(notification_dict)

                if not notification_dict['read']:
                    unread += 1

        # Sort notifications by most recent
        results.sort(key=lambda x: x['issued'], reverse=True)

        return results, unread if unread > 0 else ""
