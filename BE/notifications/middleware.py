"""
WebSocket JWT Authentication Middleware

Supports authentication via:
1. Query parameter: ws://host/path/?token=xxx
2. Cookie: BKN_AUTH (set on login)

The JWT is EdDSA-signed SimpleJWT access token.
"""
import logging
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import TokenError, InvalidToken

from application.models import User

logger = logging.getLogger(__name__)


@database_sync_to_async
def get_user_from_id(user_id):
    """Fetch user from database by user_id."""
    try:
        return User.objects.get(user_id=user_id)
    except User.DoesNotExist:
        return AnonymousUser()


class JWTAuthMiddleware(BaseMiddleware):
    """
    WebSocket authentication middleware.

    Checks for JWT token in:
    1. Query parameter: ?token=xxx
    2. BKN_AUTH cookie

    JWT payload expected:
    {
        'iss': 'https://bakney.com',
        'aud': 'bakney',
        'user_id': 'uuid-string'
    }
    """

    async def __call__(self, scope, receive, send):
        # Try query param first, then cookie
        token = self._get_token_from_query(scope)
        if not token:
            token = self._get_token_from_cookie(scope)

        if token:
            scope['user'] = await self._authenticate_token(token)
        else:
            scope['user'] = AnonymousUser()

        return await super().__call__(scope, receive, send)

    def _get_token_from_query(self, scope):
        """Extract token from ?token=xxx query parameter."""
        query_string = scope.get('query_string', b'').decode('utf-8')
        params = parse_qs(query_string)
        tokens = params.get('token', [])
        return tokens[0] if tokens else None

    def _get_token_from_cookie(self, scope):
        """Extract token from BKN_AUTH cookie."""
        headers = dict(scope.get('headers', []))
        cookie_header = headers.get(b'cookie', b'').decode('utf-8')

        for cookie in cookie_header.split(';'):
            cookie = cookie.strip()
            if cookie.startswith('BKN_AUTH='):
                return cookie[9:]  # len('BKN_AUTH=') = 9
        return None

    async def _authenticate_token(self, token):
        """Verify JWT and return User or AnonymousUser."""
        try:
            access_token = AccessToken(token)

            if access_token.get('iss') != 'https://bakney.com':
                return AnonymousUser()
            if access_token.get('aud') != 'bakney':
                return AnonymousUser()

            user_id = access_token.get('user_id')
            if user_id:
                return await get_user_from_id(user_id)
        except (TokenError, InvalidToken) as e:
            logger.warning(f"WebSocket auth: {e}")
        except Exception as e:
            logger.error(f"WebSocket auth: Unexpected error - {e}", exc_info=True)

        return AnonymousUser()


def JWTAuthMiddlewareStack(inner):
    """Wrapper function to apply JWTAuthMiddleware."""
    return JWTAuthMiddleware(inner)
