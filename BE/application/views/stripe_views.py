"""
@ copyright: Bakney SRL
"""

import stripe
import logging

from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from application.serializers.payment_serializers import PaymentSerializer
from core.middleware import IsAuthenticated
from rest_framework.exceptions import ValidationError, NotFound
from django.utils import timezone
from datetime import datetime

from django.db import transaction

from stripe.error import InvalidRequestError

from application.models import SportAssociation
from application.models.carnet_models import CarnetSubscription
from application.models.courses_models import CourseSubscription, CourseSubscriptionInstallment
from application.models.invoices_models import Invoice, InvoiceSuppliers
from application.models.payment_models import Payment
from application.utils.api_utils import is_valid_uuid, BalanceSheetData
from application.utils.notification_utils import NotificationUtils
from application.utils.payments_utils import generate_invoice_description
from application.utils.stripe_utils import online_payments_available, stripe_webhook_secret
from communications.models import SmsCreditPayment, CommunicationConfiguration
from notifications.services import NotificationService
from core import settings
from django.db.models import Max
from application.printing_tasks import print_document_invoice

logger = logging.getLogger(__name__)


def stripe_connect_disabled_response():
    return Response(
        {'error': 'Stripe Connect onboarding is disabled for self-hosted direct Stripe.'},
        status=status.HTTP_410_GONE,
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def stripe_info(request):
    """
    This endpoint returns onboarding info
    :param request:
    :return:
    """

    logger.info("stripe -> info disabled -> user: {}".format(request.user.user_id))
    return stripe_connect_disabled_response()


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def stripe_complete_on_boarding(request):
    """
    This endpoint updates the onboarding process
    :param request:
    :return:
    """

    logger.info("stripe -> complete-on-boarding disabled -> user: {}".format(request.user.user_id))
    return stripe_connect_disabled_response()


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def stripe_on_boarding(request):
    """
    This endpoint start the Stripe connection by prefilling the base info
    :param request:
    :return:
    """

    logger.info("stripe -> on-boarding disabled -> user: {}".format(request.user.user_id))
    return stripe_connect_disabled_response()


@api_view(['POST'])
def stripe_multiple_pay(request):
    logger.info("Processing multiple Stripe payments", extra={'payment_count': len(request.data.get("payments", []))})
    payments = request.data.get("payments", [])

    payments_groups = {}

    if payments and len(payments) > 0:
        checkout_info = ''
        # check if payments is a list of strings or objects
        if isinstance(payments, list) and len(payments) > 0:
            # check if first element is a string (payment ID) or object
            if isinstance(payments[0], str):
                # get all payments by IDs
                payments = Payment.objects.filter(payment_id__in=payments)
                # serialize payments
                payments = PaymentSerializer(payments, many=True).data
            # if it's already a list of objects, keep as is

        for payment in payments:
            if payment['sport_association'] not in payments_groups.keys():
                payments_groups[payment['sport_association']] = []
            # group payments for sport_association
            payments_groups[payment['sport_association']].append(payment)

        # TODO: this needs to be refactored for multiple associations payments
        # get the checkout info from the sport association
        sport_association = SportAssociation.objects.filter(
            pk=payments[0]['sport_association']
        )
        if sport_association.exists():
            checkout_info = sport_association.first().checkout_info

        for idx, sport_association_key in enumerate(payments_groups.keys()):
            # TODO: skip multiple association for now \
            #  implement multiple association payment in the future
            if idx > 0:
                continue

            total = 0
            stripe_payments_methods = ['card', 'sepa_debit']
            for payment in payments_groups[sport_association_key]:
                # retrieve the payment
                payment = Payment.objects.get(payment_id=payment['payment_id'])
                if not online_payments_available(payment.sport_association):
                    return Response(
                        {'error': 'Online payments are not configured.'},
                        status=status.HTTP_409_CONFLICT,
                    )
                if payment.sport_association.stripe_available_methods is not None:
                    stripe_payments_methods = payment.sport_association.stripe_available_methods

                if payment.payment_intent_id and payment.paid is False:
                    payment_intent = stripe.PaymentIntent.retrieve(
                        payment.payment_intent_id,
                    )
                    if payment_intent is not None and \
                            payment_intent.status == 'succeeded' and \
                            payment.paid is False:
                        # mark already paid transaction on the db
                        mark_payment_as_paid(request._request, payment, response=False)
                        continue
            # retrieve the unpaid payments
            unpaid_payments = Payment.objects.filter(
                payment_id__in=[p['payment_id'] for p in payments_groups[sport_association_key]],
                paid=False
            )

            # calculate total
            for payment in unpaid_payments:
                total += payment.amount

            amount = int(total * 100)
            if True:#payment_intent is None:
                # create metadata with payment id and payment data
                metadata = {}
                description = '' # it will be filled for each payment in the loop
                for payment in unpaid_payments:
                    metadata[payment.payment_id] = payment.amount

                    description += f'{payment.associate.first_name[:3]}-{payment.associate.last_name[:3]}-' \
                                   f'€{payment.amount}|'

                logger.info("Creating Stripe PaymentIntent for multiple payments", extra={'amount': amount, 'payment_count': len(unpaid_payments)})
                payment_intent = stripe.PaymentIntent.create(
                    amount=amount,
                    currency='eur',
                    description=description,
                    payment_method_types=stripe_payments_methods,
                    metadata=metadata

                )
                logger.info("Stripe PaymentIntent created successfully", extra={'payment_intent_id': payment_intent.stripe_id})
                for payment in unpaid_payments:
                    payment.payment_intent_id = payment_intent.stripe_id
                    payment.save()

            data = {
                "client_secret": payment_intent.client_secret or None,
                "info": {
                    "checkout_info": checkout_info,
                }
            }

            return Response({'data': data, "payments": payments}, status=status.HTTP_200_OK)


    return Response({"data": {"msg": "no payments provided."}}, status=status.HTTP_400_BAD_REQUEST)



@api_view(['POST'])
#@permission_classes([IsAuthenticated])
def stripe_pay(request, payment_id):
    """
    This endpoint allows to pay by providing a payment id
    :payment_id, request:
    :return:
    """
    logger.info("Stripe pay request", extra={'payment_id': payment_id, 'user_id': str(request.user.user_id) if request.user.is_authenticated else 'anonymous'})

    '''
    validate payment is:
    - not paid
    - exists
    - belongs to the user
    '''
    payment = None

    # check if there is one payment in request
    one_fee_payment = request.GET.get('one_fee_payment', False)

    # if one_fee_payment:
    #     # get the subscription and check if there is a one payment option available
    #

    # check that there is not a payment intent in the query
    payment_intent_q = request.GET.get('payment_intent_id', None)

    if payment_intent_q:
        payment = Payment.objects.filter(payment_intent_id=payment_intent_q).first()
        if payment is not None:
            if not online_payments_available(payment.sport_association):
                return Response(
                    {'error': 'Online payments are not configured.'},
                    status=status.HTTP_409_CONFLICT,
                )
            payment_intent = stripe.PaymentIntent.retrieve(
                payment.payment_intent_id,
            )
            # check if there are metadata in the payment
            if payment_intent is not None and \
                payment_intent.status == 'succeeded' and \
                payment_intent.metadata is not None:
                if payment.paid is False:
                    mark_payment_as_paid(request._request, payment, response=False)
                # loop through the metadata keys
                for key in payment_intent.metadata.keys():
                    # key is the payment id
                    payment = Payment.objects.filter(payment_id=key).first()
                    if payment is not None and payment.paid is False:
                        # mark already paid transaction on the db
                        mark_payment_as_paid(request._request, payment, response=False)
                return Response({'data': {'msg': 'payment already paid.'}}, status=status.HTTP_200_OK)
            elif payment_intent is not None and \
                    payment_intent.status == 'succeeded' and \
                    payment.paid is False:
                # mark already paid transaction on the db
                return mark_payment_as_paid(request._request, payment)

    if not is_valid_uuid(payment_id):
        raise ValidationError('not valid uuid')
    else:
        payment = Payment.objects.filter(payment_id=payment_id).first()
        if payment is None:
            raise NotFound('payment not found')
        if not online_payments_available(payment.sport_association):
            return Response(
                {'error': 'Online payments are not configured.'},
                status=status.HTTP_409_CONFLICT,
            )
        if payment.paid:
            # already paid the transaction
            data = {
                "msg": "payment mark as payed.",
                "status": "paid"
            }
            return Response({'data': data}, status=status.HTTP_200_OK)

    amount = int(payment.amount * 100)
    payment_intent = None

    if payment.payment_intent_id:
        try:
            payment_intent = stripe.PaymentIntent.retrieve(
                payment.payment_intent_id,
            )
            if payment_intent is not None and \
                payment_intent.status == 'succeeded' and \
                    payment.paid is False:
                # mark already paid transaction on the db
                return mark_payment_as_paid(request._request, payment)
            elif payment_intent is not None and \
                payment_intent.status == 'succeeded' and \
                    payment.paid is True:
                # already paid the transaction
                data = {
                    "msg": "payment mark as payed.",
                    "status": "paid"
                }
                return Response({'data': data}, status=status.HTTP_200_OK)

            # check that the payment_intent amount is the same as the payment amount otherwise set payment_intent_q to None
            if payment_intent is not None and payment_intent.amount != int(payment.amount * 100):
                payment_intent = None
                payment.payment_intent_id = None
                payment.save()

        except InvalidRequestError as e:
            logger.exception(e)
            payment_intent = None

    if payment_intent is None:
        logger.info("Creating Stripe PaymentIntent", extra={'payment_id': str(payment.payment_id), 'amount': amount})
        payment_intent = stripe.PaymentIntent.create(
            amount=amount,
            currency='eur',
            description=f'Associato: {payment.associate.first_name} {payment.associate.last_name} - '
                        f'Utente: {payment.user.first_name} {payment.user.last_name} - '
                        f'Pagamento di {payment.amount} euro per {payment.sport_association.denomination}',
            payment_method_types=payment.sport_association.stripe_available_methods,
        )
        logger.info("Stripe PaymentIntent created successfully", extra={'payment_id': str(payment.payment_id), 'payment_intent_id': payment_intent.stripe_id})
        payment.payment_intent_id = payment_intent.stripe_id
        payment.save()

    data = {
        "client_secret": payment_intent.client_secret or None,
    }

    return Response({'data': data}, status=status.HTTP_200_OK)


@permission_classes([IsAuthenticated])
def mark_payment_as_paid(request, payment, response=True):
    logger.info("Marking payment as paid", extra={'payment_id': str(payment.payment_id), 'amount': float(payment.amount)})
    if request is not None and request.user is not None:
        user = payment.user if payment.user is not None else payment.associate.user
    elif request is not None:
        user = request.user

    with transaction.atomic():
        # select the payment for safe update
        payment = Payment.objects.select_for_update().get(payment_id=payment.payment_id)

        # check if there are suppliers invoices to pay
        suppliers_invoice = InvoiceSuppliers.objects.filter(
            payment=payment,
        ).first()

        if suppliers_invoice:
            suppliers_invoice.paid = True
            suppliers_invoice.payment_date = timezone.now()
            suppliers_invoice.save()

        if payment.paid is False:
            payment.paid = True
            payment.payment_date = timezone.now()

            # check if there is not an invoice yet
            if payment.invoice is None:
                membership_fee = payment.amount if payment.subject is Payment.SUBSCRIPTION else 0.00
                activity_fee = payment.amount if payment.subject is not Payment.SUBSCRIPTION else 0.00
                if payment.meta_payment_categories and payment.subject is Payment.SUBSCRIPTION:
                    # sum the amount of the meta payment categories
                    for meta_payment_category in payment.meta_payment_categories:
                        if meta_payment_category["amount"]:
                            activity_fee = float(activity_fee) + float(meta_payment_category["amount"])
                    if membership_fee > 0:
                        membership_fee = float(membership_fee) - activity_fee

                date_from, date_to = BalanceSheetData.get_range_from_year_and_starting_date(
                    date=datetime.now(),
                    starting_day=payment.sport_association.user.balance_sheet_start_day,
                    starting_month=payment.sport_association.user.balance_sheet_start_month
                )

                # Always query invoices in the current fiscal year since the invoice
                # is being created now, regardless of when the payment was created.
                # This ensures invoice numbers reset at the start of each fiscal year.
                last_invoice = Invoice.objects.filter(
                    sport_association=payment.sport_association,
                    archived=False,
                    creation_date__gte=date_from,
                    creation_date__lt=date_to
                )

                # Get the latest invoice number or set to 0 if None or non-numeric
                try:
                    latest_invoice_number = int(last_invoice.aggregate(Max('number'))['number__max'])
                except (TypeError, ValueError):
                    latest_invoice_number = 0

                # Get the user's starting invoice number or set to 0 if None or non-numeric
                try:
                    user_start_invoice_number = int(payment.sport_association.user.starting_number_invoices)
                except (TypeError, ValueError):
                    user_start_invoice_number = 0

                # Choose the maximum between the latest invoice number and the user's starting invoice number
                max_invoice_number = max(latest_invoice_number, user_start_invoice_number)

                # The new invoice number will be one more than the maximum value
                invoice_number = max_invoice_number + 1

                # description text based on the type of the payment
                description = generate_invoice_description(payment, payment.sport_association)

                invoice = Invoice.objects.create(
                    sport_association=payment.sport_association,
                    description=description,
                    membership_fee=membership_fee,
                    activity_fee=activity_fee,
                    number=invoice_number,
                    meta_payment_categories=payment.meta_payment_categories,
                )
                payment.invoice = invoice
                payment.save()

                course_subscription = None
                is_carnet = False
                if payment.subject is Payment.COURSE:
                    course_subscription = CourseSubscription.objects.filter(payment=payment).first()
                    if course_subscription is None:
                        course_subscription = CourseSubscriptionInstallment.objects.filter(payment=payment).first()
                        if course_subscription is None:
                            course_subscription = CarnetSubscription.objects.filter(payment=payment).first()
                            is_carnet = True
                    if course_subscription:
                        course_subscription.paid = True
                        course_subscription.save()
                    else:
                        logger.error(f"payment not found in courses for payment_intent_id {payment.payment_intent_id}")

                # headers = {
                #     'Authorization': request.META.get('HTTP_AUTHORIZATION')
                # }

                # printing the invoice
                # printing_response = requests.get(CURRENT_HOST + "/document/invoice/" + str(invoice.invoice_id),
                #                                  headers=headers)
                if request is not None:
                    print_document_invoice.delay(str(invoice.invoice_id), request.headers.get('authorization'))
                else:
                    # log the invoice.sport_association.user and get the bearer token
                    u = invoice.sport_association.user
                    # log the user
                    print_document_invoice.delay(str(invoice.invoice_id), settings.DOCUMENT_BYPASS_TOKEN)

                description = ""
                if is_carnet:
                    try:
                        description = f"carnet {course_subscription.carnet_id.title}"
                    except Exception as e:
                        logger.exception(e)
                        description = "carnet"
                elif payment.subject is Payment.COURSE:
                    if isinstance(course_subscription, CourseSubscriptionInstallment):
                        description = f"rata del corso {course_subscription.course_subscription.course.title}"
                    else:
                        description = "il corso \"{}\"".format(course_subscription.course.title)
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
                NotificationService.send_notification(payment.user, messages)

    data = {
        "msg": "payment mark as payed.",
        "status": "paid"
    }

    logger.info("payment_approve -> ended -> user: {}".format(payment.user.user_id))
    return Response({'data': data}, status=status.HTTP_200_OK) if response else None


@api_view(['POST'])
def stripe_webhook(request):
    logger.info("Received Stripe webhook")
    payload = request.body
    sig_header = request.headers.get('stripe-signature')
    endpoint_secret = stripe_webhook_secret()

    if not endpoint_secret:
        logger.warning("Stripe webhook received while STRIPE_WEBHOOK_SECRET is not configured")
        return Response(
            {'error': 'Stripe webhook is not configured.'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, endpoint_secret
        )
    except ValueError:
        logger.error("Invalid Stripe webhook payload", exc_info=True)
        return Response(status=status.HTTP_400_BAD_REQUEST)
    except stripe.error.SignatureVerificationError:
        logger.error("Invalid Stripe webhook signature", exc_info=True)
        return Response(status=status.HTTP_400_BAD_REQUEST)

    logger.info("Processing Stripe webhook event", extra={'event_type': event['type']})
    # Handle the event
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        metadata = session.get('metadata') or {}

        # Platform subscription billing is disabled in self-hosted deployments.
        # Keep direct checkout-session side effects that are not platform plan billing.
        if 'sms_balance' in metadata:
            sms_payment = SmsCreditPayment.objects.filter(payment_intent_id=session['id']).first()
            if sms_payment is None:
                return Response(status=status.HTTP_400_BAD_REQUEST)

            sms_payment.paid = True
            sms_payment.save()
            # update the sms balance
            configuration = CommunicationConfiguration.objects.filter(
                sport_association=sms_payment.sport_association
            ).first()
            if configuration is None:
                return Response(status=status.HTTP_400_BAD_REQUEST)

            configuration.sms_balance += int(metadata['sms_balance'])
            configuration.save()

            # clear other sms payments that are not paid
            SmsCreditPayment.objects.filter(
                sport_association=sms_payment.sport_association,
                paid=False
            ).delete()
    elif event['type'] == 'charge.succeeded':
        try:
            # get payment from payment_intent
            payments = Payment.objects.filter(payment_intent_id=event['data']['object']['payment_intent'])
            if not payments.exists():
                return Response(status=status.HTTP_400_BAD_REQUEST)
            for payment in payments:
                if payment.paid is False:
                    mark_payment_as_paid(None, payment, response=False)
        except Exception as e:
            logger.error(f"Error in managing charge.succeeded: {e}")
        return Response(status=status.HTTP_200_OK)

    elif event['type'] in (
        'invoice.payment_succeeded',
        'invoice.payment_failed',
        'customer.subscription.updated',
    ):
        logger.info("Ignoring disabled platform billing webhook event", extra={'event_type': event['type']})
        return Response(status=status.HTTP_200_OK)

    else:
        print('Unhandled event type {}'.format(event['type']))

    return Response(status=status.HTTP_200_OK)
