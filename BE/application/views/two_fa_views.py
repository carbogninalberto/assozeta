"""
@ copyright: Bakney SRL
"""
from django.db import transaction
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes

from core import settings
from core.middleware import IsAuthenticated

from application.models.user_models import User
import segno
import pyotp

import logging

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def two_fa_info(request):
    """
    This endpoint returns info about two factor authentication for the requesting user
    :param request:
    :return:
    """

    logger.info("two_fa -> init -> user: {}".format(request.user.user_id))

    data = {
        "enabled": request.user.two_fa,
    }
    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def two_fa_setup(request):
    """
    API endpoint to setup a new two fa secret
    :param request: data: { OTP, username, password }
    :return:
    """


    logger.info("two_fa -> setup new secret -> user: {}".format(request.user.user_id))

    otp_secret = pyotp.random_base32()
    otp_uri = pyotp.totp.TOTP(otp_secret).provisioning_uri(name=f"{settings.EMAIL_HOST_SUPPORT}", issuer_name=f"{settings.WHITELABEL_NAME}")
    qrcode = segno.make(otp_uri)
    qrcode_uri = qrcode.svg_data_uri()

    data = {
        "qrcode_uri": qrcode_uri,
        "otp_secret": otp_secret
    }
    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def two_fa_update(request):
    """
    API endpoint to update the two fa secret
    :param request: data: { OTP, username, password }
    :return:
    """


    logger.info("two_fa -> update secret -> user: {}".format(request.user.user_id))
    enable = request.data['enable'] or False
    secret = request.data['secret'] or ''
    otp = request.data['otp'] or ''
    user = request.user
    user.__class__ = User

    otp_valid = False
    if secret != '' and otp != '' and len(otp) == 6:
        totp = pyotp.TOTP(secret)
        otp_valid = totp.verify(otp)

    logger.debug("2FA update decision", extra={'user_id': str(user.user_id), 'enable': enable, 'otp_valid': otp_valid})
    if enable and not otp_valid:
        logger.warning("2FA enable failed - OTP validation failed", extra={'user_id': str(user.user_id)})
        return Response({'data': {'msg': 'Otp validation failed.'}}, status=status.HTTP_400_BAD_REQUEST)

    logger.info("Enabling 2FA" if enable else "Disabling 2FA", extra={'user_id': str(user.user_id)})
    # A failed user/audit/index write must not leave a successful-looking audit.
    # Lock and reload only the mutation target: authentication/scoping has already
    # selected it. Restricted update fields preserve concurrent password/profile
    # changes instead of writing the earlier request.user snapshot back wholesale.
    with transaction.atomic():
        current = User.original_objects.select_for_update().get(pk=user.pk)
        current.two_fa = bool(enable)
        fields = ['two_fa']
        if enable:
            current.two_fa_secret = secret
            fields.append('two_fa_secret')
        current.save(update_fields=fields)

    message = 'Two factor authentication enabled.' if enable else 'Disabled two factor authentication'
    return Response({'data': {'msg': message}}, status=status.HTTP_200_OK)
