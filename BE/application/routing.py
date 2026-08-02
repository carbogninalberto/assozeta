from django.urls import path
from . import consumers
from .health_consumer import HealthConsumer

websocket_urlpatterns = [
    path('ws/updates/', consumers.UpdateConsumer.as_asgi()),
    path('ws/health/', HealthConsumer.as_asgi()),
]