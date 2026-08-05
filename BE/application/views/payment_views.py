"""
@ copyright: Bakney SRL
"""
import base64
import csv
from datetime import datetime, date
from io import StringIO

import logging

import stripe
from django.core.cache import cache
from django.db.models import Q, Count, Prefetch, F, Sum, Case, When, DecimalField
from decimal import Decimal
from django.utils import timezone
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes

from django.db import transaction

from application.models.balance_sheet_models import CustomAccounts, CustomAccountsTransfer
from application.serializers.balance_sheet import CustomAccountSerializer
from application.tasks import association_quote_assign
from application.utils.subscriptions_utils import smart_search
from core import settings
from core.middleware import IsAuthenticated

from application.models.carnet_models import CarnetSubscription
from application.models.courses_models import CourseSubscription, CourseSubscriptionInstallment
from application.models.invoices_models import Invoice, InvoiceSuppliers
from application.models.payment_models import Payment, PaymentCategory, SupplierAndCustomers
from application.models.user_models import SportAssociation, User, Associate, Instructor, InstructorHours
from application.models.subscriptions_models import Signature
from application.serializers.courses_serializers import CourseSerializerInfo
from application.serializers.payment_serializers import PaymentSerializer, PaymentSerializerInfo, \
    PaymentCategorySerializer, PaymentEntrySerializer, PaymentCompressedSerializer, PaymentOptimizedSerializer
from application.serializers.subscriptions_serializers import SignatureRequestSerializer
from application.serializers.auth_serializers import SportAssociationBasicInfo
from application.models.subscriptions_models import Subscription
from application.utils import api_utils
from application.utils.api_utils import is_valid_uuid, KTDatatablePagination
from application.utils.excel_utils import get_excel_base64
from application.utils.notification_utils import NotificationUtils
from application.utils.payments_utils import calculate_simulation, generate_invoice_description
from application.utils.stripe_utils import online_payments_available

from docmanager.views.printing_views import document_invoice
from notifications.services import NotificationService

from core.tasks import send_mail_async
from application.printing_tasks import print_document_invoice, print_document_compensation
from application.services import InvoiceService

