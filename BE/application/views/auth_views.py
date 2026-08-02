"""
@ copyright: Bakney SRL
"""
import datetime
import logging
import redis
import secrets
import re

import stripe
from disposable_email_checker.validators import validate_disposable_email
from django.core.exceptions import ValidationError
from django.utils import timezone

import pyotp
import pytz
from django.db import IntegrityError
from django.template.loader import render_to_string

from application.models import BillingSubscription, BillingPlan
from application.models.balance_sheet_models import CustomAccounts
from application.models.subscriptions_models import SubscriptionTransfer
from application.serializers.auth_serializers import UserSerializer, SportAssociationSerializer, \
    UserSerializerSignup
from application.models.user_models import User, SportAssociation, UserPartial, CollaborationInvites, UsersOnboarding
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.conf import settings

from application.utils.api_utils import generate_readable_unique_string, export_customer_to_crm, \
    migrate_bcrypt_to_django
from application.services.social_auth_service import SocialAuthService
from application.services.jwt_token_service import JWTTokenService
from core.middleware import IsAuthenticated
from application.tasks import send_email_text
from core.settings import WHITELABEL_NAME
from core.tasks import send_mail_async

logger = logging.getLogger(__name__)


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

    # Find user by username or email
    try:
        if re.match(r"[^@]+@[^@]+\.[^@]+", data['username']):
            user = User.objects.get(email__iexact=data['username'])
            data['username'] = user.username
        else:
            user = User.objects.get(username__iexact=data['username'])

        # Normalize username to uppercase
        if user.username != data['username'].upper():
            user.username = data['username'].upper()
            user.save()
        data['username'] = data['username'].upper()

    except User.DoesNotExist:
        logger.warning("Login failed - user not found", extra={'username': data.get('username', 'unknown')})
        return Response({'error': 'invalid credential (not found)'}, status=status.HTTP_401_UNAUTHORIZED)

    # Verify credentials
    if is_social_login:
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
        # Password authentication
        if not user.check_password(data['password']):
            # Try bcrypt migration for legacy passwords
            new_hash = migrate_bcrypt_to_django(data['password'], user.password)
            if not new_hash:
                logger.warning("Login failed - invalid password", extra={'user_id': str(user.user_id)})
                return Response({'error': 'invalid credentials'}, status=status.HTTP_401_UNAUTHORIZED)
            # Save migrated password hash
            user.password = new_hash
            user.save(update_fields=['password'])

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
    user.last_login = datetime.datetime.now()
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
    billing_sub = None

    # check if there is a sub transfer token in data
    subscription_transfer = False
    subscription_transfer_token = None
    subscription_transfer_obj = None
    role = User.ASSOCIATION if data['sport_association'] else User.ATHLETE

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
    sport_association_serialized = SportAssociationSerializer(data=data)


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

            if data['sport_association']:
                logger.info("Creating sport association", extra={'user_id': str(user.user_id), 'denomination': sport_association_serialized.initial_data.get('denomination')})
                sport_association = SportAssociation.objects.create(
                    user=user,
                    denomination=sport_association_serialized.initial_data['denomination'],
                    tax_code=sport_association_serialized.initial_data['tax_code']
                )
                sport_association.save()

                # generate the stripe coupon
                logger.info("Creating Stripe coupon", extra={'user_id': str(user.user_id), 'sport_association_id': str(sport_association.sport_association_id)})
                coupon = generate_readable_unique_string()
                # generate the coupon in stripe
                coupon_stripe = stripe.Coupon.create(
                    percent_off=10.0,
                    duration="once",
                    name="{}".format(sport_association.denomination)[:40],
                )
                stripe.PromotionCode.create(
                    coupon=coupon_stripe.id,
                    code=coupon,
                    active=True,
                )
                sport_association.affiliate_code = coupon

                # update the field in sport association
                sport_association.affiliate_code_stripe = coupon_stripe.id
                sport_association.save()

                custom_account = CustomAccounts.objects.create(
                    name='cassa',
                    initial_balance=0,
                    account_type=1,
                    account_code='cassa',
                    editable=False,
                    sport_association=sport_association,
                )
                custom_account.save()

                # adding banca
                custom_account = CustomAccounts.objects.create(
                    name='banca',
                    initial_balance=0,
                    account_type=2,
                    account_code='banca',
                    editable=False,
                    sport_association=sport_association,
                )
                custom_account.save()

                # adding default plan
                base_plan = BillingPlan.objects.filter(name__exact="Piano Pro").first()
                if base_plan is not None:
                    billing_sub, billing_sub_created = BillingSubscription.objects.get_or_create(
                        user=user,
                        auto_renewal=True,
                        renewal_type=BillingSubscription.ANNUALLY,
                        ends_on=pytz.timezone('Europe/Rome').localize(datetime.datetime.now() +
                                                                      datetime.timedelta(days=14),
                                                                      is_dst=None),
                        billing_plan=base_plan
                    )
                    billing_sub.save()

                # create the onboarding object
                try:
                    UsersOnboarding.objects.create(user=user)
                except Exception as e:
                    # create sentry issue
                    logging.exception(e)

                # export_customer_to_crm
                logger.info("Exporting customer to CRM", extra={'sport_association_id': str(sport_association.sport_association_id)})
                export_customer_to_crm(sport_association)
        except ValueError as e:
            logger.error("Signup failed", extra={'email': data.get('email'), 'error': str(e)}, exc_info=True)
            if user is not None:
                user.delete()
            if sport_association is not None:
                sport_association.delete()
            if billing_sub is not None:
                billing_sub.delete()
            return Response({"msg": e}, status=status.HTTP_400_BAD_REQUEST)

        # Generate JWT tokens for the new user
        tokens = JWTTokenService.generate_tokens_for_user(user)
        content = JWTTokenService.build_login_response(user, tokens)

        # send email
        logger.info("Sending welcome email", extra={'user_id': str(user.user_id), 'email': user.email})
        AuthUtils.send_welcome_email(user)
        logger.info("User signup completed successfully", extra={'user_id': str(user.user_id), 'role': user.role})

        # Override with signup-specific data
        if user.role == User.ASSOCIATION or user.role == User.COLLABORATOR:
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
        # there is a email
        user = User.objects.filter(email__iexact=data['email']).first()
        if not user:
            logger.warning("Password reset requested for non-existent email", extra={'email': data['email']})
            return Response({'msg': 'Email sent to the user if the email exists.'}, status=status.HTTP_200_OK)
        # set reset in Redis db
        logger.info("Generating password reset token", extra={'user_id': str(user.user_id)})
        token = str(secrets.token_hex(16))
        r = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            ssl=settings.REDIS_SSLAUTH,
            password=settings.REDIS_PASSWORD,
            username=settings.REDIS_USERNAME,
            ssl_certfile=settings.REDIS_SSLCERT,
            db=2
        )
        r.set(name=token, value=str(user.user_id), ex=3600)
        r.close()
        # sending reset email
        logger.info("Sending password reset email", extra={'user_id': str(user.user_id), 'email': user.email})
        AuthUtils.send_reset_email(user, token)

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
            'app_host': settings.APP_HOST,
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
            'app_host': settings.APP_HOST,
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
            'token': token,
            'app_host': settings.APP_HOST,
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
