from django.urls import path
from . import consumers

websocket_urlpatterns = [
    path('ws/agent/', consumers.AgentConsumer.as_asgi()),
]
