from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes

from application.models import User
from application.models.user_models import UsersOnboarding
from application.serializers.auth_serializers import SportAssociationOnboardingStep0Serializer, \
    UsersOnboardingSerializer
from core.middleware import IsAuthenticated

import logging

logger = logging.getLogger(__name__)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def onboarding_update(request):
    logger.info("Processing onboarding update", extra={
        'user_id': str(request.user.user_id),
        'update_type': next((k for k in ['sport_association', 'membership_data', 'lead_data', 'onboarding_data'] if k in request.data), 'unknown')
    })

    if not request.user.is_sport_association(raise_exception=False):
        logger.warning("Unauthorized onboarding update - not sport association", extra={
            'user_id': str(request.user.user_id),
            'role': request.user.role
        })
        return Response({"error": "You are not a sport association."}, status=status.HTTP_403_FORBIDDEN)

    try:
        if 'sport_association' in request.data:
            logger.debug("Updating sport association data", extra={'user_id': str(request.user.user_id)})

            # update with serializer
            serializer = SportAssociationOnboardingStep0Serializer(request.user.sportassociation, data=request.data['sport_association'], partial=True)

            if serializer.is_valid(raise_exception=True):

                instance = serializer.save()
                logger.info("Sport association updated successfully", extra={
                    'user_id': str(request.user.user_id),
                    'sport_association_id': str(instance.sport_association_id)
                })

        elif 'membership_data' in request.data:
            logger.debug("Updating membership data", extra={'user_id': str(request.user.user_id)})

            serializer = SportAssociationOnboardingStep0Serializer(request.user.sportassociation, data=request.data['membership_data'], partial=True)

            if serializer.is_valid(raise_exception=True):
                instance = serializer.save()
                logger.info("Membership data updated successfully", extra={
                    'user_id': str(request.user.user_id),
                    'sport_association_id': str(instance.sport_association_id),
                    'membership_fee': request.data['membership_data'].get('membership_fee'),
                    'subscription_fee': request.data['membership_data'].get('subscription_fee')
                })

            request.user.balance_sheet_year = User.OTHER
            if 'season' in request.data['membership_data']:
                request.user.subscription_start_day = request.data['membership_data']['season']['day']
                request.user.subscription_start_month = request.data['membership_data']['season']['month']
            if 'fiscal' in request.data['membership_data']:
                request.user.balance_sheet_start_day = request.data['membership_data']['fiscal']['day']
                request.user.balance_sheet_start_month = request.data['membership_data']['fiscal']['month']

            # update fees
            if 'membership_fee' in request.data['membership_data'] \
                    and request.data['membership_data']['membership_fee'] is not None:
                request.user.sportassociation.membership_fee = float(request.data['membership_data']['membership_fee'])
            if 'subscription_fee' in request.data['membership_data'] \
                    and request.data['membership_data']['subscription_fee'] is not None:
                request.user.sportassociation.subscription_fee = float(request.data['membership_data']['subscription_fee'])
            request.user.sportassociation.save()
            request.user.save()
        elif 'lead_data' in request.data:
            logger.debug("Updating lead data", extra={'user_id': str(request.user.user_id)})
            request.user.lead_sport_association_role = request.data['lead_data']['lead_sport_association_role']
            request.user.lead_sport_association_size = request.data['lead_data']['lead_sport_association_size']
            request.user.lead_sport_market_channel = request.data['lead_data']['lead_sport_market_channel']

            if 'phone' in request.data['lead_data']:
                request.user.phone = request.data['lead_data']['phone']
            request.user.save()
            logger.info("Lead data updated successfully", extra={
                'user_id': str(request.user.user_id),
                'role': request.data['lead_data']['lead_sport_association_role'],
                'size': request.data['lead_data']['lead_sport_association_size']
            })

        elif 'onboarding_data' in request.data:
            logger.debug("Updating onboarding data", extra={'user_id': str(request.user.user_id)})
            users_onboarding = UsersOnboarding.objects.get(user=request.user)
            validator = UsersOnboardingSerializer(users_onboarding, data=request.data['onboarding_data'], partial=True)

            if validator.is_valid(raise_exception=True):
                instance = validator.save()
                logger.info("Onboarding data updated successfully", extra={
                    'user_id': str(request.user.user_id),
                    'onboarding_id': str(instance.users_onboarding_id)
                })

        logger.info("Onboarding update completed successfully", extra={'user_id': str(request.user.user_id)})
        return Response({
            "message": "Updated successfully"
        }, status=status.HTTP_200_OK)
    except ValidationError as e:
        logger.warning("Onboarding validation error", extra={
            'user_id': str(request.user.user_id),
            'error': str(e)
        })
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        logger.error("Onboarding update error", extra={
            'user_id': str(request.user.user_id),
            'error': str(e)
        }, exc_info=True)
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

