"""
ASGI config for core project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/3.1/howto/deployment/asgi/
"""

import os

from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

# Initialize Django ASGI application early to ensure settings are loaded
django_asgi_app = get_asgi_application()

# Import after Django setup
from notifications.middleware import JWTAuthMiddlewareStack
from instance.upload_stream import stream_uploads
import application.routing
import application.chat.routing
import notifications.routing

application = ProtocolTypeRouter({
    # The backup upload is streamed to the runner instead of buffered.
    "http": stream_uploads(django_asgi_app),
    "websocket": JWTAuthMiddlewareStack(
        URLRouter(
            application.routing.websocket_urlpatterns +
            application.chat.routing.websocket_urlpatterns +
            notifications.routing.websocket_urlpatterns
        )
    ),
})
