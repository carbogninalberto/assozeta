from django.urls import path
from . import views

urlpatterns = [
    path('chat/test/', views.chat_test, name='chat_test'),
]
