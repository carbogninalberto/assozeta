from decimal import Decimal, InvalidOperation

from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from django.db import transaction
from django.utils import timezone

from application.models import User
from application.models.user_models import UsersOnboarding
from application.serializers.auth_serializers import SportAssociationOnboardingStep0Serializer, \
    UsersOnboardingSerializer
from core.middleware import IsAuthenticated
from instance.models import InstanceConfiguration

import logging

logger = logging.getLogger(__name__)


def _get_matching_fresh_instance_config(user):
    sport_association = user.sportassociation
    config = InstanceConfiguration.objects.select_for_update().select_related('primary_association').filter(
        self_hosted=True,
        setup_provenance=InstanceConfiguration.SETUP_PROVENANCE_FRESH,
        primary_association=sport_association,
    ).first()

    if config is None:
        return None

    if str(config.primary_association.user_id) != str(user.user_id):
        return None

    return config


def _validate_fresh_final_membership_payload(membership_data):
    if not isinstance(membership_data, dict):
        return {'membership_data': 'This field must be an object.'}

    errors = {}

    def validate_day_month(section_name):
        section = membership_data.get(section_name)
        if not isinstance(section, dict):
            errors[section_name] = 'This section is required.'
            return

        for field_name, upper_bound in (('day', 31), ('month', 12)):
            value = section.get(field_name)
            if value in (None, ''):
                errors[f'{section_name}.{field_name}'] = 'This field is required.'
                continue
            try:
                parsed_decimal = Decimal(str(value))
                parsed_value = int(parsed_decimal)
            except (InvalidOperation, TypeError, ValueError, OverflowError):
                errors[f'{section_name}.{field_name}'] = 'This field must be a valid number.'
                continue
            if parsed_decimal != parsed_decimal.to_integral_value():
                errors[f'{section_name}.{field_name}'] = 'This field must be a whole number.'
                continue
            if parsed_value < 1 or parsed_value > upper_bound:
                errors[f'{section_name}.{field_name}'] = f'This field must be between 1 and {upper_bound}.'

    validate_day_month('season')
    validate_day_month('fiscal')

    for field_name in ('subscription_fee', 'membership_fee'):
        value = membership_data.get(field_name)
        if value in (None, ''):
            errors[field_name] = 'This field is required.'
            continue
        try:
            amount = Decimal(str(value).replace(',', '.'))
        except (InvalidOperation, ValueError):
            errors[field_name] = 'This field must be a valid amount.'
            continue
        if not amount.is_finite():
            errors[field_name] = 'This field must be a valid amount.'

    return errors


def _complete_matching_fresh_instance_onboarding(user, config=None):
    if config is None:
        config = _get_matching_fresh_instance_config(user)

    if config is None:
        return False

    if config.onboarding_completed_at is None:
        config.onboarding_completed_at = timezone.now()
        config.save(update_fields=['onboarding_completed_at', 'updated_at'])

    return True


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

            with transaction.atomic():
                membership_data = request.data['membership_data']
                fresh_config = _get_matching_fresh_instance_config(request.user)
                if fresh_config and fresh_config.onboarding_completed_at is None:
                    validation_errors = _validate_fresh_final_membership_payload(membership_data)
                    if validation_errors:
                        return Response({
                            "error": "Fresh self-host onboarding requires a complete membership payload.",
                            "details": validation_errors,
                        }, status=status.HTTP_400_BAD_REQUEST)

                serializer = SportAssociationOnboardingStep0Serializer(request.user.sportassociation, data=membership_data, partial=True)

                if serializer.is_valid(raise_exception=True):
                    instance = serializer.save()
                    logger.info("Membership data updated successfully", extra={
                        'user_id': str(request.user.user_id),
                        'sport_association_id': str(instance.sport_association_id),
                        'membership_fee': membership_data.get('membership_fee'),
                        'subscription_fee': membership_data.get('subscription_fee')
                    })

                request.user.balance_sheet_year = User.OTHER
                if 'season' in membership_data:
                    request.user.subscription_start_day = membership_data['season']['day']
                    request.user.subscription_start_month = membership_data['season']['month']
                if 'fiscal' in membership_data:
                    request.user.balance_sheet_start_day = membership_data['fiscal']['day']
                    request.user.balance_sheet_start_month = membership_data['fiscal']['month']

                # update fees
                if 'membership_fee' in membership_data \
                        and membership_data['membership_fee'] is not None:
                    request.user.sportassociation.membership_fee = float(membership_data['membership_fee'])
                if 'subscription_fee' in membership_data \
                        and membership_data['subscription_fee'] is not None:
                    request.user.sportassociation.subscription_fee = float(membership_data['subscription_fee'])
                request.user.sportassociation.save()
                request.user.save()
                _complete_matching_fresh_instance_onboarding(request.user, fresh_config)
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
