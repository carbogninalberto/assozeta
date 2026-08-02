"""
@ copyright: Bakney SRL
"""
import datetime

import pytz
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from core.middleware import IsAuthenticated

from application.models import BillingSubscription, BillingPlan
from application.models.user_models import User

import logging

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def billing_active_plan(request):
    logger.info("Retrieving active billing plan", extra={'user_id': str(request.user.user_id)})

    # check if sport association
    if request.user.role != User.ASSOCIATION:
        logger.warning("Unauthorized billing access - not association", extra={
            'user_id': str(request.user.user_id),
            'role': request.user.role
        })
        return Response({'msg': 'Info not available.'}, status=status.HTTP_403_FORBIDDEN)

    billing_subscription = BillingSubscription.objects.filter(user=request.user).first()

    if billing_subscription is None:
        logger.debug("No billing subscription found, creating default", extra={'user_id': str(request.user.user_id)})
        base_plan = BillingPlan.objects.filter(name__exact="Piano Pro").first()
        if base_plan is not None:
            billing_subscription = BillingSubscription.objects.create(
                user=request.user,
                auto_renewal=True,
                renewal_type=BillingSubscription.ANNUALLY,
                ends_on=pytz.timezone('Europe/Rome').localize(datetime.datetime.now() +
                                                              datetime.timedelta(days=7),
                                                              is_dst=None),
                billing_plan=base_plan
            )
            billing_subscription.save()
            logger.info("Default billing subscription created", extra={
                'user_id': str(request.user.user_id),
                'plan_name': base_plan.name,
                'renewal_type': BillingSubscription.ANNUALLY
            })
        else:
            logger.warning("Base plan not found for new subscription", extra={'user_id': str(request.user.user_id)})


    logger.info("Billing plan retrieved successfully", extra={
        'user_id': str(request.user.user_id),
        'plan_name': billing_subscription.billing_plan.name if billing_subscription else None,
        'ends_on': str(billing_subscription.ends_on) if billing_subscription else None
    })
    data = {
        "active_plan": {
            "name": billing_subscription.billing_plan.name,
            "billing_type": billing_subscription.billing_plan.billing_type
        },
        "auto_renewal": billing_subscription.auto_renewal,
        "renewal_type": billing_subscription.renewal_type,
        "ends_on": billing_subscription.ends_on,
    }
    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def billing_checkout(request):
    logger.info("Processing billing checkout", extra={'user_id': str(request.user.user_id)})

    # check if sport association
    if request.user.role != User.ASSOCIATION:
        logger.warning("Unauthorized billing checkout - not association", extra={
            'user_id': str(request.user.user_id),
            'role': request.user.role
        })
        return Response({'msg': 'Info not available.'}, status=status.HTTP_403_FORBIDDEN)

    billing_subscription = BillingSubscription.objects.filter(user=request.user).first()
    if billing_subscription is None:
        return Response({'msg': 'No active subscription found.'}, status=status.HTTP_404_NOT_FOUND)

    logger.info("Billing checkout completed", extra={
        'user_id': str(request.user.user_id),
        'plan_name': billing_subscription.billing_plan.name,
        'renewal_type': billing_subscription.renewal_type
    })
    data = {
        "active_plan": {
            "name": billing_subscription.billing_plan.name
        },
        "auto_renewal": billing_subscription.auto_renewal,
        "renewal_type": billing_subscription.renewal_type,
        "ends_on": billing_subscription.ends_on,
    }
    return Response({'data': data}, status=status.HTTP_200_OK)
