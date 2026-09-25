from rest_framework_simplejwt.authentication import JWTAuthentication

from application.impersonation import resolve_request_identity


class ScopedJWTAuthentication(JWTAuthentication):
    """Resolve the existing User-Id context for every authenticated REST endpoint."""
    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        actor, token = result
        return resolve_request_identity(request, actor), token