logger = logging.getLogger(__name__)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payment_add(request):
    """
    API endpoint to add a new payment
    """
    logger.info("Adding new payment", extra={'user_id': str(request.user.user_id)})

    if request.user.role == User.ATHLETE:
        logger.warning("Payment add attempt by athlete user", extra={'user_id': str(request.user.user_id)})
        # not allowed
        return Response(status=status.HTTP_403_FORBIDDEN)

    sport_association = SportAssociation.objects.get(user=request.user)
    data = request.data
    # paid is true only if the user has the auto_paid_payment flag set to true
    data['paid'] = request.user.auto_paid_payment

    if data['creation_date'] is None or data['creation_date'] == "":
        # set creation date to today with also time
        data['creation_date'] = timezone.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    else:
        try:
            # transform the date in a datetime object with current timezone and time
            data['creation_date'] = datetime.strptime(data['creation_date'], '%Y-%m-%d').replace(tzinfo=None)
        except Exception as e:
            logger.exception(e)

    if data['payment_date'] == "":
        data['payment_date'] = None

    if data['payment_date'] is None and data['paid'] is True:
        # set payment date to today with also time
        data['payment_date'] = timezone.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if data['payment_date'] is not None:
        try:
            # drop the timezone from the payment date
            data['payment_date'] = datetime.strptime(data['payment_date'], '%Y-%m-%d').replace(tzinfo=None)
        except Exception as e:
            logger.exception(e)

    # put date in a serializer
    serializer = PaymentEntrySerializer(data=data)

    if serializer.is_valid():
        payment = serializer.save()
        payment.user = request.user
        payment.sport_association = sport_association
        # checking the complex_item
        if data['complex_item']:
            if data['complex_item']['group'] == 'Istruttori':
                payment.instructor = Instructor.objects.get(instructor_id=data['complex_item']['instructor_id'])
            if data['complex_item']['group'] == 'Fornitori':
                payment.supplier = SupplierAndCustomers.objects.get(supplier_id=data['complex_item']['supplier_id'])
            if data['complex_item']['group'] == 'Iscrizioni':
                payment.associate = Subscription.objects.get(
                    subscription_id=data['complex_item']['subscription_id']).associate
            if data['complex_item']['group'] == 'Persone':
                payment.associate = Associate.objects.get(associate_id=data['complex_item']['associate_id'])

        payment.save()

        # if payment is paid generate invoice
        logger.debug("Checking if invoice should be generated", extra={'payment_id': str(payment.payment_id), 'paid': payment.paid, 'expense': payment.expense})
        if payment.paid and payment.invoice is None and payment.expense is False:
            logger.info("Generating invoice for paid payment", extra={'payment_id': str(payment.payment_id)})
            with transaction.atomic():

                if payment.associate is None and payment.supplier is None and payment.instructor is None:
                    # skip
                    return Response(PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)

                membership_fee = payment.amount if payment.subject is Payment.SUBSCRIPTION else 0.00
                activity_fee = payment.amount if payment.subject is not Payment.SUBSCRIPTION else 0.00
                if payment.meta_payment_categories and payment.subject is Payment.SUBSCRIPTION:
                    # sum the amount of the meta payment categories
                    for meta_payment_category in payment.meta_payment_categories:
                        if meta_payment_category["amount"]:
                            activity_fee = float(activity_fee) + float(str(meta_payment_category["amount"]))
                if membership_fee > 0:
                    membership_fee = float(str(membership_fee)) - float(activity_fee)

                invoice_number = InvoiceService.get_next_invoice_number(
                    sport_association,
                    payment.creation_date,
                    request.user,
                    use_payment_date=False
                )

                # description text based on the type of the payment
                description = generate_invoice_description(payment, sport_association)
                if payment.supplier is None:
                    invoice = Invoice.objects.create(
                        sport_association=sport_association,
                        description=description,
                        membership_fee=membership_fee,
                        activity_fee=activity_fee,
                        number=invoice_number,
                        meta_payment_categories=payment.meta_payment_categories,
                    )
                    if request.user.payment_date_equal_invoice_date:
                        invoice.creation_date = payment.payment_date
                        invoice.save()
                    payment.invoice = invoice
                    logger.info("Invoice created and printing", extra={'payment_id': str(payment.payment_id), 'invoice_id': str(invoice.invoice_id)})
                    print_document_invoice.apply(args=[str(payment.invoice.invoice_id), request.headers.get('authorization')])
                payment.save()
        logger.info("Payment added successfully", extra={'payment_id': str(payment.payment_id)})
        return Response(PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)
    else:
        logger.error("Payment validation failed", extra={'user_id': str(request.user.user_id), 'errors': str(serializer.errors)})
        return Response({"msg": f"validation errors: {serializer.errors}"}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payment_bulk_add(request):
    """
    API endpoint to add multiple payments for multiple associates in a single operation.
    """
    logger.info("Adding bulk payments", extra={'user_id': str(request.user.user_id)})

    # Permission check: reject if user is an athlete
    if request.user.role == User.ATHLETE:
        logger.warning("Bulk payment add attempt by athlete user", extra={'user_id': str(request.user.user_id)})
        return Response(status=status.HTTP_403_FORBIDDEN)

    sport_association = SportAssociation.objects.get(user=request.user)
    data = request.data

    # Validate required fields
    associate_ids = data.get('associate_ids')
    if not associate_ids or not isinstance(associate_ids, list):
        return Response(
            {"msg": "associate_ids is required and must be a non-empty list"},
            status=status.HTTP_400_BAD_REQUEST
        )

    if len(associate_ids) > 10000:
        return Response(
            {"msg": "associate_ids cannot exceed 10,000 items"},
            status=status.HTTP_400_BAD_REQUEST
        )

    amount = data.get('amount')
    if amount is None:
        return Response(
            {"msg": "amount is required"},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        amount = Decimal(str(amount))
        if amount < 0:
            return Response(
                {"msg": "amount must be >= 0"},
                status=status.HTTP_400_BAD_REQUEST
            )
    except Exception:
        return Response(
            {"msg": "amount must be a valid decimal number"},
            status=status.HTTP_400_BAD_REQUEST
        )

    payment_type = data.get('type', Payment.DEFAULT)
    valid_types = [choice[0] for choice in Payment.TYPE_CHOICES]
    if payment_type not in valid_types:
        return Response(
            {"msg": f"type must be one of: {valid_types}"},
            status=status.HTTP_400_BAD_REQUEST
        )

    subject = data.get('subject', Payment.OTHER)
    valid_subjects = [Payment.OTHER, Payment.SUBSCRIPTION, Payment.COURSE, Payment.ACCOUNT_TRANSFER]
    if subject not in valid_subjects:
        return Response(
            {"msg": f"subject must be one of: {valid_subjects}"},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Validate associate IDs - single efficient query
    # Note: deleted=False is already handled by SoftDeleteGroupAwareManager
    requested_ids = set(associate_ids)
    valid_associates = Associate.objects.filter(
        associate_id__in=requested_ids,
        sport_association=sport_association,
    ).values_list('associate_id', flat=True)
    # Keep original UUID objects for foreign key assignment
    valid_uuid_ids = set(valid_associates)
    # Convert to strings for comparison with input IDs
    valid_ids_str = {str(uid) for uid in valid_uuid_ids}
    invalid_ids = [str(aid) for aid in requested_ids if str(aid) not in valid_ids_str]

    if not valid_uuid_ids:
        return Response(
            {
                "data": {
                    "created_count": 0,
                    "total_requested": len(requested_ids),
                    "invalid_associate_ids": invalid_ids
                }
            },
            status=status.HTTP_200_OK
        )

    # Pre-compute default values (required because bulk_create bypasses PaymentManager.create() and save())
    # Custom accounts
    custom_accounts_id = data.get('custom_accounts')
    if custom_accounts_id:
        custom_accounts = CustomAccounts.objects.filter(
            pk=custom_accounts_id,
            sport_association=sport_association
        ).first()
    else:
        custom_accounts = Payment.get_default_bank_account(sport_association)

    # Payment category
    payment_category_id = data.get('payment_category')
    if payment_category_id:
        payment_category = PaymentCategory.objects.filter(pk=payment_category_id).first()
    else:
        payment_category = PaymentCategory.objects.filter(
            name__iexact='entrate e proventi da attività tipiche'
        ).first()

    # Description - generate based on subject if not provided
    description = data.get('description')
    if description is None:
        if subject == Payment.SUBSCRIPTION:
            description = 'Iscrizione'
        elif subject == Payment.COURSE:
            description = 'Quota Corso'

    # Paid status
    paid = data.get('paid')
    if paid is None:
        paid = request.user.auto_paid_payment

    # Expense flag
    expense = data.get('expense', False)

    # Creation date
    creation_date = data.get('creation_date')
    if creation_date is None or creation_date == "":
        creation_date = timezone.now()
    else:
        try:
            creation_date = datetime.strptime(creation_date, '%Y-%m-%d').replace(tzinfo=None)
        except Exception:
            creation_date = timezone.now()

    # Payment date
    payment_date = data.get('payment_date')
    if payment_date == "":
        payment_date = None
    if payment_date is None and paid is True:
        payment_date = timezone.now()
    elif payment_date is not None:
        try:
            payment_date = datetime.strptime(payment_date, '%Y-%m-%d').replace(tzinfo=None)
        except Exception:
            pass

    # Notes and meta payment categories
    notes = data.get('notes')
    meta_payment_categories = data.get('meta_payment_categories', [])

    # Build Payment objects
    import uuid as uuid_module
    payments_to_create = []
    for associate_id in valid_uuid_ids:
        payments_to_create.append(Payment(
            payment_id=uuid_module.uuid4(),
            associate_id=associate_id,
            user=request.user,
            sport_association=sport_association,
            amount=amount,
            type=payment_type,
            subject=subject,
            description=description,
            expense=expense,
            paid=paid,
            creation_date=creation_date,
            payment_date=payment_date,
            payment_category=payment_category,
            custom_accounts=custom_accounts,
            notes=notes,
            meta_payment_categories=meta_payment_categories,
        ))

    # Chunked bulk insert with transaction
    BATCH_SIZE = 500
    with transaction.atomic():
        for i in range(0, len(payments_to_create), BATCH_SIZE):
            batch = payments_to_create[i:i + BATCH_SIZE]
            Payment.objects.bulk_create(batch)

    logger.info(
        "Bulk payments added successfully",
        extra={
            'user_id': str(request.user.user_id),
            'created_count': len(payments_to_create),
            'invalid_count': len(invalid_ids)
        }
    )

    return Response(
        {
            "data": {
                "created_count": len(payments_to_create),
                "total_requested": len(requested_ids),
                "invalid_associate_ids": invalid_ids
            }
        },
        status=status.HTTP_201_CREATED
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payment_sign(request):
    # getting body
    data = request.data

    max_keys_length = 2

    user = request.user if request.collaborator is False else request.original_user

    # checking body for security/correctness
    if 'payment_id' in data.keys() and \
            'signature' in data.keys() and \
            len(data.keys()) == max_keys_length:

        # instantiate new variables for each field to make code more readable
        if not is_valid_uuid(data['payment_id']):
            raise ValidationError('not valid payload')
        else:
            payment = Payment.objects.filter(payment_id=data['payment_id']).first()
            if payment is None:
                raise ValidationError('payment not found')

        signature = SignatureRequestSerializer(data=data['signature'])

        if signature.is_valid(raise_exception=True):
            signature = signature.save()
            # save new signature if present
            if signature.there_is_signature:
                new_signature = Signature.objects.create(
                    signature=signature.data,
                    user=user
                )
                new_signature.save()

                payment.signature = new_signature
                payment.save()

                print_document_compensation.apply(
                    args=[str(payment.payment_id), request.headers.get('authorization')])

        return Response({"status": "success"}, status=status.HTTP_200_OK)
    else:
        return Response({"msg": "validation errors."}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def payment_simulation_partial_quotes(request):

    # get the date
    date = request.GET.get('date', None)

    subscription_plans_considered, quotes_to_emit = calculate_simulation(
        date=date,
        sport_association=request.user.sport_association.sport_association_id
    )

    return Response({
        "subscription_plans_considered": subscription_plans_considered,
        "quotes_to_emit": quotes_to_emit
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def payment_simulation_partial_quotes_apply(request):

    # get the date
    date = request.GET.get('date', None)

    association_quote_assign(
        date=date,
        sport_association=request.user.sport_association.sport_association_id
    )

    return Response({
        "msg": "Partial quotes applied."
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payment_bulk_archive(request):

    data = request.data

    # check that invoice_ids key is present
    if 'payment_ids' not in data.keys():
        raise Exception("missing required field")

    sport_association = SportAssociation.objects.get(user=request.user)

    # get all payments
    payments = Payment.objects.filter(
        sport_association=sport_association,
        payment_id__in=data['payment_ids']
    )
    # bulk update
    for payment in payments:
        payment.archived = True

    Payment.objects.bulk_update(payments, ['archived'])

    return Response({'message': 'All payments archived'}, status=status.HTTP_200_OK)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payment_bulk_delete(request):

    data = request.data

    # check that invoice_ids key is present
    if 'payment_ids' not in data.keys():
        raise Exception("missing required field")

    sport_association = SportAssociation.objects.get(user=request.user)

    # get all payments
    payments = Payment.objects.filter(
        sport_association=sport_association,
        payment_id__in=data['payment_ids']
    )
    # bulk soft-delete
    for payment in payments:
        payment.deleted = True

    Payment.objects.bulk_update(payments, ['deleted'])

    return Response({'message': 'All payments deleted'}, status=status.HTTP_200_OK)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def payment_update(request, uid):
    """
    API endpoint to add a new payment
    """


    is_valid_uuid(uid)

    sport_association = SportAssociation.objects.get(user=request.user)
    payment = Payment.objects.filter(payment_id=uid).first()

    if payment.sport_association.sport_association_id != sport_association.sport_association_id:
        raise PermissionDenied("User not allowed.")

    data = request.data

    if 'creation_date' in data.keys() and data['creation_date'] == '':
        del data['creation_date']

    if 'payment_date' in data.keys() and data['payment_date'] == '':
        del data['payment_date']
    # update fields with serializer, if not valid return error
    serializer = PaymentEntrySerializer(payment, data=data, partial=True)
    if serializer.is_valid(raise_exception=True):
        payment.instructor = None
        payment.supplier = None
        if 'complex_item' in data.keys() and data['complex_item']:
            # set all to None
            payment.instructor = None
            payment.supplier = None
            payment.associate = None
            if data['complex_item']['group'] == 'Istruttori':
                payment.instructor = Instructor.objects.get(instructor_id=data['complex_item']['instructor_id'])
            if data['complex_item']['group'] == 'Fornitori':
                payment.supplier = SupplierAndCustomers.objects.get(supplier_id=data['complex_item']['supplier_id'])
            if data['complex_item']['group'] == 'Iscrizioni':
                payment.associate = Subscription.objects.get(
                    subscription_id=data['complex_item']['subscription_id']).associate
            if data['complex_item']['group'] == 'Persone':
                payment.associate = Associate.objects.get(associate_id=data['complex_item']['associate_id'])

        if 'subscription_id' in data.keys() and data['subscription_id']:
            payment.associate = Subscription.objects.get(subscription_id=data['subscription_id']).associate
        payment.payment_intent_id = None # reset payment intent id if changing the payment info
        serializer.save()
        # get the invoice for the payment and refresh the document
        if payment.invoice is not None:
            # TODO: understand how to handle the invoice update
            print_document_invoice.apply(args=[str(payment.invoice.invoice_id), request.headers.get('authorization')])
        return Response({ "data": {"payment": PaymentSerializer(payment).data }}, status=status.HTTP_200_OK)

    return Response({"msg": "validation errors."}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payment_request(request, uid):
    """
    API endpoint to add a new payment
    """

    is_valid_uuid(uid)

    sport_association = SportAssociation.objects.get(user=request.user)
    payment = Payment.objects.filter(payment_id=uid).first()

    if payment.sport_association.sport_association_id != sport_association.sport_association_id:
        raise PermissionDenied("User not allowed.")

    # send email to associate if the payment is not paid
    if payment.paid is False:

        recipient_list = []
        if payment.associate is not None and payment.associate.email is not None:
            recipient_list.append(payment.associate.email)
        # if user is not the one that made the request
        if payment.user != request.user:
            recipient_list.append(request.user.email)

        description = f"{payment.description} " if payment.description is not None else ""
        if payment.subject == Payment.SUBSCRIPTION:
            description = "per l'iscrizione "
        elif payment.subject == Payment.COURSE:
            description = f"per il corso "
        email_html = f"""
                Ciao {payment.associate.first_name} {payment.associate.last_name},<br>
                ti informiamo che il pagamento di {payment.amount}€ {description}è in attesa di pagamento.<br>
                Per favore procedi con il pagamento.<br>
                <br>
                
                Se hai un già un account {settings.WHITELABEL_NAME}, puoi vedere i dettagli del pagamento online cliccando <a href="{settings.APP_URL}">qui</a>.<br>
                
                {f"<br>Puoi pagare online! <br><br>" if payment.sport_association.user.online_payments is True else ''}
                
                Cordiali saluti,<br>
                {payment.sport_association.denomination}
                """
        send_mail_async.apply_async(
            kwargs={
                "subject": f"{payment.sport_association.denomination} | Pagamento in attesa",
                "message": email_html,
                "from_email": settings.DEFAULT_TEAM_EMAIL,
                "recipient_list": recipient_list,
                "html_message": email_html,
                "fail_silently": False,
                "sport_association_id": payment.sport_association.sport_association_id,
                "reply_to": [payment.sport_association.user.email],
            }
        )
        return Response({"msg": "email sent."}, status=status.HTTP_200_OK)
    else:
        return Response({"msg": "email not sent, payment already paid."}, status=status.HTTP_412_PRECONDITION_FAILED)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def payment_delete(request, uid):
    """
    API endpoint to add a new payment
    """
    logger.info("Deleting payment", extra={'user_id': str(request.user.user_id), 'payment_id': uid})

    is_valid_uuid(uid)

    sport_association = SportAssociation.objects.get(user=request.user)
    payment = Payment.objects.filter(payment_id=uid, sport_association=request.user.sport_association).first()
    if payment is None:
        return Response({'msg': 'Payment not found.'}, status=status.HTTP_404_NOT_FOUND)
    if payment.sport_association.sport_association_id != sport_association.sport_association_id:
        raise PermissionDenied("User not allowed.")

    # if payment.invoice is None and (payment.paid is False or \
    #                                 (payment.paid is True and payment.type not in [Payment.SUBSCRIPTION,
    #                                                                                Payment.COURSE])):
    # new
    if payment.invoice:
        payment.invoice.cancelled = True
        # set invoice meta to payment data serialized
        payment_serialized = PaymentSerializer(payment).data
        payment_obj = {
            "payment_id": str(payment_serialized["payment_id"]),
            "user": str(payment_serialized["user"]),
            "associate": str(payment_serialized["associate"]),
            "subscription": str(payment_serialized["subscription"]) if "subscription" in payment_serialized and \
                                                                       payment_serialized["subscription"] else None,
            "subject": payment_serialized["subject"],
            "amount": payment_serialized["amount"],
            "creation_date": payment_serialized["creation_date"],
            "payment_date": payment_serialized["payment_date"],
        }
        payment.invoice.meta = payment_obj
        payment.invoice.save()
        # print
        print_document_invoice.apply(args=[str(payment.invoice.invoice_id), request.headers.get('authorization')])
    # ---
    logger.info("Payment deleted successfully", extra={'payment_id': uid})
    payment.delete()
    data = {"msg": "payment deleted."}
    return Response({'data': data}, status=status.HTTP_200_OK)
    # else:
    #     return Response({"msg": "validation errors."}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payment_archive(request, uid):
    is_valid_uuid(uid)

    if not request.user.is_sport_association(raise_exception=False):
        return Response({"msg": "User not allowed."}, status=status.HTTP_403_FORBIDDEN)

    payment = Payment.objects.filter(payment_id=uid, sport_association=request.user.sport_association).first()

    if payment.archived:
        payment.archived = False
        payment.save()
        return Response({"msg": "Payment unarchived."}, status=status.HTTP_200_OK)
    else:
        payment.archived = True
        payment.save()
        return Response({"msg": "Payment archived."}, status=status.HTTP_200_OK)

    return Response({"msg": "Nothing applied."}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def payment_list(request):
    paginator = KTDatatablePagination()
    # pagination_search
    general_search = request.GET.get('query[generalSearch]', None)
    paid = request.GET.get('query[paid]', None)
    expense = request.GET.get('query[expense]', None)
    sort_field = request.GET.get('sort[field]', None)
    sort_type = request.GET.get('sort[sort]', None)
    payment_range = request.GET.get('query[payment_range]', None)
    mode = request.GET.get('mode', None)
    subject = request.GET.get('query[subject]', None)
    type = request.GET.get('query[type]', None)
    course_subscription_id = request.GET.get('course_subscription_id', None)
    archived = request.GET.get('query[archived]', False)
    associate_id = request.GET.get('associate_id', None)
    payment_categories = request.GET.get('query[payment_categories]', None)
    account = request.GET.get('query[account]', None)

    if course_subscription_id:
        is_valid_uuid(course_subscription_id)


    logger.info("payment_list -> init -> user: {} role: {}".format(request.user.user_id, request.user.role))

    # check if mode is not None and it is equal to compressed
    if mode == 'compressed':
        # Use direct relation instead of extra query
        try:
            sport_association = request.user.sportassociation
        except SportAssociation.DoesNotExist:
            sport_association = SportAssociation.objects.get(user=request.user)
            
        payments = Payment.objects.filter(
            sport_association=sport_association,
            archived=archived
        ).select_related(
            'associate',
            'instructor',
            'supplier',
            'custom_accounts',
            'invoice',
        ).values(
            'payment_id',
            'amount',
            'payment_date',
            'paid',
            'associate',
            'supplier',
            'instructor',
            'associate__first_name',
            'associate__last_name',
            'supplier__name',
            'instructor__first_name',
            'instructor__last_name',
            'custom_accounts__name',
        ).order_by('-creation_date')
        return Response({'data': payments}, status=status.HTTP_200_OK)

    # excluded pending payments
    excluded_payments = []
    payments = []
    # case athlete is asking for his payments
    if User.ATHLETE == request.user.role and course_subscription_id is not None:
        course_subscription = CourseSubscription.objects.filter(
            course_subscription_id=course_subscription_id
        ).select_related('course').first()
        if course_subscription.multi_payments:
            installments = CourseSubscriptionInstallment.objects.filter(course_subscription=course_subscription)
            for installment in installments:
                if installment.amount > 0 or course_subscription.course.sport_association.user.show_zero_payments:
                    payments.append(installment.payment)
        elif course_subscription.payment and (
                course_subscription.payment.amount > 0 or \
                course_subscription.course.sport_association.user.show_zero_payments):
            payments.append(course_subscription.payment)
        # check for CarnetSubscription payments
        carnet_subscriptions = CarnetSubscription.objects.filter(course_subscription=course_subscription).select_related('carnet_id')
        if carnet_subscriptions is not None:
            for carnet_subscription in carnet_subscriptions:
                if carnet_subscription.payment and (
                        carnet_subscription.payment.amount > 0 or \
                        carnet_subscription.carnet_id.sport_association.user.show_zero_payments):
                    payments.append(carnet_subscription.payment)
        # convert payments to a QuerySet of Payment objects
        payments = Payment.objects.filter(payment_id__in=[payment.payment_id for payment in payments])

    elif User.ATHLETE == request.user.role:
        # Optimize with prefetch_related to avoid N+1 queries
        subscriptions = Subscription.objects.filter(user=request.user).select_related(
            'sport_association__user',
            'associate'
        ).prefetch_related(
            Prefetch('coursesubscription_set',
                queryset=CourseSubscription.objects.select_related('course', 'payment').prefetch_related(
                    Prefetch('coursesubscriptioninstallment_set',
                        queryset=CourseSubscriptionInstallment.objects.filter(paid=False).select_related('payment')
                    )
                )
            )
        )
        
        # Collect associates in single pass
        subscriptions_associates = []
        for subscription in subscriptions:
            if subscription.associate:
                subscriptions_associates.append(subscription.associate)
        
        # Build payment query with all filters at once
        payments = Payment.objects.filter(
            Q(user=request.user) | Q(associate__in=subscriptions_associates),
            amount__gte=0,
            expense=False,
            creation_date__lte=timezone.now()+timezone.timedelta(days=60)
        ).select_related(
            'sport_association__user',
            'invoice__document_pdf'
        ).order_by('paid', '-creation_date')
        
        # Process exclusions using prefetched data
        for subscription in subscriptions:
            show_zero_payments = subscription.sport_association.user.show_zero_payments
            for course_subscription in subscription.coursesubscription_set.all():
                if course_subscription.course.multi_payments:
                    for installment in course_subscription.coursesubscriptioninstallment_set.all():
                        if api_utils.days_between(
                                datetime.now().date(), installment.payment_date.date()) > 31:
                            excluded_payments.append(installment.payment)
                        elif installment.payment.amount == 0 and not show_zero_payments:
                            excluded_payments.append(installment.payment)
                else:
                    if course_subscription_id and str(course_subscription_id) != str(course_subscription.course_subscription_id):
                        if course_subscription.payment:
                            excluded_payments.append(course_subscription.payment)
                    elif course_subscription.payment and course_subscription.payment.amount == 0 and not show_zero_payments:
                        excluded_payments.append(course_subscription.payment)

    else:
        # Use direct relation instead of extra query
        try:
            sport_association = request.user.sportassociation
        except SportAssociation.DoesNotExist:
            sport_association = SportAssociation.objects.get(user=request.user)
        
        # default payment from is today - 365 days
        payment_from = datetime.now() - timezone.timedelta(days=365)
        # default payment to is today
        payment_to = datetime.now() + timezone.timedelta(days=1)

        # Start with base queryset including all needed relations
        payments = Payment.objects.filter(
            sport_association=sport_association,
            archived=archived
        ).select_related(
            'invoice',
            'associate',
            'supplier', 
            'instructor',
            'custom_accounts',
            'payment_category',
            'sport_association__user'
        )

        # Build all filters with Q objects (DRY/KISS principle)
        filters = Q()
        
        if associate_id is not None:
            filters &= Q(associate_id=associate_id)

        # Check if we should skip payment range
        skip_range = False
        try:
            if is_valid_uuid(general_search):
                skip_range = True
        except Exception:
            pass
            
        if payment_range and not skip_range:
            # Parse payment range
            try:
                payment_from, payment_to = payment_range.split(' al ')
                try:
                    payment_from = datetime.strptime(payment_from, '%Y/%m/%d')
                    payment_to = datetime.strptime(payment_to, '%Y/%m/%d')
                except ValueError:
                    try:
                        payment_from = datetime.strptime(payment_from, '%d/%m/%Y')
                        payment_to = datetime.strptime(payment_to, '%d/%m/%Y')
                    except ValueError:
                        payment_from = datetime.now() - timezone.timedelta(days=365)
                        payment_to = datetime.now()
                
                payment_to = payment_to.replace(hour=23, minute=59, second=59)
                
                # Add date range filter
                filters &= (
                    Q(payment_date__range=(payment_from, payment_to), payment_date__isnull=False) |
                    Q(creation_date__range=(payment_from, payment_to), payment_date__isnull=True)
                )
            except Exception as e:
                logger.exception(f"Error parsing payment range: {e}")
        
        # Add all other filters
        if subject is not None:
            filters &= Q(subject=subject)
            
        if type is not None:
            filters &= Q(type=type)
            
        if paid is not None:
            filters &= Q(paid=(paid == 'true'))
            
        if expense is not None:
            filters &= Q(expense=(expense == 'true'))
            
        if payment_categories:
            if payment_categories == "[]":
                filters &= Q(payment_category__isnull=True)
            else:
                filters &= Q(payment_category__in=payment_categories.split(','))
                
        if account:
            if account == "[]":
                filters &= Q(custom_accounts__isnull=True)
            else:
                filters &= Q(custom_accounts__custom_account_id=account)
        
        # Apply all filters at once
        if filters:
            payments = payments.filter(filters)
        
        # Set ordering
        payments = payments.order_by('-payment_date', '-creation_date')

        # filter for general search
        if general_search:
            # check if general_search is a date
            try:
                date = datetime.strptime(general_search, '%d/%m/%Y').strftime('%Y-%m-%d')
                general_search = date
            except ValueError:
                pass

            try:
                amount_value = float(general_search.replace(',', '.'))
                payments = payments.filter(
                    Q(amount__icontains=amount_value) |
                    Q(amount__exact=amount_value)
                )
            except (ValueError, TypeError):
                # Use smart_search for non-numeric searches
                payments = smart_search(payments, general_search)
        # sort by field
        if sort_field:
            payments = payments.order_by(f"{'-' if sort_type == 'desc' else ''}{sort_field}")

        # pagination
        payments = paginator.paginate_queryset(queryset=payments, request=request)

    data = {}

    if User.ATHLETE == request.user.role:
        payments.order_by('-creation_date')
        for payment in payments:
            if payment.paid and payment.invoice is not None \
                    and payment.invoice.document_pdf is None:
                document_invoice(request._request, payment.invoice_id)
            # if payment not in excluded_payments:


        # payments is a list of Payment objects
        # excluded_payments is a list of Payment objects
        # data is a list of Payment objects
        payments = [payment for payment in payments if payment not in excluded_payments]

        # Prefetch all course installments at once to avoid N+1 queries
        payment_ids = [payment.payment_id for payment in payments]
        course_installments_map = {}
        if payment_ids:
            course_installments = CourseSubscriptionInstallment.objects.filter(
                payment_id__in=payment_ids
            ).select_related('course_subscription__course', 'payment')

            for installment in course_installments:
                course_installments_map[str(installment.payment_id)] = installment

        for idx, payment in enumerate(payments):
            data[idx] = PaymentCompressedSerializer(payment).data
            pay_available = False
            one_solution_available = False
            if online_payments_available(payment.sport_association):
                pay_available = True
                # if payment online available look for course that allows the one time payment
                course_installment = course_installments_map.get(str(payment.payment_id))
                if course_installment and course_installment.course_subscription.course.one_fee_payment and \
                        not course_installment.payment.paid:
                    # TODO: fix the course installments id to avoid doing this
                    installments = course_installment.course_subscription.course.events
                    # get installment with id == 0
                    for inst in installments:
                        date_object = datetime.strptime(str(course_installment.payment_date), '%Y-%m-%d %H:%M:%S%z')
                        if int(inst['id']) == 0 and \
                                inst['payment_date'] == date_object.strftime('%d/%m/%Y'):
                            one_solution_available = True
                            break
            data[idx]['payment_method'] = payment.type
            data[idx]['online_pay_available'] = pay_available
            data[idx]['sport_association_denomination'] = payment.sport_association.denomination
            data[idx]['one_solution_available'] = one_solution_available
            if payment.paid and payment.invoice is not None:
                data[idx]['document_pdf'] = payment.invoice.document_pdf.document_id \
                    if payment.invoice.document_pdf else None
    else:
        data = PaymentOptimizedSerializer(payments, many=True).data

        # Prefetch all instructor hours at once to avoid N+1 queries
        payment_ids_with_instructor = [p['payment_id'] for p in data if p.get('instructor') is not None]
        
        # Single query to get all instructor hours with documents
        instructor_hours_map = {}
        if payment_ids_with_instructor:
            instructor_hours = InstructorHours.objects.filter(
                payment_id__in=payment_ids_with_instructor
            ).select_related('document')
            
            for ih in instructor_hours:
                instructor_hours_map[str(ih.payment_id)] = ih
        
        # Collect payments needing document generation
        payments_needing_docs = []
        
        # Process the data with prefetched instructor hours
        for idx, payment in enumerate(data):
            if payment.get('instructor') is not None:
                instructor_hour = instructor_hours_map.get(payment['payment_id'])
                
                if instructor_hour is not None:
                    if instructor_hour.document is None:
                        payments_needing_docs.append(payment['payment_id'])
                    else:
                        data[idx]['instructor_document'] = instructor_hour.document.document_id
                        data[idx]['instructor_document_token'] = instructor_hour.document.token
        
        # Batch process document generation if needed
        if payments_needing_docs:
            for payment_id in payments_needing_docs:
                print_document_compensation.apply(
                    args=[str(payment_id), request.headers.get('authorization')])
            
            # Re-fetch instructor hours after document generation
            updated_hours = InstructorHours.objects.filter(
                payment_id__in=payments_needing_docs
            ).select_related('document')
            
            for ih in updated_hours:
                for idx, payment in enumerate(data):
                    if payment['payment_id'] == str(ih.payment_id) and ih.document is not None:
                        data[idx]['instructor_document'] = ih.document.document_id
                        data[idx]['instructor_document_token'] = ih.document.token

    res_obj = {
        'data': data,
        "meta": {}
    }

    if User.ATHLETE != request.user.role:
        res_obj['meta'] = {
            "total": paginator.page.paginator.count,
            "page": paginator.page.number,
            "pages": paginator.page.paginator.num_pages,
            "perpage": paginator.page.paginator.per_page,
            "rowIds": [payment.payment_id for payment in payments]
        }

    return Response(res_obj, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def payment_stats(request):
    payment_range = request.GET.get('query[payment_range]', None)
    expense = request.GET.get('query[expense]', None)
    general_search = request.GET.get('query[generalSearch]', None)
    subject = request.GET.get('query[subject]', None)
    payment_categories = request.GET.get('query[payment_categories]', None)
    paid = request.GET.get('query[paid]', None)
    type = request.GET.get('query[type]', None)
    account = request.GET.get('query[account]', None)

    sport_association = SportAssociation.objects.get(user=request.user)

    if sport_association is None:
        return Response({"msg": "Sport association not found."}, status=status.HTTP_404_NOT_FOUND)

    # payment from is this month
    payment_from = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # payment to is today
    payment_to = datetime.now()

    if payment_range is not None:
        # if payment range split pattern: 'YYYY/MM/DD al YYYY/MM/DD'
        payment_from, payment_to = payment_range.split(' al ')
        try:
            payment_from = datetime.strptime(payment_from, '%Y/%m/%d')
            payment_to = datetime.strptime(payment_to, '%Y/%m/%d')
        except Exception as e:
            try:
                # try to parse the date in the format 'DD/MM/YYYY'
                payment_from = datetime.strptime(payment_from, '%d/%m/%Y')
                payment_to = datetime.strptime(payment_to, '%d/%m/%Y')
            except Exception as e:
                logger.exception(e)
                payment_from, payment_to = datetime.now() - timezone.timedelta(
                    days=365), datetime.now() + timezone.timedelta(days=1)

    #payment_to += + timezone.timedelta(days=1)
    payment_to = payment_to.replace(hour=0, minute=0, second=0, microsecond=0)

    # Build query filters dynamically (DRY principle)
    filters = Q(sport_association=sport_association, archived=False)
    # Date range filter using COALESCE for better performance

    # More efficient date filtering
    filters &= (
        Q(payment_date__range=(payment_from, payment_to), payment_date__isnull=False) |
        Q(creation_date__range=(payment_from, payment_to), payment_date__isnull=True)
    )
    
    # Apply optional filters (KISS principle - simple conditionals)
    if subject is not None:
        filters &= Q(subject=subject)
    
    if type is not None:
        filters &= Q(type=type)
    
    if paid is not None:
        filters &= Q(paid=(paid == 'true'))
    
    if expense is not None:
        filters &= Q(expense=(expense == 'true'))
    
    if payment_categories:
        if payment_categories == "[]":
            filters &= Q(payment_category__isnull=True)
        else:
            filters &= Q(payment_category__in=payment_categories.split(','))
    
    if account:
        if account == "[]":
            filters &= Q(custom_accounts__isnull=True)
        else:
            filters &= Q(custom_accounts__custom_account_id=account)
    
    # Apply all filters at once with select_related for optimization
    payments = Payment.objects.filter(filters).select_related(
        'custom_accounts',
        'payment_category'
    ).order_by('-creation_date')

    # filter for general search
    if general_search:
        # check if general_search is a date
        try:
            date = datetime.strptime(general_search, '%d/%m/%Y').strftime('%Y-%m-%d')
            general_search = date
        except ValueError:
            pass

        try:
            # Try to filter by amount
            amount_value = float(general_search.replace(',', '.'))
            payments = payments.filter(
                Q(amount__icontains=amount_value) |
                Q(amount__exact=amount_value)
            )
        except (ValueError, TypeError):
            # If not a number, use smart_search
            # No need to add select_related again as we already did it above
            payments = smart_search(payments, general_search)

    # calculate stripe fees with caching
    stripe_charges = {
        'total': 0,
        'application_fee_amount': 0,
        'stripe_fee': 0,
    }
    
    if request.user.stripe_account_id and request.user.stripe_on_boarding_completed:
        # Create cache key based on user, account, and date range
        cache_key = f"stripe_fees_{request.user.stripe_account_id}_{int(payment_from.timestamp())}_{int(payment_to.timestamp())}"
        
        # Try to get from cache first
        cached_charges = cache.get(cache_key)
        
        if cached_charges is not None:
            stripe_charges = cached_charges
        else:
            # If not in cache, fetch from Stripe API
            stripe_charges['application_fee_amount'] = 0
            stripe_charges['stripe_fee'] = 0
            stripe_charges['other'] = 0
            stripe_charges['total'] = 0

            try:
                transactions = stripe.BalanceTransaction.list(
                    created={
                        'gte': int(payment_from.timestamp()),
                        'lte': int(payment_to.timestamp())
                    },
                    stripe_account=request.user.stripe_account_id,
                    cancelation_reason = None,
                )
                
                for txn in transactions.auto_paging_iter():
                    # Exclude payouts
                    if txn['type'] == 'payout':
                        continue

                    for fee_detail in txn['fee_details']:
                        if fee_detail['type'] == 'stripe_fee':
                            stripe_charges['stripe_fee'] += fee_detail['amount']
                        elif fee_detail['type'] == 'application_fee':
                            stripe_charges['application_fee_amount'] += fee_detail['amount']
                        else:
                            logger.info(f"fee_detail: {fee_detail}")
                            continue

                        stripe_charges['total'] += fee_detail['amount']
                        
                # Round charges
                stripe_charges['total'] = round(float(stripe_charges['total']) / 100, 2)
                stripe_charges['stripe_fee'] = round(float(stripe_charges['stripe_fee']) / 100, 2)
                stripe_charges['application_fee_amount'] = round(float(stripe_charges['application_fee_amount']) / 100, 2)
                
                # Cache for 5 minutes (300 seconds)
                cache.set(cache_key, stripe_charges, 300)
                
            except Exception as e:
                logger.error(f"txs fees issue: {e}")
                # Return empty charges on error but don't cache
                stripe_charges = {
                    'total': 0,
                    'application_fee_amount': 0,
                    'stripe_fee': 0,
                }

    accounts = {
        'cash': 0,
        'bank': 0,
        'other': 0,
    }


    accounts_map = {
        1: 'cash',
        2: 'bank',
        3: 'other'
    }

    # Single database query to get all aggregated values
    aggregated_data = payments.aggregate(
        total_transactions=Count('payment_id'),
        current_expenses=Sum(Case(
            When(expense=True, then='amount'),
            default=0,
            output_field=DecimalField()
        )),
        current_income=Sum(Case(
            When(expense=False, then='amount'),
            default=0,
            output_field=DecimalField()
        )),
        monthly_expenses=Sum(Case(
            When(expense=True, paid=True, then='amount'),
            default=0,
            output_field=DecimalField()
        )),
        monthly_to_pay=Sum(Case(
            When(expense=True, paid=False, then='amount'),
            default=0,
            output_field=DecimalField()
        )),
        monthly_income=Sum(Case(
            When(expense=False, paid=True, then='amount'),
            default=0,
            output_field=DecimalField()
        )),
        monthly_to_cash_in=Sum(Case(
            When(expense=False, paid=False, then='amount'),
            default=0,
            output_field=DecimalField()
        ))
    )

    # Group transactions by account type and calculate net changes
    account_changes = payments.filter(paid=True).values('custom_accounts__account_type').annotate(
        net_change=Sum(Case(
            When(expense=True, then=-F('amount')),
            When(expense=False, then=F('amount')),
            default=0,
            output_field=DecimalField()
        ))
    )

    # Apply changes to initial balances
    for change in account_changes:
        try:
            account_type = change['custom_accounts__account_type']
            if account_type in accounts_map:
                accounts[accounts_map[account_type]] += change['net_change'] or Decimal('0')
        except Exception as e:
            logger.exception(e)

    # Calculate net balance
    net_balance = Decimal(aggregated_data['current_income'] or 0) - \
                  Decimal(aggregated_data['current_expenses'] or 0) + \
                  Decimal(aggregated_data['monthly_to_pay'] or 0) - \
                  Decimal(aggregated_data['monthly_to_cash_in'] or 0) - \
                  Decimal(stripe_charges['total'] or 0)

    return Response({
        "data": {
            "stripe_charges": stripe_charges,
            "monthly_income": aggregated_data['monthly_income'] or 0,
            "monthly_expenses": aggregated_data['monthly_expenses'] or 0,
            "monthly_to_pay": aggregated_data['monthly_to_pay'] or 0,
            "monthly_to_cash_in": aggregated_data['monthly_to_cash_in'] or 0,
            "current_income": aggregated_data['current_income'] or 0,
            "current_expenses": aggregated_data['current_expenses'] or 0,
            "accounts": accounts,
            "total_transactions": aggregated_data['total_transactions'],
            "net_balance": net_balance,
        }
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def payment_list_export(request):

    logger.info("payment_list_export")
    export_mode = request.GET.get('m', 'csv')
    export_type = request.GET.get('export_type', None)
    general_search = request.GET.get('query[generalSearch]', None)
    paid = request.GET.get('query[paid]', None)
    expense = request.GET.get('query[expense]', None)
    payment_range = request.GET.get('query[payment_range]', None)
    archived = request.GET.get('archived', False)
    payment_categories = request.GET.get('query[payment_categories]', None)
    account = request.GET.get('query[account]', None)

    is_petty_cash_book = False

    if export_type == 'petty_cash_book':
        is_petty_cash_book = True

    if User.ATHLETE == request.user.role:
        raise Exception("Cannot export payments for athlete")
    else:
        sport_association = SportAssociation.objects.get(user=request.user)
        # payments = Payment.objects.filter(sport_association=sport_association)
        payment_from = datetime.now() - timezone.timedelta(days=365 * 10)
        # default payment to is today
        payment_to = datetime.now()
        if payment_range is not None or payment_range == '':
            # if payment range split pattern: 'YYYY/MM/DD al YYYY/MM/DD'
            payment_from, payment_to = payment_range.split(' al ')
            try:
                payment_from = datetime.strptime(payment_from, '%Y/%m/%d')
                payment_to = datetime.strptime(payment_to, '%Y/%m/%d')
            except Exception as e:
                try:
                    # try to parse the date in the format 'DD/MM/YYYY'
                    payment_from = datetime.strptime(payment_from, '%d/%m/%Y')
                    payment_to = datetime.strptime(payment_to, '%d/%m/%Y')
                except Exception as e:
                    logger.exception(e)
                    payment_from, payment_to = datetime.now() - timezone.timedelta(days=365), datetime.now() + timezone.timedelta(days=1)

        payment_to += + timezone.timedelta(days=1)
        payment_to = payment_to.replace(hour=0, minute=0, second=0, microsecond=0)
        payments = Payment.objects.filter(
            sport_association=sport_association,
            archived=archived).filter(
            (Q(payment_date__range=(payment_from, payment_to)) & Q(payment_date__isnull=False)) |
            (Q(creation_date__range=(payment_from, payment_to)) & Q(payment_date__isnull=True))
        ).prefetch_related(
            Prefetch(
                'coursesubscription_set',  # Note: this is the default reverse lookup name
                queryset=CourseSubscription.objects.all_objects().select_related('course')
            ),
            Prefetch(
                'coursesubscriptioninstallment_set',  # Note: this is the default reverse lookup name
                queryset=CourseSubscriptionInstallment.objects.all_objects().select_related(
                    'course_subscription',
                    'course_subscription__course'
                )
            )
        ).order_by('-payment_date')
        if paid is not None or is_petty_cash_book:
            if paid == 'true' or is_petty_cash_book:
                payments = payments.filter(paid=True)
            else:
                payments = payments.filter(paid=False)
        if expense is not None and not is_petty_cash_book:
            if expense == 'true':
                payments = payments.filter(expense=True)
            else:
                payments = payments.filter(expense=False)
        if payment_categories is not None and not payment_categories == "[]" and not payment_categories == "":
            payments = payments.filter(payment_category__in=payment_categories.split(','))
        elif payment_categories == "[]":
            payments = payments.filter(payment_category__isnull=True)

        if account is not None and not account == "" and not account == "[]":
            payments = payments.filter(custom_accounts__custom_account_id=account)
        elif account == "[]":
            payments = payments.filter(custom_accounts__isnull=True)

        # filter for general search
        if general_search and not is_petty_cash_book:
            # check if general_search is a date
            try:
                general_search = datetime.strptime(general_search, '%d/%m/%Y').strftime('%Y-%m-%d')
            except ValueError:
                pass

            try:
                payments = payments.filter(
                    Q(amount__icontains=float(general_search.replace(',', '.'))) |
                    Q(amount__exact=float(general_search.replace(',', '.')))
                )
            except Exception as e:
                payments = smart_search(
                    payments,
                    general_search,
                )

        if is_petty_cash_book:
            custom_accounts_transfers = CustomAccountsTransfer.objects.filter(
                sport_association=sport_association,
                date__range=(payment_from, payment_to)
            )
            # we need to fill the payments array with the custom_accounts_transfers
            # by creating fake payments with the same fields of the custom_accounts_transfers
            payments = list(payments)
            for custom_accounts_transfer in custom_accounts_transfers:
                payment_from_account = Payment(
                    payment_id=custom_accounts_transfer.custom_account_transfer_id,
                    amount=custom_accounts_transfer.amount,
                    payment_date=custom_accounts_transfer.date,
                    paid=True,
                    expense=True,
                    custom_accounts=custom_accounts_transfer.custom_account_from,
                    description=f"Trasferimento da {custom_accounts_transfer.custom_account_from.name} a {custom_accounts_transfer.custom_account_to.name}",
                    subject=Payment.ACCOUNT_TRANSFER,
                    creation_date=custom_accounts_transfer.date,
                    sport_association=sport_association
                )
                payment_to_account = Payment(
                    payment_id=custom_accounts_transfer.custom_account_transfer_id,
                    amount=custom_accounts_transfer.amount,
                    payment_date=custom_accounts_transfer.date,
                    paid=True,
                    expense=False,
                    custom_accounts=custom_accounts_transfer.custom_account_to,
                    description=f"Trasferimento da {custom_accounts_transfer.custom_account_from.name} a {custom_accounts_transfer.custom_account_to.name}",
                    subject=Payment.ACCOUNT_TRANSFER,
                    creation_date=custom_accounts_transfer.date,
                    sport_association=sport_association
                )
                payments.append(payment_from_account)
                payments.append(payment_to_account)

        if type(payments) is not list:
            payments = list(payments)

        # sort by payment_date
        payments.sort(key=lambda x: x.payment_date if x.payment_date else timezone.now(), reverse=False)

        users = []
        activities = []
        progressive_accounts = []

        custom_accounts_initial_balance = []
        if is_petty_cash_book:
            # from 1970-01-01 to payment_from
            date_from_account_balance = datetime(1970, 1, 1)
            # get all the accounts
            custom_accounts = CustomAccounts.objects.all()
            for account in custom_accounts:
                balance = CustomAccountSerializer(account).get_current_balance_from(
                    date_from=date_from_account_balance,
                    date_to=payment_from,
                )
                custom_accounts_initial_balance.append({
                    'custom_account_id': account.custom_account_id,
                    'name': account.name,
                    'balance': float(balance)
                })

        for payment in payments:
            ## update progressive for each payment
            # filter the custom_accounts_initial_balance based on payment.account
            if payment.paid:
                # find the custom_accounts_initial_balance with the same name of payment.custom_accounts
                for idx, custom_account in enumerate(custom_accounts_initial_balance):
                    if payment.custom_accounts and custom_account['custom_account_id'] == payment.custom_accounts.custom_account_id:
                        custom_account['balance'] = float(custom_account['balance']) \
                                                    + (float(payment.amount) * (-1 if payment.expense else 1))
                        progressive_accounts.append(custom_account['balance'])
                        # update the custom_accounts_initial_balance
                        custom_accounts_initial_balance[idx]['balance'] = custom_account['balance']
                        break
            else:
                progressive_accounts.append("-")

            ## end update progressive for each payment
            if payment.associate:
                users.append('{} {}'.format(payment.associate.first_name, payment.associate.last_name))
            elif payment.instructor:
                users.append('{} {}'.format(payment.instructor.first_name, payment.instructor.last_name))
            elif payment.supplier:
                users.append(payment.supplier.name)
            else:
                users.append(payment.description)

            if payment.subject == 0:
                if payment.payment_category:
                    activities.append(payment.payment_category.name)
                else:
                    activities.append('Altro')
            elif payment.subject == 1:
                activities.append('Iscrizione')
            elif payment.subject == 2:
                activities.append('Corso')
            elif payment.subject == 3:
                activities.append('Giroconto')

        if len(payments) != len(users):
            logger.error("Size different between payments and users during export")
            raise Exception("Size different between payments and users during export")

        # get payment categories
        payment_categories = PaymentCategory.objects.filter(
            Q(sport_association=sport_association) | Q(sport_association__isnull=True))
        # create a dict with key = payment_category_id and value = name
        payment_categories_dict = {}
        for payment_category in payment_categories:
            payment_categories_dict[str(payment_category.payment_category_id)] = payment_category.name

        columns = [
            'Informazioni intestatario',
            'Stato',
            'Modalità di pagamento',
            'Data creazione',
            'Data pagamento',
            'Numero ricevuta',
            'Attività',
            'Causali aggiuntive',
            'Causale',
            'Conto',
            'Tipo',
            'Corso',
            'Importo',
            'Note',
            # 'Saldo Progressivo'
        ]

        if is_petty_cash_book:
            columns.append('Saldo progressivo')

        data = {
            "file": ""
        }

        if export_mode == 'xlsx':
            data['type'] = 'xlsx'
            data['filename'] = "[{}] {} a {}.xlsx".format(
                date.today().strftime("%Y-%m-%d"),
                "PAGAMENTI",
                sport_association.denomination
            )

            rows = [
                users,
                ["Pagato" if payment.paid else "In Attesa" for payment in payments],
                ["-" if payment.type == Payment.DEFAULT else payment.type for payment in payments],
                [payment.creation_date.strftime('%Y-%m-%d') for payment in payments],
                [payment.payment_date.strftime('%Y-%m-%d') if payment.payment_date else "" for payment in payments],
                # [payment.type for payment in payments],
                [payment.invoice.number if payment.invoice else "" for payment in payments],
                activities,
                [','.join(
                        [payment_categories_dict[mpc['payment_category_id']] for mpc in
                         payment.meta_payment_categories
                         if 'payment_category_id' in mpc and mpc['payment_category_id'] in payment_categories_dict.keys()
                         ]) if payment.meta_payment_categories and len(payment.meta_payment_categories) > 0
                    else '' for payment in payments],
                [payment.payment_category.name if payment.payment_category else '-' for payment in payments],
                [payment.custom_accounts.name if payment.custom_accounts else '-' for payment in payments],
                ["Uscita" if payment.expense else "Entrata" for payment in payments],
                [
                    (payment.coursesubscription_set.first().course.title if payment.coursesubscription_set.exists() else
                     payment.coursesubscriptioninstallment_set.first().course_subscription.course.title if payment.coursesubscriptioninstallment_set.exists() else
                     payment.course['label'] if payment.course is not None and payment.course != '' and 'label' in payment.course else "")
                    for payment in payments
                ],
                [payment.amount for payment in payments],
                [payment.notes for payment in payments],
            ]
            if is_petty_cash_book:
                rows.append(progressive_accounts)

            # create an excel file passing all the fields from class Payment
            excel_base64 = get_excel_base64(
                *rows,
                columns=columns
            )
            if excel_base64 is None:
                raise Exception("Error while generating excel file")
            else:
                data["file"] = excel_base64
        else:
            data['type'] = 'csv'
            data['filename'] = "[{}] {} a {}.csv".format(
                date.today().strftime("%Y-%m-%d"),
                "PAGAMENTI",
                sport_association.denomination
            )
            f = StringIO()
            csv.writer(f).writerow(columns)
            if is_petty_cash_book:
                csv.writer(f).writerows(
                    [[
                        user,
                        "Pagato" if payment.paid else "In Attesa",
                        "-" if payment.type == Payment.DEFAULT else payment.type,
                        payment.creation_date.strftime('%Y-%m-%d'),
                        payment.payment_date.strftime('%Y-%m-%d') if payment.payment_date else "",
                        # [payment.type for payment in payments],
                        payment.invoice.number if payment.invoice else "",
                        activity,
                        ','.join(
                            [payment_categories_dict[mpc['payment_category_id']] for mpc in
                             payment.meta_payment_categories
                             if mpc['payment_category_id'] in payment_categories_dict.keys()
                             ]) if payment.meta_payment_categories and len(payment.meta_payment_categories) > 0
                        else '',
                        payment.custom_accounts.name,
                        "Uscita" if payment.expense else "Entrata",
                        ( payment.coursesubscription_set.first().course.title if payment.coursesubscription_set.exists() else
                        payment.coursesubscriptioninstallment_set.first().course_subscription.course.title if payment.coursesubscriptioninstallment_set.exists() else
                        payment.course['label'] if payment.course is not None and payment.course != '' and 'label' in payment.course else "" ),
                        payment.amount,
                        payment.notes,
                        progressive_account
                    ] for payment, user, activity, progressive_account in list(zip(
                        payments, users, activities, progressive_accounts))])
            else:
                csv.writer(f).writerows(
                    [[
                        user,
                        "Pagato" if payment.paid else "In Attesa",
                        "-" if payment.type == Payment.DEFAULT else payment.type,
                        payment.creation_date.strftime('%Y-%m-%d'),
                        payment.payment_date.strftime('%Y-%m-%d') if payment.payment_date else "",
                        # [payment.type for payment in payments],
                        payment.invoice.number if payment.invoice else "",
                        activity,
                        ','.join(
                            [payment_categories_dict[mpc['payment_category_id']] for mpc in
                             payment.meta_payment_categories
                             if mpc['payment_category_id'] in payment_categories_dict.keys()
                             ]) if payment.meta_payment_categories and len(payment.meta_payment_categories) > 0
                        else '',
                        payment.payment_category.name if payment.payment_category else '-',
                        payment.custom_accounts.name if payment.custom_accounts else '-',
                        "Uscita" if payment.expense else "Entrata",
                        (payment.coursesubscription_set.first().course.title if payment.coursesubscription_set.exists() else
                        payment.coursesubscriptioninstallment_set.first().course_subscription.course.title if payment.coursesubscriptioninstallment_set.exists() else
                        payment.course['label'] if payment.course is not None and payment.course != '' and 'label' in payment.course else ""),
                        payment.amount,
                        payment.notes,
                    ] for payment, user, activity in list(zip(
                        payments, users, activities))])
            data["file"] = base64.b64encode(f.getvalue().encode())

        return Response({'data': data}, status=status.HTTP_200_OK)
    return Response({'mgs': 'error'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
def payment_info(request, uid):

    is_valid_uuid(uid)
    # one_fee_payment = request.GET.get('one_fee_payment', False)

    if request.user is not None:
        user = None
    else:
        user = request.user

    # case athlete is asking for his payment
    if user is not None and User.ATHLETE == request.user.role:
        payment = Payment.objects.filter(payment_id=uid).first()
    elif user is not None and User.ATHLETE != request.user.role:
        sport_association = SportAssociation.objects.get(user=user)
        payment = Payment.objects.filter(sport_association=sport_association, payment_id=uid).first()
    else:
        payment = Payment.objects.filter(payment_id=uid).first()
        if payment is None:
            return Response({'msg': 'Pagamento non trovato'}, status=status.HTTP_404_NOT_FOUND)
        sport_association = payment.sport_association
        user = sport_association.user

    data = PaymentSerializerInfo(payment).data

    if payment.subject == Payment.COURSE:
        course_subscription = CourseSubscription.objects.filter(payment=payment).first()
        if course_subscription is None:
            course_subscription_installment = CourseSubscriptionInstallment.objects.filter(payment=payment).first()
            if course_subscription_installment is None:
                carnet_subscription = CarnetSubscription.objects.filter(payment=payment).first()
                if carnet_subscription is not None:
                    data['info'] = {
                        'title': f"Carnet ({carnet_subscription.carnet_id.title})",
                        'description': carnet_subscription.carnet_id.description,
                        'fee': carnet_subscription.carnet_id.fee,
                        'sport_association': SportAssociationBasicInfo(
                            carnet_subscription.carnet_id.sport_association).data,
                    }
                    logger.info("payment_info -> ended (CARNET) -> user: {}".format(user.user_id))
                    return Response({'data': data}, status=status.HTTP_200_OK)
                else:
                    raise Exception("No course subscription found for payment {}".format(payment.payment_id))
            else:
                course_subscription = course_subscription_installment.course_subscription
        course = course_subscription.course
        data["info"] = CourseSerializerInfo(course).data
    elif payment.subject == Payment.SUBSCRIPTION:
        data["info"] = SportAssociationBasicInfo(
            payment.sport_association).data
    elif payment.subject == Payment.OTHER:
        data["info"] = SportAssociationBasicInfo(
            payment.sport_association).data
        # add meta payment categories
        data["info"]["meta_payment_categories"] = payment.meta_payment_categories
        # loop through the meta payment categories and add the meta payment category name
        if "meta_payment_categories" in data['info'] and data["info"]["meta_payment_categories"]:
            for meta_payment_category in data["info"]["meta_payment_categories"]:
                meta_payment_category["payment_category_name"] = PaymentCategory.objects.get(
                    payment_category_id=meta_payment_category["payment_category_id"]).name

    # add the payment categories
    data["info"]["payment_category_name"] = payment.payment_category.name if payment.payment_category else None

    logger.info("payment_info -> ended -> user: {}".format(user.user_id))
    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payment_approve(request, uid):
    logger.info("Approving payment", extra={'user_id': str(request.user.user_id), 'payment_id': uid})

    is_valid_uuid(uid)

    # get data payment_date
    payment_date = request.data.get('payment_date', None)
    send_receipt_email = request.data.get('send_receipt_email', False)
    generate_invoice = request.data.get('generate_invoice', True)

    # convert payment_date to datetime if not none
    if payment_date is not None:
        payment_date = datetime.strptime(payment_date, '%Y-%m-%d')
        # make it timezone aware
        payment_date = timezone.make_aware(payment_date)

    logger.info("payment_approve -> init -> user: {}".format(request.user.user_id))
    sport_association = SportAssociation.objects.get(user=request.user)
    with transaction.atomic():
        # select the payment for safe update
        payment = Payment.objects.select_for_update().filter(payment_id=uid).first()
        if payment.sport_association.sport_association_id != sport_association.sport_association_id:
            raise PermissionDenied("User not allowed.")

        logger.debug("Payment status check", extra={'payment_id': uid, 'currently_paid': payment.paid, 'is_expense': payment.expense})
        if payment.paid is False:
            logger.info("Marking payment as paid", extra={'payment_id': uid})
            payment.paid = True

            if payment.expense:
                # check if there is a payment date
                if payment.payment_date is None and payment_date is None:
                    payment.payment_date = timezone.now()
                elif payment_date is not None:
                    payment.payment_date = payment_date
                payment.save()
                # check if there are suppliers invoices to pay
                suppliers_invoice = InvoiceSuppliers.objects.filter(
                    payment=payment,
                ).first()
                if suppliers_invoice:
                    suppliers_invoice.paid = True
                    suppliers_invoice.payment_date = payment.payment_date
                    suppliers_invoice.save()
                return Response({'data': {"payment": PaymentSerializer(payment).data }}, status=status.HTTP_200_OK)

            if payment.payment_date is None and payment_date is None:
                payment.payment_date = timezone.now()
            elif payment_date is not None:
                payment.payment_date = payment_date

            # check if there is an associate or a supplier or a instructor
            if payment.associate is None and payment.supplier is None and payment.instructor is None:
                payment.save()
                return Response({'data': {"payment":  PaymentSerializer(payment).data }}, status=status.HTTP_200_OK)

            membership_fee = payment.amount if payment.subject is Payment.SUBSCRIPTION else 0.00
            activity_fee = payment.amount if payment.subject is not Payment.SUBSCRIPTION else 0.00
            if payment.meta_payment_categories and payment.subject is Payment.SUBSCRIPTION:
                # sum the amount of the meta payment categories
                for meta_payment_category in payment.meta_payment_categories:
                    if meta_payment_category["amount"]:
                        activity_fee = float(activity_fee) + float(str(meta_payment_category["amount"]))
            if membership_fee > 0:
                membership_fee = float(str(membership_fee)) - float(activity_fee)

            invoice_number = InvoiceService.get_next_invoice_number(
                sport_association,
                payment.payment_date,
                request.user,
                use_payment_date=True
            )

            # description text based on the type of the payment
            description = generate_invoice_description(payment, sport_association)
            if payment.supplier is not None or payment.associate is not None:
                invoice = Invoice.objects.create(
                    sport_association=sport_association,
                    description=description,
                    membership_fee=membership_fee,
                    activity_fee=activity_fee,
                    number=invoice_number,
                    meta_payment_categories=payment.meta_payment_categories,
                )
                if request.user.payment_date_equal_invoice_date:
                    invoice.creation_date = payment.payment_date
                invoice.save()
                payment.invoice = invoice
            payment.save()

            # check if there are suppliers invoices to pay
            suppliers_invoice = InvoiceSuppliers.objects.filter(
                payment=payment,
            ).first()

            if suppliers_invoice:
                suppliers_invoice.paid = True
                suppliers_invoice.payment_date = timezone.now()
                suppliers_invoice.save()

            course_subscription = None
            is_carnet = False
            carnet_sub = None
            if payment.subject is Payment.COURSE:
                course_subscription = CourseSubscription.objects.filter(payment=payment).first()
                if course_subscription is None:
                    course_subscription = CourseSubscriptionInstallment.objects.filter(payment=payment).first()
                    if course_subscription is None:
                        carnet_sub = CarnetSubscription.objects.filter(payment=payment).first()
                        if carnet_sub:
                            course_subscription = carnet_sub.course_subscription.all()
                            is_carnet = True
                    else:
                        course_subscription = course_subscription.course_subscription
                if not is_carnet and course_subscription:
                    course_subscription.paid = True
                    course_subscription.save()
                elif course_subscription is not None:
                    for cs in course_subscription:
                        cs.paid = True
                        cs.save()

            description = ""
            if is_carnet:
                description = f"carnet {carnet_sub.carnet_id.title}"
            elif payment.subject is Payment.COURSE:
                try:
                    description = "il corso \"{}\"".format(course_subscription.course.title)
                except Exception as e:
                    description = 'il corso'
            elif payment.subject is Payment.SUBSCRIPTION:
                description = "l'iscrizione all'associazione sportiva"
            else:
                description = payment.description

            notification_description = f"Pagamento di € {str(payment.amount).replace('.', ',')}"
            if description:
                notification_description += f"per {description}"
            notification_description += "."

            # check if there is an associate
            if payment.associate:
                notification_description += " Pagamento dell'associato {}.".format(payment.associate.get_full_name())
            elif payment.user:
                notification_description += " Pagamento dell'utente {}.".format(payment.user.get_full_name())

            messages = [
                {
                    "type": NotificationUtils.PAYMENT,
                    "msg": notification_description,
                }
            ]

            NotificationService.send_notification(request.user, messages)

        data = {"msg": "payment mark as payed.", "payment": PaymentSerializer(payment).data}
    if payment.invoice and generate_invoice:
        print_document_invoice.apply_async(args=[
            str(payment.invoice.invoice_id),
            request.headers.get('authorization'),
            send_receipt_email,
        ])
        data['payment']['invoice_generating'] = True
    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payment_cancel(request, uid):

    sport_association = SportAssociation.objects.get(user=request.user)
    # select the payment for safe update
    payment = Payment.objects.filter(payment_id=uid).first()
    if payment.sport_association.sport_association_id != sport_association.sport_association_id:
        raise PermissionDenied("User not allowed.")

    payment_serialized = PaymentSerializer(payment).data
    if payment.paid is True:
        payment.paid = False
        payment.save()
        if payment.invoice:
            payment.invoice.cancelled = True
            # set invoice meta to payment data serialized
            payment_serialized = PaymentSerializer(payment).data
            payment_obj = {
                "payment_id": str(payment_serialized["payment_id"]),
                "user": str(payment_serialized["user"]),
                "associate": str(payment_serialized["associate"]),
                "subscription": str(payment_serialized["subscription"]) if "subscription" in payment_serialized and \
                                                                           payment_serialized["subscription"] else None,
                "subject": payment_serialized["subject"],
                "amount": payment_serialized["amount"],
                "creation_date": payment_serialized["creation_date"],
                "payment_date": payment_serialized["payment_date"],
            }
            payment.invoice.meta = payment_obj
            payment.invoice.save()
            print_document_invoice.apply_async(args=[str(payment.invoice.invoice_id), request.headers.get('authorization')])
    else:
        return Response({'error': 'Payment not paid.'}, status=status.HTTP_400_BAD_REQUEST)

    data = {"msg": "payment cancelled.", "payment": payment_serialized}
    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payment_generate_invoice(request, uid):

    is_valid_uuid(uid)

    logger.info("payemnt_generate_invoice -> init -> user: {}".format(request.user.user_id))
    sport_association = SportAssociation.objects.get(user=request.user)
    # get only_print from request.data
    only_print = request.data.get('only_print', False)
    if only_print:
        payment = Payment.objects.filter(payment_id=uid).first()
        print_document_invoice.apply_async(
            args=[str(payment.invoice.invoice_id), request.headers.get('authorization')])
        data = {"msg": "generated invoice.", 'payment': PaymentSerializer(payment).data}
        data['payment']['invoice_generating'] = True
        return Response({'data': data}, status=status.HTTP_200_OK)

    # select the payment for safe update
    with transaction.atomic():
        payment = Payment.objects.select_for_update().filter(payment_id=uid).first()
        if payment.sport_association.sport_association_id != sport_association.sport_association_id:
            raise PermissionDenied("User not allowed.")

        # get subscription_id from request.data
        subscription_id = request.data.get("subscription_id", None)

        if subscription_id is None:
            return Response({'error': 'Seleziona l\'associato.'}, status=status.HTTP_400_BAD_REQUEST)

        is_valid_uuid(subscription_id)

        # get subscription
        subscription = Subscription.objects.filter(subscription_id=subscription_id).first()

        payment.paid = True
        payment.associate = subscription.associate

        membership_fee = payment.amount if payment.subject is Payment.SUBSCRIPTION else 0.00
        activity_fee = payment.amount if payment.subject is not Payment.SUBSCRIPTION else 0.00
        if payment.meta_payment_categories and payment.subject is Payment.SUBSCRIPTION:
            # sum the amount of the meta payment categories
            for meta_payment_category in payment.meta_payment_categories:
                if meta_payment_category["amount"]:
                    activity_fee = float(activity_fee) + float(str(meta_payment_category["amount"]))
        if membership_fee > 0:
            membership_fee = float(str(membership_fee)) - float(activity_fee)

        invoice_number = InvoiceService.get_next_invoice_number(
            sport_association,
            payment.creation_date,
            request.user,
            use_payment_date=False
        )

        # description text based on the type of the payment
        description = generate_invoice_description(payment, sport_association)

        invoice = Invoice.objects.create(
            sport_association=sport_association,
            description=description,
            membership_fee=membership_fee,
            activity_fee=activity_fee,
            number=invoice_number,
            meta_payment_categories=payment.meta_payment_categories,
        )
        if request.user.payment_date_equal_invoice_date:
            invoice.creation_date = payment.payment_date
            invoice.save()
        payment.invoice = invoice
        payment.save()

        data = {"msg": "generated invoice.", 'payment': PaymentSerializer(payment).data}
        data['payment']['invoice_generating'] = True
    print_document_invoice.apply_async(args=[str(payment.invoice.invoice_id), request.headers.get('authorization')])
    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def payment_category_list(request):

    logger.info("payment_category_list -> init -> user: {}".format(request.user.user_id))

    # check if the user is a sport association
    if request.user.role != User.ASSOCIATION:
        logger.info("payment_category_list -> ended -> user: {}".format(request.user.user_id))
        return Response({'error': 'user not allowed.'}, status=status.HTTP_403_FORBIDDEN)

    sport_association = SportAssociation.objects.filter(user=request.user).first()
    payment_categories = PaymentCategory.objects.all().filter(
        Q(sport_association=sport_association) | Q(sport_association=None)).order_by('-creation_date')

    data = PaymentCategorySerializer(payment_categories, many=True).data

    logger.info("payment_category_list -> ended -> user: {}".format(request.user.user_id))

    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payment_category_add(request):

    logger.info("payment_category_add -> init -> user: {}".format(request.user.user_id))

    # check if the user is a sport association
    if request.user.role != User.ASSOCIATION:
        logger.info("payment_category_add -> ended -> user: {}".format(request.user.user_id))
        return Response({'error': 'user not authorized'}, status=status.HTTP_401_UNAUTHORIZED)

    serializer = PaymentCategorySerializer(data=request.data)
    # get the sport association of the user
    sport_association = SportAssociation.objects.filter(user=request.user).first()

    # set the sport association of the payment category
    serializer.initial_data['sport_association'] = sport_association.sport_association_id

    if serializer.is_valid():
        serializer.save()
        logger.info("payment_category_add -> ended -> user: {}".format(request.user.user_id))
        return Response({'data': serializer.data, 'msg': 'payment category created'}, status=status.HTTP_200_OK)

    logger.info("payment_category_add -> ended -> user: {}".format(request.user.user_id))
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def payment_category_update(request, uid):

    logger.info("payment_category_update -> init -> user: {}".format(request.user.user_id))

    # check if the user is a sport association
    if request.user.role != User.ASSOCIATION:
        logger.info("payment_category_update -> ended -> user: {}".format(request.user.user_id))
        return Response({'error': 'user not authorized'}, status=status.HTTP_401_UNAUTHORIZED)

    # get the sport association of the user
    sport_association = SportAssociation.objects.filter(user=request.user).first()

    # check that the payment category is of the sport association
    payment_category = PaymentCategory.objects.filter(
        payment_category_id=uid,
        sport_association=sport_association
    ).first()

    if not payment_category:
        logger.info("payment_category_update -> ended -> user: {}".format(request.user.user_id))
        return Response({'error': 'payment category not found'}, status=status.HTTP_404_NOT_FOUND)

    serializer = PaymentCategorySerializer(payment_category, data=request.data, partial=True)
    if serializer.is_valid():
        serializer.save()
        logger.info("payment_category_update -> ended -> user: {}".format(request.user.user_id))
        return Response({'data': serializer.data, 'msg': 'payment category updated'}, status=status.HTTP_200_OK)

    logger.info("payment_category_update -> ended -> user: {}".format(request.user.user_id))
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def payment_category_delete(request, uid):

    logger.info("payment_category_delete -> init -> user: {}".format(request.user.user_id))

    # check if the user is a sport association
    if request.user.role != User.ASSOCIATION:
        logger.info("payment_category_delete -> ended -> user: {}".format(request.user.user_id))
        return Response({'error': 'user not authorized'}, status=status.HTTP_401_UNAUTHORIZED)

    # get the sport association of the user
    sport_association = SportAssociation.objects.filter(user=request.user). first()

    # check that the payment category sport association is the same of the user
    payment_category = PaymentCategory.objects.filter(
        payment_category_id=uid,
        sport_association=sport_association
    ).first()

    if payment_category.sport_association != sport_association:
        logger.info("payment_category_delete -> ended -> user: {}".format(request.user.user_id))
        return Response({'error': 'payment category not found'}, status=status.HTTP_404_NOT_FOUND)

    payment_category.deleted = True
    payment_category.save()

    logger.info("payment_category_delete -> ended -> user: {}".format(request.user.user_id))

    return Response({'msg': 'payment category deleted'}, status=status.HTTP_200_OK)
