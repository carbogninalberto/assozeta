"""
@ copyright: Bakney SRL
"""
from datetime import datetime, date, timedelta
from uuid import uuid4

from codicefiscale import codicefiscale
from django.core.files.base import ContentFile
from django.db.models import Q
from django.db.models.functions import Concat, ExtractYear
from django.db.models import Value as V
from django.db.models.query import Prefetch
from django.utils import timezone

import dateutil
from dateutil.relativedelta import relativedelta
import os
import secrets
import string
import logging
import base64
import csv
from fuzzywuzzy import fuzz
import pandas
from io import StringIO, BytesIO

from django.db import transaction
from django.utils.timezone import make_aware

from rest_framework.exceptions import APIException, ValidationError, PermissionDenied
from rest_framework.response import Response
from rest_framework import status, viewsets
from rest_framework.decorators import api_view, permission_classes

from application.printing_tasks import print_document_medical_appointment
from application.signals import subscription_signed, subscription_approved
from application.utils.printing import current_view_subscriptions
from application.utils.subscriptions_utils import create_subscription, get_optimized_subscriptions, \
    add_course_to_subscription, smart_search, cleanup_storage_keys
from application.services.subscription_service import (
    SubscriptionService, TagService, MedicalCertificateService, SubscriptionImportService
)
from core.middleware import IsAuthenticated

from application.models.carnet_models import CarnetSubscription
from application.models.courses_models import CourseSubscription, CourseSubscriptionInstallment, Course
from application.models.payment_models import Payment
from application.models.user_models import SportAssociation, SportAssociationMembershipCardConfiguration, User, Associate, Family
from application.models.subscriptions_models import Signature, MedicalCertificate, AssociateImportDraft, \
    SubscriptionTransfer, Tags, SubscriptionFile, AssociateImportDraftStatus, MedicalAppointments, SubscriptionToken, \
    SubscriptionMembership, CumulativeSubscriptionGymLinks
from application.permissions import IsProPlanAssociation, IsTeamsPlanAssociation
from application.serializers.courses_serializers import CourseDetailSerializer, \
    CourseSubscriptionOverviewSerializer
from application.serializers.payment_serializers import PaymentSerializer, PaymentOptimizedSerializer
from application.serializers.subscriptions_serializers import SubscriptionSerializer, SignatureRequestSerializer, \
    SubscriptionInfoSerializer, \
    SubscriptionFastBasicSerializer, SubscriptionTransferCreateSerializer, \
    SubscriptionMembershipSerializer, SubscriptionFastOptimizedSerializer
from application.serializers.auth_serializers import UserAccountSerializer
from application.models.subscriptions_models import Subscription
from application.serializers.user_serializers import AssociateSerializer, MedicalAppointmentsSerializer
from application.utils.api_utils import is_valid_uuid, SubscriptionMaps, BalanceSheetData, KTDatatablePagination, \
    check_date, generate_image_with_text
from application.utils.notification_utils import NotificationUtils
from application.views.auth_views import AuthUtils
from django.core.files.storage import default_storage

