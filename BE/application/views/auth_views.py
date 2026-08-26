"""
@ copyright: Bakney SRL
"""
import datetime
import json
import logging
import redis
import secrets
import re

from disposable_email_checker.validators import validate_disposable_email
from django.core.exceptions import ValidationError
from django.utils import timezone

import pyotp
from django.db import IntegrityError
from django.template.loader import render_to_string

from application.models.subscriptions_models import SubscriptionTransfer
from application.serializers.auth_serializers import UserSerializer, SportAssociationSerializer, \
    UserSerializerSignup
from application.models.user_models import User, UserPartial, CollaborationInvites
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.conf import settings
from django_ratelimit.decorators import ratelimit

from application.utils.api_utils import migrate_bcrypt_to_django
from application.services.social_auth_service import SocialAuthService
from application.services.jwt_token_service import JWTTokenService
from core.middleware import IsAuthenticated
from application.tasks import send_email_text
from core.settings import WHITELABEL_NAME
from core.tasks import send_mail_async

logger = logging.getLogger(__name__)


def _login_ip_key(group, request):
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
    return forwarded_for.split(',', 1)[0].strip() or request.META.get('REMOTE_ADDR', 'unknown')


def _login_account_key(group, request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 'invalid-request'
    return str(payload.get('username', '')).strip().casefold() or 'missing-username'


@ratelimit(group='login-ip', key=_login_ip_key, rate='20/m', method='POST', block=True)
@ratelimit(group='login-account', key=_login_account_key, rate='5/m', method='POST', block=True)
@api_view(['POST'])
@permission_classes([AllowAny])
def oauth2_login(request):
    """
    API endpoint to login a user.
    Supports password auth and social auth (Google/Apple).

    Request body:
        Password login: {username, password} or {username, password, otp}
        Social login: {username, token, backend} or {username, token, backend, otp}

    Returns:
        {access_token, refresh_token, expires_in, token_type, role, user_data, ...}
    """
    logger.info("User login attempt", extra={'username': request.data.get('username', 'unknown')})

    data = request.data

    # Validate request structure
    is_password_login = 'username' in data and 'password' in data and 'token' not in data
    is_social_login = 'username' in data and 'token' in data and 'backend' in data
    has_otp = 'otp' in data

    if not (is_password_login or is_social_login):
        return Response({"msg": "Invalid request."}, status=status.HTTP_400_BAD_REQUEST)

    # Email is not unique in legacy data, so defer account selection until credentials are checked.
    login_identifier = str(data['username']).strip()
    if re.match(r"[^@]+@[^@]+\.[^@]+", login_identifier):
        candidates = list(User.objects.filter(email__iexact=login_identifier).order_by('user_id'))
    else:
        candidates = list(User.objects.filter(username__iexact=login_identifier).order_by('user_id'))

    if not candidates:
        logger.warning("Login failed - user not found", extra={'username': data.get('username', 'unknown')})
        return Response({'error': 'invalid credential (not found)'}, status=status.HTTP_401_UNAUTHORIZED)

    # Verify credentials
    if is_social_login:
        preferred_candidates = [
            candidate for candidate in candidates
            if candidate.username.casefold() == login_identifier.casefold()
        ]
        if len(preferred_candidates) == 1:
            user = preferred_candidates[0]
        elif len(candidates) == 1:
            user = candidates[0]
        else:
            logger.warning("Login failed - ambiguous account", extra={'username': login_identifier})
            return Response({'error': 'invalid credentials'}, status=status.HTTP_401_UNAUTHORIZED)

        # Verify social token
        backend = data['backend']
        if backend in ['google-oauth2', 'google-identity', 'google']:
            success, user_info = SocialAuthService.verify_google_token(data['token'])
        elif backend in ['apple-id', 'apple']:
            success, user_info = SocialAuthService.verify_apple_token(data['token'])
        else:
            return Response(
                {'error': f'Unsupported backend: {backend}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not success:
            logger.warning("Social auth verification failed", extra={'backend': backend})
            return Response({'error': 'Invalid social token'}, status=status.HTTP_401_UNAUTHORIZED)

        # Verify email matches the user
        if user_info['email'].lower() != user.email.lower():
            logger.warning("Social auth email mismatch",
                          extra={'expected': user.email, 'got': user_info['email']})
            return Response({'error': 'Email mismatch'}, status=status.HTTP_401_UNAUTHORIZED)
    else:
        matching_candidates = []
        migrated_passwords = {}
        for candidate in candidates:
            if candidate.check_password(data['password']):
                matching_candidates.append(candidate)
                continue

            new_hash = migrate_bcrypt_to_django(data['password'], candidate.password)
            if new_hash:
                matching_candidates.append(candidate)
                migrated_passwords[candidate.user_id] = new_hash

        preferred_candidates = [
            candidate for candidate in matching_candidates
            if candidate.username.casefold() == login_identifier.casefold()
        ]
        if len(preferred_candidates) == 1:
            user = preferred_candidates[0]
        elif len(matching_candidates) == 1:
            user = matching_candidates[0]
        else:
            logger.warning("Login failed - invalid or ambiguous password", extra={'username': login_identifier})
            return Response({'error': 'invalid credentials'}, status=status.HTTP_401_UNAUTHORIZED)

        if user.user_id in migrated_passwords:
            user.password = migrated_passwords[user.user_id]
            user.save(update_fields=['password'])

    # Preserve legacy username normalization, but only after successful authentication.
    normalized_username = user.username.upper()
    if user.username != normalized_username:
        user.username = normalized_username
        user.save(update_fields=['username'])

    # Check 2FA if enabled
    logger.debug("Checking 2FA requirement", extra={'user_id': str(user.user_id), 'two_fa_enabled': user.two_fa})
    if user.two_fa:
        if has_otp and len(data['otp']) == 6:
            totp = pyotp.TOTP(user.two_fa_secret)
            if not totp.verify(data['otp']):
                return Response({"msg": "OTP code not valid."}, status=status.HTTP_401_UNAUTHORIZED)
        else:
            return Response({"msg": "OTP code required."}, status=status.HTTP_401_UNAUTHORIZED)

    # Generate JWT tokens
    logger.info("Generating JWT tokens", extra={'user_id': str(user.user_id), 'backend': data.get('backend', 'password')})
    tokens = JWTTokenService.generate_tokens_for_user(user)
    content = JWTTokenService.build_login_response(user, tokens)

    logger.info("User logged in successfully", extra={'user_id': str(user.user_id), 'role': user.role})

    # Update last login and clear deletion flag
    user.last_login = timezone.now()
    if user.delete_on is not None and user.delete_on >= timezone.now().date():
        user.delete_on = None
    user.save()

    # Create response
    drf_response = Response(content, status=status.HTTP_200_OK)

    # Set BKN_AUTH cookie with SimpleJWT access token (for WebSocket auth)
    drf_response.set_cookie(
        'BKN_AUTH', tokens['access_token'],
        httponly=True, secure=True, samesite='Strict',
        max_age=int(settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds()),
        path='/'
    )

    return drf_response


@api_view(['POST'])
@permission_classes([AllowAny])
def partial_signup(request):


    # getting body
    data = request.data

    if 'email' in data.keys():
        # check if email is already added
        if UserPartial.objects.filter(email__iexact=data['email']).first() is None:
            user_partial_account = UserPartial.objects.create(email=data['email'])
            user_partial_account.save()

            send_email_text.delay(
                recipient_list=['info@bakney.com'],
                subject=f"Nuovo utente parziale",
                message=f"Utente parziale con email {data['email']} registrato."
            )
    return Response({"msg": "Email added."}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def oauth2_delete_account(request):

    user = request.user

    # check if user.collaborator is True,
    # it could happen that the user doesn't have the collaborator field
    try:
        if request.collaborator is True:
            user = request.original_user
    except AttributeError:
        pass

    user.delete_on = timezone.now().date() + datetime.timedelta(days=30)
    user.save()

    # send confirmation email
    send_email_text.delay(
        recipient_list=[user.email],
        subject=f"{WHITELABEL_NAME} | Eliminazione account",
        message=f"La tua richiesta di eliminazione dell'account è stata presa in carico correttamente. "
                f"L'account verrà eliminato il giorno {user.delete_on.strftime('%d/%m/%Y')}."
    )

    return Response({"msg": "Account will be deleted in 30 days."}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([AllowAny])
def oauth2_signup(request):
    """
    API endpoint to sign up a user by oauth2 cap
    :param request: data: {first_name, last_name, username, email, password} or {token, user}
    :return: token, refresh token
    """
    logger.info("User signup attempt", extra={'email': request.data.get('email', 'unknown')})

    # getting body
    data = request.data
    is_external_user = False
    user = None
    sport_association = None

    if 'sport_association' not in data:
        return Response({
            "msg": "Invalid request.",
            "details": {"sport_association": ["This field is required."]},
        }, status=status.HTTP_400_BAD_REQUEST)

    if not isinstance(data['sport_association'], bool):
        return Response({
            "msg": "Invalid request.",
            "details": {"sport_association": ["Must be a boolean."]},
        }, status=status.HTTP_400_BAD_REQUEST)

    if data['sport_association']:
        logger.warning(
            "Association signup blocked",
            extra={'email': data.get('email', 'unknown')},
        )
        return Response({
            "msg": "Invalid request.",
            "details": {
                "sport_association": [
                    "Association accounts can only be created during instance setup."
                ]
            },
        }, status=status.HTTP_403_FORBIDDEN)

    # check if there is a sub transfer token in data
    subscription_transfer = False
    subscription_transfer_token = None
    subscription_transfer_obj = None
    role = User.ATHLETE

    # validate email
    try:
        if settings.DEBUG is False:
            validate_disposable_email((data['email']).lower())
        if data['email'].split('@')[1].lower() in ['skrank.com']:
            raise ValidationError
    except ValidationError:
        logger.warning("Signup blocked - disposable email detected", extra={'email': data.get('email', 'unknown')})
        return Response({"msg": "You are nasty 🖕( •_• )🖕"}, status=status.HTTP_400_BAD_REQUEST)

    if 'collaboratorToken' in data.keys():
        role = User.COLLABORATOR

    # check in data
    if 'subscription_transfer' in data.keys() and \
        'subscription_transfer_token' in data.keys():
        # try to get the transfer token, otherwise skip and procede
        subscription_transfer = data['subscription_transfer'] or False
        subscription_transfer_token = data['subscription_transfer_token'] or None
        subscription_transfer_obj = SubscriptionTransfer.objects.filter(
            token=subscription_transfer_token
        ).filter()
        if subscription_transfer_obj is None:
            subscription_transfer = False

    if 'token' in data.keys() and 'username' in data.keys() and 'backend' in data.keys():
        is_external_user = True
        # Verify social token
        backend = data['backend']
        if backend in ['google-oauth2', 'google-identity', 'google']:
            success, user_info = SocialAuthService.verify_google_token(data['token'])
        elif backend in ['apple-id', 'apple']:
            success, user_info = SocialAuthService.verify_apple_token(data['token'])
        else:
            return Response({'error': f'Unsupported backend: {backend}'}, status=status.HTTP_400_BAD_REQUEST)

        if not success:
            return Response({'error': 'Invalid social token'}, status=status.HTTP_401_UNAUTHORIZED)

        # Check if user already exists with this email
        existing_user = User.objects.filter(email__iexact=user_info['email']).first()
        if existing_user:
            # User already exists - update role and return tokens
            try:
                user_serializer = UserSerializer(existing_user,
                                                 data={"role": role},
                                                 partial=True)
                if user_serializer.is_valid(raise_exception=True):
                    user = user_serializer.save()
            except IntegrityError:
                pass
            except ValueError as e:
                return Response({"msg": e}, status=status.HTTP_400_BAD_REQUEST)
        else:
            # Create new user from social auth info
            user = User.objects.create_user(
                first_name=user_info.get('first_name') or data.get('first_name', ''),
                last_name=user_info.get('last_name') or data.get('last_name', ''),
                username=data['username'].upper(),
                email=user_info['email'],
                password=None,  # No password for social users
                role=role,
            )

    serialized = UserSerializerSignup(data=data)
    validated = serialized.is_valid()

    # checking type of login
    if validated or is_external_user is True:
        # there is a third party token (facebook)
        try:
            user_partial_account = UserPartial.objects.filter(email__iexact=data['email']).first()
            if user_partial_account is not None:
                user_partial_account.delete()
            if is_external_user is False and serialized.is_valid(raise_exception=True):
                connected_user = None
                collaborator_role = User.FULL
                collaborator_permissions = None
                if role == User.COLLABORATOR:
                    collaboration_invite = CollaborationInvites.objects.filter(token=data['collaboratorToken']).first()
                    if collaboration_invite is None:
                        return Response({"msg": "Collaboration invite not found."}, status=status.HTTP_400_BAD_REQUEST)
                    connected_user = collaboration_invite.user
                    # set the collaboration invite parameters
                    collaborator_role = collaboration_invite.collaborator_role
                    collaborator_permissions = collaboration_invite.collaborator_permissions

                    # delete invite
                    collaboration_invite.delete()
                    if collaboration_invite.expiration_date < timezone.now():
                        return Response({"msg": "Collaboration invite expired."}, status=status.HTTP_400_BAD_REQUEST)
                user = User.objects.create_user(
                    first_name=serialized.validated_data['first_name'],
                    last_name=serialized.validated_data['last_name'],
                    username=str(serialized.validated_data['username']).upper(),
                    email=serialized.validated_data['email'],
                    password=serialized.initial_data['password'],
                    role=role,
                    connected_user=connected_user,
                    collaborator_role=collaborator_role,
                    collaborator_permissions=collaborator_permissions,
                )

            # check if subscription_transfer
            if subscription_transfer is True:
                subscription_transfer_obj.recipient = user
                subscription_transfer_obj.save()

        except ValueError as e:
            logger.error("Signup failed", extra={'email': data.get('email'), 'error': str(e)}, exc_info=True)
            if user is not None:
                user.delete()
            return Response({"msg": e}, status=status.HTTP_400_BAD_REQUEST)

        # Generate JWT tokens for the new user
        tokens = JWTTokenService.generate_tokens_for_user(user)
        content = JWTTokenService.build_login_response(user, tokens)

        # send email
        logger.info("Sending welcome email", extra={'user_id': str(user.user_id), 'email': user.email})
        AuthUtils.send_welcome_email(user)
        logger.info("User signup completed successfully", extra={'user_id': str(user.user_id), 'role': user.role})

        # Override with signup-specific data
        if user.role == User.COLLABORATOR:
            sport_association = user.connected_user.sport_association
            content['user_data']['sport_association'] = SportAssociationSerializer(sport_association).data
            if sport_association.regulation is None or sport_association.regulation == '' or \
                    sport_association.demand is None or sport_association.demand == '':
                content['user_data']['sport_association']['empty_sections'] = True
            content['user_data']['requires_welcome'] = False

        return Response(content, status=status.HTTP_200_OK)
    else:
        details = serialized.errors
        logging.exception(details)
        return Response({"msg": "Invalid request.", "details": details}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([AllowAny])
def oauth2_reset_password(request):
    """
    API endpoint to reset password of a user.
    :param request:
    :return:
    """
    logger.info("Password reset request", extra={'email': request.data.get('email', 'token_validation')})

    # getting body
    data = request.data
    # serializing data
    if 'email' in data.keys() and len(data.keys()) == 1:
        # Legacy data may contain multiple accounts with the same email. Issue a
        # distinct link for each one rather than resetting an arbitrary first row.
        email = str(data['email']).strip()
        users = list(User.objects.filter(email__iexact=email).order_by('user_id'))
        if not users:
            logger.warning("Password reset requested for non-existent email", extra={'email': email})
            return Response({'msg': 'Email sent to the user if the email exists.'}, status=status.HTTP_200_OK)

        r = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            ssl=settings.REDIS_SSLAUTH,
            password=settings.REDIS_PASSWORD,
            username=settings.REDIS_USERNAME,
            ssl_certfile=settings.REDIS_SSLCERT,
            db=2
        )
        try:
            for user in users:
                logger.info("Generating password reset token", extra={'user_id': str(user.user_id)})
                token = str(secrets.token_hex(16))
                r.set(name=token, value=str(user.user_id), ex=3600)
                logger.info("Sending password reset email", extra={'user_id': str(user.user_id), 'email': user.email})
                AuthUtils.send_reset_email(user, token)
        finally:
            r.close()

        return Response({'msg': 'Email sent to the user if the email exists.'}, status=status.HTTP_200_OK)

    elif 'token' in data.keys() and 'password' in data.keys() and len(data.keys()) == 2:
        # check redis
        r = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            ssl=settings.REDIS_SSLAUTH,
            password=settings.REDIS_PASSWORD,
            username=settings.REDIS_USERNAME,
            ssl_certfile=settings.REDIS_SSLCERT,
            db=2
        )
        user_id = r.get(data['token'])

        password_pattern = re.compile(r"^(?=.*[A-Z])(?=.*[!@#$&\.\-\_*])(?=.*[0-9]).{10,}$")
        password_validated = re.search(password_pattern, data['password'])

        # user_id is not none means that the token is correct and not expired
        if user_id is not None and password_validated:
            user = User.objects.get(user_id=user_id.decode())
            logger.info("Resetting password", extra={'user_id': str(user.user_id)})
            user.set_password(data['password'])
            user.save()
            # delete temporary token from Redis
            r.delete(data['token'])
            r.close()
            # send confirmation email
            logger.info("Sending password reset confirmation email", extra={'user_id': str(user.user_id)})
            AuthUtils.send_confirm_reset_email(user)
            logger.info("Password reset completed successfully", extra={'user_id': str(user.user_id)})
            return Response({'msg': 'Password reset.'}, status=status.HTTP_200_OK)
        else:
            r.close()
            return Response({"msg": "Invalid request."}, status=status.HTTP_400_BAD_REQUEST)

    else:
        return Response({"msg": "Invalid request."}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([AllowAny])
def oauth2_refresh_token(request):
    """
    API endpoint to refresh access token using JWT.

    Request body:
        {refresh_token: string}

    Returns:
        {access_token, refresh_token, expires_in, token_type}
    """
    data = request.data

    if 'refresh_token' not in data:
        return Response({"msg": "refresh_token required"}, status=status.HTTP_400_BAD_REQUEST)

    result = JWTTokenService.refresh_access_token(data['refresh_token'])

    if result is None:
        return Response(
            {"msg": "Invalid or expired refresh token"},
            status=status.HTTP_401_UNAUTHORIZED
        )

    response = Response(result, status=status.HTTP_200_OK)
    response.set_cookie(
        'BKN_AUTH', result['access_token'],
        httponly=True, secure=True, samesite='Strict',
        max_age=int(settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds()),
        path='/'
    )
    return response


@api_view(['GET'])
@permission_classes([AllowAny])
def oauth2_check_email(request):
    """
    API endpoint to check if an email is already taken
    :param request: data: {username, password} or {token}
    :return: token, refresh token
    """


    data = request.query_params
    if len(data['email']) > 255:
        return Response({"valid": False, "exception": "invalid payload."}, status=status.HTTP_400_BAD_REQUEST)

    get_user = False
    if 'get_user' in data.keys():
        get_user = True

    user = User.objects.filter(email__iexact=data['email']).first()
    if get_user and user is None:
        # we should refactor this, maybe using another endpoint specifically for getting user data
        # NOTE: this is a temporary solution, used for getting user data in the frontend when transfering subscriptions
        user = User.objects.filter(username__iexact=data['email']).first()
    data = {"valid": False, "exception": "email already taken."}
    if get_user and user is not None:
        data['user'] = {
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "user_id": user.user_id,
        }
    if user is not None:
        return Response(data, status=status.HTTP_409_CONFLICT)
    else:
        return Response({"valid": True, "data": {"msg": "email is available."}}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([AllowAny])
def oauth2_check_username(request):
    """
    API endpoint to check if a username is already taken
    :param request:
    :return:
    """


    data = request.query_params
    if len(data['username']) > 150:
        return Response({"valid": False, "exception": "invalid payload."}, status=status.HTTP_400_BAD_REQUEST)

    user = User.objects.filter(username__iexact=data['username'])
    if user.exists():
        return Response({"valid": False, "exception": "username already taken."}, status=status.HTTP_409_CONFLICT)
    else:
        return Response({"valid": True, "data": {"msg": "username is available."}}, status=status.HTTP_200_OK)


class AuthUtils:
    """Utility class for authentication-related operations."""

    @staticmethod
    def send_welcome_email(user):
        data = {
            'first_name': user.first_name,
            'last_name': user.last_name,
            'app_host': settings.APP_URL,
            'settings': {
                'WHITELABEL_NAME': settings.WHITELABEL_NAME,
                'IS_WHITELABEL': settings.IS_WHITELABEL
            }
        }
        recipient_list = [f"{user.email}".lower()]
        message = render_to_string('email/account/email_welcome_message.html', data)
        subject = f"{settings.WHITELABEL_NAME} | Benvenuto"

        send_mail_async.apply_async(
            kwargs={
                "subject": subject,
                "message": message,
                "from_email": settings.DEFAULT_FROM_EMAIL,
                "recipient_list": recipient_list,
                "html_message": message,
                "fail_silently": False
            }
        )

    @staticmethod
    def send_password_welcome_email(user, password, sport_association):
        data = {
            'first_name': user.first_name,
            'last_name': user.last_name,
            'username': user.username,
            'email': user.email,
            'password': password,
            'sport_association': sport_association,
            'app_host': settings.APP_URL,
            'settings': {
                'WHITELABEL_NAME': settings.WHITELABEL_NAME,
                'IS_WHITELABEL': settings.IS_WHITELABEL
            }
        }
        recipient_list = [user.email]
        message = render_to_string('email/account/email_welcome_password_message.html', data)
        subject = f"{settings.WHITELABEL_NAME} | Benvenuto"

        send_mail_async.apply_async(
            kwargs={
                "subject": subject,
                "message": message,
                "from_email": settings.DEFAULT_FROM_EMAIL,
                "recipient_list": recipient_list,
                "html_message": message,
                "fail_silently": False
            }
        )

    @staticmethod
    def send_reset_email(user, token):
        data = {
            'first_name': user.first_name,
            'last_name': user.last_name,
            'username': user.username,
            'token': token,
            'app_host': settings.APP_URL,
            'settings': {
                'WHITELABEL_NAME': settings.WHITELABEL_NAME,
                'IS_WHITELABEL': settings.IS_WHITELABEL
            }
        }
        recipient_list = [user.email]
        message = render_to_string('email/account/email_reset_message.html', data)
        subject = f"{settings.WHITELABEL_NAME} | Reset Password"

        send_mail_async.apply_async(
            kwargs={
                "subject": subject,
                "message": message,
                "from_email": settings.DEFAULT_FROM_EMAIL,
                "recipient_list": recipient_list,
                "html_message": message,
                "fail_silently": False
            }
        )

    @staticmethod
    def send_confirm_reset_email(user):
        data = {
            'first_name': user.first_name,
            'last_name': user.last_name,
            'app_host': settings.APP_URL,
            'settings': {
                'WHITELABEL_NAME': settings.WHITELABEL_NAME,
                'IS_WHITELABEL': settings.IS_WHITELABEL
            }
        }
        recipient_list = [user.email]
        message = render_to_string('email/account/email_confirm_reset_message.html', data)
        subject = f"{settings.WHITELABEL_NAME} | Conferma Nuova Password"

        send_mail_async.apply_async(
            kwargs={
                "subject": subject,
                "message": message,
                "from_email": settings.DEFAULT_FROM_EMAIL,
                "recipient_list": recipient_list,
                "html_message": message,
                "fail_silently": False
            }
        )
