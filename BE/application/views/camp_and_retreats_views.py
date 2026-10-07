from django.db import transaction

from rest_framework import serializers
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes

from application.models import CampsAndRetreats, CampsAndRetreatsPeriod, CampsAndRetreatsPeriodsService, \
    CampsAndRetreatsSubscription, CampsAndRetreatsSubscriptionPeriod, Subscription, User
from application.serializers.camps_and_retreats_serializers import CampsAndRetreatsSerializer, \
    CampsAndRetreatsInfoSerializer, CampsAndRetreatsPeriodSerializer, CampsAndRetreatsPeriodInfoSerializer, CampsAndRetreatsSubscriptionInfoSerializer, CampsAndRetreatsPeriodsServiceAddSerializer, \
    CampsAndRetreatsSubscriptionListInfoSerializer, CampsAndRetreatsPublicInfoSerializer
from application.utils.api_utils import is_valid_uuid
from application.utils.camps_and_retreats_utils import add_new_periods, generate_payment_for_periods, delete_unpaid_periods
from core.middleware import IsAuthenticated

import logging

logger = logging.getLogger(__name__)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def camps_and_retreats_add(request):
    """
    This method is used to add a new camp or retreat
    """

    if not request.user.is_sport_association(raise_exception=False):
        return Response({"error": "Only sport associations can add camps or retreats"}, status=status.HTTP_403_FORBIDDEN)

    try:
        logger.info(f"Adding new camp or retreat with data: {request.data}")
        request.data['sport_association'] = request.user.sport_association.sport_association_id
        serializer = CampsAndRetreatsSerializer(data=request.data)

        if serializer.is_valid(raise_exception=True):
            camp_and_retreat = serializer.save()
            camp_and_retreat.save()

        return Response({
            "message": "Camp or retreat added successfully",
            "camp_and_retreat": serializer.data
        }, status=status.HTTP_201_CREATED)
    except ValidationError as e:
        logger.error(f"Validation error adding new camp or retreat: {str(e)}")
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        logger.error(f"Error adding new camp or retreat: {str(e)}")
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def camps_and_retreats_update(request, uid):

    is_valid_uuid(uid)
    if not request.user.is_sport_association(raise_exception=False):
        return Response({"error": "Only sport associations can update camps or retreats"}, status=status.HTTP_403_FORBIDDEN)

    try:
        camp_and_retreat = CampsAndRetreats.objects.get(camps_and_retreats_id=uid)
        if camp_and_retreat.sport_association != request.user.sport_association:
            raise PermissionDenied("You are not allowed to update this camp or retreat")

        serializer = CampsAndRetreatsSerializer(camp_and_retreat, data=request.data, partial=True)

        if serializer.is_valid(raise_exception=True):
            serializer.save()

        return Response({
            "message": "Camp or retreat updated successfully",
            "camp_and_retreat": serializer.data
        }, status=status.HTTP_200_OK)
    except PermissionDenied:
        raise
    except CampsAndRetreats.DoesNotExist:
        return Response({"error": "Camp or retreat not found"}, status=status.HTTP_404_NOT_FOUND)
    except ValidationError as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def camps_and_retreats_delete(request, uid):

    is_valid_uuid(uid)
    if not request.user.is_sport_association(raise_exception=False):
        return Response({"error": "Only sport associations can delete camps or retreats"}, status=status.HTTP_403_FORBIDDEN)

    try:
        camp_and_retreat = CampsAndRetreats.objects.get(camps_and_retreats_id=uid)
        if camp_and_retreat.sport_association != request.user.sport_association:
            raise PermissionDenied("You are not allowed to delete this camp or retreat")

        camp_and_retreat.delete()
        return Response({"message": "Camp or retreat deleted successfully"}, status=status.HTTP_200_OK)
    except PermissionDenied:
        raise
    except CampsAndRetreats.DoesNotExist:
        return Response({"error": "Camp or retreat not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def camps_and_retreats_list(request):
    """
    This method is used to get a list of all camps and retreats
    """

    if not request.user.is_sport_association(raise_exception=False):
        return Response({"error": "Only sport associations can view camps or retreats"}, status=status.HTTP_403_FORBIDDEN)

    camps_and_retreats = CampsAndRetreats.objects.filter(sport_association=request.user.sport_association)
    serializer = CampsAndRetreatsSerializer(camps_and_retreats, many=True)

    return Response({"data": serializer.data}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def camps_and_retreats_info(request, uid):
    """
    This method is used to get a list of all camps and retreats
    """

    is_valid_uuid(uid)

    # check if there is a user logged in
    try:
        if not request.user.is_sport_association(raise_exception=False):
            camps_and_retreat = CampsAndRetreats.objects.filter(camps_and_retreats_id=uid).first()
            if camps_and_retreat is None:
                return Response({"error": "Camp or retreat not found"}, status=status.HTTP_404_NOT_FOUND)
            serializer = CampsAndRetreatsPublicInfoSerializer(camps_and_retreat)
            return Response({"data": serializer.data}, status=status.HTTP_200_OK)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    try:
        camp_and_retreat = CampsAndRetreats.objects.get(camps_and_retreats_id=uid)
        if camp_and_retreat.sport_association != request.user.sport_association:
            raise PermissionDenied("You are not allowed to view this camp or retreat")

        serializer = CampsAndRetreatsInfoSerializer(camp_and_retreat)
        return Response({"data": serializer.data}, status=status.HTTP_200_OK)
    except PermissionDenied:
        raise
    except CampsAndRetreats.DoesNotExist:
        return Response({"error": "Camp or retreat not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def camps_and_retreats_periods_add(request):
    if not request.user.is_sport_association(raise_exception=False):
        return Response({"error": "You are not a sport association."}, status=status.HTTP_403_FORBIDDEN)

    try:
        serializer = CampsAndRetreatsPeriodSerializer(data=request.data)

        if serializer.is_valid(raise_exception=True):
            period = serializer.save()
            period.save()

        return Response({
            "message": "Added successfully",
            "period": serializer.data
        }, status=status.HTTP_201_CREATED)
    except ValidationError as e:
        logger.error(f"Validation error adding: {str(e)}")
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        logger.error(f"Error adding: {str(e)}")
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def camps_and_retreats_periods_update(request, uid):

    is_valid_uuid(uid)
    if not request.user.is_sport_association(raise_exception=False):
        return Response({"error": "You are not a sport association."}, status=status.HTTP_403_FORBIDDEN)

    try:
        period = CampsAndRetreatsPeriod.objects.get(camps_and_retreats_period_id=uid)
        if period.camps_and_retreats.sport_association != request.user.sport_association:
            raise PermissionDenied("You are not allowed editing this.")

        serializer = CampsAndRetreatsPeriodSerializer(period, data=request.data, partial=True)

        if serializer.is_valid(raise_exception=True):
            serializer.save()

        return Response({
            "message": "Updated successfully",
            "period": serializer.data
        }, status=status.HTTP_200_OK)
    except PermissionDenied:
        raise
    except CampsAndRetreatsPeriod.DoesNotExist:
        return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
    except ValidationError as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def camps_and_retreats_periods_delete(request, uid):

    is_valid_uuid(uid)
    if not request.user.is_sport_association(raise_exception=False):
        return Response({"error": "You are not a sport association."}, status=status.HTTP_403_FORBIDDEN)

    try:
        period = CampsAndRetreatsPeriod.objects.get(camps_and_retreats_period_id=uid)
        if period.camps_and_retreats.sport_association != request.user.sport_association:
            raise PermissionDenied("You are not allowed deleting this.")

        period.delete()
        return Response({"message": "Deleted successfully"}, status=status.HTTP_200_OK)
    except PermissionDenied:
        raise
    except CampsAndRetreatsPeriod.DoesNotExist:
        return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def camps_and_retreats_periods_info(request, uid):
    """
    This method is used to get a list of all camps and retreats
    """

    if not request.user.is_sport_association(raise_exception=False):
        return Response({"error": "Only sport associations can view camps or retreats"}, status=status.HTTP_403_FORBIDDEN)

    is_valid_uuid(uid)

    try:
        period = CampsAndRetreatsPeriod.objects.get(camps_and_retreats_period_id=uid)
        if period.camps_and_retreats.sport_association != request.user.sport_association:
            raise PermissionDenied("You are not allowed to view this")

        serializer = CampsAndRetreatsPeriodInfoSerializer(period)
        return Response({"data": serializer.data}, status=status.HTTP_200_OK)
    except PermissionDenied:
        raise
    except CampsAndRetreatsPeriod.DoesNotExist:
        return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def camps_and_retreats_periods_services_add(request):
    if not request.user.is_sport_association(raise_exception=False):
        return Response({"error": "You are not a sport association."}, status=status.HTTP_403_FORBIDDEN)

    try:
        serializer = CampsAndRetreatsPeriodsServiceAddSerializer(data=request.data)

        if serializer.is_valid(raise_exception=True):
            service = serializer.save()
            service.save()

        return Response({
            "message": "Added successfully",
            "service": serializer.data
        }, status=status.HTTP_201_CREATED)
    except ValidationError as e:
        logger.error(f"Validation error adding: {str(e)}")
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        logger.error(f"Error adding: {str(e)}")
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def camps_and_retreats_periods_services_update(request, uid):

    is_valid_uuid(uid)
    if not request.user.is_sport_association(raise_exception=False):
        return Response({"error": "You are not a sport association."}, status=status.HTTP_403_FORBIDDEN)

    try:
        service = CampsAndRetreatsPeriodsService.objects.get(camps_and_retreats_period_service_id=uid)
        if service.camps_and_retreats_period.camps_and_retreats.sport_association != request.user.sport_association:
            raise PermissionDenied("You are not allowed editing this.")

        serializer = CampsAndRetreatsPeriodsServiceAddSerializer(service, data=request.data, partial=True)

        if serializer.is_valid(raise_exception=True):
            serializer.save()

        # check for current created payments that are not paid yet and change the meta_payment_categories if they are changed
        for period in CampsAndRetreatsSubscriptionPeriod.objects.filter(
                camps_and_retreats_period=service.camps_and_retreats_period,
                payment__paid=False):
            for meta_payment_category in period.payment.meta_payment_categories:
                if meta_payment_category['camps_and_retreats_period_service_id'] == str(service.camps_and_retreats_period_service_id):
                    meta_payment_category['amount'] = float(str(serializer.data['fee']).replace(',', '.'))
                    meta_payment_category['title'] = serializer.data['title']
                    meta_payment_category['payment_category_id'] = str(serializer.data['payment_category'])
                    period.payment.save()

            # update amount and notes
            # create the notes for the payment
            notes = f"Iscrizione al periodo {period.camps_and_retreats_period.title} del campo {period.camps_and_retreats_subscription.camps_and_retreats.title}"

            notes += f"\n{period.camps_and_retreats_subscription.camps_and_retreats.description}"

            # add the payment category for the period in the notes
            amount = 0
            notes += "\n"
            for meta_payment_category in period.payment.meta_payment_categories:
                notes += f"\n{meta_payment_category['title']} - {meta_payment_category['amount']}€"
                amount += meta_payment_category['amount']

            amount += float(str(period.camps_and_retreats_period.fee).replace(',', '.'))
            period.payment.amount = amount
            period.payment.notes = notes
            period.payment.save()


        return Response({
            "message": "Updated successfully",
            "period": serializer.data
        }, status=status.HTTP_200_OK)
    except PermissionDenied:
        raise
    except CampsAndRetreatsPeriodsService.DoesNotExist:
        return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
    except ValidationError as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def camps_and_retreats_periods_services_delete(request, uid):

    is_valid_uuid(uid)
    if not request.user.is_sport_association(raise_exception=False):
        return Response({"error": "You are not a sport association."}, status=status.HTTP_403_FORBIDDEN)

    try:
        service = CampsAndRetreatsPeriodsService.objects.get(camps_and_retreats_period_service_id=uid)
        if service.camps_and_retreats_period.camps_and_retreats.sport_association != request.user.sport_association:
            raise PermissionDenied("You are not allowed deleting this.")

        service.delete()
        return Response({"message": "Deleted successfully"}, status=status.HTTP_200_OK)
    except PermissionDenied:
        raise
    except CampsAndRetreatsPeriodsService.DoesNotExist:
        return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def camps_and_retreats_subscriptions_list(request, uid):
    if not request.user.is_sport_association(raise_exception=False):
        return Response({"error": "You are not a sport association"}, status=status.HTTP_403_FORBIDDEN)

    is_valid_uuid(uid)

    subscriptions = CampsAndRetreatsSubscription.objects.filter(
        camps_and_retreats__sport_association=request.user.sport_association,
        camps_and_retreats__camps_and_retreats_id=uid
    ).order_by('-created_at')
    serializer = CampsAndRetreatsSubscriptionListInfoSerializer(subscriptions, many=True)

    return Response({"data": serializer.data}, status=status.HTTP_200_OK)


def _validate_camp_enrollment_scope(request, camp, registration, periods):
    """Validate the complete relationship before creating an enrollment or quota."""
    if registration is None:
        raise ValidationError({"subscription": "An existing registration is required."})
    if registration.sport_association_id != camp.sport_association_id or registration.archived or registration.deleted:
        raise PermissionDenied("The registration does not belong to this camp association.")
    if request.user.is_sport_association(raise_exception=False):
        if camp.sport_association.user_id != request.user.pk:
            raise PermissionDenied("Camp not available to this association.")
    elif request.user.role == User.ATHLETE:
        directly_owned = registration.user_id == request.user.pk or registration.associate.user_id == request.user.pk
        family_id = registration.associate.family_id
        # A missing family is never evidence of a relationship between strangers.
        family_owned = bool(family_id) and Subscription.objects.filter(
            user=request.user, sport_association_id=camp.sport_association_id,
            associate__family_id=family_id, archived=False, deleted=False,
        ).exists()
        if not directly_owned and not family_owned:
            raise PermissionDenied("Registration not available to this account.")
    else:
        raise PermissionDenied("Account cannot enroll a participant.")

    if not isinstance(periods, list):
        raise ValidationError({"periods": "A list of periods is required."})
    uuid_field = serializers.UUIDField()
    seen_periods = set()
    for period in periods:
        if not isinstance(period, dict):
            raise ValidationError({"periods": "Each selected period must be an object."})
        period_id = uuid_field.run_validation(period.get('camps_and_retreats_period'))
        if period_id in seen_periods:
            raise ValidationError({"periods": "A period can be selected only once."})
        seen_periods.add(period_id)
        period['camps_and_retreats_period'] = str(period_id)
        if not CampsAndRetreatsPeriod.objects.filter(pk=period_id, camps_and_retreats=camp).exists():
            raise PermissionDenied("Period does not belong to the selected camp.")
        services = period.get('services', [])
        period['services'] = services
        if not isinstance(services, list):
            raise ValidationError({"services": "A list of services is required."})
        seen_services = set()
        for service in services:
            if not isinstance(service, dict):
                raise ValidationError({"services": "Each selected service must be an object."})
            service_id = uuid_field.run_validation(service.get('camps_and_retreats_period_service_id'))
            if service_id in seen_services:
                raise ValidationError({"services": "A service can be selected only once."})
            seen_services.add(service_id)
            service['camps_and_retreats_period_service_id'] = str(service_id)
            if not CampsAndRetreatsPeriodsService.objects.filter(pk=service_id, camps_and_retreats_period_id=period_id).exists():
                raise PermissionDenied("Service does not belong to the selected period.")


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def camps_and_retreats_subscriptions_add(request, uid):
    try:
        is_valid_uuid(uid)
        payload = request.data.copy()
        payload['camps_and_retreats'] = uid
        serializer = CampsAndRetreatsSubscriptionInfoSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        camp = serializer.validated_data['camps_and_retreats']
        registration = serializer.validated_data['subscription']
        periods = payload.get('periods', [])
        _validate_camp_enrollment_scope(request, camp, registration, periods)
        # Roll back the enrollment and quota together if payment generation fails.
        with transaction.atomic():
            subscription = serializer.save()
            add_new_periods(periods, subscription)
            generate_payment_for_periods(subscription)
        return Response({"message": "Added successfully", "subscription": serializer.data}, status=status.HTTP_201_CREATED)
    except PermissionDenied:
        raise
    except ValidationError as e:
        logger.error(f"Validation error adding: {str(e)}")
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        logger.error(f"Error adding: {str(e)}")
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def camps_and_retreats_subscriptions_update(request, uid):

    is_valid_uuid(uid)
    if not request.user.is_sport_association(raise_exception=False):
        return Response({"error": "You are not a sport association."}, status=status.HTTP_403_FORBIDDEN)

    try:
        subscription = CampsAndRetreatsSubscription.objects.get(camps_and_retreats_subscription_id=uid)
        if subscription.camps_and_retreats.sport_association != request.user.sport_association:
            raise PermissionDenied("You are not allowed deleting this.")
        periods = request.data.get('periods', [])

        # remove all the periods that are not in the new list if they are not paid
        #remove_all_periods_not_in_list(periods, subscription)

        # patch existing periods and payments if they are not paid and changed
        #patch_existing_periods_and_delete_periods(periods, subscription)

        # delete all the unpaid periods
        delete_unpaid_periods(subscription)

        # add new ones
        add_new_periods(periods, subscription)

        # generate payment for the periods
        generate_payment_for_periods(subscription)

        return Response({
            "message": "Updated successfully"
        }, status=status.HTTP_200_OK)
    except PermissionDenied:
        raise
    except CampsAndRetreatsSubscription.DoesNotExist:
        return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
    except CampsAndRetreatsPeriodsService.DoesNotExist:
        return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
    except ValidationError as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def camps_and_retreats_subscriptions_delete(request, uid):

    is_valid_uuid(uid)
    if not request.user.is_sport_association(raise_exception=False):
        return Response({"error": "You are not a sport association."}, status=status.HTTP_403_FORBIDDEN)

    try:
        sub = CampsAndRetreatsSubscription.objects.get(camps_and_retreats_subscription_id=uid)
        if sub.camps_and_retreats.sport_association != request.user.sport_association:
            raise PermissionDenied("You are not allowed deleting this.")

        # delete all the payments and periods unpaid
        delete_unpaid_periods(sub)

        sub.delete()
        return Response({"message": "Deleted successfully"}, status=status.HTTP_200_OK)
    except PermissionDenied:
        raise
    except CampsAndRetreatsSubscription.DoesNotExist:
        return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
