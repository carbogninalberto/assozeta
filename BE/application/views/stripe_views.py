"""
@ copyright: Bakney SRL
"""

import pytz
import stripe
import logging

from django.db.models import Q
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from application.serializers.payment_serializers import PaymentSerializer
from core.middleware import IsAuthenticated
from rest_framework.exceptions import ValidationError, NotFound
from django.utils import timezone
from datetime import datetime, timedelta

from django.db import transaction

from stripe.error import InvalidRequestError

from application.models import BillingPayment, User, BillingPlan, BillingSubscription, SportAssociation
from application.models.carnet_models import CarnetSubscription
from application.models.courses_models import CourseSubscription, CourseSubscriptionInstallment
from application.models.invoices_models import Invoice, InvoiceSuppliers
from application.models.payment_models import Payment
from application.utils.api_utils import is_valid_uuid, BalanceSheetData
from application.utils.notification_utils import NotificationUtils
from application.utils.payments_utils import generate_invoice_description
from communications.models import SmsCreditPayment, CommunicationConfiguration
from notifications.services import NotificationService
from core import settings
from django.db.models import Max
from application.printing_tasks import print_document_invoice

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def stripe_info(request):
    """
    This endpoint returns onboarding info
    :param request:
    :return:
    """

    logger.info("stripe -> complete-on-boarding -> user: {}".format(request.user.user_id))

    return Response({'data': {
        "stripe_on_boarding_completed": request.user.stripe_on_boarding_completed,
    }}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def stripe_complete_on_boarding(request):
    """
    This endpoint updates the onboarding process
    :param request:
    :return:
    """

    logger.info("stripe -> complete-on-boarding -> user: {}".format(request.user.user_id))

    stripe_account = stripe.Account.retrieve(request.user.stripe_account_id)

    if request.user.stripe_account_id is not None and \
            len(request.user.stripe_account_id) > 0 and \
            stripe_account.charges_enabled is True:
        request.user.stripe_on_boarding_completed = True
        request.user.save()
    else:
        raise Exception("Stripe account id is not set")

    return Response({'msg': 'On boarding completed!'}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def stripe_on_boarding(request):
    """
    This endpoint start the Stripe connection by prefilling the base info
    :param request:
    :return:
    """

    logger.info("stripe -> on-boarding -> user: {}".format(request.user.user_id))

    stripe_account = None
    stripe_account_is_new = False

    if request.user.stripe_account_id is None or \
            len(request.user.stripe_account_id) == 0:

        stripe_account = stripe.Account.create(
            type="standard",
            country="IT",
            email=request.user.email,
            business_type='non_profit',
            company={
                'tax_id': "000000000",
            }
        )
        stripe_account_is_new = True
    else:
        try:
            stripe_account = stripe.Account.retrieve(request.user.stripe_account_id)
        except stripe.error.PermissionError as e:
            if e.json_body['error']['code'] == "account_invalid":
                stripe_account = stripe.Account.create(
                    type="standard",
                    country="IT",
                    email=request.user.email,
                    business_type='non_profit',
                )
                stripe_account_is_new = True
            else:
                return Response({"error": e.json_body['error']['message']},
                                status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    if stripe_account is None:
        return Response({"error": 'Stripe account not found'},
                        status=status.HTTP_417_EXPECTATION_FAILED)
    elif stripe_account_is_new:
        request.user.stripe_account_id = stripe_account.id
        request.user.stripe_on_boarding_completed = False
        request.user.save()

    # create onboarding link
    account_link = stripe.AccountLink.create(
        account=stripe_account.id,
        refresh_url=settings.STRIPE_REFRESH_URL,
        return_url=settings.STRIPE_RETURN_URL,
        type="account_onboarding",
    )

    data = {
        "on_boarding_url": account_link.url
    }
    return Response({'data': data}, status=status.HTTP_200_OK)


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
            sport_association_stripe_account = None
            stripe_payments_methods = ['card', 'sepa_debit']
            for payment in payments_groups[sport_association_key]:
                # retrieve the payment
                payment = Payment.objects.get(payment_id=payment['payment_id'])
                # let's assure it is not already paid
                sport_association_stripe_account = payment.sport_association.user.stripe_account_id
                if payment.sport_association.stripe_available_methods is not None:
                    stripe_payments_methods = payment.sport_association.stripe_available_methods
                if sport_association_stripe_account is None or \
                        len(sport_association_stripe_account) == 0:
                    raise ValidationError('sport association stripe account not set')

                if payment.payment_intent_id and payment.paid is False:
                    payment_intent = stripe.PaymentIntent.retrieve(
                        payment.payment_intent_id,
                        stripe_account=sport_association_stripe_account,
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
            fee = 0
            if True:#payment_intent is None:
                # create metadata with payment id and payment data
                metadata = {}
                description = '' # it will be filled for each payment in the loop
                for payment in unpaid_payments:
                    metadata[payment.payment_id] = payment.amount

                    description += f'{payment.associate.first_name[:3]}-{payment.associate.last_name[:3]}-' \
                                   f'€{payment.amount}|'

                logger.info("Creating Stripe PaymentIntent for multiple payments", extra={'amount': amount, 'stripe_account': sport_association_stripe_account, 'payment_count': len(unpaid_payments)})
                payment_intent = stripe.PaymentIntent.create(
                    amount=amount,
                    currency='eur',
                    application_fee_amount=fee,
                    description=description,
                    stripe_account=sport_association_stripe_account,
                    payment_method_types=stripe_payments_methods,
                    metadata=metadata

                )
                logger.info("Stripe PaymentIntent created successfully", extra={'payment_intent_id': payment_intent.stripe_id})
                for payment in unpaid_payments:
                    payment.payment_intent_id = payment_intent.stripe_id
                    payment.save()

            data = {
                "client_secret": payment_intent.client_secret or None,
                "stripe_account": sport_association_stripe_account or None,
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
    sport_association_stripe_account = None
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
            sport_association_stripe_account = payment.sport_association.user.stripe_account_id
            payment_intent = stripe.PaymentIntent.retrieve(
                payment.payment_intent_id,
                stripe_account=sport_association_stripe_account,
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
        sport_association_stripe_account = payment.sport_association.user.stripe_account_id
        if sport_association_stripe_account is None or \
                len(sport_association_stripe_account) == 0:
            raise ValidationError('sport association stripe account not set')
        if payment is None:
            raise NotFound('payment not found')
        if payment.paid:
            # already paid the transaction
            data = {
                "msg": "payment mark as payed.",
                "status": "paid"
            }
            return Response({'data': data}, status=status.HTTP_200_OK)

    amount = int(payment.amount * 100)
    fee = 0
    payment_intent = None

    if payment.payment_intent_id:
        try:
            payment_intent = stripe.PaymentIntent.retrieve(
                payment.payment_intent_id,
                stripe_account=sport_association_stripe_account,
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
        logger.info("Creating Stripe PaymentIntent", extra={'payment_id': str(payment.payment_id), 'amount': amount, 'stripe_account': sport_association_stripe_account})
        payment_intent = stripe.PaymentIntent.create(
            amount=amount,
            currency='eur',
            application_fee_amount=fee,
            description=f'Associato: {payment.associate.first_name} {payment.associate.last_name} - '
                        f'Utente: {payment.user.first_name} {payment.user.last_name} - '
                        f'Pagamento di {payment.amount} euro per {payment.sport_association.denomination}',
            stripe_account=sport_association_stripe_account,
            payment_method_types=payment.sport_association.stripe_available_methods,
        )
        logger.info("Stripe PaymentIntent created successfully", extra={'payment_id': str(payment.payment_id), 'payment_intent_id': payment_intent.stripe_id})
        payment.payment_intent_id = payment_intent.stripe_id
        payment.save()

    data = {
        "client_secret": payment_intent.client_secret or None,
        "stripe_account": sport_association_stripe_account or None,
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
    event = None
    payload = request.body
    sig_header = request.headers.get('stripe-signature')
    endpoint_secret = settings.STRIPE_WEBHOOK_SECRET

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, endpoint_secret
        )
    except ValueError as e:
        logger.error("Invalid Stripe webhook payload", exc_info=True)
        # Invalid payload
        raise e
    except stripe.error.SignatureVerificationError as e:
        logger.error("Invalid Stripe webhook signature", exc_info=True)
        # Invalid signature
        raise e

    logger.info("Processing Stripe webhook event", extra={'event_type': event['type']})
    # Handle the event
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        # Fulfill the purchase...
        if session['payment_status'] == 'paid' and \
            session['client_reference_id'] is not None and \
                session['subscription'] is not None:

            payment_user = User.objects.filter(user_id=session['client_reference_id']).first()
            if payment_user is None:
                return Response(status=status.HTTP_400_BAD_REQUEST)

            # mark the payment as paid
            payment = BillingPayment.objects.create(
                user=payment_user,
                subscription_id=session['subscription'],
                amount=session['amount_total'] / 100
            )
            payment.save()

            # get current billing plan paid
            # NOTE: with discount we should use the subtotal so that plan is mapped correctly
            # we might explore the mapping with the right subscription type from stripe
            total_paid = session['amount_subtotal']  # use amount_subtotal for non taxed, amount_total taxed
            billing_plan = BillingPlan.objects.filter(
                Q(monthly_fee=total_paid) |
                Q(annually_fee=total_paid)
            ).first()

            if billing_plan is None:
                return Response(status=status.HTTP_400_BAD_REQUEST)

            is_monthly = True
            if billing_plan.annually_fee == total_paid:
                is_monthly = False

            days = 30 if is_monthly else 365

            # update the user BillingSubscription
            billing_subscription = BillingSubscription.objects.filter(user=payment_user).first()
            billing_subscription.billing_plan = billing_plan
            billing_subscription.renewal_type = BillingSubscription.MONTHLY \
                if is_monthly else BillingSubscription.ANNUALLY
            billing_subscription.auto_renewal = True
            billing_subscription.ends_on = pytz.timezone('Europe/Rome').localize(
                datetime.now() + timedelta(days=days),
                is_dst=None
            )
            billing_subscription.save()

        elif 'sms_balance' in session['metadata']:
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

            configuration.sms_balance += int(session['metadata']['sms_balance'])
            configuration.save()

            # clear other sms payments that are not paid
            SmsCreditPayment.objects.filter(
                sport_association=sms_payment.sport_association,
                paid=False
            ).delete()
    elif event['type'] == 'charge.succeeded':
        try:
            # get payment from payment_intent
            payment = Payment.objects.filter(payment_intent_id=event['data']['object']['payment_intent']).first()
            if payment is None:
                return Response(status=status.HTTP_400_BAD_REQUEST)
            return mark_payment_as_paid(None, payment)
        except Exception as e:
            logger.error(f"Error in managing charge.succeeded: {e}")
        return Response(status=status.HTTP_200_OK)

    elif event['type'] == 'invoice.payment_succeeded':
        # Handle automatic subscription renewal
        invoice = event['data']['object']
        subscription_id = invoice['subscription']
        customer_id = invoice['customer']

        # Try to find the user by their subscription_id from previous payments
        last_payment = BillingPayment.objects.filter(
            subscription_id=subscription_id
        ).order_by('-payment_date').first()

        payment_user = None

        if last_payment is None:
            # No previous payment found - fetch subscription from Stripe to get client_reference_id
            logger.warning(f"No BillingPayment found for subscription {subscription_id}. Fetching from Stripe API.")

            try:
                # Retrieve the subscription from Stripe
                stripe_subscription = stripe.Subscription.retrieve(subscription_id)

                # Retrieve the checkout session that created this subscription
                # Search for sessions with this subscription ID
                checkout_sessions = stripe.checkout.Session.list(
                    subscription=subscription_id,
                    limit=1
                )

                if checkout_sessions.data and len(checkout_sessions.data) > 0:
                    session = checkout_sessions.data[0]
                    client_reference_id = session.get('client_reference_id')

                    if client_reference_id:
                        payment_user = User.objects.filter(user_id=client_reference_id).first()
                        if payment_user:
                            logger.info(f"Found user {payment_user.user_id} from Stripe checkout session")

                # Fallback: try to find user by customer metadata
                if payment_user is None:
                    customer = stripe.Customer.retrieve(customer_id)
                    if customer.get('metadata') and customer.metadata.get('user_id'):
                        payment_user = User.objects.filter(user_id=customer.metadata['user_id']).first()
                        if payment_user:
                            logger.info(f"Found user {payment_user.user_id} from Stripe customer metadata")

                # Last resort: search by customer email
                if payment_user is None and stripe_subscription.get('metadata') and stripe_subscription.metadata.get('user_email'):
                    payment_user = User.objects.filter(email=stripe_subscription.metadata['user_email']).first()
                    if payment_user:
                        logger.info(f"Found user {payment_user.user_id} from subscription metadata email")

                if payment_user is None:
                    logger.error(
                        f"Unable to find user for subscription {subscription_id}. "
                        f"Tried: checkout session, customer metadata, subscription metadata."
                    )
                    return Response(status=status.HTTP_200_OK)

            except stripe.error.StripeError as e:
                logger.error(f"Stripe API error while fetching subscription {subscription_id}: {e}")
                return Response(status=status.HTTP_200_OK)
        else:
            payment_user = last_payment.user

        # Get the period end and amount from Stripe invoice
        period_end = invoice['lines']['data'][0]['period']['end']
        # Use amount_subtotal (pre-tax) to match initial purchase logic for plan lookup
        total_paid = invoice['amount_subtotal']

        # Determine the billing plan based on amount paid (same logic as initial purchase)
        billing_plan = BillingPlan.objects.filter(
            Q(monthly_fee=total_paid) |
            Q(annually_fee=total_paid)
        ).first()

        if billing_plan is None:
            logger.error(
                f"No BillingPlan found matching amount {total_paid} for subscription {subscription_id}. "
                f"Using existing plan from user's subscription."
            )

        # Determine if monthly or annual renewal
        is_monthly = True
        if billing_plan and billing_plan.annually_fee == total_paid:
            is_monthly = False

        # Create new payment record for this renewal
        BillingPayment.objects.create(
            user=payment_user,
            subscription_id=subscription_id,
            amount=invoice['amount_paid'] / 100  # Store actual amount paid (with tax)
        )

        # Update the BillingSubscription with all relevant fields
        billing_subscription = BillingSubscription.objects.filter(
            user=payment_user
        ).order_by('-ends_on').first()

        if billing_subscription:
            # Update billing plan if we found a matching one (handles plan changes)
            if billing_plan:
                billing_subscription.billing_plan = billing_plan

            # Update renewal type (monthly vs annual)
            billing_subscription.renewal_type = BillingSubscription.MONTHLY \
                if is_monthly else BillingSubscription.ANNUALLY

            # Sync auto_renewal status from Stripe subscription
            try:
                stripe_subscription = stripe.Subscription.retrieve(subscription_id)
                # Check if subscription is set to cancel at period end
                billing_subscription.auto_renewal = not stripe_subscription.get('cancel_at_period_end', False)
            except stripe.error.StripeError as e:
                logger.warning(f"Could not retrieve subscription status from Stripe: {e}")
                # Keep existing auto_renewal value if Stripe call fails

            # Update ends_on date from Stripe
            billing_subscription.ends_on = datetime.fromtimestamp(
                period_end,
                tz=pytz.timezone('Europe/Rome')
            )
            billing_subscription.save()
            logger.info(
                f"Renewed subscription for user {payment_user.user_id}: "
                f"plan={billing_plan.name if billing_plan else 'unchanged'}, "
                f"type={'monthly' if is_monthly else 'annual'}, "
                f"ends_on={billing_subscription.ends_on}"
            )
        else:
            logger.warning(f"No BillingSubscription found for user {payment_user.user_id}")

        return Response(status=status.HTTP_200_OK)

    elif event['type'] == 'invoice.payment_failed':
        # Handle failed subscription renewal
        invoice = event['data']['object']
        subscription_id = invoice['subscription']

        # Find the user
        payment = BillingPayment.objects.filter(
            subscription_id=subscription_id
        ).order_by('-payment_date').first()

        if payment and payment.user:
            logger.warning(f"Payment failed for subscription {subscription_id}, user {payment.user.user_id}")
            # TBD: Implement notification email and grace period logic when required

        return Response(status=status.HTTP_200_OK)

    elif event['type'] == 'customer.subscription.updated':
        # Handle subscription changes (plan upgrades/downgrades)
        subscription = event['data']['object']
        subscription_id = subscription['id']

        logger.info(f"Subscription updated: {subscription_id}")
        # TBD: Implement plan change logic when required (upgrade/downgrade functionality)

        return Response(status=status.HTTP_200_OK)

    else:
        print('Unhandled event type {}'.format(event['type']))

    return Response(status=status.HTTP_200_OK)
