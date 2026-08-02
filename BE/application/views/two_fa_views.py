"""
@ copyright: Bakney SRL
"""
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

    # disabling the 2fa
    logger.debug("2FA update decision", extra={'user_id': str(user.user_id), 'enable': enable, 'otp_valid': otp_valid})
    if not enable:
        logger.info("Disabling 2FA", extra={'user_id': str(user.user_id)})
        user.two_fa = False
        user.save()
        return Response({'data': {'msg': 'Disabled two factor authentication'}}, status=status.HTTP_200_OK)
    elif enable and otp_valid:
        logger.info("Enabling 2FA", extra={'user_id': str(user.user_id)})
        user.two_fa = True
        user.two_fa_secret = secret
        user.save()
        return Response({'data': {'msg': 'Two factor authentication enabled.'}}, status=status.HTTP_200_OK)
    elif enable and not otp_valid:
        logger.warning("2FA enable failed - OTP validation failed", extra={'user_id': str(user.user_id)})
        return Response({'data': {'msg': 'Otp validation failed.'}}, status=status.HTTP_400_BAD_REQUEST)

    return Response({'data': {'msg': 'Provided data are not valid'}}, status=status.HTTP_400_BAD_REQUEST)  # pragma: no cover
