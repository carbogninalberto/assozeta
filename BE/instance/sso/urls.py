from django.urls import path
from .views import PairingChallengeView, PairingCommitView, LoginStartView, LoginCallbackView, LoginSessionView

urlpatterns = [
    path('pairing/challenge', PairingChallengeView.as_view(), name='bakney-pairing-challenge'),
    path('pairing/commit', PairingCommitView.as_view(), name='bakney-pairing-commit'),
    path('login-start', LoginStartView.as_view(), name='bakney-sso-start'),
    path('callback', LoginCallbackView.as_view(), name='bakney-sso-callback'),
    path('session', LoginSessionView.as_view(), name='bakney-sso-session'),
]