from core import settings
from core.settings import STORAGE_DIR
from docmanager.models import Document
from notifications.services import NotificationService
from application.tasks import send_email_template, print_document_subscription

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def subscription_tags_list(request):
    """
    API endpoint to get the list of tags for the subscriptions
    :param request: None
    :return: list of tags
    """
    # Optimized: Use values() to only fetch required fields, avoiding full model instantiation
    tags = Tags.objects.filter(
        sport_association=request.user.sport_association
    ).values('tag_id', 'tag_name').order_by('tag_name')

    return Response(
        {'tags': list(tags)},
        status=status.HTTP_200_OK
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def subscription_tags_add(request):
    """
    API endpoint to add a new tag for the subscriptions
    :param request: tag_name
    :return: list of tags
    """
    try:
        tag_name = request.data.get('tag_name', None)
        tag = TagService.create_tag(tag_name, request.user.sport_association)

        return Response(
            {'tag': {'tag_id': tag.tag_id, 'tag_name': tag.tag_name}},
            status=status.HTTP_200_OK
        )
    except ValidationError as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def subscription_tags_update(request, tag_id):
    """
    API endpoint to update a tag for the subscriptions
    :param request: tag_name
    :return: list of tags
    """
    try:
        tag_name = request.data.get('tag_name', None)
        tag = TagService.update_tag(tag_id, tag_name, request.user.sport_association)

        return Response(
            {'tag_id': tag.tag_id, 'tag_name': tag.tag_name},
            status=status.HTTP_200_OK
        )
    except ValidationError as e:
        error_msg = str(e)
        # Return 400 for validation errors (tag_name), 404 for not found
        if 'tag_name is required' in error_msg:
            return Response({'msg': error_msg}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'msg': error_msg}, status=status.HTTP_404_NOT_FOUND)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def subscription_tags_delete(request, tag_id):
    """
    API endpoint to delete a tag for the subscriptions
    :param request: tag_name
    :return: list of tags
    """
    try:
        TagService.delete_tag(tag_id, request.user.sport_association)

        return Response({'msg': 'tag deleted'}, status=status.HTTP_200_OK)
    except ValidationError as e:
        return Response({'msg': str(e)}, status=status.HTTP_404_NOT_FOUND)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def subscription_tags_assign(request, tag_id, subscription_id):
    """
    API endpoint to update a tag for the subscriptions
    :param request: tag_id, subscription_id
    :return: list of tags
    """
    try:
        TagService.assign_tag_to_subscription(
            tag_id, subscription_id, request.user.sport_association
        )

        return Response({'msg': 'tag assigned'}, status=status.HTTP_200_OK)
    except ValidationError as e:
        return Response({'msg': str(e)}, status=status.HTTP_404_NOT_FOUND)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def subscription_tags_unassign(request, tag_id, subscription_id):
    """
    API endpoint to update a tag for the subscriptions
    :param request: tag_id, subscription_id
    :return: list of tags
    """
    try:
        TagService.unassign_tag_from_subscription(
            tag_id, subscription_id, request.user.sport_association
        )

        return Response({'msg': 'tag unassigned'}, status=status.HTTP_200_OK)
    except ValidationError as e:
        return Response({'msg': str(e)}, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def subscription_renew(request):
    logger.info("Subscription renewal started", extra={'user_id': str(request.user.user_id)})
    data = request.data
    if request.user.role != User.ASSOCIATION:
        request.user = SportAssociation.objects.get(sport_association_id=data['sport_association']['sport_association_id']).user

    created_storage_keys = []
    try:
        with transaction.atomic():
            # Delay callbacks until creation and renewal post-processing both succeed.
            logger.info("Creating new subscription", extra={'user_id': str(request.user.user_id), 'sport_association_id': str(request.user.sport_association.sport_association_id)})
            ok, fresh_sub = create_subscription(data, request.user, request.headers.get('authorization'), False)
            if not ok or fresh_sub is None:
                raise RuntimeError("error creating subscription")
            created_storage_keys.extend(getattr(fresh_sub, '_created_storage_keys', []))

            logger.info("Subscription created successfully", extra={'user_id': str(request.user.user_id), 'subscription_id': str(fresh_sub.subscription_id)})
            payments = Payment.objects.filter(
                associate__first_name=fresh_sub.associate.first_name,
                associate__last_name=fresh_sub.associate.last_name,
                associate__tax_code__iexact=fresh_sub.associate.tax_code,
                sport_association=fresh_sub.sport_association,
                subject=Payment.SUBSCRIPTION,
                creation_date__gte=fresh_sub.start_date,
                creation_date__lte=fresh_sub.end_date,
                paid=False
            )
            if fresh_sub.payment:
                payments = payments.exclude(payment_id=fresh_sub.payment.payment_id)
            payments.order_by('creation_date').delete()

            if 'user' in data and 'email' in data['user']:
                user = User.objects.filter(email=data['user']['email']).first()
                if user is not None:
                    fresh_sub.user = user
                    fresh_sub.save()

            if 'tags' in data and len(data['tags']) > 0:
                if isinstance(data['tags'][0], str):
                    tag_ids = data['tags']
                else:
                    tag_ids = [tag['tag_id'] for tag in data['tags']]
                tags = Tags.objects.filter(tag_id__in=tag_ids)
                fresh_sub.tags.set(tags)

            if 'courses' in data and len(data['courses']) > 0:
                for c in data['courses']:
                    u: User = request.user
                    course = Course.objects.filter(course_id=c['value']).first()
                    add_course_to_subscription(course, fresh_sub, u.sport_association, data=None, is_athlete=False)

        logger.info("Subscription renewal completed", extra={'user_id': str(request.user.user_id), 'subscription_id': str(fresh_sub.subscription_id)})
        return Response(
            {"status": "success", "payment_id": fresh_sub.payment.payment_id if fresh_sub.payment is not None else None},
            status=status.HTTP_200_OK
        )
    except APIException:
        cleanup_storage_keys(created_storage_keys)
        raise
    except Exception as e:
        cleanup_storage_keys(created_storage_keys)
        logger.error("Subscription renewal failed", extra={'user_id': str(request.user.user_id), 'error': str(e)}, exc_info=True)
        return Response(
            {"status": "error", "msg": str(e)},
            status=status.HTTP_400_BAD_REQUEST
        )


def _get_asd_custom_data_for_grouped_subscriptions(user):
    """Extract ASD custom data from the most recent grouped subscription."""
    asd_data = Subscription.objects.filter(
        user=user,
    ).filter(
        Q(custom_data__icontains='asd-o-ssd') |
        Q(custom_data__icontains='scuola-asc')
    ).order_by('-creation_date').first()

    if asd_data is None:
        return {}

    return {
        "denomination": asd_data.custom_data.get("denomination", "") if asd_data and hasattr(asd_data, "custom_data") else "",
        "city": asd_data.custom_data.get("city", "") if asd_data and hasattr(asd_data, "custom_data") else "",
        "province": asd_data.custom_data.get("province", "") if asd_data and hasattr(asd_data, "custom_data") else "",
        "address": asd_data.custom_data.get("address", "") if asd_data and hasattr(asd_data, "custom_data") else "",
        "cap": asd_data.custom_data.get("zip", "") if asd_data and hasattr(asd_data, "custom_data") else "",
        "country": asd_data.custom_data.get("country", "Italia") if asd_data and hasattr(asd_data, "custom_data") else "Italia",
        "tax_code": asd_data.custom_data.get("tax_code", "") if asd_data and hasattr(asd_data, "custom_data") else "",
        "email": asd_data.custom_data.get("email", "") if asd_data and hasattr(asd_data, "custom_data") else ""
    }


def _handle_family_subscription(data, request, is_athlete_request):
    """Handle family/grouped subscription creation."""
    type_of_family = data.get('type', Family.FAMILY)
    if type_of_family not in Family.ALL_TYPES:
        type_of_family = Family.FAMILY

    payments = []
    created_storage_keys = []

    asd_custom_data = {}
    if type_of_family == Family.GROUPED_SUBSCRIPTIONS:
        asd_custom_data = _get_asd_custom_data_for_grouped_subscriptions(request.user)

    try:
        with transaction.atomic():
            family = Family.objects.create(type=type_of_family)

            for entry in data['multiple_entry_form_data']:
                if entry['valid'] is True:
                    entry['sport_association'] = data['sport_association']
                    entry['associate_data']['family'] = family.family_id
                    entry['custom_data'] = {**entry['custom_data'], **asd_custom_data}

                    ok, fresh_sub = create_subscription(
                        entry,
                        request.user,
                        request.headers.get('authorization'),
                        is_athlete_request
                    )
                    if fresh_sub is not None:
                        created_storage_keys.extend(getattr(fresh_sub, '_created_storage_keys', []))
                        if fresh_sub.payment is not None:
                            payments.append(fresh_sub.payment)
    except Exception:
        cleanup_storage_keys(created_storage_keys)
        raise

    return Response({
        "status": "success",
        "subs": [],
        "payments": [PaymentSerializer(payment).data for payment in payments]
    }, status=status.HTTP_200_OK)


def _send_athlete_subscription_email(subscription):
    """Send notification email when athlete creates subscription."""
    SubscriptionService.send_athlete_subscription_email(subscription)


def _handle_quick_subscription(data, request):
    """Handle quick subscription for existing associate."""
    sport_association = SportAssociation.objects.get(sport_association_id=data['sport_association'])
    associate = Associate.objects.get(associate_id=data['associate'])

    with transaction.atomic():
        sport_association = SportAssociation.objects.select_for_update().get(
            sport_association_id=sport_association.sport_association_id
        )
        # Create subscription using service
        subscription = SubscriptionService.create_quick_subscription(
            sport_association, associate, data, request.user
        )
        subscription.save()

        # Trigger document printing
        auth_token = request.headers.get('authorization')
        transaction.on_commit(
            lambda: print_document_subscription.delay(str(subscription.subscription_id), auth_token),
            robust=True
        )

        # Send notification
        messages = [{
            "type": NotificationUtils.SUBSCRIPTION,
            "msg": "Nuova Iscrizione aggiunta per {}.".format(associate.get_full_name())
        }]
        transaction.on_commit(
            lambda: NotificationService.send_notification(request.user, messages),
            robust=True
        )

        # Send email for athlete subscriptions
        if request.user.role == User.ATHLETE:
            transaction.on_commit(lambda: _send_athlete_subscription_email(subscription), robust=True)

    return Response({
        "status": "success",
        "payment_id": subscription.payment.payment_id
    }, status=status.HTTP_200_OK)


def _prepare_subscription_request(request, data, mode):
    """Prepare and validate subscription request parameters."""

    logger.info(
        "Preparing subscription request",
        extra={
            'user_id': str(request.user.user_id) if request.user.is_authenticated else 'anonymous',
            'mode': mode,
            'is_family_wizard': data.get('is_family_wizard') is True,
        }
    )

    # Handle anonymous user
    if not request.user.is_authenticated:
        logger.info("New anonymous subscription request")
        if 'sport_association' in data:
            sport_association_user = User.objects.get(username=data['sport_association'])
            request.user = sport_association_user

    is_athlete_request = 'sport_association' in data and request.user.role != User.ASSOCIATION
    is_quick_sub = mode == 'only_associate'

    if request.user.is_authenticated and request.user.role == User.ASSOCIATION and is_quick_sub:
        data['sport_association'] = SportAssociation.objects.filter(user=request.user).first().sport_association_id

    return is_athlete_request, is_quick_sub


@api_view(['POST'])
# @permission_classes([IsAuthenticated])
def subscription_add(request):
    """
    API endpoint to add a new subscription
    :param request: data: {newUserAccount, newAssociate, signature, paymentMethod}
    :return: token, refresh token
    """
    logger.info("Adding new subscription", extra={'user_id': str(request.user.user_id) if request.user.is_authenticated else 'anonymous'})
    data = request.data
    mode = request.GET.get('m', None)
    is_athlete_request, is_quick_sub = _prepare_subscription_request(request, data, mode)

    # Handle full subscription creation
    required_keys = ['new_user_account', 'associate_data', 'associate_tutor_data', 'signature']

    logger.info(f"is_quick_sub: {is_quick_sub}")
    logger.info(f"all(key in data for key in required_keys): {all(key in data for key in required_keys)}")

    if not is_quick_sub and all(key in data for key in required_keys):
        logger.info("Creating subscription (not quick)")
        # Handle family subscriptions
        if data.get('is_family_wizard') is True:
            logger.info("Creating family subscription")
            return _handle_family_subscription(data, request, is_athlete_request)

        # Handle single subscription
        logger.info("Creating single subscription")
        ok, fresh_sub = create_subscription(data, request.user, request.headers.get('authorization'), is_athlete_request)
        logger.info(f"is creation successful: {ok}")
        payment_amount = fresh_sub.payment.amount if fresh_sub.payment is not None else 0
        payment_id = fresh_sub.payment.payment_id if fresh_sub.payment is not None else None

        return Response({"status": "success", "payment_id": payment_id, "amount": payment_amount}, status=status.HTTP_200_OK)

    # Handle quick subscription
    elif is_quick_sub and 'sport_association' in data and 'associate' in data:
        logger.info("Creating quick subscription")
        return _handle_quick_subscription(data, request)

    else:
        logger.info("Validation errors on subscription creation")
        return Response({"msg": "validation errors."}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def subscription_transfer(request, uid):
    is_valid_uuid(uid)

    # check if the subscription exists and if the user is the owner of the subscription
    # and check if there is a pending transfer
    try:
        subscription = Subscription.objects.get(subscription_id=uid)
    except Subscription.DoesNotExist:
        return Response({'msg': 'Subscription not found.'}, status=status.HTTP_404_NOT_FOUND)

    if subscription.user != request.user:
        return Response({'msg': 'You are not the owner of the subscription.'}, status=status.HTTP_403_FORBIDDEN)

    # get the pending transfer
    try:
        # get all the pending transfer and set to expired
        SubscriptionTransfer.objects.filter(expires_at__lt=timezone.now()).update(status=SubscriptionTransfer.EXPIRED)
        subscription_transfers = SubscriptionTransfer.objects.filter(
            subscription=subscription,
            status=SubscriptionTransfer.PENDING)
        if subscription_transfers.count() > 0:
            return Response({'msg': 'C\'è già una richiesta di trasferimento in attesa di essere approvata.'}, status=status.HTTP_400_BAD_REQUEST)
    except SubscriptionTransfer.DoesNotExist:
        pass

    recipient_id = request.data.get('recipient', None)
    if recipient_id is not None:

        if recipient_id == str(request.user.user_id):
            return Response({'msg': 'You cannot transfer the subscription to yourself.'}, status=status.HTTP_409_CONFLICT)

        # get recipient user
        recipient = User.objects.filter(user_id=recipient_id).exclude(user_id=request.user.user_id).first()

        # if I provide the recipient_id I expect it is valid
        if recipient_id is not None and recipient is None:
            return Response(status=status.HTTP_400_BAD_REQUEST)
    else:
        # check if subscription associate has tutor and if it has get the tutor
        first_name = None
        last_name = None
        email = request.data.get('email', None)
        if email is None:
            return Response({'msg': 'Email is required.'}, status=status.HTTP_400_BAD_REQUEST)

        tutor = subscription.associate.get_main_tutor()
        if tutor is not None:
            first_name = tutor.first_name
            last_name = tutor.last_name
        else:
            first_name = subscription.associate.first_name
            last_name = subscription.associate.last_name

        alphabet = string.ascii_letters + string.digits
        username = first_name[:3] + last_name[:3] + ''.join(
            secrets.choice(alphabet) for i in range(8)).upper()
        password = ''.join(secrets.choice(alphabet) for i in range(8))

        user = User.objects.create_user(
            username=str(username).upper(),
            password=password,
            email=email,
            first_name=first_name,
            last_name=last_name,
            role=User.ATHLETE,
        )
        AuthUtils.send_password_welcome_email(user, password, request.user.sport_association)

        recipient = user

    data = {
        'subscription': uid,
        'requester': str(request.user.user_id),
        'recipient': str(recipient.user_id) if recipient is not None else None,
    }

    if recipient is not None:
        messages = [
            {
                "type": NotificationUtils.SUBSCRIPTION,
                "msg": f"l'associazione {subscription.sport_association.denomination} ha trasferito "
                       f"l'iscrizione {subscription.associate.get_full_name()} al tuo account"
            }
        ]

        NotificationService.send_notification(recipient, messages)

    serializer = SubscriptionTransferCreateSerializer(data=data)
    if serializer.is_valid():
        transfer = serializer.save()
        # auto accept the transfer, new business logic, but keeping the 
        # track of the transfer
        subscription.user = transfer.recipient
        subscription.save()

        # check associate and tutor user and update
        subscription.associate.user = transfer.recipient
        subscription.associate.save()

        tutor = subscription.associate.get_main_tutor()
        if tutor is not None:
            tutor.user = transfer.recipient
            tutor.save()
        transfer.status = SubscriptionTransfer.ACCEPTED
        transfer.expires_at = timezone.now()
        transfer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def subscription_sign(request):
    """
    API endpoint to sign a subscription
    :param request: data: {subscription_id, signature}
    :return: token, refresh token
    """
    logger.info("Signing subscription", extra={'user_id': str(request.user.user_id)})

    # getting body
    data = request.data

    max_keys_length = 2
    user = request.user

    # is_athlete_request = user.role == User.ATHLETE
    #
    # if not is_athlete_request:
    #     raise PermissionDenied('User not allowed')

    # checking body for security/correctness
    if 'subscription_id' in data.keys() and \
            'signature' in data.keys() and \
            len(data.keys()) == max_keys_length:

        # instantiate new variables for each field to make code more readable
        if not is_valid_uuid(data['subscription_id']):
            raise ValidationError('not valid payload')
        else:
            subscription = Subscription.objects.filter(subscription_id=data['subscription_id'])[0]

        signature = SignatureRequestSerializer(data=data['signature'])

        # check if user is owner of the subscription
        # if subscription.user != user:
        #     raise PermissionDenied('User not allowed')

        if signature.is_valid(raise_exception=True):
            new_storage_key = None
            try:
                with transaction.atomic():
                    subscription = Subscription.objects.select_for_update().get(
                        subscription_id=subscription.subscription_id
                    )
                    old_storage_key = subscription.signature_storage_key
                    signature = signature.save()
                    # save new signature if present
                    if signature.there_is_signature:
                        new_signature = Signature.objects.create(
                            signature=signature.data,
                            user=user
                        )
                        new_signature.save()
                        # Upload signature to storage and update subscription
                        subscription.set_signature_from_base64(signature.data)
                        new_storage_key = subscription.signature_storage_key
                        subscription.status_flag = Subscription.PENDING
                        subscription.save(update_fields=['signature_url', 'signature_storage_key', 'status_flag'])
                        if old_storage_key and old_storage_key != new_storage_key:
                            transaction.on_commit(
                                lambda: Subscription.delete_signature_key_if_unreferenced(old_storage_key),
                                robust=True
                            )
                        logger.info("Subscription signed successfully", extra={'user_id': str(user.user_id), 'subscription_id': str(subscription.subscription_id)})
                    transaction.on_commit(
                        lambda: subscription_signed.send(sender=subscription.__class__, subscription=subscription),
                        robust=True
                    )

                    # printing the document
                    logger.info("Printing subscription document", extra={'subscription_id': str(subscription.subscription_id)})
                    auth_token = request.headers.get('authorization')
                    transaction.on_commit(
                        lambda: print_document_subscription.delay(str(subscription.subscription_id), auth_token),
                        robust=True
                    )
            except Exception:
                if new_storage_key:
                    cleanup_storage_keys([new_storage_key])
                raise

        return Response({"status": "success"}, status=status.HTTP_200_OK)
    else:
        return Response({"msg": "validation errors."}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
# @permission_classes([IsAuthenticated])
def subscription_calculate_tax_code(request):
    data = request.data
    tax_code = None

    if 'first_name' in data.keys() and \
            'sex' in data.keys() and \
            'last_name' in data.keys() and \
            'born_date' in data.keys() and \
            'born_city' in data.keys():
        if data['last_name'] is None or data['last_name'] == '' or \
            data['first_name'] is None or data['first_name'] == '' or \
            data['sex'] is None or data['sex'].upper() not in ['M', 'F'] or \
            data['born_date'] is None or data['born_date'] == '' or \
            data['born_city'] is None or data['born_city'] == '':
            return Response({"msg": "alcuni dati sono mancanti."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            tax_code = codicefiscale.encode(
                lastname=data['last_name'],
                firstname=data['first_name'],
                gender=data['sex'],
                birthdate=data['born_date'],
                birthplace=data['born_city']
            )
        except Exception as e:
            if 'not mapped to code' in str(e):
                return Response({"msg": "Città non mappata."}, status=status.HTTP_400_BAD_REQUEST)
            return Response({"msg": "alcuni dati non sono validi."}, status=status.HTTP_400_BAD_REQUEST)
    return Response({"tax_code": tax_code}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def subscription_list_all(request):

    # get query param current_year
    current_year = request.GET.get('current_year', None)
    type = request.GET.get('type', None)
    preregistration = request.GET.get('preregistration', None)
    sport_association = request.GET.get('sport_association', None)
    course_id = request.GET.get('course_id', None)

    if preregistration is not None and preregistration == '1':
        # return all the subscriptions active and not except the newest one not active
        # if there is a newer one with the same associate full name and tax_code exclude it
        if request.user.role == User.ATHLETE:
            subscriptions = Subscription.objects.filter(
                sport_association__sport_association_id=sport_association,
                user=request.user,
                status_flag__in=[Subscription.NOT_SIGNED, Subscription.PENDING, Subscription.ACCEPTED, Subscription.RESIGNED]
            ).select_related(
                'associate',
                'sport_association'
            )
        else:
            sport_association = SportAssociation.objects.get(user=request.user)
            subscriptions = Subscription.objects.filter(
                sport_association=sport_association,
                archived=False
            ).select_related(
                'associate'
            )

        subscriptions = subscriptions.order_by('associate__first_name', 'associate__last_name', 'associate__tax_code', '-creation_date')

        # get the last subscription for each associate
        last_subscriptions = []
        for subscription in subscriptions:
            if subscription.is_next_year and \
                    f"{subscription.associate.full_name_lower}__{subscription.associate.tax_code}" not in last_subscriptions:
                last_subscriptions.append(f"{subscription.associate.full_name_lower}__{subscription.associate.tax_code}")

        data = {}
        added_subs = []
        for idx, subscription in enumerate(subscriptions):
            # if already "renewed" skip
            if f"{subscription.associate.full_name_lower}__{subscription.associate.tax_code}" in last_subscriptions:
                continue
            # if already added one subscription for the same associate skip
            if f"{subscription.associate.full_name_lower}__{subscription.associate.tax_code}" in added_subs:
                continue
            data[idx] = {
                'associate': {
                    'first_name': subscription.associate.first_name,
                    'last_name': subscription.associate.last_name,
                    'associate_id': subscription.associate.associate_id,
                },
                'subscription_id': subscription.subscription_id,
            }
            data[idx]['current_year'] = subscription.active
            added_subs.append(f"{subscription.associate.full_name_lower}__{subscription.associate.tax_code}")
        return Response({'data': data}, status=status.HTTP_200_OK)

    if request.user.role == User.ATHLETE:
        if sport_association is None:
            return Response({'msg': 'not allowed'}, status=status.HTTP_403_FORBIDDEN)
        subscriptions = Subscription.objects.filter(
            user=request.user,
            sport_association__sport_association_id=sport_association
        ).select_related(
            'associate',
            'sport_association'
        ).order_by('-creation_date')
    else:
        sport_association = SportAssociation.objects.get(user=request.user)
        subscriptions = Subscription.objects.filter(
            sport_association=sport_association,
            archived=False
        ).select_related(
            'associate',
            'sport_association'
        )

    if current_year is not None and (current_year == '1' or current_year == 'true'):
        today = timezone.now()
        subscriptions = subscriptions.filter(
            Q(start_date__lte=today) &
            Q(end_date__gte=today)
        )

    if type is not None:
        if type == 'athletes':
            # filter for SOCI TESSERATI e TESSERATI
            subscriptions = subscriptions.filter(
                type__in=[Subscription.ASSOCIATE_AND_MEMBER, Subscription.MEMBER_ONLY]
            )
        elif type == 'associates':
            # filter for LIBRO SOCI
            subscriptions = subscriptions.filter(
                type__in=[Subscription.ASSOCIATE_AND_MEMBER, Subscription.ASSOCIATE_ONLY]
            )

    if course_id is not None:
        course_subscriptions = CourseSubscription.objects.filter(
            course_id=course_id
        ).values_list('subscription_id', flat=True)

        subscriptions = subscriptions.exclude(
            subscription_id__in=course_subscriptions,
        )

    data = {}
    meta = {}
    for idx, subscription in enumerate(subscriptions):
        data[idx] = {
            'associate': {
                'first_name': subscription.associate.first_name,
                'last_name': subscription.associate.last_name,
                'associate_id': subscription.associate.associate_id,
            },
            'subscription_id': subscription.subscription_id,
        }
        data[idx]['current_year'] = subscription.active
        data[idx]['season'] = subscription.season

    return Response({'data': data, "meta": meta}, status=status.HTTP_200_OK)


def _parse_list_query_params(request):
    """Parse all query parameters for subscription list."""
    params = {
        'general_search': request.GET.get('query[generalSearch]', None),
        'type_of_associate': request.GET.get('query[type_of_associate]', None),
        'tags': request.GET.get('query[tags]', None),
        'tags_and': request.GET.get('query[tags_and]', None),
        'from_age': request.GET.get('query[from_age]', None),
        'to_age': request.GET.get('query[to_age]', None),
        'prefix': request.GET.get('query[prefix]', None),
        'status_flag': request.GET.get('query[status_flag]', None),
        'current_year': request.GET.get('query[current_year]', None),
        'period_start': request.GET.get('query[period_start]', None),
        'period_end': request.GET.get('query[period_end]', None),
        'certificate_missing_flag': request.GET.get('query[certificate_missing_flag]', None),
        'certificate_expired_flag': request.GET.get('query[certificate_expired_flag]', None),
        'subscription_not_paid_flag': request.GET.get('query[subscription_not_paid_flag]', None),
        'sort_lastname_asc_flag': request.GET.get('query[sort_lastname_asc_flag]', None),
        'sort_lastname_desc_flag': request.GET.get('query[sort_lastname_desc_flag]', None),
        'hide_associate_and_members': request.GET.get('query[hide_associate_and_members]', None),
        'hide_members': request.GET.get('query[hide_members]', None),
        'sort_field': request.GET.get('sort[field]', None),
        'sort_type': request.GET.get('sort[sort]', None),
        'field': request.GET.get('field', None),
        'type': request.GET.get('type', None),
        'mode': request.GET.get('m', None),
        'filter': request.GET.get('filter', None)
    }

    if params['prefix']:
        params['general_search'] = f"{params['prefix']}{params['general_search']}"

    params['tags_ids'] = params['tags'].split(',') if params['tags'] else []

    return params


def _get_base_subscriptions_queryset(sport_association, params={}):
    """Get base optimized subscriptions queryset with all necessary joins."""
    from django.db.models import Prefetch, Exists, OuterRef

    payments_prefetch = Prefetch(
        'associate__payment_set',
        queryset=Payment.objects.filter(archived=False).only(
            'payment_id', 'paid', 'amount', 'archived', 'associate_id'
        )
    )

    # Subquery to check if there's a future subscription for renewal_available
    # This runs ONCE for all subscriptions instead of N times
    future_subscription_exists = Subscription.objects.filter(
        sport_association=OuterRef('sport_association'),
        start_date__gte=OuterRef('end_date'),
        archived=False,
        associate__tax_code__iexact=OuterRef('associate__tax_code')
    )
    if params['mode'] is None:
        return Subscription.objects.filter(
            sport_association=sport_association,
            archived=False
        ).select_related(
            # Foreign key relationships (1-to-1 joins)
            'associate',
            'medical',
            'medical__document',
            'document_pdf',
            'user',
            'sport_association',
            'payment'
        ).prefetch_related(
            # Many-to-many and reverse relationships
            'tags',
            payments_prefetch
        ).annotate(
            # Annotate whether a future renewal exists (eliminates N+1 queries)
            _has_future_renewal=Exists(future_subscription_exists)
        ).order_by('-creation_date')
    else:
       return Subscription.objects.filter(
            sport_association=sport_association,
            archived=False
        ).order_by('-creation_date')


def _apply_basic_filters(subscriptions, params):
    """Apply basic filters to subscriptions queryset."""
    if params['tags_ids']:
        logger.info(f"tags_ids: {params['tags_ids']} | tags_and: {params['tags_and'] == '1'}")
        if params['tags_and'] == '1':
            for tag_id in params['tags_ids']:
                subscriptions = subscriptions.filter(tags__tag_id=tag_id)
        else:
            subscriptions = subscriptions.filter(tags__tag_id__in=params['tags_ids'])

    if params['from_age']:
        subscriptions = subscriptions.filter(associate__born_date__lte=datetime.now() - relativedelta(years=int(params['from_age'])))

    if params['to_age']:
        subscriptions = subscriptions.filter(associate__born_date__gt=datetime.now() - relativedelta(years=int(params['to_age'])))

    if params['hide_associate_and_members'] == '1':
        subscriptions = subscriptions.exclude(type=Subscription.ASSOCIATE_AND_MEMBER)
    if params['hide_members'] == '1':
        subscriptions = subscriptions.exclude(type=Subscription.MEMBER_ONLY)

    return subscriptions


def _apply_date_filters(subscriptions, params):
    """Apply date-based filters to subscriptions."""
    if params['status_flag']:
        if ',' in params['status_flag']:
            subscriptions = subscriptions.filter(status_flag__in=params['status_flag'].split(','))
        else:
            subscriptions = subscriptions.filter(status_flag=params['status_flag'])

    if params['current_year']:
        today = timezone.now()
        if params['current_year'] == '2':
            subscriptions = subscriptions.filter(Q(start_date__gt=today))
        elif params['current_year'] == '1':
            subscriptions = subscriptions.filter(Q(start_date__lte=today) & Q(end_date__gte=today))
        elif params['current_year'] == '0':
            subscriptions = subscriptions.filter(Q(end_date__lt=today))
        elif params['current_year'] == '3':
            if not params['period_start'] or not params['period_end']:
                return subscriptions

            try:
                period_start_date = datetime.strptime(params['period_start'], '%d/%m/%Y').strftime('%Y-%m-%d')
                period_end_date = datetime.strptime(params['period_end'], '%d/%m/%Y').strftime('%Y-%m-%d')
                subscriptions = subscriptions.filter(
                    Q(Q(start_date__lte=period_start_date) & Q(end_date__lte=period_end_date)) |
                    Q(Q(start_date__gte=period_start_date) & Q(end_date__lte=period_end_date)) |
                    Q(Q(start_date__gte=period_start_date) & Q(end_date__gte=period_end_date)) |
                    Q(Q(start_date__lte=period_start_date) & Q(end_date__gte=period_end_date))
                )
            except (TypeError, ValueError):
                pass

    return subscriptions


def _apply_search_filter(subscriptions, general_search):
    """Apply general search filter to subscriptions."""
    if general_search and "scade=" in general_search.lower():
        search_date = general_search.replace("scade=", "").strip()
        try:
            search_date = datetime.strptime(search_date, '%d/%m/%Y').strftime('%Y-%m-%d')
            return subscriptions.filter(Q(end_date__icontains=search_date))
        except ValueError:
            pass

    # if general_search starts with @ check for subscription.user.username__icontains
    if general_search and general_search.startswith('@='):
        return subscriptions.filter(user__username__icontains=general_search.replace('@=', ''))


    if general_search:
        try:
            search_date = datetime.strptime(general_search, '%d/%m/%Y').strftime('%Y-%m-%d')
            general_search = search_date
        except ValueError:
            pass

        try:
            return smart_search(subscriptions, general_search)
        except Exception as e:
            logger.info(f"Search {general_search} got error: {e}")
            return subscriptions.annotate(
                full_name=Concat('associate__first_name', V(' '), 'associate__last_name')
            ).filter(
                Q(full_name__icontains=general_search) |
                Q(associate__first_name__icontains=general_search) |
                Q(associate__last_name__icontains=general_search) |
                Q(associate__tax_code__icontains=general_search) |
                Q(creation_date__icontains=general_search) |
                Q(tags__tag_name__icontains=general_search) |
                Q(user__username__icontains=general_search) |
                Q(custom_data__icontains=general_search)
            )

    return subscriptions


def _apply_additional_filters(subscriptions, params):
    """Apply additional filters for certificates, payments, and type."""
    if params['type_of_associate']:
        subscriptions = subscriptions.filter(custom_data__icontains=params['type_of_associate'])

    if params['certificate_missing_flag'] == '1':
        subscriptions = subscriptions.filter(medical__isnull=True)

    if params['certificate_expired_flag'] == '1':
        subscriptions = subscriptions.filter(medical__expiration_date__lte=datetime.now().date())

    if params['subscription_not_paid_flag'] == '1':
        subscriptions = subscriptions.filter(payment__paid=False)

    printing_title = "Iscrizioni filtrate"
    if params['type'] == 'athletes':
        subscriptions = subscriptions.filter(type__in=[Subscription.ASSOCIATE_AND_MEMBER, Subscription.MEMBER_ONLY])
        printing_title = 'Tesserati'
    elif params['type'] == 'associates':
        subscriptions = subscriptions.filter(type__in=[Subscription.ASSOCIATE_AND_MEMBER, Subscription.ASSOCIATE_ONLY])
        printing_title = 'Libro Soci'

    return subscriptions, printing_title


def _apply_sorting(subscriptions, params):
    """Apply sorting to subscriptions queryset."""
    if params['sort_lastname_asc_flag'] and params['sort_lastname_asc_flag'] != '0' or \
       params['sort_lastname_desc_flag'] and params['sort_lastname_desc_flag'] != '0':
        subscriptions = subscriptions.order_by(
            f"{'-' if params['sort_lastname_desc_flag'] == '1' else ''}associate__last_name"
        )

    if params['sort_field']:
        if params['sort_field'] == 'associate':
            subscriptions = subscriptions.order_by(
                f"{'-' if params['sort_type'] == 'desc' else ''}associate__first_name",
                f"{'-' if params['sort_type'] == 'desc' else ''}associate__last_name"
            )
        elif params['sort_field'] == 'age':
            subscriptions = subscriptions.annotate(age=ExtractYear('associate__born_date')).order_by(
                f"{'-' if params['sort_type'] == 'desc' else ''}age"
            )
        else:
            subscriptions = subscriptions.order_by(
                f"{'-' if params['sort_type'] == 'desc' else ''}{params['sort_field']}"
            )

    return subscriptions


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def subscription_list(request):
    logger.info(f"perpage: {request.GET.get('pagination[perpage]', 10)} - page: {request.GET.get('pagination[page]', 1)}")
    params = _parse_list_query_params(request)

    # Handle field extraction endpoint
    if params['field']:
        subscriptions = Subscription.objects.filter(
            sport_association__user=request.user, archived=False
        ).exclude(
            ~Q(associate__tutors__email__isnull=False, associate__tutors__email__gt='',
               associate__tutors__phone__isnull=False, associate__tutors__phone__gt=''),
            Q(associate__email='') | Q(associate__email=None) |
            Q(associate__phone='') | Q(associate__phone=None)
        ).values_list(f"associate__{params['field']}", flat=True).distinct()
        return Response({'data': subscriptions}, status=status.HTTP_200_OK)

    logger.info("subscription_list")

    # Handle athlete request
    if request.user.role == User.ATHLETE:
        data = get_optimized_subscriptions(request.user)
        return Response({'data': data, "meta": {}}, status=status.HTTP_200_OK)

    sport_association = SportAssociation.objects.get(user=request.user)
    subscriptions = _get_base_subscriptions_queryset(sport_association, params)
    subscriptions = _apply_basic_filters(subscriptions, params)

    # Handle mode-based filtering
    if params['mode'] is None:
        subscriptions = _apply_date_filters(subscriptions, params)
        subscriptions = _apply_search_filter(subscriptions, params['general_search'])
        subscriptions, printing_title = _apply_additional_filters(subscriptions, params)
        subscriptions = _apply_sorting(subscriptions, params)

        # Handle POST request for printing
        if request.method == 'POST':
            file, filename = current_view_subscriptions(sport_association, request.data, subscriptions, title=printing_title)
            return Response({"file": file, "filename": filename}, status=status.HTTP_200_OK)

        # Paginate and serialize
        paginator = KTDatatablePagination()
        subscriptions = paginator.paginate_queryset(queryset=subscriptions, request=request)
        # Use ultra-fast base Serializer (30-40% faster than ModelSerializer)
        subscription_serialized = SubscriptionFastOptimizedSerializer(subscriptions, many=True, read_only=True).data

        data = {idx: subscription for idx, subscription in enumerate(subscription_serialized)}
        meta = {
            "total": paginator.page.paginator.count,
            "page": paginator.page.number,
            "pages": paginator.page.paginator.num_pages,
            "perpage": paginator.page.paginator.per_page,
            "rowIds": [subscription.subscription_id for subscription in subscriptions]
        }
    else:
        # Handle alternate mode
        if params['filter'] is None:
            subscriptions = subscriptions.filter(status_flag=Subscription.ACCEPTED)
        elif params['filter'] != 'all':
            subscriptions = subscriptions.filter(status_flag=params['filter'])

        # Optimize queryset with select_related to avoid N+1 queries
        subscriptions = subscriptions.select_related('associate')

        # Use high-performance serializer (base Serializer instead of ModelSerializer)
        subs = SubscriptionFastBasicSerializer(subscriptions, many=True).data
        data = {idx: subscription for idx, subscription in enumerate(subs)}
        meta = {}

    response_data = {'data': data, "meta": meta}
    return Response(response_data, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def subscription_list_archived(request):
    paginator = KTDatatablePagination()
    general_search = request.GET.get('query[generalSearch]', None)
    sort_lastname_asc_flag = request.GET.get('query[sort_lastname_asc_flag]', None)
    sort_lastname_desc_flag = request.GET.get('query[sort_lastname_desc_flag]', None)
    sort_field = request.GET.get('sort[field]', None)
    sort_type = request.GET.get('sort[sort]', None)

    is_athlete = True if User.ATHLETE == request.user.role else False
    if is_athlete:
        return Response(status=status.HTTP_401_UNAUTHORIZED)

    sport_association = SportAssociation.objects.get(user=request.user)
    subscriptions = Subscription.objects.filter(
        sport_association=sport_association,
        archived=True
    ).select_related(
        'associate',
        'medical',
        'medical__document',
        'document_pdf',
        'user',
        'sport_association'
    ).prefetch_related(
        'tags'
    ).order_by('-creation_date')
    data = {}

    if general_search:
        # check if general_search is a date
        try:
            date = datetime.strptime(general_search, '%d/%m/%Y').strftime('%Y-%m-%d')
            general_search = date
        except ValueError:
            pass

        subscriptions = smart_search(subscriptions, general_search)
        # sort by field
    if sort_field:
        # when associate split first_name, last_name
        if sort_field == 'associate':
            subscriptions = subscriptions.order_by(
                f"{'-' if sort_type == 'desc' else ''}associate__first_name",
                f"{'-' if sort_type == 'desc' else ''}associate__last_name"
            )
        else:
            subscriptions = subscriptions.order_by(f"{'-' if sort_type == 'desc' else ''}{sort_field}")

        # apply the sort by lastname asc or desc if one of the two is present
    if sort_lastname_asc_flag or sort_lastname_desc_flag:
        subscriptions = subscriptions.order_by(
            f"{'-' if sort_lastname_desc_flag == '1' else ''}associate__last_name"
        )

    subscriptions = paginator.paginate_queryset(queryset=subscriptions, request=request)

    for idx, subscription in enumerate(subscriptions):
        data[idx] = SubscriptionSerializer(subscription).data
        date_from, date_to = BalanceSheetData.get_range_from_year_and_starting_date(
            date=subscription.creation_date,
            starting_day=request.user.balance_sheet_start_day,
            starting_month=request.user.balance_sheet_start_month
        )
        data[idx]['current_year'] = date_to >= timezone.now()
        if subscription.creation_date > date_to:
            data[idx]['next_years'] = True
        else:
            data[idx]['next_years'] = False
        if data[idx]['current_year']:
            data[idx]['renewal_available'] = False
        else:
            # override the date_from and date_to to get the correct renewal_available
            date_from, date_to = BalanceSheetData.get_range_from_year_and_starting_date(
                date=timezone.now(),
                starting_day=request.user.balance_sheet_start_day,
                starting_month=request.user.balance_sheet_start_month
            )
            current_year_sub = Subscription.objects.filter(
                sport_association=sport_association,
                start_date__gte=subscription.end_date,
                archived=False,
                associate__tax_code__iexact=subscription.associate.tax_code
            ).count()
            data[idx]['renewal_available'] = current_year_sub == 0
        if subscription.medical is not None:
            if subscription.medical.document:
                data[idx]['medical_document'] = subscription.medical.document.document_id
    meta = {
        "total": paginator.page.paginator.count,
        "page": paginator.page.number,
        "pages": paginator.page.paginator.num_pages,
        "perpage": paginator.page.paginator.per_page,
        "rowIds": [subscription.subscription_id for subscription in subscriptions]
    }
    return Response({'data': data, 'meta': meta}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def subscription_associates_draft_list(request):

    logger.info("subscription_associates_draft_list")

    is_athlete = True if User.ATHLETE == request.user.role else False
    associates_draft = None
    if is_athlete:
        return Response(status=status.HTTP_401_UNAUTHORIZED)
    else:
        sport_association = SportAssociation.objects.get(user=request.user)
        associates_draft = AssociateImportDraft.objects.filter(sport_association=sport_association)
    data = {}
    if associates_draft is not None:
        for idx, associate in enumerate(associates_draft):
            data[idx] = associate.data
            data[idx]['associate_import_draft_id'] = associate.associate_import_draft_id
            data[idx]['valid'] = associate.valid


    # IMPORTING STATUS
    importing_status = AssociateImportDraftStatus.objects.filter(
        sport_association=request.user.sport_association,
        status_type=AssociateImportDraftStatus.IMPORTING
    ).order_by('-creation_date').first()

    # if older than 5 minutes set to false
    if importing_status is not None:
        # relative delta between now and creation date
        delta = timezone.now() - importing_status.creation_date
        if delta.total_seconds() > 3600:
            importing_status.doing = False
            importing_status.save()

    if importing_status is None:
        importing = False
    else:
        importing = importing_status.doing

    # APPROVING STATUS
    approving_status = AssociateImportDraftStatus.objects.filter(
        sport_association=request.user.sport_association,
        status_type=AssociateImportDraftStatus.APPROVING
    ).order_by('-creation_date').first()

    if approving_status is not None:
        # relative delta between now and creation date
        delta = timezone.now() - approving_status.creation_date
        if delta.total_seconds() > 60*5:
            approving_status.doing = False
            approving_status.save()

    if approving_status is None:
        approving = False
    else:
        approving = approving_status.doing

    return Response({'data': data, 'approving': approving, "importing": importing}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def subscription_associates_draft_edit(request, uid):

    is_athlete = True if User.ATHLETE == request.user.role else False
    data = request.data
    if is_athlete:
        return Response(status=status.HTTP_401_UNAUTHORIZED)

    logger.info("subscription_associates_draft_edit")
    is_valid_uuid(uid)

    sport_association = SportAssociation.objects.get(user=request.user)
    associates_draft = AssociateImportDraft.objects.filter(
        sport_association=sport_association,
        associate_import_draft_id=uid
    ).first()

    if associates_draft is None:
        return Response({'msg': 'not found.'}, status=status.HTTP_404_NOT_FOUND)

    expiration_date = None

    if 'edit_data' in data.keys():
        # TODO: add security check to avoid store a malicious payload
        associates_draft.data = data['edit_data']
        valid = True

        for key in SubscriptionMaps.get_mandatory_fields():
            if key not in data['edit_data']['associate'].keys():
                valid = False
            if key in data['edit_data']['associate'].keys() and \
                    (data['edit_data']['associate'][key] is None or
                     data['edit_data']['associate'][key] == ''):
                valid = False
            # this check means that al fields are valid
            else:
                now = date.today()
                born_date = dateutil.parser.parse(data['edit_data']['associate']['born_date'])
                born_date = born_date.strftime('%d/%m/%Y')
                age = relativedelta(now, datetime.strptime(born_date, '%d/%m/%Y')).years
                if age < 18:
                    try:
                        born_date_tutor = dateutil.parser.parse(data['edit_data']['associate_tutor']['born_date'])
                        born_date_tutor = born_date_tutor.strftime('%d/%m/%Y')
                        tutor_age = relativedelta(now, datetime.strptime(born_date_tutor, '%d/%m/%Y')).years
                        if tutor_age < 18:
                            valid = False
                    except Exception as e:
                        born_date_tutor = "01/01/1990"
                        logger.info(f"Cannot parse born_date_tutor, skip validation for tuttor: {e}")
        associates_draft.valid = valid
        # CHECK CERTIFICATE DATA
        if 'certificate_expiring_date' in data.keys():
            expiration_date = data['certificate_expiring_date']
        # check medical certificate
        if 'medical_certificate' not in associates_draft.data.keys():
            associates_draft.data['medical_certificate'] = {
                'medical_id': None,
                'filename': None,
                'certificate_expring_date': None
            }
            associates_draft.save()
        # get medical certificate from draft data and set expiration date
        if 'medical_id' not in associates_draft.data['medical_certificate'].keys():
            medical_certificate = None
        else:
            medical_certificate = MedicalCertificate.objects.filter(
                medical_id=associates_draft.data['medical_certificate']['medical_id']).first()
        if medical_certificate is None:
            medical_certificate = MedicalCertificate.objects.create(user=request.user)
        if expiration_date:
            medical_certificate.expiration_date = make_aware(datetime.strptime(expiration_date, '%d/%m/%Y'))
        medical_certificate.save()
        # set medical certificate to associates_draft
        associates_draft.data['medical_certificate']['medical_id'] = str(medical_certificate.medical_id)
        if expiration_date:
            associates_draft.data['medical_certificate']['certificate_expring_date'] = str(expiration_date)
        associates_draft.save()

    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def subscription_associates_draft_delete(request, uid):

    is_athlete = True if User.ATHLETE == request.user.role else False
    data = request.data
    if is_athlete:
        return Response(status=status.HTTP_401_UNAUTHORIZED)

    is_valid_uuid(uid)

    sport_association = SportAssociation.objects.get(user=request.user)
    associates_draft = AssociateImportDraft.objects.filter(
        sport_association=sport_association,
        associate_import_draft_id=uid
    ).first()

    if associates_draft:
        associates_draft.delete()

    return Response({'msg': 'successfully deleted.'}, status=status.HTTP_200_OK)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def subscription_associates_draft_bulk_delete(request):
    if User.ATHLETE == request.user.role:
        return Response(status=status.HTTP_401_UNAUTHORIZED)

    # Get list of IDs to delete from request
    draft_ids = request.data.get('associate_import_draft_ids', [])

    # Validate input
    if not isinstance(draft_ids, list):
        return Response(
            {'msg': 'La richiesta deve contenere una lista di ID da eliminare.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Bulk delete with proper filters for security
    deleted_count = AssociateImportDraft.objects.filter(
        sport_association=request.user.sport_association,
        associate_import_draft_id__in=draft_ids
    ).delete()[0]  # delete() returns tuple (count_deleted, dict_with_counts)

    return Response({
        'msg': f"{deleted_count}/{len(draft_ids)} bozze eliminate con successo.",
        'deleted_count': deleted_count,
        'total_count': len(draft_ids)
    }, status=status.HTTP_200_OK)



@api_view(['POST'])
@permission_classes([IsAuthenticated])
def subscription_associates_draft_approve(request):
    # get the list of associates_draft_id to approve by the request body
    data = request.data

    if User.ATHLETE == request.user.role:
        return Response(status=status.HTTP_401_UNAUTHORIZED)

    if 'associate_import_draft_ids' not in data.keys():
        return Response({'msg': 'bad request, missing drafts to approve.'}, status=status.HTTP_400_BAD_REQUEST)

    drafts = data['associate_import_draft_ids']
    only_valid = True #data['only_valid']
    subscription_details = data['subscription_details']

    if 'type' not in subscription_details.keys():
        subscription_details['type'] = 1
    if 'plan_id' not in subscription_details.keys():
        subscription_details['plan_id'] = None
    if 'membership_plan_id' not in subscription_details.keys():
        subscription_details['membership_plan_id'] = None
    if 'role' not in subscription_details.keys():
        subscription_details['role'] = 1

    all = False if 'all' not in data.keys() else data['all']

    if all:
        associates_draft = AssociateImportDraft.objects.filter(
            sport_association=request.user.sport_association
        )
    else:
        # check if all the drafts are valid, omitting the ones that are not valid
        valid_drafts = []
        for draft in drafts:
            try:
                is_valid_uuid(draft)
                valid_drafts.append(draft)
            except ValidationError:
                pass
        associates_draft = AssociateImportDraft.objects.filter(
            sport_association=request.user.sport_association,
            associate_import_draft_id__in=valid_drafts
        )
    to_approve = associates_draft.count()
    if only_valid:
        associates_draft = associates_draft.filter(valid=True)
        if associates_draft.count() == 0:
            return Response({'msg': 'Dati mancanti, iscrizione non approvata.'}, status=status.HTTP_400_BAD_REQUEST)

    AssociateImportDraftStatus.objects.filter(
        sport_association=request.user.sport_association,
        status_type=AssociateImportDraftStatus.APPROVING
    ).delete()

    AssociateImportDraftStatus.objects.create(
        sport_association=request.user.sport_association,
        doing=True,
        status_type=AssociateImportDraftStatus.APPROVING
    )
    approved = 0
    for draft in associates_draft.iterator():
        # rename associate key to associate_data
        # rename associate_tutor key to associate_tutor_data
        draft.data['associate_data'] = draft.data['associate']
        draft.data['associate_tutor_data'] = draft.data['associate_tutor']
        draft.data['new_user_account'] = {
            "new_member": False,
        }
        draft.data['signature'] = {
            "there_is_signature": False,
            "data": '',
        }
        draft.data['associate_data']['type'] = subscription_details['type']
        draft.data['associate_data']['role'] = subscription_details['role']
        draft.data['membership_plan_id'] = subscription_details['membership_plan_id']
        draft.data['plan_id'] = subscription_details['plan_id']
        try:
            ok, fresh_sub = create_subscription(
                draft.data,
                request.user,
                request.headers.get('authorization'
            ), is_athlete_request=False)
            if ok:
                draft.delete()
                approved += 1
        except Exception as e:
            try:
                if 'detail' in e and 'code' in e.detail and e.detail['code'] == "409":
                    draft.delete()
                    approved += 1
                logger.error(f"Error: {e}")
                pass
            except Exception as e:
                logger.error(f"Error: {e}")
                pass

    AssociateImportDraftStatus.objects.filter(
        sport_association=request.user.sport_association,
        status_type=AssociateImportDraftStatus.APPROVING
    ).delete()

    return Response({'msg': f"{approved} su {to_approve} atleti approvati.", "approved": approved}, status=status.HTTP_200_OK)


def _get_export_columns(is_simplified):
    """Get column headers for export based on mode."""
    if is_simplified:
        return [
            'data', 'nome', 'cognome', 'data di nascita', 'codice fiscale', 'minore', 'stato',
            'certificato medico', 'scadenza certificato medico', 'validità certificato medico',
            'firmata', 'email', 'iscritto il', 'scadenza iscrizione', 'numero tessera',
            'tipo tessera', 'tags'
        ]
    else:
        return [
            'stato iscrizione', 'certificato medico', 'scadenza certificato medico',
            'validità certificato medico', 'firma', 'nome', 'cognome', 'sesso',
            'codice fiscale', 'data di nascita', 'città di nascita', 'città di residenza',
            'indirizzo di residenza', 'cap di residenza', 'email', 'nome contatto telefono',
            'telefono', 'nome contatto telefono 2', 'telefono 2', 'nome contatto telefono 3',
            'telefono 3', 'nome contatto telefono 4', 'telefono 4', 'nome tutore',
            'cognome tutore', 'sesso tutore', 'codice fiscale tutore', 'data di nascita tutore',
            'città di nascita tutore', 'città di residenza tutore', 'indirizzo di residenza tutore',
            'cap di residenza tutore', 'email tutore', 'nome contatto telefono tutore',
            'telefono tutore', 'nome contatto telefono 2 tutore', 'telefono 2 tutore',
            'nome contatto telefono 3 tutore', 'telefono 3 tutore', 'nome contatto telefono 4 tutore',
            'telefono 4 tutore', 'iscritto il', 'scadenza iscrizione', 'numero tessera',
            'tipo tessera', 'tags'
        ]


def _get_medical_status(subscription):
    """Get medical certificate status string."""
    if subscription.medical is None or subscription.medical.expiration_date is None:
        return ''
    if subscription.medical.expiration_date >= date.today():
        return 'valido'
    return 'esente' if subscription.associate.is_minor else 'scaduto'


def _build_simplified_row(subscription):
    """Build a simplified export row for a subscription."""
    return [
        str(subscription.creation_date.strftime('%Y-%m-%d') if subscription.creation_date else ''),
        subscription.associate.first_name,
        subscription.associate.last_name,
        str(subscription.associate.born_date.strftime('%Y-%m-%d') if subscription.associate.born_date else ''),
        subscription.associate.tax_code,
        'minorenne' if subscription.associate.is_minor else 'maggiorenne',
        SubscriptionMaps.STATUS_DICT[subscription.status_flag],
        'si' if subscription.medical else 'no',
        subscription.medical.expiration_date.strftime('%Y-%m-%d') if subscription.medical and subscription.medical.expiration_date else '',
        _get_medical_status(subscription),
        'si' if subscription.has_signature else 'no',
        subscription.associate.email,
        subscription.start_date.strftime('%Y-%m-%d') if subscription.start_date else '',
        subscription.end_date.strftime('%Y-%m-%d') if subscription.end_date else '',
        subscription.subscription_number if subscription.subscription_number else '',
        subscription.subscription_type if subscription.subscription_type else '',
        ", ".join([tag.tag_name for tag in subscription.tags.all()]),
    ]


def _build_full_row(subscription):
    """Build a full export row for a subscription with tutor info."""
    tutor = subscription.associate.main_tutor
    return [
        SubscriptionMaps.STATUS_DICT[subscription.status_flag],
        'si' if subscription.medical else 'no',
        subscription.medical.expiration_date.strftime('%Y-%m-%d') if subscription.medical and subscription.medical.expiration_date else '',
        _get_medical_status(subscription),
        'si' if subscription.has_signature else 'no',
        subscription.associate.first_name,
        subscription.associate.last_name,
        subscription.associate.sex,
        subscription.associate.tax_code,
        subscription.associate.born_date.strftime('%Y-%m-%d') if subscription.associate.born_date else '',
        subscription.associate.born_city,
        subscription.associate.address_city,
        subscription.associate.address,
        subscription.associate.address_cap,
        subscription.associate.email,
        subscription.associate.phone_label,
        subscription.associate.phone,
        subscription.associate.phone_2_label,
        subscription.associate.phone_2,
        subscription.associate.phone_3_label,
        subscription.associate.phone_3,
        subscription.associate.phone_4_label,
        subscription.associate.phone_4,
        tutor.first_name if tutor else '',
        tutor.last_name if tutor else '',
        tutor.sex if tutor else '',
        tutor.tax_code if tutor else '',
        tutor.born_date.strftime('%Y-%m-%d') if tutor and tutor.born_date else '',
        tutor.born_city if tutor else '',
        tutor.address_city if tutor else '',
        tutor.address if tutor else '',
        tutor.address_cap if tutor else '',
        tutor.email if tutor else '',
        tutor.phone_label if tutor else '',
        tutor.phone if tutor else '',
        tutor.phone_2_label if tutor else '',
        tutor.phone_2 if tutor else '',
        tutor.phone_3_label if tutor else '',
        tutor.phone_3 if tutor else '',
        tutor.phone_4_label if tutor else '',
        tutor.phone_4 if tutor else '',
        subscription.start_date.strftime('%Y-%m-%d') if subscription.start_date else '',
        subscription.end_date.strftime('%Y-%m-%d') if subscription.end_date else '',
        subscription.subscription_number if subscription.subscription_number else '',
        subscription.subscription_type if subscription.subscription_type else '',
        ", ".join([tag.tag_name for tag in subscription.tags.all()]),
    ]


def _export_to_excel(dataframe, cols):
    """Export dataframe to Excel format."""
    f = BytesIO()
    writer = pandas.ExcelWriter(f, engine='xlsxwriter')
    dataframe.to_excel(writer, sheet_name='Libro Soci')
    worksheet = writer.sheets['Libro Soci']
    for i, col in enumerate(cols):
        worksheet.set_column(i, i, len(col) + 10)
    writer.close()
    return base64.b64encode(f.getvalue())


def _export_to_csv(rows, cols):
    """Export rows to CSV format."""
    f = StringIO()
    csv.writer(f).writerow(cols)
    csv.writer(f).writerows(rows)
    return base64.b64encode(f.getvalue().encode())


@api_view(['POST', 'GET'])
@permission_classes([IsAuthenticated])
def subscription_list_export(request):

    logger.info("subscription_list_export")
    export_mode = request.GET.get('filetype', 'csv')

    if User.ATHLETE == request.user.role:
        raise Exception("Cannot export info for athlete")

    sport_association = SportAssociation.objects.get(user=request.user)
    subscriptions = Subscription.objects.filter(
        sport_association=sport_association,
        archived=False
    ).select_related(
        'associate',
        'user',
        'medical',
        'sport_association'
    ).prefetch_related(
        'tags',
        'associate__tutors'
    ).order_by('-creation_date').iterator(chunk_size=2000)

    is_simplified = 'simplified' in export_mode
    cols = _get_export_columns(is_simplified)
    rows = [_build_simplified_row(sub) if is_simplified else _build_full_row(sub) for sub in subscriptions]

    df = pandas.DataFrame(rows)
    df.columns = cols

    data = {
        'filename': "[{}] {} a {}.{}".format(
            date.today().strftime("%Y-%m-%d"),
            "ISCRIZIONI",
            sport_association.denomination,
            'xls' if 'xls' in export_mode else 'csv'
        )
    }

    if 'xls' in export_mode:
        data['type'] = 'xls'
        data['file'] = _export_to_excel(df, cols)
    else:
        data['type'] = 'csv'
        data['file'] = _export_to_csv(rows, cols)

    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def subscription_info(request, uid):

    is_valid_uuid(uid)

    # check if the user is an athlete
    if request.user.role == User.ATHLETE:
        # Get unique tax codes from associates of user's subscriptions
        user_subscriptions = Subscription.objects.filter(user=request.user)
        tax_codes = user_subscriptions.filter(
            associate__tax_code__isnull=False
        ).values_list('associate__tax_code', flat=True).distinct()

        # Get all subscription IDs for users with those tax codes, plus original user subscriptions
        allowed_subscription_ids = list(Subscription.objects.filter(
            Q(user=request.user) | Q(associate__tax_code__in=tax_codes)
        ).values_list('subscription_id', flat=True))
        
        allowed_subscription_ids = [str(id) for id in allowed_subscription_ids]
        
        logger.info(f"allowed_subscription_ids: {allowed_subscription_ids}")

        # Check if the requested subscription ID is in the allowed list
        if uid not in allowed_subscription_ids:
            raise PermissionDenied("Subscription not found.")

        # Fetch the sport_association with optimized queries
        sport_association = Subscription.objects.filter(subscription_id=uid).values('sport_association_id').first()
        if sport_association is None:
            raise PermissionDenied("Sport association id not found.")
        
        logger.info(f"sport_association_id: {sport_association['sport_association_id']}")
        sport_association = SportAssociation.objects.filter(sport_association_id=str(sport_association['sport_association_id'])).first()
        if sport_association is None:
            raise PermissionDenied("Sport association not found.")
    else:
        sport_association = SportAssociation.objects.get(user=request.user)

    subscription = Subscription.objects.select_related(
        'associate', 'medical', 'medical__document', 'document_pdf', 'sport_association', 'user'
    ).prefetch_related(
        'tags',
        Prefetch('subscriptionfile_set',
            queryset=SubscriptionFile.objects.select_related('document')
        )
    ).filter(subscription_id=uid).first()
    if subscription is None:
        return Response({'msg': 'Subscription not found.'}, status=status.HTTP_404_NOT_FOUND)
    if subscription.sport_association.sport_association_id != sport_association.sport_association_id:
        raise PermissionDenied("User not allowed.")

    course_subs = CourseSubscription.objects.select_related(
        'course', 'payment'
    ).prefetch_related(
        Prefetch('coursesubscriptioninstallment_set',
            queryset=CourseSubscriptionInstallment.objects.select_related('payment')
        )
    ).filter(subscription=subscription)

    # Build dict for O(1) lookup
    course_subs_dict = {str(cs.course_id): cs for cs in course_subs}

    courses = CourseDetailSerializer([cs.course for cs in course_subs], many=True).data

    for idx, course in enumerate(courses):
        course_sub = course_subs_dict[course['course_id']]  # O(1) lookup!
        courses[idx]['course_subscription'] = CourseSubscriptionOverviewSerializer(course_sub).data
        if course_sub.multi_payments:
            courses[idx]['installments'] = [
                {
                    'course_subscription_installment_id': i.course_subscription_installment_id,
                    'payment_id': i.payment.payment_id if i.payment else None,
                    "amount": i.amount,
                    "payment_date": i.payment_date,
                    "paid": i.payment.paid if i.payment else i.paid
                }
                for i in course_sub.coursesubscriptioninstallment_set.all()
            ]

    # order courses by courses[idx]['creation_date'] desc
    courses = sorted(courses, key=lambda x: x['creation_date'], reverse=True)

    if SubscriptionInfoSerializer(subscription).data['document_pdf'] is None:
        # print the document
        auth_token = request.headers.get('authorization')
        print_document_subscription.delay(str(subscription.subscription_id), auth_token)

    data = {
        "info": SubscriptionInfoSerializer(subscription).data,
        "courses": courses,
        "payments": []
    }

    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def subscription_payments(request, uid):
    if request.user.role == User.ATHLETE:
        return Response(status=status.HTTP_401_UNAUTHORIZED)

    is_valid_uuid(uid)
    paginator = KTDatatablePagination()
    # get general search
    general_search = request.GET.get('query[generalSearch]', None)
    paid = request.GET.get('query[paid]', None)
    expense = request.GET.get('query[expense]', None)
    sort_field = request.GET.get('sort[field]', None)
    sort_type = request.GET.get('sort[sort]', None)
    subject = request.GET.get('query[subject]', None)
    type = request.GET.get('query[type]', None)
    course_subscription_id = request.GET.get('course_subscription_id', None)
    archived = request.GET.get('query[archived]', False)

    sport_association = SportAssociation.objects.get(user=request.user)
    subscription = Subscription.objects.filter(subscription_id=uid).first()
    if subscription.sport_association.sport_association_id != sport_association.sport_association_id:
        raise PermissionDenied("User not allowed.")

    payments = Payment.objects.filter(
        sport_association=sport_association,
    ).filter(
        Q(associate=subscription.associate) |
        (Q(associate__tax_code__iexact=subscription.associate.tax_code)
         & Q(associate__isnull=False)
         & ~Q(associate__tax_code__exact='')  # Excludes empty strings
         & ~Q(associate__tax_code__regex=r'^\s*$'))  # Excludes whitespace-only strings
    ).order_by('-creation_date')

    if subject is not None:
        payments = payments.filter(subject=subject)

    if type is not None:
        payments = payments.filter(type=type)

    if paid is not None:
        if paid == 'true':
            payments = payments.filter(paid=True)
        else:
            payments = payments.filter(paid=False)
    if expense is not None:
        if expense == 'true':
            payments = payments.filter(expense=True)
        else:
            payments = payments.filter(expense=False)
        # filter for general search
    if general_search:
        # check if general_search is a date
        try:
            date = datetime.strptime(general_search, '%d/%m/%Y').strftime('%Y-%m-%d')
            general_search = date
        except ValueError:
            pass

        try:
            payments = payments.filter(
                Q(amount__icontains=float(general_search.replace(',', '.'))) |
                Q(amount__exact=float(general_search.replace(',', '.')))
            )
        except Exception as e:
            payments = payments. \
                annotate(full_name=Concat('associate__first_name', V(' '), 'associate__last_name')) \
                .filter(
                Q(payment_id__icontains=general_search) |
                Q(notes__icontains=general_search) |
                Q(meta__icontains=general_search) |
                Q(description__icontains=general_search) |
                Q(payment_category__name__icontains=general_search) |
                Q(meta__icontains=general_search) |
                Q(full_name__icontains=general_search) |
                Q(supplier__name__icontains=general_search) |
                Q(instructor__first_name__icontains=general_search) |
                Q(instructor__last_name__icontains=general_search) |
                Q(associate__first_name__icontains=general_search) |
                Q(associate__last_name__icontains=general_search) |
                Q(associate__tax_code__icontains=general_search) |
                Q(payment_date__icontains=general_search) |
                Q(type__icontains=general_search) |
                Q(subject__icontains=general_search) |
                Q(creation_date__icontains=general_search) |
                Q(custom_accounts__name__icontains=general_search) |
                Q(coursesubscription__course__title__icontains=general_search) |
                Q(coursesubscriptioninstallment__course_subscription__course__title__icontains=general_search)
            )
    # sort by field
    if sort_field:
        payments = payments.order_by(f"{'-' if sort_type == 'desc' else ''}{sort_field}")

    payments = paginator.paginate_queryset(queryset=payments, request=request)
    payments_data = PaymentOptimizedSerializer(payments, many=True).data

    for idx, payment_data in enumerate(payments_data):
        payment_data['is_carnet'] = False
        if payment_data['subject'] == Payment.COURSE:
            course_payment = CourseSubscription.objects.all_objects().filter(payment_id=payment_data['payment_id']).first()
            if course_payment is None:
                course_payment = CourseSubscriptionInstallment.objects.all_objects().filter(payment=payment_data['payment_id']).first()
                if course_payment:
                    course_payment = course_payment.course_subscription
                    payment_data['course_subscriptions'] = {
                        'course_id': course_payment.course.course_id,
                        'title': course_payment.course.title,
                        'deleted': course_payment.course.deleted
                    }
                else:
                    # check if it's a carnet payment
                    carnet_payment = CarnetSubscription.objects.filter(payment=payment_data['payment_id']).first()
                    if carnet_payment:
                        payment_data['is_carnet'] = True
                        payment_data['carnet'] = {
                            'course_id': carnet_payment.carnet_id.carnet_id,
                            'title': carnet_payment.carnet_id.title,
                        }
            else:
                payment_data['course_subscriptions'] = {
                    'course_id': course_payment.course.course_id,
                    'title': course_payment.course.title,
                    'deleted': course_payment.course.deleted
                }

    return Response({
        'data': payments_data,
        'meta': {
            "total": paginator.page.paginator.count,
            "page": paginator.page.number,
            "pages": paginator.page.paginator.num_pages,
            "perpage": paginator.page.paginator.per_page,
            "rowIds": [payment.payment_id for payment in payments]
        }
    }, status=status.HTTP_200_OK)


@api_view(['GET', 'POST'])
def subscription_card(request, uid):

    is_valid_uuid(uid)
    if request.method == 'GET':
        # get token from query params
        token = request.GET.get('token', None)

        if token is None or token == '':
            return Response({'msg': 'il token è scaduto'}, status=status.HTTP_400_BAD_REQUEST)

        # verify the token
        subscription_token = SubscriptionToken.objects.filter(token=token, subscription_id=uid).first()
        if subscription_token is None:
            return Response({'msg': 'il token è scaduto'}, status=status.HTTP_400_BAD_REQUEST)

        # check if the token is valid
        if subscription_token.expiration_date < timezone.now():
            # delete the token
            subscription_token.delete()
            return Response({'msg': 'il token è scaduto'}, status=status.HTTP_400_BAD_REQUEST)

        # get the subscription
        subscription = Subscription.objects.filter(subscription_id=uid).select_related(
            'associate', 'sport_association', 'sport_association__user').first()

        membership_card_configuration: SportAssociationMembershipCardConfiguration = SportAssociationMembershipCardConfiguration.objects.filter(sport_association=subscription.sport_association).first()
        if membership_card_configuration is None:
            membership_card_configuration = SportAssociationMembershipCardConfiguration(sport_association=subscription.sport_association)
            membership_card_configuration.save()


        data = {
            "member": {
                "subscription_number": subscription.subscription_number,
                "subscription_type": subscription.subscription_type,
                "subscription_end_date": subscription.end_date,
                "associate": {
                    "first_name": subscription.associate.first_name,
                    "last_name": subscription.associate.last_name,
                    "avatar": None,
                },
                "sport_association": {
                    "denomination": subscription.sport_association.denomination,
                    "user": {
                        "avatar_image": subscription.sport_association.user.avatar_image,
                    },
                    "membership_card_configuration": {
                        "emit_only_on_approval": membership_card_configuration.emit_only_on_approval,
                        "customized_template": membership_card_configuration.customized_template if membership_card_configuration.customized_template is not None else {}
                    }
                }
            }
        }

        return Response({'member': data['member']}, status=status.HTTP_200_OK)
    elif request.method == 'POST':
        # create the SubscriptionToken
        token = SubscriptionToken.objects.create(
            subscription_id=uid,
            expiration_date=timezone.now() +  timedelta(days=3)
        )

        return Response({'token': token.token}, status=status.HTTP_200_OK)
    return Response({'msg': 'ok'}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def subscription_approve(request, uid):

    is_valid_uuid(uid)

    logger.info("subscription_approve")
    sport_association = SportAssociation.objects.get(user=request.user)
    subscription = Subscription.objects.filter(subscription_id=uid).first()
    if subscription is None:
        return Response({'msg': 'Subscription not found.'}, status=status.HTTP_404_NOT_FOUND)
    if subscription.sport_association.sport_association_id != sport_association.sport_association_id:
        raise PermissionDenied("User not allowed.")

    if subscription.status_flag != Subscription.ACCEPTED: #\
        #and subscription.status_flag != Subscription.REJECTED:
        subscription.status_flag = Subscription.ACCEPTED
        subscription.acceptance_date = timezone.now()
        # check if there are past subscription for this associate that are approved and have the signature
        if not subscription.has_signature:
            past_subscriptions = Subscription.objects.filter(
                associate=subscription.associate,
                status_flag__in=[Subscription.ACCEPTED, Subscription.PENDING],
            ).filter(
                Q(signature_storage_key__isnull=False) | Q(signature_url__isnull=False)
            ).exclude(subscription_id=subscription.subscription_id).order_by('-creation_date')
            last_subscription = past_subscriptions.first()
            if last_subscription and last_subscription.has_signature:
                subscription.signature_storage_key = last_subscription.signature_storage_key
                subscription.signature_url = last_subscription.signature_url

        subscription.save()
    else:
        raise PermissionDenied("User cannot change the status of the subscription.")

    data = {"msg": "subscription approved."}
    subscription_approved.send(sender=subscription.__class__, subscription=subscription)

    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def subscription_update(request, uid):
    is_valid_uuid(uid)
    logger.info("subscription_update")

    # Authorization check
    if request.user.role == User.ATHLETE:
        subscription = Subscription.objects.filter(user=request.user, subscription_id=uid).first()
        if subscription is None:
            raise PermissionDenied("User not allowed.")
    else:
        sport_association = SportAssociation.objects.get(user=request.user)
        subscription = Subscription.objects.filter(subscription_id=uid).first()
        if subscription is None:
            raise PermissionDenied("Subscription not found.")
        if subscription.sport_association.sport_association_id != sport_association.sport_association_id:
            raise PermissionDenied("User not allowed.")

    # Prepare data
    data = request.data
    if 'start_date' in data and data['start_date'] is not None:
        validated_start_date = check_date(data['start_date'])
        if validated_start_date is None:
            raise ValidationError("Invalid start_date format")
        data['start_date'] = validated_start_date.strip().capitalize()
    if 'end_date' in data and data['end_date'] is not None:
        validated_end_date = check_date(data['end_date'])
        if validated_end_date is None:
            raise ValidationError("Invalid end_date format")
        data['end_date'] = validated_end_date.strip().capitalize()
    if 'type' in data and data['type'] is not None:
        data['type'] = int(data['type']) if data['type'] != '' else Subscription.ASSOCIATE_AND_MEMBER
    if 'role' in data and data['role'] is not None:
        data['role'] = int(data['role']) if data['role'] != '' else Subscription.SOCIO_ORDINARIO
    if 'subscription_number' in data and data['subscription_number'] is not None:
        data['subscription_number'] = str(data['subscription_number']).strip()
    if 'subscription_type' in data and data['subscription_type'] is not None:
        data['subscription_type'] = str(data['subscription_type']).strip()

    # Update subscription using service
    SubscriptionService.update_subscription(subscription, data)

    # Trigger document printing
    auth_token = request.headers.get('authorization')
    print_document_subscription.delay(str(subscription.subscription_id), auth_token)

    return Response({'data': {"msg": "subscription updated."}}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def subscription_edit(request, uid):
    """
    this endpoint temporalily is used to update the associate tutor data
    """
    is_valid_uuid(uid)
    data = request.data

    logger.info("subscription_edit")
    # subscription = Subscription.objects.filter(subscription_id=uid).first()
    associate = Associate.objects.filter(associate_id=uid).first()
    if associate.user is None or associate.user.user_id != request.user.user_id:
        raise PermissionDenied("User not allowed.")

    now = date.today()
    age = relativedelta(now, datetime.strptime(str(associate.born_date), '%Y-%m-%d')).years
    if age >= 18:
        raise ValidationError("Cannot edit associate tutor data for associate over 18 years old")

    associate_tutor_data = AssociateSerializer(data=data['associate_tutor_data'])
    tutor_age = relativedelta(
        now,
        datetime.strptime(str(associate_tutor_data.initial_data['born_date']), '%d/%m/%Y')
    ).years

    if tutor_age < 18:
        raise ValidationError("Cannot edit associate tutor data for associate tutor under 18 years old")

    if associate_tutor_data.is_valid(raise_exception=True):
        tutor = associate_tutor_data.save()
        if associate.main_tutor is not None:
            # remove the relation and the data
            associate.main_tutor.delete()
        associate.tutor = tutor
        associate.is_minor = True
        associate.save()

    data = {"msg": "subscription associate edited."}

    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def subscription_delete(request, uid):

    try:
        logger.info("subscription_delete")

        sport_association = SportAssociation.objects.get(user=request.user)
        subscription = Subscription.objects.filter(subscription_id=uid).first()
        if subscription is None:
            return Response({'msg': 'Subscription not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Delete subscription using service (handles all cleanup)
        SubscriptionService.delete_subscription(subscription, sport_association)

        return Response({'data': {"msg": "subscription delete."}}, status=status.HTTP_200_OK)
    except PermissionDenied as e:
        logger.exception(e.detail)
        return Response({"exception": e.detail}, status=status.HTTP_403_FORBIDDEN)
    except Exception as e:
        logger.exception(e)
        return Response({"exception": e}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def subscription_archive(request, uid):
    try:
        logger.info("subscription_archive")
        sport_association = SportAssociation.objects.get(user=request.user)
        subscription = Subscription.objects.filter(subscription_id=uid).first()

        if subscription:
            if subscription.sport_association.sport_association_id != sport_association.sport_association_id:
                raise PermissionDenied("User not allowed.")
        else:
            return Response({'exception': 'Subscription not found'}, status=status.HTTP_404_NOT_FOUND)

        subscription.archived = not subscription.archived


        if subscription.archived:

            # getting the course subscription (it might be empty)
            course_subscriptions = CourseSubscription.objects.filter(subscription=subscription)
            # checking all the course subscriptions one by one
            for course_subscription in course_subscriptions:
                # there are some associated payments
                if course_subscription.payment is not None and course_subscription.payment.paid is False:
                    # there are some associated invoices to the payment
                    if course_subscription.payment.invoice is not None:
                        # delete the associated invoice
                        course_subscription.payment.invoice.delete()
                    # finally delete the payment
                    course_subscription.payment.archived = True
                    course_subscription.payment.save()

                # check if there are installments
                if course_subscription.multi_payments:
                    # retrieve all installments
                    installments = CourseSubscriptionInstallment.objects.filter(course_subscription=course_subscription)
                    for installment in installments:
                        # check if installment was paid
                        if installment.payment is not None and installment.payment.paid is False:
                            # delete the payment
                            installment.payment.archived = True
                            installment.payment.save()
                            # installment has an associated invoice
                            if installment.payment.invoice is not None:
                                installment.payment.invoice.delete()
                            # finally delete the installment
                            # installment.delete()
                        installment.archived = True
                        installment.save()
                # finally unsubscribe from the course
                course_subscription.archived = True
                course_subscription.save()
            # check if there is a payment for the subscription
            if subscription.payment is not None and subscription.payment.paid is False:
                # check if there is an invoice
                if subscription.payment.invoice is not None:
                    # delete the invoice
                    subscription.payment.invoice.delete()
                # delete the subscription payment
                subscription.payment.archived = True
                subscription.payment.save()

            subscription.archived = True
            subscription.save()

            # get all remaining unpaid payments and not archived and archive them
            payments = Payment.objects.filter(
                sport_association=sport_association,
                associate=subscription.associate,
                creation_date__range=[subscription.start_date, subscription.end_date],
                archived=False,
                paid=False
            ).iterator(chunk_size=100)

            for payment in payments:
                payment.archived = True
                payment.save()

        subscription.save()

        data = {"msg": "subscription archival completed."}

        return Response({'data': data}, status=status.HTTP_200_OK)
    except PermissionDenied as e:
        logger.exception(e.detail)
        return Response({"exception": e.detail}, status=status.HTTP_403_FORBIDDEN)
    except Exception as e:
        logger.exception(e)
        return Response({"exception": e}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def subscription_reject(request, uid):

    is_valid_uuid(uid)

    logger.info("subscription_reject")
    sport_association = SportAssociation.objects.get(user=request.user)
    subscription = Subscription.objects.filter(subscription_id=uid).first()
    if subscription is None:
        return Response({'msg': 'Subscription not found.'}, status=status.HTTP_404_NOT_FOUND)
    if subscription.sport_association.sport_association_id != sport_association.sport_association_id:
        raise PermissionDenied("User not allowed.")

    logger.info(
        "subscription flag {} accepted {} rejected {}".format(subscription.status_flag, Subscription.ACCEPTED,
                                                              Subscription.REJECTED))
    if subscription.status_flag != Subscription.REJECTED:
        #subscription.status_flag != Subscription.ACCEPTED: #\
        #and subscription.status_flag != Subscription.REJECTED:
        subscription.status_flag = Subscription.REJECTED
        subscription.acceptance_date = None
        subscription.save()
    else:
        raise PermissionDenied("User cannot change the status of the subscription.")

    data = {"msg": "subscription rejected."}

    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def subscription_medical_appointments_list(request, uid):

    is_valid_uuid(uid)

    subscription = Subscription.objects.filter(
        subscription_id=uid,
        sport_association=request.user.sport_association
    ).first()

    if subscription is None:
        return Response({'data': 'no subscription found.'}, status=status.HTTP_404_NOT_FOUND)


    medical_appointments = MedicalAppointments.objects.filter(
        subscription=subscription
    ).order_by('-date')

    data = {}

    for idx, medical_appointment in enumerate(medical_appointments):
        if medical_appointment.document is None:
            print_document_medical_appointment(
                str(medical_appointment.medical_appointments_id), request.headers.get('authorization'))
        data[idx] = MedicalAppointmentsSerializer(medical_appointment).data

    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def subscription_medical_appointments_add(request, uid):

    is_valid_uuid(uid)

    subscription = Subscription.objects.filter(
        subscription_id=uid,
        sport_association=request.user.sport_association
    ).first()

    if subscription is None:
        return Response({'data': 'no subscription found.'}, status=status.HTTP_404_NOT_FOUND)

    data = request.data
    data['subscription'] = subscription.subscription_id
    medical_appointment = MedicalAppointmentsSerializer(data=request.data)

    if medical_appointment.is_valid(raise_exception=True):
        medical_appointment = medical_appointment.save()

        print_document_medical_appointment(
            str(medical_appointment.medical_appointments_id), request.headers.get('authorization'))

    return Response({'data': 'medical appointment created.'}, status=status.HTTP_200_OK)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def subscription_medical_appointments_delete(request, uid, medical_appointments_id):

    is_valid_uuid(uid)
    is_valid_uuid(medical_appointments_id)

    medical_appointment = MedicalAppointments.objects.filter(
        medical_appointments_id=medical_appointments_id,
        subscription=uid,
        subscription__sport_association=request.user.sport_association
    )

    if not medical_appointment.exists():
        return Response({'data': 'no medical appointment found.'}, status=status.HTTP_404_NOT_FOUND)

    medical_appointment.delete()

    return Response({'data': 'medical appointment created.'}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def subscription_medical_certificate_upload(request, uid):

    logger.info("upload_medical_certificate")

    try:
        medical_certificate_file = request.data.get('medical_certificate')

        # Upload and create medical certificate using service
        medical_certificate_doc, document, output_dict = MedicalCertificateService.upload_medical_certificate(
            medical_certificate_file, request.user
        )

        # Check if subscription or draft
        subscription = Subscription.objects.filter(subscription_id=uid).first()
        if subscription is None:
            # Handle draft
            draft = AssociateImportDraft.objects.filter(associate_import_draft_id=uid).first()
            if draft is None:
                raise TypeError("subscription not found!")

            MedicalCertificateService.attach_certificate_to_draft(
                medical_certificate_doc, document, draft
            )
            return Response({
                'msg': 'updated draft',
                'expiring_date': output_dict['expiring_date']
            }, status=status.HTTP_200_OK)
        else:
            # Handle subscription
            subscription, expiring_date_str = MedicalCertificateService.attach_certificate_to_subscription(
                medical_certificate_doc, subscription
            )
            return Response({
                'medical': subscription.medical.document.document_id,
                'expiring_date': expiring_date_str
            }, status=status.HTTP_200_OK)

    except TypeError as e:
        logger.exception(e)
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        logger.exception(e)
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def subscription_import_status(request):
    # get status

    import_status = AssociateImportDraftStatus.objects.filter(
        sport_association=request.user.sport_association,
        status_type=AssociateImportDraftStatus.IMPORTING
    ).order_by('-creation_date').first()

    if import_status is None:
        importing = False
    else:
        importing = import_status.doing

    return Response({'importing': importing}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def subscription_medical_certificate_set_certificate_expiration(request, uid):

    logger.info("set_certificate_expiration")

    data = request.data
    expiration_date = data['certificate_expiring_date']
    subscription = Subscription.objects.filter(subscription_id=uid).first()
    if subscription is None:
        # check if is a draft
        subscription = AssociateImportDraft.objects.filter(associate_import_draft_id=uid).first()
        if subscription is None:
            raise TypeError("subscription not found!")
        else:
            if 'medical_certificate' not in subscription.data.keys():
                subscription.data['medical_certificate'] = {
                    'medical_id': None,
                    'filename': None,
                    'certificate_expring_date': None
                }
                subscription.save()
            # get medical certificate from draft data and set expiration date
            if 'medical_id' not in subscription.data['medical_certificate'].keys():
                medical_certificate = None
            else:
                medical_certificate = MedicalCertificate.objects.filter(
                    medical_id=subscription.data['medical_certificate']['medical_id']).first()
            if medical_certificate is None:
                medical_certificate = MedicalCertificate.objects.create(user=request.user)
            medical_certificate.expiration_date = make_aware(datetime.strptime(expiration_date, '%d/%m/%Y'))
            medical_certificate.save()
            # set medical certificate to subscription
            subscription.data['medical_certificate']['medical_id'] = str(medical_certificate.medical_id)
            subscription.data['medical_certificate']['certificate_expring_date'] = str(expiration_date)
            subscription.save()
            # and we are not sending the email to the user because the subscription is not created yet
            return Response({'msg': f"Medical certificate update with date {medical_certificate.expiration_date}"}, status=status.HTTP_200_OK)
    if subscription.medical is None:
        # create empty medical certificate
        medical_certificate = MedicalCertificate.objects.create(user=request.user)
        # generate a fake image document with the medical certificate expiration date and data
        filename = f"medical_certificate_{subscription.associate.first_name}_{subscription.associate.last_name}.png"

        # create document
        image_png = generate_image_with_text(
            f'''
{subscription.associate.first_name} {subscription.associate.last_name}\n
\n
Questa certificazione è valida fino al {make_aware(datetime.strptime(expiration_date, '%d/%m/%Y'))}\n
\n
\n
Attenzione: questo è un placeholder, il certificato medico deve essere caricato in seguito.
            '''
        )

        document = Document.objects.create(filename=filename)
        document.save()

        storing_path = os.path.join(STORAGE_DIR, str(document.creation_date.timestamp()), str(document.document_id))
        file = os.path.join(storing_path, document.filename)

        # store image_png in file
        # image png is Image from PIL
        f = BytesIO()
        image_png.save(f, format='PNG')
        f.seek(0)

        django_file = ContentFile(f.read(), name=filename)

        # save the file
        default_storage.save(file, django_file)

        medical_certificate.document = document

        # assign medical certificate to subscription
        subscription.medical = medical_certificate
        subscription.save()
    else:
        # get medical certificate
        medical_certificate = subscription.medical
    # set expiration date
    if expiration_date is not None:
        medical_certificate.expiration_date = make_aware(datetime.strptime(expiration_date, '%d/%m/%Y'))
    else:
        subscription.medical = None
        subscription.save()
        medical_certificate.document.delete()  # delete the document
        medical_certificate.delete()
        return Response({'msg': 'Medical certificate removed'}, status=status.HTTP_200_OK)
    medical_certificate.save()

    #send_updated_certificate_email.delay(subscription.subscription_id)

    return Response({'msg': f"Medical certificate update with date {subscription.medical.expiration_date}"}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def subscription_medical_certificate_edit(request, uid):

    is_valid_uuid(uid)

    data = request.data
    subscription = Subscription.objects.filter(subscription_id=uid).first()

    if subscription is None:
        return Response({'exception': 'Subscription not found'}, status=status.HTTP_404_NOT_FOUND)

    if subscription.medical is None:
        return Response({'exception': 'Medical certificate not found'}, status=status.HTTP_404_NOT_FOUND)

    medical_certificate = subscription.medical

    # update notes and competitive_medical_certificate boolean value
    if 'notes' in data:
        medical_certificate.notes = data['notes']
    if 'competitive_medical_certificate' in data:
        # check if the value is a boolean
        if isinstance(data['competitive_medical_certificate'], bool):
            medical_certificate.competitive_medical_certificate = data['competitive_medical_certificate']
    medical_certificate.save()

    return Response({'msg': f"Medical certificate updated"}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def subscription_medical_certificate_send_email_reminder(request, uid):

    logger.info("subscription_medical_certificate_send_email_reminder")

    subscription = Subscription.objects.filter(subscription_id=uid).first()
    if subscription.medical is None:
        raise TypeError("medical certificate not present!")

    data = {
        'athlete_first_name': subscription.associate.first_name,
        'athlete_last_name': subscription.associate.last_name,
        'sport_association': {
            "denomination": subscription.sport_association.denomination,
        },
        'certificate_expiring_date': subscription.medical.expiration_date.strftime("%d/%m/%Y"),
        'app_host': settings.APP_URL,
        'settings': {
            'WHITELABEL_NAME': settings.WHITELABEL_NAME,
            'IS_WHITELABEL': settings.IS_WHITELABEL
        }
    }

    # check if the subscription has an associate email or the tutor
    email_to_send = None

    if subscription.associate.email is not None:
        email_to_send = subscription.associate.email
    elif subscription.associate.main_tutor is not None and subscription.associate.main_tutor.email is not None:
        email_to_send = subscription.associate.main_tutor.email
    else:
        # send to the user
        email_to_send = subscription.user.email

    if email_to_send is None:
        raise TypeError("email not present!")

    send_email_template.delay(
        recipient_list=[email_to_send],
        subject=f"[{subscription.sport_association.denomination}] Ricordati di rinnovare il tuo certificato",
        template="email/account/email_subscription_medical_certificate_expiring.html",
        data=data,
        sport_association_id=subscription.sport_association.sport_association_id
    )

    return Response({'msg': f"Email reminder sent"}, status=status.HTTP_200_OK)


# Import helper functions have been moved to SubscriptionImportService


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def subscription_import_upload(request):

    sport_association = SportAssociation.objects.filter(user=request.user).first()
    if sport_association is None:
        return Response(status=status.HTTP_401_UNAUTHORIZED)

    logger.info("subscription_import_upload")
    data = request.data
    file_info = {"columns": [], "map": SubscriptionMaps.MAP}

    # Handle new file upload
    if 'associates_file' in data.keys():
        associates_file = data['associates_file']
        document, file_path = SubscriptionImportService.upload_and_store_file(associates_file)
        file_info['document_id'] = document.document_id

        rows_data_df = get_pandas_df_from_file(file_path)
        if rows_data_df is None:
            raise ValidationError('Cannot extract dataframe')

        file_info['columns'] = rows_data_df.columns.tolist()
        SubscriptionImportService.auto_map_columns(file_info['columns'], file_info['map'])

    # Handle existing document mapping
    elif 'document_id' in data.keys() and 'map' in data.keys():
        file_path = SubscriptionImportService.retrieve_document_path(data['document_id'])

        rows_data_df = get_pandas_df_from_file(file_path)
        if rows_data_df is None:
            raise ValidationError('Cannot extract dataframe')

        file_info['columns'] = rows_data_df.columns.tolist()

        try:
            SubscriptionImportService.manage_import_status(sport_association, action='start')
            objects_to_create = SubscriptionImportService.process_import_rows(
                rows_data_df, data, sport_association, request.user
            )
            AssociateImportDraft.objects.bulk_create(objects_to_create)
            logger.info("subscription_import_upload - end")
        except Exception as e:
            logger.exception(e)
        finally:
            SubscriptionImportService.manage_import_status(sport_association, action='end')

    else:
        raise TypeError("associates_file key not present!")

    return Response(file_info, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def subscription_associates_draft_add(request):

    sport_association = SportAssociation.objects.filter(user=request.user).first()

    if sport_association is None:
        return Response(status=status.HTTP_401_UNAUTHORIZED)

    logger.info("subscription_associates_draft_add")

    data = request.data

    new_user_account = UserAccountSerializer(data=data['new_user_account'])
    associate_data = AssociateSerializer(data=data['associate_data'])
    associate_tutor_data = AssociateSerializer(data=data['associate_tutor_data'])
    signature = SignatureRequestSerializer(data=data['signature'])

    valid = False

    if associate_data.is_valid() \
            and associate_tutor_data.is_valid():
        valid = True

    new_associate = AssociateImportDraft.objects.create(
        sport_association=sport_association,
        data={
            "associate": associate_data.initial_data,
            "associate_tutor": associate_tutor_data.initial_data,
            "new_user_account": new_user_account.initial_data,
            "signature": signature.initial_data,
            "medical_certificate": data['medical_certificate']
        },
        valid=valid
    )
    new_associate.save()

    return Response({'msg': "Draft saved!"}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def subscription_upload_document(request, uid):

    is_valid_uuid(uid)

    # get subscription
    subscription = Subscription.objects.filter(subscription_id=uid).first()

    if subscription is None:
        raise TypeError("subscription not found!")

    data = request.data

    if 'document' in data.keys() and 'filename' in data.keys():
        document_data = data['document']
        # logger.info(document)
        # template = os.path.join(BASE_DIR, 'templates/document/application/subscription.html')

        document = Document.objects.create(filename=data['filename'])
        document.save()

        storing_path = os.path.join(STORAGE_DIR, str(document.creation_date.timestamp()), str(document.document_id))
        file = os.path.join(storing_path, document.filename)
        file_data = base64.b64decode(document_data)
        file_like = BytesIO(file_data)

        default_storage.save(file, file_like)

        # create SubscriptionDocument object
        subscription_document = SubscriptionFile.objects.create(document=document, subscription=subscription)
        subscription_document.save()
    else:
        return Response({'exception': 'document key not present'}, status=status.HTTP_400_BAD_REQUEST)

    return Response({'mgs': 'file uploaded'}, status=status.HTTP_200_OK)



@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def subscription_delete_document(request, uid, subscription_file_id):

    is_valid_uuid(uid)
    is_valid_uuid(subscription_file_id)

    # check if user is the sport association owner
    sport_association = SportAssociation.objects.filter(user=request.user).first()
    if sport_association is None:
        raise PermissionDenied("User not allowed.")

    # get subscription
    subscription = Subscription.objects.filter(
        subscription_id=uid,
        sport_association=sport_association
    ).first()

    if subscription is None:
        raise TypeError("subscription not found!")

    # get document
    subscription_file = SubscriptionFile.objects.filter(
        subscription_file_id=subscription_file_id,
        subscription=subscription
    ).first()

    if subscription_file is None:
        raise TypeError("subscription file not found!")

    # delete document
    subscription_file.document.delete()
    subscription_file.delete()

    return Response({'msg': 'subscription file deleted'}, status=status.HTTP_200_OK)


def get_pandas_df_from_file(associates_file):
    opened_file = default_storage.open(associates_file, 'rb')
    rows_data_df = None
    if associates_file.lower().endswith('.csv'):
        rows_data_df = pandas.read_csv(opened_file, delimiter=';')
    elif associates_file.lower().endswith(('xls', 'xlsx', 'xlsm', 'xlsb', 'odf', 'ods', 'odt')):
        try:
            rows_data_df = pandas.read_excel(opened_file, engine="openpyxl")
        except Exception:
            rows_data_df = pandas.read_excel(opened_file, engine="xlrd")
    else:
        raise TypeError("file extension not recognized!")

    return rows_data_df


class SubscriptionMembershipViewSet(viewsets.ModelViewSet):
    queryset = SubscriptionMembership.objects.all()
    serializer_class = SubscriptionMembershipSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self, subscription=None):
        sport_association = SportAssociation.objects.get(user=self.request.user)
        queryset = SubscriptionMembership.objects.filter(
            sport_association=sport_association,
        )

        # Only apply subscription filter if the parameter is provided
        subscription = self.request.query_params.get('subscription')
        if subscription:
            queryset = queryset.filter(subscription_id=subscription)

        # check if there is the associate_id
        associate = self.request.query_params.get('associate_id')
        if associate:
            queryset = queryset.filter(associate_id=associate)

        return queryset

    def perform_create(self, serializer):
        sport_association = SportAssociation.objects.get(user=self.request.user)
        serializer.save(sport_association=sport_association)

    def add(self, request):
        request.data['sport_association'] = str(request.user.sport_association.sport_association_id)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def delete(self, request, pk=None):
        instance = self.get_object()
        sport_association = SportAssociation.objects.get(user=request.user)

        if instance.sport_association.sport_association_id != sport_association.sport_association_id:
            raise PermissionDenied("User not allowed.")

        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def update(self, request, pk=None):
        instance = self.get_object()
        sport_association = SportAssociation.objects.get(user=request.user)

        if instance.sport_association.sport_association_id != sport_association.sport_association_id:
            raise PermissionDenied("User not allowed.")

        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def list(self, request):
        queryset = self.get_queryset(subscription=request.query_params.get('subscription'))
        serializer = self.get_serializer(queryset, many=True)
        return Response({"data": serializer.data}, status=status.HTTP_200_OK)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_associations_for_federation(request):
    # get all the subscription by filtering by sport association in custom_data
    subscriptions = Subscription.objects.filter(
        Q(custom_data__icontains='"type_of_associate": "asd-o-ssd"') |
        Q(custom_data__icontains='"type_of_associate": "scuola-asc"'),
        sport_association=request.user.sport_association
    )

    associations = []
    seen_denominations = set()
    for subscription in subscriptions:
        try:
            if 'denomination' in subscription.custom_data.keys() and \
                    subscription.custom_data['denomination'] not in seen_denominations:
                seen_denominations.add(subscription.custom_data['denomination'])
                associations.append({
                    'id': str(subscription.subscription_id),
                    'denomination': subscription.custom_data['denomination']
                })
        except Exception as e:
            logger.exception(e)

    return Response(associations, status=status.HTTP_200_OK)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def subscription_generate_token_link(request):
    # gym name
    gym_name = request.data.get('gym_name', None)
    if gym_name is None:
        return Response({'error': 'gym_name is required'}, status=status.HTTP_400_BAD_REQUEST)
    # generate random uuid4 token
    token = str(uuid4())

    gym_token_link = CumulativeSubscriptionGymLinks.objects.create(
        sport_association=request.user.sport_association,
        gym_name=gym_name,
        token=token,
        expires_at=make_aware(datetime.now() + timedelta(days=2))
    )

    gym_token_link.save()

    return Response({
        'token': gym_token_link.token,
        'gym_name': gym_token_link.gym_name,
        'expires_at': gym_token_link.expires_at
    }, status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([])
def validate_token_link_and_get_subscriptions(request):
    token = request.query_params.get('token', None)

    # check if token is valid uuid4
    is_valid_uuid(token)

    gym_token_link = CumulativeSubscriptionGymLinks.objects.filter(
        token=token,
        expires_at__gt=make_aware(datetime.now())
    ).first()

    # clear expired links
    CumulativeSubscriptionGymLinks.objects.filter(
        expires_at__lt=make_aware(datetime.now())
    ).delete()

    if gym_token_link is None:
        return Response({'expired': True}, status=status.HTTP_200_OK)

    # Get associate IDs that have active subscriptions in the current year
    active_associate = Subscription.objects.filter(
        sport_association=gym_token_link.sport_association,
        status_flag__in=[Subscription.ACCEPTED, Subscription.PENDING],
        end_date__gte=make_aware(datetime.now())
    )

    active_associate_ids = []
    for sub in active_associate:
        sub: Subscription = sub
        if sub.active:
            active_associate_ids.append(sub.associate_id)


    candidates = Subscription.objects.filter(
        sport_association=gym_token_link.sport_association,
        custom_data__icontains=gym_token_link.gym_name[:3] if len(gym_token_link.gym_name) > 3 else gym_token_link.gym_name,
        start_date__lte=make_aware(datetime.now()) - timedelta(days=180), # 180 days before today
    ).exclude(associate_id__in=active_associate_ids)

    fuzzy_matches = []
    for obj in candidates:
        gym_name = None
        if 'school_name' in obj.custom_data.keys():
            gym_name = obj.custom_data['school_name']
        elif 'denomination' in obj.custom_data.keys():
            gym_name = obj.custom_data['denomination']
        else:
            continue
        similarity = fuzz.ratio(gym_token_link.gym_name.lower(), gym_name.lower())
        if similarity > 70:  # similarity threshold %
            fuzzy_matches.append(obj.subscription_id)
    subscriptions = candidates.filter(subscription_id__in=fuzzy_matches)

    # serialize subscriptions
    subscriptions = SubscriptionSerializer(subscriptions, many=True, read_only=True).data

    default_custom_data = None
    # find the first subscription with custom_data that has school_name key
    # and return its custom_data as default_custom_data
    for sub in subscriptions:
        if 'school_name' in sub['custom_data'].keys():
            default_custom_data = {
                "school_name": sub['custom_data']['school_name'],
                "school_teacher_name": sub['custom_data'].get('school_teacher_name', ''),
                "responsible_instructor_name": sub['custom_data'].get('responsible_instructor_name', ''),
                "principal_style": sub['custom_data'].get('principal_style', ''),
                "type_of_associate": "atleta",
                "denomination": sub['custom_data'].get('denomination', ''),
                "type": 2
            }
            break

    return Response({
        'expired': False,
        'subscriptions': subscriptions,
        'default_custom_data': default_custom_data
    }, status=status.HTTP_200_OK)
