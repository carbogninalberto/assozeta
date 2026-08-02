"""
Social Authentication Service
Verifies Google and Apple tokens directly without drf-social-oauth2.
"""
import logging
from typing import Optional, Dict, Any, Tuple

import jwt
import requests
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from django.conf import settings

logger = logging.getLogger(__name__)


class SocialAuthService:
    """Service for verifying social provider tokens."""

    GOOGLE_ISSUERS = ['accounts.google.com', 'https://accounts.google.com']
    APPLE_KEYS_URL = 'https://appleid.apple.com/auth/keys'
    APPLE_ISSUER = 'https://appleid.apple.com'

    @classmethod
    def verify_google_token(cls, token: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Verify a Google OAuth2 token (ID token or access token).

        Args:
            token: Google OAuth2 token from frontend

        Returns:
            Tuple of (success: bool, user_info: dict or None)
        """
        try:
            # First try to verify as ID token
            idinfo = id_token.verify_oauth2_token(
                token,
                google_requests.Request(),
                settings.SOCIAL_AUTH_GOOGLE_OAUTH2_KEY
            )

            if idinfo['iss'] not in cls.GOOGLE_ISSUERS:
                logger.warning("Invalid Google token issuer", extra={'iss': idinfo.get('iss')})
                return False, None

            return True, {
                'email': idinfo.get('email'),
                'email_verified': idinfo.get('email_verified', False),
                'first_name': idinfo.get('given_name', ''),
                'last_name': idinfo.get('family_name', ''),
                'picture': idinfo.get('picture'),
                'sub': idinfo.get('sub'),  # Google user ID
            }
        except ValueError as e:
            # Token might be an access token, not an ID token
            logger.debug("Google ID token verification failed, trying access token",
                        extra={'error': str(e)})
            return cls._verify_google_access_token(token)

    @classmethod
    def _verify_google_access_token(cls, access_token: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Verify Google access token by calling userinfo endpoint."""
        try:
            response = requests.get(
                'https://www.googleapis.com/oauth2/v3/userinfo',
                headers={'Authorization': f'Bearer {access_token}'},
                timeout=10
            )

            if response.status_code != 200:
                logger.warning("Google userinfo request failed",
                             extra={'status': response.status_code})
                return False, None

            data = response.json()
            return True, {
                'email': data.get('email'),
                'email_verified': data.get('email_verified', False),
                'first_name': data.get('given_name', ''),
                'last_name': data.get('family_name', ''),
                'picture': data.get('picture'),
                'sub': data.get('sub'),
            }
        except Exception as e:
            logger.error("Google access token verification failed",
                        extra={'error': str(e)}, exc_info=True)
            return False, None

    @classmethod
    def verify_apple_token(cls, identity_token: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Verify an Apple Sign In identity token.

        Args:
            identity_token: Apple identity token from frontend

        Returns:
            Tuple of (success: bool, user_info: dict or None)
        """
        try:
            # Fetch Apple's public keys
            keys_response = requests.get(cls.APPLE_KEYS_URL, timeout=10)
            apple_keys = keys_response.json()['keys']

            # Decode header to get key ID
            header = jwt.get_unverified_header(identity_token)
            kid = header.get('kid')

            # Find matching key
            key_data = next((k for k in apple_keys if k['kid'] == kid), None)
            if not key_data:
                logger.warning("Apple key not found", extra={'kid': kid})
                return False, None

            # Construct public key from JWK
            from jwt.algorithms import RSAAlgorithm
            public_key = RSAAlgorithm.from_jwk(key_data)

            # Verify and decode token
            decoded = jwt.decode(
                identity_token,
                public_key,
                algorithms=['RS256'],
                audience=settings.SOCIAL_AUTH_APPLE_ID_CLIENT,
                issuer=cls.APPLE_ISSUER
            )

            return True, {
                'email': decoded.get('email'),
                'email_verified': decoded.get('email_verified', False),
                'sub': decoded.get('sub'),  # Apple user ID
                # Note: Apple only provides name on first login
                'first_name': '',
                'last_name': '',
            }
        except jwt.ExpiredSignatureError:
            logger.warning("Apple token expired")
            return False, None
        except jwt.InvalidTokenError as e:
            logger.warning("Invalid Apple token", extra={'error': str(e)})
            return False, None
        except Exception as e:
            logger.error("Apple token verification failed",
                        extra={'error': str(e)}, exc_info=True)
            return False, None
