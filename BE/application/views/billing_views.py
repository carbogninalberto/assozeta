"""
@ copyright: Bakney SRL
"""

from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from core.middleware import IsAuthenticated

from application.models.user_models import User

import logging

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def billing_active_plan(request):
    # Retain an explicit tombstone for old clients without reading or creating
    # legacy entitlements. Association access is governed by roles/permissions.
    if request.user.role != User.ASSOCIATION:
        return Response({'msg': 'Info not available.'}, status=status.HTTP_403_FORBIDDEN)
    return Response({'msg': 'Software subscriptions are no longer required.'},
                    status=status.HTTP_410_GONE)


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

    logger.info("Platform billing checkout is disabled", extra={'user_id': str(request.user.user_id)})
    return Response(
        {'msg': 'Platform billing checkout is disabled for self-hosted instances.'},
        status=status.HTTP_410_GONE,
    )
