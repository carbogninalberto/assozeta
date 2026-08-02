"""
JWT Token Service
Handles token generation and response formatting for frontend compatibility.
"""
import logging
from typing import Dict, Any, Optional

from django.conf import settings
from rest_framework_simplejwt.tokens import RefreshToken

from application.models import User
from application.models.user_models import (
    SportAssociation,
    SportAssociationMembershipCardConfiguration,
    Instructor
)
from application.serializers.auth_serializers import UserAuthSerializer, SportAssociationSerializer
from application.utils.api_utils import compress_base64

logger = logging.getLogger(__name__)


class JWTTokenService:
    """Service for JWT token operations."""

    @staticmethod
    def generate_tokens_for_user(user: User) -> Dict[str, Any]:
        """
        Generate JWT access and refresh tokens for a user.

        Args:
            user: User instance to generate tokens for

        Returns:
            Dict with access_token, refresh_token, expires_in, token_type
        """
        refresh = RefreshToken.for_user(user)

        # Add custom claims to refresh token (inherited by access token)
        refresh['user_id'] = str(user.user_id)
        refresh['role'] = user.role
        refresh['email'] = user.email
        refresh['iss'] = 'https://bakney.com'
        refresh['aud'] = 'bakney'

        access_token = refresh.access_token

        return {
            'access_token': str(access_token),
            'refresh_token': str(refresh),
            'expires_in': int(settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds()),
            'token_type': 'Bearer',
        }

    @staticmethod
    def refresh_access_token(refresh_token_str: str) -> Optional[Dict[str, Any]]:
        """
        Refresh an access token using a refresh token.

        Args:
            refresh_token_str: The refresh token string

        Returns:
            Dict with new tokens or None if invalid
        """
        try:
            refresh = RefreshToken(refresh_token_str)

            return {
                'access_token': str(refresh.access_token),
                'refresh_token': str(refresh),  # New refresh token (rotation)
                'expires_in': int(settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds()),
                'token_type': 'Bearer',
            }
        except Exception as e:
            logger.warning("Token refresh failed", extra={'error': str(e)})
            return None

    @classmethod
    def build_login_response(cls, user: User, tokens: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build the complete login response matching frontend contract.

        This replicates the logic from the original get_content_from_response()
        to ensure backward compatibility with the frontend.

        Args:
            user: Authenticated user
            tokens: Token dict from generate_tokens_for_user()

        Returns:
            Complete response dict with tokens + user data
        """
        content = tokens.copy()
        content['role'] = User.ROLE_CHOICES[user.role - 1][1]
        content['user_data'] = UserAuthSerializer(user).data

        if user.role == User.COLLABORATOR:
            content['requires_welcome'] = False

        if user.role == User.ASSOCIATION or user.role == User.COLLABORATOR:
            # Get sport association
            if user.role == User.ASSOCIATION:
                sport_association = SportAssociation.objects.get(user=user)
            else:
                sport_association = SportAssociation.objects.get(user=user.connected_user)

            # Check for connected instructor (collaborators)
            if user.role == User.COLLABORATOR:
                connected_instructor = Instructor.objects.filter(
                    associated_user_id=user.user_id
                ).first()
                if connected_instructor:
                    content['user_data']['instructor_id'] = connected_instructor.instructor_id

            content['user_data']['sport_association'] = SportAssociationSerializer(sport_association).data
            content['user_data']['temporary_invoice_deletion'] = user.temporary_invoice_deletion

            # Compress avatar image
            if content['user_data']['avatar_image'] is not None:
                content['user_data']['avatar_image'] = compress_base64(
                    content['user_data']['avatar_image']
                )

            # Check for empty sections
            if (sport_association.regulation is None or sport_association.regulation == '' or
                    sport_association.demand is None or sport_association.demand == ''):
                content['user_data']['sport_association']['empty_sections'] = True
                # Compress logo
                if content['user_data']['sport_association']['logo'] is not None:
                    content['user_data']['sport_association']['logo'] = compress_base64(
                        content['user_data']['sport_association']['logo']
                    )

            content['user_data']['preview_and_custom_features'] = user.get_preveiw_and_custom_features()

            # Membership card configuration
            membership_card_config = SportAssociationMembershipCardConfiguration.objects.filter(
                sport_association=user.sport_association
            ).first()
            if membership_card_config is None:
                membership_card_config = SportAssociationMembershipCardConfiguration(
                    sport_association=user.sport_association
                )
                membership_card_config.save()

            content['user_data']['sport_association']['membership_card_configuration'] = {
                'emit_only_on_approval': membership_card_config.emit_only_on_approval,
                'customized_template': membership_card_config.customized_template
            }
            content['tables_settings'] = user.tables_settings

        if user.is_superuser:
            content['user_data']['is_superuser'] = True

        return content
