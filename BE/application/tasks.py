import logging
import os
import time
from datetime import datetime, timedelta
import random
from threading import Timer

import pytz
import requests
import stripe
from dateutil.relativedelta import relativedelta
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.core.mail import send_mail
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils import timezone

from django.db import transaction, OperationalError

from application.models import BillingPlan, BillingSubscription, AttendanceRegistry, AttendanceDay, Payment, \
    NurturingEmailsPlan, NurturingEmails, Reminders
from application.models.carnet_models import CarnetSubscription
from application.models.courses_models import CourseSubscription, CourseSubscriptionInstallment, Course
from application.models.subscriptions_models import Subscription, MedicalCertificate, SubscriptionToken
from celery import shared_task

from application.models.user_models import UserPartial, User, SportAssociation, Associate, \
    SportAssociationDocumentsArchive
from application.utils.api_utils import BalanceSheetData, generate_readable_unique_string, check_email
from application.utils.printing import PrintingService
from application.utils.stripe_utils import stripe_direct_credentials_configured
from application.views.stripe_views import mark_payment_as_paid
from communications.models import AutomationWorkflow, CommunicationConfiguration
from core import settings
from core.celery import app
from core.settings import STORAGE_DIR, CURRENT_HOST, WHITELABEL_NAME
from rest_framework.test import APIRequestFactory
from core.tasks import send_mail_async
from docmanager.models import Document
# from docmanager.tasks import save_to_storage, save_binary_to_storage

logger = logging.getLogger(__name__)


@shared_task(name='renew_memberships_payments')
def renew_memberships_payments(attempt=1, max_attempts=3):
    """
    Renew membership payments for subscriptions with auto_renewal enabled.

    Args:
        attempt: Current attempt number (1-based)
        max_attempts: Maximum retry attempts (default: 3)
    """
    logger.info("Starting renew_memberships_payments task", extra={'task_name': 'renew_memberships_payments', 'attempt': attempt})
    lock_id = "renew_memberships_payments_lock"

    # Attempt to acquire lock, expire in 5 minutes to prevent hanging locks
    lock = cache.add(lock_id, "LOCKED", timeout=300)
    skipped_subscriptions = []
    has_lock_errors = False

    if not lock:
        logger.info("Task already running, skipping execution", extra={'task_name': 'renew_memberships_payments'})
        return

    try:
        # Query subscriptions without locking all at once - removed outer atomic block
        course_subscriptions = CourseSubscription.objects.filter(
            type=CourseSubscription.MEMBERSHIP_TYPE,
            membership_active=True,
            auto_renewal=True,
            billed_until__lte=timezone.now(),
            membership_fee__gt=0,
            deleted=False,
            course__status_flag=Course.ACTIVE,
        ).select_related('course__sport_association__user', 'subscription__associate')

        count = course_subscriptions.count()
        logger.info("Found memberships to renew", extra={'count': count, 'task_name': 'renew_memberships_payments', 'attempt': attempt})

        for cs in course_subscriptions:
            try:
                if cs.subscription is None:
                    continue

                logger.info(f"renewing membership for {cs.course.title}, billed until {cs.billed_until}")

                # Process each subscription in its own atomic transaction with row-level locking
                with transaction.atomic():
                    # Lock only this specific subscription to prevent concurrent processing
                    try:
                        locked_cs = CourseSubscription.objects.select_for_update(nowait=True).get(pk=cs.pk)
                    except OperationalError:
                        logger.info("Subscription already locked, skipping", extra={'course_subscription_id': str(cs.course_subscription_id)})
                        skipped_subscriptions.append({
                            'payment_id': 'not retrieved',
                            'course_subscription_id': str(cs.course_subscription_id),
                            'billed_from': cs.billed_until.strftime('%Y-%m-%d'),
                            'billed_until': cs.billed_until.strftime('%Y-%m-%d'),
                            'error': 'subscription already locked'
                        })
                        has_lock_errors = True
                        continue
                    except Exception as e:
                        logger.error("Error in selecting for update", extra={'course_subscription_id': str(cs.course_subscription_id)}, exc_info=True)
                        skipped_subscriptions.append({
                            'payment_id': 'not retrieved',
                            'course_subscription_id': str(cs.course_subscription_id),
                            'billed_from': cs.billed_until.strftime('%Y-%m-%d'),
                            'billed_until': cs.billed_until.strftime('%Y-%m-%d'),
                            'error': str(e)
                        })
                        has_lock_errors = True
                        continue

                    # Calculate billing periods
                    new_billed_until = locked_cs.billed_until + relativedelta(months=locked_cs.billed_frequency)

                    # Check for existing payments in this period (single query)
                    existing_payment = Payment.objects.filter(
                        meta__course_subscription_id=str(locked_cs.course_subscription_id),
                        meta__billed_from=locked_cs.billed_until.strftime('%Y-%m-%d'),
                        meta__billed_until=new_billed_until.strftime('%Y-%m-%d')
                    ).first()

                    if existing_payment and existing_payment.paid:
                        skipped_subscriptions.append({
                            'course_subscription_id': str(locked_cs.course_subscription_id),
                            'billed_from': locked_cs.billed_until.strftime('%Y-%m-%d'),
                            'billed_until': new_billed_until.strftime('%Y-%m-%d'),
                            'payment_id': str(existing_payment.payment_id),
                        })
                        logger.info(f"Payment already exists for period, skipping - payment_id: {existing_payment.payment_id} - course_subscription_id: {str(locked_cs.course_subscription_id)}, billed_from: {locked_cs.billed_until.strftime('%Y-%m-%d')}, billed_until: {new_billed_until.strftime('%Y-%m-%d')}")
                        payment = None
                        # we let update after the date of billed_until
                        logger.info(f"Updating the billed until to reflect the new paid period - course_subscription_id: {str(locked_cs.course_subscription_id)}, billed_from: {locked_cs.billed_until.strftime('%Y-%m-%d')}, billed_until: {new_billed_until.strftime('%Y-%m-%d')}")
                    elif existing_payment:
                        payment = existing_payment
                        logger.info(f"Payment exists but is not paid, updating - payment_id: {payment.payment_id} - course_subscription_id: {str(locked_cs.course_subscription_id)}, billed_from: {locked_cs.billed_until.strftime('%Y-%m-%d')}, billed_until: {new_billed_until.strftime('%Y-%m-%d')}")
                    else:
                        # Create payment and update subscription in a single transaction
                        payment = Payment.objects.create(
                            user=locked_cs.course.sport_association.user,
                            description=f"Abbonamento: {locked_cs.course.title} dal {locked_cs.billed_until.strftime('%d/%m/%Y')} al {new_billed_until.strftime('%d/%m/%Y')}",
                            associate=locked_cs.subscription.associate,
                            creation_date=locked_cs.billed_until,
                            payment_date=locked_cs.billed_until,
                            amount=locked_cs.membership_fee,
                            subject=Payment.COURSE,
                            sport_association=locked_cs.course.sport_association,
                            meta={
                                'course_id': str(locked_cs.course.course_id),
                                'course_subscription_id': str(locked_cs.course_subscription_id),
                                'billed_from': locked_cs.billed_until.strftime('%Y-%m-%d'),
                                'billed_until': new_billed_until.strftime('%Y-%m-%d'),
                                'amount': str(locked_cs.membership_fee),
                                'renewal_timestamp': timezone.now().isoformat()
                            }
                        )
                        logger.info(f"Payment created - payment_id: {payment.payment_id} - course_subscription_id: {str(locked_cs.course_subscription_id)}, billed_from: {locked_cs.billed_until.strftime('%Y-%m-%d')}, billed_until: {new_billed_until.strftime('%Y-%m-%d')}")
                    # Update subscription and its payments atomically
                    CourseSubscription.objects.filter(pk=locked_cs.pk).update(
                        billed_until=new_billed_until
                    )
                    if payment:
                        locked_cs.membership_payments.add(payment)

            except Exception as e:
                logger.error("Failed to renew membership", extra={'course_subscription_id': str(cs.course_subscription_id)}, exc_info=True)
    except Exception as e:
        logger.error("Error in renew_memberships_payments task", extra={'task_name': 'renew_memberships_payments'}, exc_info=True)

    finally:
        # Release the lock
        cache.delete(lock_id)
        logger.info("Completed renew_memberships_payments task", extra={'task_name': 'renew_memberships_payments', 'attempt': attempt, 'skipped': len(skipped_subscriptions)})

        # If there are lock errors and we haven't exhausted retries, schedule another attempt
        if has_lock_errors and attempt < max_attempts:
            logger.info(f"Scheduling retry attempt {attempt + 1}/{max_attempts}")
            renew_memberships_payments.apply_async(kwargs={'attempt': attempt + 1, 'max_attempts': max_attempts}, countdown=60)
            return  # Don't send email yet

        # Send email on final attempt or when no lock errors
        if skipped_subscriptions:
            skipped_details = "\n\n".join([
                f"- Course Subscription ID: {s['course_subscription_id']}\n"
                f"  Billed from: {s['billed_from']}\n"
                f"  Billed until: {s['billed_until']}\n"
                f"  Payment ID: {s['payment_id']}"
                for s in skipped_subscriptions
            ])
            attempt_info = f"\n\n⚠️ WARNING: Task failed after {max_attempts} attempts.\n" if has_lock_errors else ""
            send_mail(
                subject=f"{WHITELABEL_NAME} | [RENEW MEMBERSHIPS PAYMENTS] Skipped subscriptions",
                message=f"The following {len(skipped_subscriptions)} subscription(s) were skipped (Attempt {attempt}/{max_attempts}):{attempt_info}\n\n{skipped_details}",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[settings.DEFAULT_SUPPORT_EMAIL],
            )

@shared_task(name="delete_users_with_request")
def delete_users_with_request():
    logger.info("Starting delete_users_with_request task", extra={'task_name': 'delete_users_with_request'})
    try:
        print(f"[DELETE USERS WITH REQUEST] starting...")
        # get all Users that have delete_on field != None and deleted = False
        users = User.objects.filter(
            deleted=False,
            is_active=True
        ).filter(
            Q(delete_on__isnull=False) & Q(delete_on__lte=datetime.now().date())
        ).iterator(chunk_size=50)

        users_list = list(users)
        logger.info("Found users to delete", extra={'count': len(users_list), 'task_name': 'delete_users_with_request'})
        for user in users_list:
            print(f"[DELETE USERS WITH REQUEST] found {len(users_list)} users to delete")
            logger.info("Deleting user account", extra={'user_id': str(user.user_id), 'email': user.email})
            # email the user telling that the account is deleted
            send_email_text.delay(
                subject=f"{WHITELABEL_NAME} | Il tuo account è stato eliminato",
                message=f"Ciao {user.first_name}, Il tuo account è stato eliminato dal sistema "
                        f"perché hai richiesto la cancellazione 30 giorni fa. ",
                recipient_list=[user.email],
            )

            user.email = f"{user.email}__deleted"
            user.username = f"{user.username}__deleted"
            user.is_active = False
            user.deleted = True
            user.delete_on = None
            user.save()
        logger.info("Completed delete_users_with_request task", extra={'count': len(users_list), 'task_name': 'delete_users_with_request'})
        print(f"[DELETE USERS WITH REQUEST] finished!")
    except Exception as e:
        logger.error("Error in delete_users_with_request task", extra={'task_name': 'delete_users_with_request'}, exc_info=True)
        print(f"[DELETE USERS WITH REQUEST] error {e}")


@shared_task(name="print_document_subscription")
def print_document_subscription(subscription_id, auth_token):
    logger.info("Starting print_document_subscription task", extra={'task_name': 'print_document_subscription', 'subscription_id': str(subscription_id)})
    try:
        print(f"[PRINTING TASK] starting...")
        headers = {'Authorization': auth_token}
        response = requests.get(f"{CURRENT_HOST}/document/subscription/{subscription_id}", headers=headers)
        logger.info("Completed print_document_subscription task", extra={'task_name': 'print_document_subscription', 'subscription_id': str(subscription_id), 'status_code': response.status_code})
        # Depending on the response, you can take further actions or handle errors
        print(f"[PRINTING TASK] finished!")
    except Exception as e:
        logger.error("Error in print_document_subscription task", extra={'task_name': 'print_document_subscription', 'subscription_id': str(subscription_id)}, exc_info=True)
        print(f"[PRINTING TASK] error {e}")


@shared_task(name="clear_expired_subscription_tokens")
def clear_expired_subscription_tokens():
    logger.info("Starting clear_expired_subscription_tokens task", extra={'task_name': 'clear_expired_subscription_tokens'})
    try:
        print(f"[CLEAR EXPIRED SUBSCRIPTION TOKENS] starting...")
        deleted_count, _ = SubscriptionToken.objects.filter(
            expiration_date__lt=datetime.now().date(),
        ).delete()
        logger.info("Completed clear_expired_subscription_tokens task", extra={'task_name': 'clear_expired_subscription_tokens', 'deleted_count': deleted_count})
        print(f"[CLEAR EXPIRED SUBSCRIPTION TOKENS] finished!")
    except Exception as e:
        logger.error("Error in clear_expired_subscription_tokens task", extra={'task_name': 'clear_expired_subscription_tokens'}, exc_info=True)
        print(f"[CLEAR EXPIRED SUBSCRIPTION TOKENS] error {e}")

@shared_task(name="update_age_of_associates")
def update_age_of_associates():
    logger.info("Starting update_age_of_associates task", extra={'task_name': 'update_age_of_associates'})
    try:
        print("[update_age_of_associates] update age of associates")
        # getting all the associates
        associates_qs = Associate.objects.filter(
            is_minor=True,
        )
        count = associates_qs.count()
        logger.info("Found associates to check", extra={'count': count, 'task_name': 'update_age_of_associates'})
        print(f"[update_age_of_associates] found {count} associates")
        associates = associates_qs.iterator(chunk_size=100)

        updated_count = 0
        for associate in associates:
            # update the age of the associate
            if associate.calculate_age() >= 18:
                associate.is_minor = False
                associate.save()
                updated_count += 1
                logger.debug("Associate now adult", extra={'associate_id': str(associate.associate_id), 'associate_name': f"{associate.first_name} {associate.last_name}"})
                print(f"[update_age_of_associates] {associate.first_name} {associate.last_name} is not minor anymore")

        logger.info("Completed update_age_of_associates task", extra={'task_name': 'update_age_of_associates', 'updated_count': updated_count})
        print("[update_age_of_associates] OK!")
    except Exception as e:
        logger.error("Error in update_age_of_associates task", extra={'task_name': 'update_age_of_associates'}, exc_info=True)
        print(f"[update_age_of_associates] error {e}")


@shared_task(name="generate_coupon_if_not_exists")
def generate_coupon_if_not_exists():
    try:
        print("[DEPLOY TASK] generate coupon if not exists")
        # getting all the subscriptions that are not deleted and are not archived
        sport_associations_qs = SportAssociation.objects.filter(
            affiliate_code=None,
        )

        print(f"[DEPLOY TASK] found {sport_associations_qs.count()} sport associations")
        sport_associations = sport_associations_qs.iterator(chunk_size=50)
        for sport_association in sport_associations:
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
        print("[DEPLOY TASK] OK!")
    except Exception as e:
        print(f"[DEPLOY TASK] error {e}")


@shared_task(name="change_type_of_stripe_payments")
def change_type_of_stripe_payments(batch_size=100, delay_seconds=1):
    try:
        if not stripe_direct_credentials_configured():
            print("[change_type_of_stripe_payments] Stripe direct credentials are not configured")
            return

        payments = Payment.objects.filter(
            type=Payment.TYPE_CHOICES[0][0],
            payment_intent_id__isnull=False,
            paid=True,
        ).filter(
            Q(creation_date__gt=datetime.now() - timedelta(days=10)) |
            Q(payment_date__gte=datetime.now() - timedelta(days=10))
        ).select_related('sport_association__user')

        total_payments = payments.count()
        processed = 0

        while processed < total_payments:
            batch = payments[processed:processed + batch_size]
            payments_to_update = []

            for payment in batch:
                try:
                    payment_intent = stripe.PaymentIntent.retrieve(
                        payment.payment_intent_id,
                    )

                    if payment_intent and payment_intent.status == 'succeeded':
                        payment.type = Payment.TYPE_CHOICES[5][0]
                        payments_to_update.append(payment)
                        print(
                            f"[change_type_of_stripe_payments] payment {payment.payment_id} updated -> {payment.type}")

                except stripe.error.StripeError as e:
                    print(
                        f"[change_type_of_stripe_payments] Stripe API error for payment {payment.payment_id}: {str(e)}")

                time.sleep(delay_seconds)  # Add delay between each API call

            if payments_to_update:
                with transaction.atomic():
                    Payment.objects.bulk_update(payments_to_update, ['type'])

            processed += batch_size
            print(f"[change_type_of_stripe_payments] Processed {processed}/{total_payments} payments")

    except Exception as e:
        print(f"[DEPLOY TASK] error {e}")


@shared_task(name="mark_as_paid_payments_checking_stripe")
def mark_as_paid_payments_checking_stripe():
    """
    Check unpaid payments with payment intents on Stripe and mark them as paid if succeeded.
    Uses parallel processing to speed up Stripe API calls.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    logger.info("Starting mark_as_paid_payments_checking_stripe task", extra={'task_name': 'mark_as_paid_payments_checking_stripe'})
    try:
        if not stripe_direct_credentials_configured():
            logger.info("Stripe direct credentials are not configured", extra={'task_name': 'mark_as_paid_payments_checking_stripe'})
            print("[mark_as_paid_payments_checking_stripe] Stripe direct credentials are not configured")
            return

        payments = list(Payment.objects.filter(
            paid=False,
            payment_intent_id__isnull=False,
        ).select_related('sport_association__user', 'user'))

        total_payments = len(payments)
        logger.info("Found unpaid payments to check", extra={'count': total_payments, 'task_name': 'mark_as_paid_payments_checking_stripe'})
        print(f"[mark_as_paid_payments_checking_stripe] Found {total_payments} unpaid payments to check")

        if total_payments == 0:
            return

        successful_updates = 0
        failed_updates = 0

        # Process payments in parallel using ThreadPoolExecutor
        # max_workers=10 means up to 10 concurrent Stripe API calls
        with ThreadPoolExecutor(max_workers=10) as executor:
            # Submit all payment checks to the executor
            future_to_payment = {
                executor.submit(_check_and_mark_payment, payment): payment
                for payment in payments
            }

            # Process completed futures as they finish
            for future in as_completed(future_to_payment):
                payment = future_to_payment[future]
                try:
                    result = future.result()
                    if result:
                        successful_updates += 1
                        print(f"[mark_as_paid_payments_checking_stripe] Successfully marked payment {payment.payment_id} as paid")
                except Exception as e:
                    failed_updates += 1
                    logger.error("Failed to process payment", extra={'payment_id': str(payment.payment_id), 'task_name': 'mark_as_paid_payments_checking_stripe'}, exc_info=True)
                    print(f"[mark_as_paid_payments_checking_stripe] Failed to process payment {payment.payment_id}: {str(e)}")

        logger.info("Completed mark_as_paid_payments_checking_stripe task", extra={'successful': successful_updates, 'failed': failed_updates, 'total': total_payments, 'task_name': 'mark_as_paid_payments_checking_stripe'})
        print(f"[mark_as_paid_payments_checking_stripe] Completed: {successful_updates} successful, {failed_updates} failed out of {total_payments} total")

    except Exception as e:
        logger.error("Critical error in mark_as_paid_payments_checking_stripe task", extra={'task_name': 'mark_as_paid_payments_checking_stripe'}, exc_info=True)
        print(f"[mark_as_paid_payments_checking_stripe] Critical error: {e}")


def _check_and_mark_payment(payment):
    """
    Helper function to check a single payment on Stripe and mark it as paid.
    Returns True if payment was marked as paid, False otherwise.
    """
    try:
        with transaction.atomic():
            # Re-fetch payment with lock to ensure thread safety
            payment = Payment.objects.select_for_update().get(payment_id=payment.payment_id)

            # Check if payment was already marked as paid by another thread
            if payment.paid:
                print(f"[_check_and_mark_payment] Payment {payment.payment_id} already marked as paid")
                return False

            # Retrieve payment intent from Stripe
            payment_intent = stripe.PaymentIntent.retrieve(
                payment.payment_intent_id,
            )

            # Check if payment intent is succeeded
            if payment_intent and payment_intent.status == 'succeeded':
                # Set payment type to 'stripe' before marking as paid
                if payment.type != Payment.STRIPE:
                    payment.type = Payment.STRIPE
                    payment.save(update_fields=['type'])

                # Create a fake DRF request for mark_payment_as_paid function
                user = payment.user
                factory = APIRequestFactory()
                request = factory.get(f'stripe/pay/{payment.payment_id}')
                request._force_auth_user = user
                request.user = user

                # Mark payment as paid
                mark_payment_as_paid(request, payment)
                return True

            return False

    except stripe.error.InvalidRequestError as e:
        print(f"[_check_and_mark_payment] Stripe InvalidRequestError for payment {payment.payment_id}: {str(e)}")
        return False
    except stripe.error.StripeError as e:
        print(f"[_check_and_mark_payment] Stripe error for payment {payment.payment_id}: {str(e)}")
        return False
    except Exception as e:
        print(f"[_check_and_mark_payment] Unexpected error for payment {payment.payment_id}: {str(e)}")
        raise  # Re-raise to be caught by the executor


@shared_task(name="clear_deleted_subscription")
def clear_deleted_subscription():
    try:
        print("[DEPLOY TASK] clear deleted subscription data")
        # getting all the subscriptions that are deleted
        subscriptions_qs = Subscription.objects.filter(deleted=True)
        print("[DEPLOY TASK] {} subscriptions to delete".format(subscriptions_qs.count()))
        subscriptions = subscriptions_qs.iterator(chunk_size=20)
        for subscription in subscriptions:
            print("-" * 50)
            print("[DEPLOY TASK] deleting subscription {}".format(subscription.subscription_id))
            # getting the course subscription (it might be empty)
            course_subscriptions = CourseSubscription.objects.filter(subscription=subscription)
            # checking all the course subscriptions one by one
            for course_subscription in course_subscriptions:
                print("[DEPLOY TASK] deleting course subscription {}".format(
                    course_subscription.course_subscription_id))
                # there are some associated payments
                if course_subscription.payment is not None:
                    print("[DEPLOY TASK] deleting payment {}".format(course_subscription.payment.payment_id))
                    # there are some associated invoices to the payment
                    if course_subscription.payment.invoice is not None:
                        print("[DEPLOY TASK] deleting invoice {}".format(course_subscription.payment.invoice.invoice_id))
                        # delete the associated invoice
                        course_subscription.payment.invoice.delete()
                    # finally delete the payment
                    course_subscription.payment.delete()
                # check if there are installments
                if course_subscription.multi_payments:
                    print("[DEPLOY TASK] deleting installments")
                    # retrieve all installments
                    installments = CourseSubscriptionInstallment.objects.filter(course_subscription=course_subscription)
                    print("[DEPLOY TASK] {} installments to delete".format(installments.count()))
                    for installment in installments:
                        print("[DEPLOY TASK] deleting installment {}".format(installment.installment_id))
                        # check if installment was paid
                        if installment.payment is not None:
                            print("[DEPLOY TASK] deleting payment {}".format(installment.payment.payment_id))
                            # installment has an associated invoice
                            if installment.payment.invoice is not None:
                                print("[DEPLOY TASK] deleting invoice {}".format(installment.payment.invoice.invoice_id))
                                installment.payment.invoice.delete()
                            # delete the payment
                            installment.payment.delete()
                        # finally delete the installment
                        installment.delete()
                # finally unsubscribe from the course
                course_subscription.delete()
            # check if there is a pyament for the subscription
            if subscription.payment is not None:
                print("[DEPLOY TASK] deleting subscription payment {}".format(subscription.payment.payment_id))
                # check if there is an invoice
                if subscription.payment.invoice is not None:
                    print("[DEPLOY TASK] deleting subscription invoice {}".format(
                        subscription.payment.invoice.invoice_id))
                    # delete the invoice
                    subscription.payment.invoice.delete()
                # delete the subscription payment
                subscription.payment.delete()
        # finally delete the association subscriptions
        subscriptions.delete()
        print("[DEPLOY TASK] OK!")
    except Exception as e:
        print(f"[DEPLOY TASK] error {e}")


@shared_task(name="auto_archive_subscription")
def auto_archive_subscription():
    try:
        print("[DEPLOY TASK] archive subscription automatically")
        # getting all the subscriptions that are not deleted and are not archived
        subscriptions_qs = Subscription.objects.filter(archived=False, deleted=False).select_related(
            'sport_association__user', 'payment__invoice'
        )
        print(f"[DEPLOY TASK] found {subscriptions_qs.count()} subscriptions")
        subscriptions = subscriptions_qs.iterator(chunk_size=100)
        for subscription in subscriptions:
            if subscription.sport_association.user.auto_archive:
                date_from, date_to = BalanceSheetData.get_range_from_year_and_starting_date(
                    date=subscription.creation_date,
                    starting_day=subscription.sport_association.user.balance_sheet_start_day,
                    starting_month=subscription.sport_association.user.balance_sheet_start_month
                )
                if datetime.now() > date_to:
                    print(f"[DEPLOY TASK] archive subscription {subscription.id}")
                    subscription.archived = True
                    subscription.save()
                    subscription.payment.archived = True
                    subscription.payment.save()
                    subscription.payment.invoice.archived = True
                    subscription.payment.invoice.save()
        print("[DEPLOY TASK] OK!")
    except Exception as e:
        print(f"[DEPLOY TASK] error {e}")


@shared_task(name="auto_move_users_to_base_plan")
def auto_move_users_to_base_plan():
    try:
        logger.info("Starting auto_move_users_to_base_plan task", extra={'task_name': 'auto_move_users_to_base_plan'})
        # getting pro and teams plan
        plans = BillingPlan.objects.filter(billing_type__in=[2, 3])
        # getting all billing subscriptions plans
        sub_plan = BillingSubscription.objects.filter(billing_plan__in=plans).values_list('user_id', flat=True)
        # check if ends before today, timezone is considered
        sub_plan = sub_plan.filter(
            ends_on__isnull=False,
            ends_on__range=[datetime.now() - timedelta(days=1), datetime.now()]
        )

        if len(sub_plan) > 0:
            logger.info("Found users to move to base plan", extra={'task_name': 'auto_move_users_to_base_plan', 'count': len(sub_plan)})
            # getting all the users that have a subscription plan
            users = User.objects.filter(user_id__in=sub_plan, role=1)
            # send email to the users
            for user in users:
                logger.info("Sending plan expired email", extra={'task_name': 'auto_move_users_to_base_plan', 'user_id': str(user.user_id)})
                # send email to the user
                send_email_template(
                    subject="Il tuo piano è scaduto",
                    template="email/plan/subscription_expired.html",
                    data={
                        'first_name': user.first_name,
                        'last_name': user.last_name,
                        'app_host': settings.APP_URL
                    },
                    recipient_list=[user.email],
                    sport_association_id=user.sport_association.sport_association_id,
                    reply_to=[settings.DEFAULT_SUPPORT_EMAIL],
                )
        logger.info("Completed auto_move_users_to_base_plan task", extra={'task_name': 'auto_move_users_to_base_plan'})
    except Exception:
        logger.error("Error in auto_move_users_to_base_plan task", extra={'task_name': 'auto_move_users_to_base_plan'}, exc_info=True)


@shared_task(name="nurturing_warm_leads")
def nurturing_warm_leads():
    if settings.IS_WHITELABEL:
        return
    try:
        logger.info("Starting nurturing_warm_leads task", extra={'task_name': 'nurturing_warm_leads'})

        # get current Date, not datetime
        current_date = datetime.now().date()

        # get all nurturingEmailsPlan that are active today
        nurturing_emails_plan_list = NurturingEmailsPlan.objects.filter(
            Q(exclude_date_from_of_the_year__lte=current_date)
            | Q(exclude_date_from_of_the_year__isnull=True),
            Q(exclude_date_to_of_the_year__gte=current_date)
            | Q(exclude_date_to_of_the_year__isnull=True),
            Q(active_specific_date_of_the_year__exact=current_date)
            | Q(active_specific_date_of_the_year__isnull=True),
        )

        # get the free plan
        free_plan = BillingPlan.objects.filter(billing_type=BillingPlan.BASE_PLAN).first()

        # loop through nurturingEmailsPlan and send email
        for nurturing_emails_plan in nurturing_emails_plan_list:
            # extract the billing subscriptions
            billing_subscriptions = BillingSubscription.objects.all()

            # if this email is only for free plan, filter the billing subscriptions
            if nurturing_emails_plan.only_free_plan:
                billing_subscriptions = billing_subscriptions.filter(
                    billing_plan=free_plan
                )

            # loop through billing plans - use iterator to avoid loading all in memory
            for billing_subscription in billing_subscriptions.select_related('user').iterator(chunk_size=100):
                # skip if the email is already sent
                if NurturingEmails.objects.filter(
                        user=billing_subscription.user,
                        nurturing_plan=nurturing_emails_plan
                ).exists():
                    continue

                # check if to look for expiration date
                if nurturing_emails_plan.check_date_conditions:
                    # get the expiration date
                    expiration_date = billing_subscription.ends_on.date()
                    # get the days after registration
                    days_after_registration = (current_date - billing_subscription.user.date_joined.date()).days
                    # get the days before expiration
                    days_before_expiration = (expiration_date - current_date).days
                    # get the days after expiration
                    days_after_expiration = (current_date - expiration_date).days

                    # nurturing plan, just for the seek of readability
                    n_plan = nurturing_emails_plan

                    at_least_these_days_after_registration = n_plan.at_least_these_days_after_registration
                    at_most_these_days_after_registration = n_plan.at_most_these_days_after_registration
                    at_least_these_days_before_expiration = n_plan.at_least_these_days_before_expiration
                    at_most_these_days_before_expiration = n_plan.at_most_these_days_before_expiration
                    at_least_these_days_after_expiration = n_plan.at_least_these_days_after_expiration
                    at_most_these_days_after_expiration = n_plan.at_most_these_days_after_expiration

                    # DATE CONDITIONS are mutually exclusive (in pairs of least and most)
                    # if most and least are 0, it means that the condition is not set

                    if at_least_these_days_after_registration > 0:
                        if days_after_registration < at_least_these_days_after_registration:
                            continue

                    if at_most_these_days_after_registration > 0:
                        if days_after_registration > at_most_these_days_after_registration:
                            continue

                    if at_least_these_days_before_expiration > 0:
                        if days_before_expiration < at_least_these_days_before_expiration:
                            continue

                    if at_most_these_days_before_expiration > 0:
                        if days_before_expiration > at_most_these_days_before_expiration:
                            continue

                    if at_least_these_days_after_expiration > 0:
                        if days_after_expiration < at_least_these_days_after_expiration:
                            continue

                    if at_most_these_days_after_expiration > 0:
                        if days_after_expiration > at_most_these_days_after_expiration:
                            continue

                # check if there is a required nurturing plan
                if nurturing_emails_plan.required_nurturing_plan is not None:
                    # check from model NurturingEmails
                    nurturing_emails = NurturingEmails.objects.filter(
                        user=billing_subscription.user,
                        nurturing_plan=nurturing_emails_plan.required_nurturing_plan,
                        sent_date__lt=datetime.now() - timedelta(days=1)
                    )

                    # if there is no required nurturing plan, skip
                    if len(nurturing_emails) == 0:
                        logger.debug("Skipping user, required nurturing plan not met", extra={'task_name': 'nurturing_warm_leads', 'user_id': str(billing_subscription.user.user_id), 'required_plan': nurturing_emails_plan.required_nurturing_plan.name})
                        continue

                data = {
                    'first_name': billing_subscription.user.first_name,
                    'last_name': billing_subscription.user.last_name,
                    'app_host': settings.APP_URL
                }
                # perform the nurturing plan
                message = render_to_string(nurturing_emails_plan.email_template, data)
                # send email
                send_mail_async.apply_async(
                    kwargs={
                        "subject": nurturing_emails_plan.email_subject,
                        "message": message,
                        "from_email": settings.DEFAULT_TEAM_EMAIL,
                        "reply_to": [settings.DEFAULT_SUPPORT_EMAIL],
                        "recipient_list": [billing_subscription.user.email],
                        "html_message": message,
                        "fail_silently": False
                    }
                )

                # save the nurturing email
                NurturingEmails.objects.create(
                    user=billing_subscription.user,
                    nurturing_plan=nurturing_emails_plan
                )
        logger.info("Completed nurturing_warm_leads task", extra={'task_name': 'nurturing_warm_leads'})
    except Exception:
        logger.error("Error in nurturing_warm_leads task", extra={'task_name': 'nurturing_warm_leads'}, exc_info=True)


@shared_task(name="auto_mark_attendance")
def auto_mark_attendance():
    try:
        print("[auto_mark_attendance] auto mark attendance")
        # getting pro and teams plan
        plans = BillingPlan.objects.filter(billing_type__in=[2, 3])
        # getting all billing subscriptions plans
        sub_plan = BillingSubscription.objects.filter(billing_plan__in=plans).values_list('user_id', flat=True)
        # getting all the user with role 1 that have auto mark attendance enabled and plan is at least pro
        users_qs = User.objects.filter(role=1, auto_mark_attendance=True).filter(user_id__in=sub_plan)
        print(f"[auto_mark_attendance] found {users_qs.count()} users")
        users = users_qs.iterator(chunk_size=50)
        for user in users:
            # get user sport association
            sport_association = SportAssociation.objects.filter(user=user).first()
            # getting all active courses
            courses = Course.objects.filter(status_flag=Course.ACTIVE, sport_association=sport_association)
            print(f"[auto_mark_attendance] found {len(courses)} courses")
            for course in courses:
                # get attendance registry
                attendance_registry = AttendanceRegistry.objects.filter(course=course).first()
                # skip if no attendance registry
                if attendance_registry is None:
                    continue
                attendance_days = AttendanceDay.objects.filter(
                    attendance_registry=attendance_registry,
                    auto_marked=False,
                    date__range=[datetime.now() - timedelta(days=1), datetime.now()]
                )
                # skip if no attendance days
                if attendance_days is None:
                    continue
                # get all the course subscriptions for the course
                course_subscriptions = CourseSubscription.objects.filter(course=course) \
                    .values_list('course_subscription_id', flat=True)
                print(f"[auto_mark_attendance] found {len(attendance_days)} attendance days")
                for attendance_day in attendance_days:
                    # check if attendance day is today
                    # get attendance date as YYYY-MM-DD
                    rome = pytz.timezone('Europe/Rome')
                    # get attendance_date in Rome as datetime
                    attendance_date = attendance_day.date.astimezone(rome)
                    # get today date as YYYY-MM-DD
                    today_date = datetime.now(pytz.timezone('Europe/Rome'))
                    print(f"[auto_mark_attendance] attendance day {attendance_date} today {today_date} auto marked {attendance_day.auto_marked}")
                    if attendance_date < today_date and attendance_date > (today_date - timedelta(days=1)):
                        # check if attendees is None
                        if attendance_day.attendees is None:
                            attendance_day.attendees = []

                        attendees_list = []

                        if attendance_day.expected_absences is None:
                            attendance_day.expected_absences = []

                        # iterate over attendees and update the carnet if there is one
                        for attendee in course_subscriptions:
                            # get course subscription
                            course_subscription = CourseSubscription.objects.filter(
                                course_subscription_id=attendee).first()

                            # must check is not in the expected_absences
                            if attendance_day.expected_absences and any(
                                    str(attendee) == expected_absence['course_subscription_id']
                                    for expected_absence in attendance_day.expected_absences):
                                continue

                            # check if there is a carnet
                            if course_subscription:
                                # enable when using postgresql in every environment
                                # carnet = CarnetSubscription.objects.filter(
                                #     course_subscription=course_subscription,
                                #     meta__contains={"lessons_left": {"$gt": 0}}
                                # ).first()
                                carnets = CarnetSubscription.objects.filter(
                                    Q(course_subscription=course_subscription) |
                                    Q(course_subscription__isnull=True),
                                    subscription=course_subscription.subscription,
                                    disabled=False,
                                ).order_by('-creation_date')
                                carnet = None
                                for c in carnets:
                                    if c.meta['lessons_left'] > 0:
                                        if carnet is None or (
                                                carnet is not None and
                                                carnet.meta['lessons_left'] > c.meta['lessons_left']
                                        ):
                                            carnet = c
                                # end enable when using postgresql in every environment
                                if carnet:
                                    if carnet.meta['lessons_left'] == 0 or (
                                            carnet.payment is not None and carnet.payment.paid is False
                                    ):
                                        print(f"[auto_mark_attendance] carnet finished or not paid yet {course_subscription}")
                                        continue
                                    # update meta
                                    carnet.meta['lessons_left'] -= 1
                                    carnet.meta['lessons_registry'].append({
                                        "date": str(attendance_day.date),
                                        "course": {
                                            "id": str(course.course_id),
                                            "title": course.title,
                                        },
                                        "title": attendance_day.title,
                                    })
                                    carnet.save()
                                    attendees_list.append({
                                        "course_subscription_id": str(attendee)
                                    })
                                    print("[auto_mark_attendance] course carnet subscription {} marked".format(str(attendee)))
                                elif carnets.count() > 0:
                                    print(f"[auto_mark_attendance] carnet finished {course_subscription}")
                                else:
                                    attendees_list.append({
                                        "course_subscription_id": str(attendee)
                                    })
                                    print("[auto_mark_attendance] course subscription {} marked".format(str(attendee)))
                        print(f"[auto_mark_attendance] found {len(attendees_list)} attendees")
                        attendance_day.attendees = attendees_list
                        attendance_day.auto_marked = True
                        attendance_day.save()
                        print("[auto_mark_attendance] attendance day {} marked".format(attendance_day.attendance_day_id))

        print("[auto_mark_attendance] OK!")
    except Exception as e:
        print(f"[auto_mark_attendance] error {e}")


@shared_task(name="auto_delete_orphan_medical_certificate")
def auto_delete_orphan_medical_certificate():
    try:
        print("[DEPLOY TASK] delete orphan medical certificate automatically")
        # getting all the subscriptions that are not deleted and are not archived
        subscriptions = Subscription.objects.all().filter(medical__isnull=False).values_list('medical', flat=True)
        print(subscriptions)
        medical_certificates = MedicalCertificate.objects.all().exclude(medical_id__in=subscriptions).select_related('document')
        for medical_certificate in medical_certificates.iterator(chunk_size=50):
            # get document from the medical certificate
            document = medical_certificate.document
            storing_path = os.path.join(STORAGE_DIR, str(document.creation_date.timestamp()), str(document.document_id))
            file = os.path.join(storing_path, document.filename)
            # delete the file
            default_storage.delete(file)
            document.delete()
            medical_certificate.delete()
            # print the result
            print(f"[DEPLOY TASK] deleted {file}")
        print("[DEPLOY TASK] OK!")
    except Exception as e:
        print(f"[DEPLOY TASK] error {e}")


@shared_task(name="send_expiring_certificate_email")
def send_expiring_certificate_email():
    try:
        logger.info("Starting send_expiring_certificate_email task", extra={'task_name': 'send_expiring_certificate_email'})

        now = timezone.now()

        past_year = now - timedelta(days=366)

        # getting the subscription expiring in 7 days or 30 days
        subscriptions_qs = Subscription.objects.filter(
            archived=False,
            deleted=False,
            creation_date__gte=past_year,
        ).filter(
            Q(medical__expiration_date__exact=datetime.now().date() + timedelta(days=7))
            | Q(medical__expiration_date__exact=datetime.now().date() + timedelta(days=30))
        ).select_related(
            'associate',
            'sport_association',
            'sport_association__user',
            'medical',
            'user',
        )

        if subscriptions_qs.count() == 0:
            logger.info("No expiring subscriptions found", extra={'task_name': 'send_expiring_certificate_email'})
            return

        new_subscriptions = []
        for subscription in subscriptions_qs:
            if subscription.is_current:
                new_subscriptions.append(subscription)
        subscriptions = new_subscriptions

        for subscription in subscriptions:
            if subscription.medical is None:
                logger.info("No medical certificate found, skipping", extra={'task_name': 'send_expiring_certificate_email', 'subscription_id': str(subscription.subscription_id)})
                continue

            if subscription.sport_association.user.medical_certificate_notifications is False:
                logger.info("Medical certificate notifications disabled, skipping", extra={'task_name': 'send_expiring_certificate_email', 'sport_association_id': str(subscription.sport_association.sport_association_id)})
                continue

            data = {
                'athlete_first_name': subscription.associate.first_name,
                'athlete_last_name': subscription.associate.last_name,
                'sport_association': subscription.sport_association,
                'certificate_expiring_date': subscription.medical.expiration_date.strftime("%d/%m/%Y"),
                'app_host': settings.APP_URL,
                'settings': settings
            }
            # check if there is associate email
            athlete_email = None

            if subscription.associate.email is not None:
                try:
                    check_email(subscription.associate.email)
                    athlete_email = subscription.associate.email
                except Exception:
                    logger.warning("Invalid associate email", extra={'task_name': 'send_expiring_certificate_email', 'email': subscription.associate.email, 'associate_id': str(subscription.associate.associate_id)})

            # extract email from athlete or tutor
            tutor = subscription.associate.get_main_tutor()
            if athlete_email is None \
                    and tutor is not None \
                    and tutor.email is not None:
                try:
                    check_email(tutor.email)
                    athlete_email = tutor.email
                except Exception:
                    logger.warning("Invalid tutor email", extra={'task_name': 'send_expiring_certificate_email', 'email': tutor.email, 'associate_id': str(subscription.associate.associate_id)})

            if athlete_email is None:
                athlete_email = subscription.user.email

            recipient_list = [f"{athlete_email}".lower()]
            reply_to = [subscription.sport_association.user.email]

            logger.info("Sending expiring certificate email", extra={'task_name': 'send_expiring_certificate_email', 'recipient_list': recipient_list})

            message = render_to_string('email/account/email_subscription_medical_certificate_expiring.html', data)
            subject = f"{settings.WHITELABEL_NAME} | Scadenza certificato medico"

            configuration = CommunicationConfiguration.objects.filter(
                sport_association=subscription.sport_association
            ).first()

            if configuration:
                configuration.send_email(
                    subject=subject,
                    body=None,
                    html_body=message,
                    recipient_list=recipient_list,
                    reply_to=reply_to,
                )
            else:
                send_mail_async.apply_async(
                    kwargs={
                        "subject": subject,
                        "message": message,
                        "from_email": settings.DEFAULT_TEAM_EMAIL,
                        "recipient_list": recipient_list,
                        "html_message": message,
                        "fail_silently": False,
                        "sport_association_id": subscription.sport_association.sport_association_id,
                        "reply_to": reply_to,
                    }
                )

        logger.info("Completed send_expiring_certificate_email task", extra={'task_name': 'send_expiring_certificate_email'})
    except Exception as e:
        logger.error("Error in send_expiring_certificate_email task", extra={'task_name': 'send_expiring_certificate_email'}, exc_info=True)


@shared_task(name="send_expired_certificate_email")
def send_expired_certificate_email():
    try:
        logger.info("Starting send_expired_certificate_email task", extra={'task_name': 'send_expired_certificate_email'})

        now = timezone.now()

        past_year = now - timedelta(days=366)

        # getting the subscription expired yesterday
        subscriptions_qs = Subscription.objects.filter(
            archived=False,
            deleted=False,
            creation_date__gte=past_year,
            medical__expiration_date__exact=datetime.now().date() - timedelta(days=1)
        ).select_related(
            'associate',
            'sport_association',
            'sport_association__user',
            'medical',
            'user',
        )

        if subscriptions_qs.count() == 0:
            logger.info("No expired subscriptions found", extra={'task_name': 'send_expired_certificate_email'})
            return

        new_subscriptions = []
        for subscription in subscriptions_qs.iterator(chunk_size=50):
            if subscription.is_current:
                new_subscriptions.append(subscription)

        for subscription in new_subscriptions:
            if subscription.medical is None:
                logger.info("No medical certificate found, skipping", extra={'task_name': 'send_expired_certificate_email', 'subscription_id': str(subscription.subscription_id)})
                continue

            if subscription.sport_association.user.medical_certificate_notifications is False:
                logger.info("Medical certificate notifications disabled, skipping", extra={'task_name': 'send_expired_certificate_email', 'sport_association_id': str(subscription.sport_association.sport_association_id)})
                continue

            data = {
                'athlete_first_name': subscription.associate.first_name,
                'athlete_last_name': subscription.associate.last_name,
                'sport_association': subscription.sport_association,
                'certificate_expiring_date': subscription.medical.expiration_date.strftime("%d/%m/%Y"),
                'app_host': settings.APP_URL,
                'settings': {
                    'WHITELABEL_NAME': settings.WHITELABEL_NAME,
                    'IS_WHITELABEL': settings.IS_WHITELABEL
                }
            }

            athlete_email = None

            if subscription.associate.email is not None:
                try:
                    check_email(subscription.associate.email)
                    athlete_email = subscription.associate.email
                except Exception:
                    logger.warning("Invalid associate email", extra={'task_name': 'send_expired_certificate_email', 'email': subscription.associate.email, 'associate_id': str(subscription.associate.associate_id)})

            # extract email from athlete or tutor
            tutor = subscription.associate.get_main_tutor()
            if athlete_email is None \
                    and tutor is not None \
                    and tutor.email is not None:
                try:
                    check_email(tutor.email)
                    athlete_email = tutor.email
                except Exception:
                    logger.warning("Invalid tutor email", extra={'task_name': 'send_expired_certificate_email', 'email': tutor.email, 'associate_id': str(subscription.associate.associate_id)})

            if athlete_email is None:
                athlete_email = subscription.user.email

            recipient_list = [f"{athlete_email}".lower()]
            reply_to = [subscription.sport_association.user.email]

            logger.info("Sending expired certificate email", extra={'task_name': 'send_expired_certificate_email', 'recipient_list': recipient_list})

            message = render_to_string('email/account/email_subscription_medical_certificate_expired.html', data)
            subject = f"{settings.WHITELABEL_NAME} | Certificato medico scaduto"

            configuration = CommunicationConfiguration.objects.filter(
                sport_association=subscription.sport_association
            ).first()

            if configuration:
                configuration.send_email(
                    subject=subject,
                    body=None,
                    html_body=message,
                    recipient_list=recipient_list,
                    reply_to=reply_to,
                )
            else:
                send_mail_async.apply_async(
                    kwargs={
                        "subject": subject,
                        "message": message,
                        "from_email": settings.DEFAULT_TEAM_EMAIL,
                        "recipient_list": recipient_list,
                        "html_message": message,
                        "fail_silently": False,
                        "sport_association_id": subscription.sport_association.sport_association_id,
                        "reply_to": reply_to,
                    }
                )

        logger.info("Completed send_expired_certificate_email task", extra={'task_name': 'send_expired_certificate_email'})
    except Exception:
        logger.error("Error in send_expired_certificate_email task", extra={'task_name': 'send_expired_certificate_email'}, exc_info=True)


@shared_task(name="send_user_partial_registration_email")
def send_user_partial_registration_email():
    try:
        logger.info("Starting send_user_partial_registration_email task", extra={'task_name': 'send_user_partial_registration_email'})
        # getting the subscription expired yesterday
        user_partials_qs = UserPartial.objects.filter(creation_date__gte=datetime.now().date() - timedelta(days=1))

        if user_partials_qs.count() == 0:
            logger.info("No user partials found", extra={'task_name': 'send_user_partial_registration_email'})
            return

        for user_partial in user_partials_qs.iterator(chunk_size=50):
            data = {
                'email': user_partial.email,
                'app_host': settings.APP_URL,
                'settings': {
                    'WHITELABEL_NAME': settings.WHITELABEL_NAME,
                    'IS_WHITELABEL': settings.IS_WHITELABEL
                }
            }

            recipient_list = [user_partial.email.lower()]

            logger.info("Sending partial registration email", extra={'task_name': 'send_user_partial_registration_email', 'recipient_list': recipient_list})

            message = render_to_string('email/account/email_user_partial_registration.html', data)
            subject = f"Completa la tua registrazione per attivare la prova gratuita di {settings.WHITELABEL_NAME}"

            send_mail_async.apply_async(
                kwargs={
                    "subject": subject,
                    "message": message,
                    "from_email": settings.DEFAULT_TEAM_EMAIL,
                    "recipient_list": recipient_list,
                    "html_message": message,
                    "fail_silently": False
                }
            )

            user_partial.delete()

        logger.info("Completed send_user_partial_registration_email task", extra={'task_name': 'send_user_partial_registration_email'})
    except Exception:
        logger.error("Error in send_user_partial_registration_email task", extra={'task_name': 'send_user_partial_registration_email'}, exc_info=True)


@shared_task(name="send_updated_certificate_email")
def send_updated_certificate_email(subscription_id):
    try:
        logger.info("Starting send_updated_certificate_email task", extra={'task_name': 'send_updated_certificate_email', 'subscription_id': str(subscription_id)})
        # getting the subscription
        subscription = Subscription.objects.filter(
            subscription_id=subscription_id
        ).select_related('associate', 'sport_association', 'sport_association__user', 'medical', 'user').first()
        if subscription is None:
            logger.warning("Subscription not found", extra={'task_name': 'send_updated_certificate_email', 'subscription_id': str(subscription_id)})
            return

        data = {
            'athlete_first_name': subscription.associate.first_name,
            'athlete_last_name': subscription.associate.last_name,
            'sport_association': subscription.sport_association,
            'certificate_expiring_date': subscription.medical.expiration_date.strftime("%d/%m/%Y"),
            'app_host': settings.APP_URL,
            'settings': {
                'WHITELABEL_NAME': settings.WHITELABEL_NAME,
                'IS_WHITELABEL': settings.IS_WHITELABEL
            }
        }

        athlete_email = subscription.user.email
        recipient_list = [f"{subscription.sport_association.user.email}".lower()]
        if athlete_email and athlete_email.lower() != subscription.sport_association.user.email.lower():
            recipient_list.append(athlete_email.lower())
        reply_to = [subscription.sport_association.user.email]

        logger.info("Sending updated certificate email", extra={'task_name': 'send_updated_certificate_email', 'recipient_list': recipient_list})

        message = render_to_string('email/account/email_subscription_medical_certificate_changed.html', data)
        subject = f"{settings.WHITELABEL_NAME} | Nuovo certificato medico"

        send_mail_async.apply_async(
            kwargs={
                "subject": subject,
                "message": message,
                "from_email": settings.DEFAULT_TEAM_EMAIL,
                "recipient_list": recipient_list,
                "html_message": message,
                "fail_silently": False,
                "sport_association_id": subscription.sport_association.sport_association_id,
                "reply_to": reply_to,
            }
        )

        logger.info("Completed send_updated_certificate_email task", extra={'task_name': 'send_updated_certificate_email'})
    except Exception:
        logger.error("Error in send_updated_certificate_email task", extra={'task_name': 'send_updated_certificate_email'}, exc_info=True)


@shared_task(name="send_email_template")
def send_email_template(template, subject, recipient_list, data, sport_association_id=None, reply_to=None):
    try:
        logger.info("Sending template email", extra={'task_name': 'send_email_template', 'template': template, 'recipient_list': recipient_list, 'sport_association_id': str(sport_association_id) if sport_association_id else None})

        message = render_to_string(template, data)

        if sport_association_id is not None:
            configuration = CommunicationConfiguration.objects.filter(
                sport_association_id=sport_association_id
            ).first()
        else:
            configuration = None

        if configuration:
            configuration.send_email(
                subject=subject,
                body=None,
                html_body=message,
                recipient_list=recipient_list,
                reply_to=reply_to,
            )
        else:
            send_mail_async.apply_async(
                kwargs={
                    "subject": subject,
                    "message": message,
                    "from_email": settings.DEFAULT_TEAM_EMAIL,
                    "recipient_list": recipient_list,
                    "html_message": message,
                    "fail_silently": False,
                    "sport_association_id": sport_association_id,
                    "reply_to": reply_to,
                }
            )

        logger.info("Completed send_email_template", extra={'task_name': 'send_email_template'})
    except Exception as e:
        logger.error("Error in send_email_template", extra={'task_name': 'send_email_template'}, exc_info=True)


@shared_task(name="send_email_text")
def send_email_text(message, subject, recipient_list, sport_association_id=None, reply_to=None):
    try:
        logger.info("Sending text email", extra={'task_name': 'send_email_text', 'recipient_list': recipient_list, 'sport_association_id': str(sport_association_id) if sport_association_id else None})

        send_mail_async.apply_async(
            kwargs={
                "subject": subject,
                "message": message,
                "from_email": settings.DEFAULT_TEAM_EMAIL,
                "recipient_list": recipient_list,
                "fail_silently": False,
                "sport_association_id": sport_association_id,
                "reply_to": reply_to,
            }
        )

        logger.info("Completed send_email_text", extra={'task_name': 'send_email_text'})
    except Exception as e:
        logger.error("Error in send_email_text", extra={'task_name': 'send_email_text'}, exc_info=True)


@shared_task(name="send_email_html")
def send_email_html(html_message, subject, recipient_list, sport_association_id=None, reply_to=None):
    try:
        logger.info("Sending HTML email", extra={'task_name': 'send_email_html', 'recipient_list': recipient_list, 'sport_association_id': str(sport_association_id) if sport_association_id else None})

        if sport_association_id is not None:
            configuration = CommunicationConfiguration.objects.filter(
                sport_association_id=sport_association_id
            ).first()

            if configuration:
                # execute after a random delay between 1 and 60 seconds
                # define the random delay
                delay = random.randint(1, 30)

                # define the sending function task
                def send_email():
                    configuration.send_email(
                        subject=subject,
                        body=None,
                        html_body=html_message,
                        recipient_list=recipient_list,
                        reply_to=reply_to,
                    )

                # execute the sending function after the random delay
                Timer(delay, send_email).start()
                return

        send_mail_async.apply_async(
            kwargs={
                "subject": subject,
                "message": html_message,
                "from_email": settings.DEFAULT_FROM_EMAIL,
                "recipient_list": recipient_list,
                "html_message": html_message,
                "fail_silently": False,
                "sport_association_id": sport_association_id,
                "reply_to": reply_to,
            }
        )

        logger.info("Completed send_email_html", extra={'task_name': 'send_email_html'})
    except Exception as e:
        logger.error("Error in send_email_html", extra={'task_name': 'send_email_html'}, exc_info=True)


@shared_task(name="workflow_block_execute")
def workflow_block_execute(workflow_id, subscription_id=None, idx=None):
    try:
        logger.info("Executing workflow block", extra={'task_name': 'workflow_block_execute', 'workflow_id': str(workflow_id), 'subscription_id': str(subscription_id), 'idx': idx})
        subscription = Subscription.objects.filter(subscription_id=subscription_id).select_related('associate', 'user').first()
        workflow = AutomationWorkflow.objects.filter(
            automation_workflow_id=workflow_id, enabled=True
        ).select_related('sport_association', 'sport_association__user').first()

        if workflow is None:
            logger.warning("Workflow not found or disabled", extra={'task_name': 'workflow_block_execute', 'workflow_id': str(workflow_id)})
            return

        if subscription is None:
            return False, "Subscription is None"

        node = workflow.automation_tree[idx]
        next_id = idx + 1
        # check if there is a next node
        if next_id >= len(workflow.automation_tree):
            next_id = None
        if node['id'] == 'message' and node['value'] == 'email':
            # send an email
            message = node['data']['content']
            subject = node['data']['subject']
            if node['data']['recipients'] == 'athletes' or \
                    node['data']['recipients'] == 'all':
                if workflow.sport_association.user == subscription.user and node['data']['recipients'] == 'athletes':
                    recipient_list = []
                else:
                    recipient_list = [subscription.user.email]
                    if workflow.sport_association.user != subscription.user:
                        recipient_list.append(workflow.sport_association.user.email)
                # add to the recipient_list the email of the athlete and tutors if available
                if subscription.associate.email:
                    recipient_list.append(subscription.associate.email)
                tutor = subscription.associate.get_main_tutor()
                if subscription.associate.is_minor and \
                        tutor and tutor.email:
                    recipient_list.append(tutor.email)
            else:
                # recipient it's myself
                recipient_list = [workflow.sport_association.user.email]
            # clear recipient_list from duplicates
            recipient_list = list(set(recipient_list))
            # countdown is a random number
            countdown = random.randint(1, 180)

            app.send_task(
                'send_email_html',
                kwargs={
                    'html_message': message,
                    'subject': subject,
                    'recipient_list': recipient_list,
                    'sport_association_id': workflow.sport_association.sport_association_id,
                    'reply_to': [workflow.sport_association.user.email],
                },
                countdown=countdown
            )

            if next_id is not None:
                app.send_task(
                    'workflow_block_execute',
                    args=[workflow_id, subscription.subscription_id, next_id],
                    countdown=countdown
                )
        elif node['id'] == 'wait':
            # schedule the workflow block execute with the next idx
            # check node['data']['type'] == 'minutes' or 'hours' or 'days'
            if node['data']['type'] == 'minutes':
                countdown = int(node['data']['amount']) * 60
            elif node['data']['type'] == 'hours':
                countdown = int(node['data']['amount']) * 3600
            elif node['data']['type'] == 'days':
                countdown = int(node['data']['amount']) * 86400

            if next_id is not None:
                app.send_task(
                    'workflow_block_execute',
                    args=[workflow_id, subscription.subscription_id, next_id],
                    countdown=countdown
                )

        logger.info("Completed workflow block execution", extra={'task_name': 'workflow_block_execute', 'workflow_id': str(workflow_id)})
    except Exception:
        logger.error("Error in workflow_block_execute", extra={'task_name': 'workflow_block_execute', 'workflow_id': str(workflow_id)}, exc_info=True)


@shared_task(name="check_workflows_trigger")
def check_workflows_trigger(trigger_type, sport_association_id, subscription_id=None, data=None):
    try:
        logger.info("Starting check_workflows_trigger task", extra={'task_name': 'check_workflows_trigger', 'trigger_type': trigger_type, 'sport_association_id': str(sport_association_id), 'subscription_id': str(subscription_id) if subscription_id else None})
        # getting all the workflows
        workflows_qs = AutomationWorkflow.objects.filter(
            sport_association=sport_association_id,
            enabled=True
        )
        # check if there are workflows
        if workflows_qs.count() == 0:
            logger.info("No workflows found or disabled", extra={'task_name': 'check_workflows_trigger', 'sport_association_id': str(sport_association_id)})
            return
        # iterate over workflows and check if trigger is the same
        for workflow in workflows_qs.iterator(chunk_size=50):
            logger.debug("Checking workflow trigger", extra={'task_name': 'check_workflows_trigger', 'workflow_id': str(workflow.automation_workflow_id), 'trigger_type': trigger_type, 'workflow_trigger': workflow.automation_tree[0]['value']})
            if workflow.automation_tree[0]['value'] == trigger_type:
                # trigger the workflow
                subscription = Subscription.objects.filter(subscription_id=subscription_id).first()
                if subscription is None:
                    logger.warning("Subscription not found", extra={'task_name': 'check_workflows_trigger', 'subscription_id': str(subscription_id)})
                    return
                workflow.trigger(trigger_type=trigger_type, subscription=subscription, data=data)
    except Exception:
        logger.error("Error in check_workflows_trigger task", extra={'task_name': 'check_workflows_trigger'}, exc_info=True)


@shared_task(name="check_workflows_trigger_cron")
def check_workflows_trigger_cron(workflow_automation_id):
    try:
        logger.info("Starting check_workflows_trigger_cron task", extra={'task_name': 'check_workflows_trigger_cron', 'workflow_automation_id': str(workflow_automation_id)})
        # getting all the workflows
        workflow = AutomationWorkflow.objects.filter(
            automation_workflow_id=workflow_automation_id,
            enabled=True,
        ).first()
        # check if there are workflows and if the workflow has less than 2 nodes
        if workflow is None or len(workflow.automation_tree) <= 1:
            logger.info("No workflows found or disabled", extra={'task_name': 'check_workflows_trigger_cron', 'workflow_automation_id': str(workflow_automation_id)})
            return
        # trigger the workflow
        workflow.trigger(trigger_type='cron')
        logger.info("Completed check_workflows_trigger_cron task", extra={'task_name': 'check_workflows_trigger_cron'})
    except Exception:
        logger.error("Error in check_workflows_trigger_cron task", extra={'task_name': 'check_workflows_trigger_cron'}, exc_info=True)


@shared_task(name="send_reminders")
def send_reminders():
    logger.info("Starting send_reminders task", extra={'task_name': 'send_reminders'})
    try:
        # get today datetime in UTC
        current_ts = datetime.now().astimezone(pytz.utc)

        # get all the reminders
        reminders = Reminders.objects.filter(
            completed=False,
            send_at__lte=current_ts,
            sport_association__isnull=False
        ).select_related('sport_association', 'sport_association__user', 'user', 'instructor').iterator(chunk_size=50)

        for reminder in reminders:
            # send the reminder via email to the user and/or the instructor
            message = f'''
            <p>Ciao {reminder.sport_association.denomination},</p>
            <p>Il tuo evento "{reminder.event_title}" inizierà tra {reminder.event_reminder_text}.</p>
            <br>
            <p>Descrizione dell'evento:</p>
            <p>{reminder.event_description if reminder.event_description and reminder.event_description != '' else 'Nessun dettaglio.'}</p>
            <br>
            <p>Per maggiori informazioni, visita il tuo profilo su {settings.WHITELABEL_NAME}.</p>
            
            <p>Lo staff di {settings.WHITELABEL_NAME}</p>
            '''
            reply_to = [reminder.sport_association.user.email]
            if reminder.user:
                # send email to the user
                send_email_html(
                    html_message=message,
                    subject=f"{settings.WHITELABEL_NAME} | Promemoria evento {reminder.event_title}",
                    recipient_list=[reminder.user.email],
                    sport_association_id=reminder.sport_association.sport_association_id,
                    reply_to=reply_to,
                )
            if reminder.instructor:
                # send email to the instructor
                send_email_html(
                    html_message=message,
                    subject=f"{settings.WHITELABEL_NAME} | Promemoria evento {reminder.event_title}",
                    recipient_list=[reminder.instructor.email],
                    sport_association_id=reminder.sport_association.sport_association_id,
                    reply_to=reply_to,
                )
            # mark the reminder as completed
            reminder.completed = True
            reminder.save()
            logger.debug("Reminder sent and marked complete", extra={'reminder_id': str(reminder.reminders_id), 'event_title': reminder.event_title})

        logger.info("Completed send_reminders task", extra={'task_name': 'send_reminders'})
    except Exception:
        logger.error("Error in send_reminders task", extra={'task_name': 'send_reminders'}, exc_info=True)


@shared_task(name="association_quote_assign")
def association_quote_assign(date=None, sport_association=None):
    try:
        logger.info("Starting association_quote_assign task", extra={'task_name': 'association_quote_assign', 'date': date, 'sport_association': str(sport_association) if sport_association else None})
        if date is None:
            # get today datetime in UTC
            current_ts = datetime.now().astimezone(pytz.utc)
        else:
            # date is a string in the format YYYY-MM-DD
            current_ts = datetime.strptime(date, "%Y-%m-%d")

        # convert to date
        current_date = current_ts.date()

        if sport_association is None:
            # get all the sport associations that have subscription fee plans
            sport_associations = SportAssociation.objects.filter(
                ~Q(subscription_fee_plans__isnull=True) &
                ~Q(subscription_fee_plans=[])
            ).filter(
                multiple_subscription_fee=True,
                subscription_fee_plans__icontains='"advanced_options": true'
            ).select_related('user').iterator(chunk_size=50)
        else:
            sport_associations = SportAssociation.objects.filter(
                sport_association_id=sport_association
            ).select_related('user').iterator(chunk_size=50)

        for sport_association in sport_associations:
            date_from, date_to = BalanceSheetData.get_range_from_year_and_starting_date(
                date=datetime.now(),
                starting_day=sport_association.user.balance_sheet_start_day,
                starting_month=sport_association.user.balance_sheet_start_month
            )

            # get subscription fee plans
            subscription_fee_plans = sport_association.subscription_fee_plans

            # get active plans to consider and assign automatically
            active_subscription_fee_plans = []
            for plan in subscription_fee_plans:
                # get auto assign and advanced options
                auto_assign = plan.get('auto_assign', False)
                advanced_options = plan.get('advanced_options', False)
                previous_subscription_fee_plan = plan.get('previous_subscription_fee_plan', False)

                if previous_subscription_fee_plan == '':
                    continue

                if not auto_assign or not advanced_options or not previous_subscription_fee_plan:
                    continue

                # check if the plan is active
                from_day = int(plan['from_day'])
                from_month = int(plan['from_month'])
                # check if is today
                if from_day == current_date.day and from_month == current_date.month:
                    # add the plan to the active plans
                    active_subscription_fee_plans.append({
                        'from_day': from_day,
                        'from_month': from_month,
                        'to_day': int(plan['to_day']),
                        'to_month': int(plan['to_month']),
                        'previous_subscription_fee_plan': plan["previous_subscription_fee_plan"],
                        'name': plan['name'],
                        'subscription_fee': plan['subscription_fee'],
                        'id': plan['id']
                    })

                # from_year = None
                #
                # to_day = int(plan['to_day'])
                # to_month = int(plan['to_month'])
                # to_year = None
                #
                #
                # '''
                # Write the logic to ge the year of the from and to date
                # based on the balance sheet start month and day.
                # '''
                #
                # # check if the to date is in the same year
                # if to_month < from_month:
                #     to_year = date_to.year + 1
                # else:
                #     to_year = date_to.year
                #
                # # check if the from date is in the same year
                # if from_month < sport_association.user.balance_sheet_start_month:
                #     from_year = date_from.year - 1
                # else:
                #     from_year = date_from.year
                #

            # get the current year subscriptions
            subscriptions = Subscription.objects.filter(
                sport_association=sport_association,
                creation_date__gte=date_from,
                creation_date__lte=date_to,
                payment__isnull=False,
                payment__meta__isnull=False,
                meta__isnull=False,
                deleted=False,
                archived=False
            ).exclude(meta__exact='{"plan_id": null}').select_related('payment', 'associate', 'user')
            logger.info("Found subscriptions for quote assign", extra={'task_name': 'association_quote_assign', 'count': subscriptions.count(), 'sport_association_id': str(sport_association.sport_association_id)})

            for plan in active_subscription_fee_plans:
                # for each plan we should extract the quote of the last time and see if it is paid
                if plan['previous_subscription_fee_plan'] == '':
                    continue

                logger.debug("Processing plan", extra={'task_name': 'association_quote_assign', 'plan_id': plan.get('id')})

                # get who had the previous payment
                previous_subs_to_check = subscriptions.filter(
                    payment__meta__icontains=plan['previous_subscription_fee_plan']
                )

                logger.debug("Found previous subscriptions to check", extra={'task_name': 'association_quote_assign', 'count': previous_subs_to_check.count()})

                # loop through the subscriptions and assign the new plan
                for subscription in previous_subs_to_check:

                    # check if there is already this kind of payment
                    check_payments = Payment.objects.filter(
                        user=subscription.user,
                        subject=Payment.SUBSCRIPTION,
                        meta__icontains=plan['id'],
                        associate=subscription.associate,
                        amount=plan['subscription_fee'],
                    )
                    if check_payments.exists() and check_payments.count() > 0:
                        logger.debug("Payment already exists, skipping", extra={'task_name': 'association_quote_assign'})
                        continue


                    payment_meta = {
                        "subscription_data": {
                            "subscription_fee": plan['subscription_fee'],
                            "name": plan['name'],
                            "id": plan['id']
                        }
                    }
                    # create a new payment of type iscrizione
                    payment = Payment.objects.create(
                        user=subscription.user,
                        associate=subscription.associate,
                        amount=plan['subscription_fee'],
                        subject=Payment.SUBSCRIPTION,
                        sport_association=sport_association,
                        meta=payment_meta
                    )

                    logger.info("Created payment", extra={'task_name': 'association_quote_assign', 'payment_id': str(payment.payment_id)})

                    if subscription.user == sport_association.user:
                        # skip sending email to the sport association
                        continue

                    logger.info("Sending quote email", extra={'task_name': 'association_quote_assign', 'recipient': subscription.user.email})

                    # send the reminder via email to the user and/or the instructor
                    message = f'''
                        <p>Ciao {subscription.associate.first_name} {subscription.associate.last_name},</p>
                        <p>Ti è stata assegnata la quota associativa "{payment_meta['subscription_data']['name']}" con importo {plan['subscription_fee']} €.</p>
                        <br>
                        <p>Per maggiori informazioni, visita il tuo profilo su {settings.WHITELABEL_NAME}.</p>

                        <p>Cordialmente, <br />
                         {sport_association.denomination}</p>
                    '''
                    if subscription.user:
                        # send email to the user
                        send_email_html(
                            html_message=message,
                            subject=f"{sport_association.denomination} | Nuova quota associativa '{payment_meta['subscription_data']['name']}' assegnata",
                            recipient_list=[subscription.user.email],
                            sport_association_id=sport_association.sport_association_id,
                            reply_to=[sport_association.user.email],
                        )

        logger.info("Completed association_quote_assign task", extra={'task_name': 'association_quote_assign'})
    except Exception:
        logger.error("Error in association_quote_assign task", extra={'task_name': 'association_quote_assign'}, exc_info=True)

@shared_task(name="reset_communication_daily_email_balance")
def reset_email_balance():
    try:
        print("[daily_email_balance] RESET the daily balance")
        configurations = CommunicationConfiguration.objects.filter(
            daily_email_balance__gt=0,
        )
        # Update all matching configurations in a single query
        updated_count = configurations.update(daily_email_balance=0)

        print(f"[daily_email_balance] done! {updated_count} configurations updated")
    except Exception as e:
        print(f"[daily_email_balance] cannot reset daily_email_balance because: {e}")

@shared_task(name="export_invoices_to_zip")
def export_invoices_to_zip(files=None, sport_association=None):
    logger.info("Starting export_invoices_to_zip task", extra={'task_name': 'export_invoices_to_zip', 'sport_association_id': str(sport_association) if sport_association else None})
    try:
        print("[export_invoices_to_zip] export invoices to zip")
        if files is None:
            logger.info("No files to export", extra={'task_name': 'export_invoices_to_zip'})
            print("[export_invoices_to_zip] no files found")
            return

        logger.info("Exporting invoices to ZIP", extra={'file_count': len(files), 'task_name': 'export_invoices_to_zip'})
        print(f"[export_invoices_to_zip] found {len(files)} files")
        printing_service = PrintingService()
        files = Document.objects.filter(document_id__in=files)

        # add the date in YYYY-MM-DD_HH-mm format to filename
        rome_tz = pytz.timezone('Europe/Rome')
        filename = f"{datetime.now(tz=rome_tz).strftime('%Y-%m-%d_%H-%M')}_ricevute.zip"

        document = Document.objects.create(
            filename=filename,
        )
        print("filename: ", filename)

        # get the SportAssociation
        sport_association = SportAssociation.objects.filter(sport_association_id=sport_association).first()
        SportAssociationDocumentsArchive.objects.create(
            sport_association=sport_association,
            document=document
        )

        storing_path = os.path.join(STORAGE_DIR, str(document.creation_date.timestamp()), str(document.document_id))
        filepath = os.path.join(storing_path, document.filename)

        printing_service.download_multiple_files(
            files,
            export_binary=True,
            export_base64=False,
            save_path=filepath,
        )

        # save_binary_to_storage.delay(filepath, file)

        logger.info("Completed export_invoices_to_zip task", extra={'task_name': 'export_invoices_to_zip', 'document_id': str(document.document_id)})
        print("[export_invoices_to_zip] OK!")

    except Exception as e:
        logger.error("Error in export_invoices_to_zip task", extra={'task_name': 'export_invoices_to_zip'}, exc_info=True)
        print(f"[export_invoices_to_zip] error {e}")


@shared_task(name="delete_old_audit_logs")
def delete_old_audit_logs():
    """
    Delete audit log entries older than 365 days.
    The AuditLogIndex entries are deleted automatically via CASCADE.
    """
    from auditlog.models import LogEntry

    logger.info("Starting delete_old_audit_logs task", extra={'task_name': 'delete_old_audit_logs'})
    try:
        cutoff_date = timezone.now() - timedelta(days=365)
        deleted_count, _ = LogEntry.objects.filter(timestamp__lt=cutoff_date).delete()
        logger.info(
            "Completed delete_old_audit_logs task",
            extra={'task_name': 'delete_old_audit_logs', 'deleted_count': deleted_count}
        )
        print(f"[delete_old_audit_logs] Deleted {deleted_count} audit log entries older than 365 days")
    except Exception as e:
        logger.error("Error in delete_old_audit_logs task", extra={'task_name': 'delete_old_audit_logs'}, exc_info=True)
        print(f"[delete_old_audit_logs] error {e}")


# Export retention settings
EXPORT_MAX_AGE_DAYS = 30
EXPORT_MAX_COUNT = 3
EXPORT_ACTIVE_CACHE_TIMEOUT = 2 * 60 * 60


def export_active_cache_key(sport_association_id):
    return f'association-export-active:{sport_association_id}'


def cleanup_old_exports(sport_association_id: str = None, max_count: int = EXPORT_MAX_COUNT):
    """
    Clean up old export files based on retention policy.

    Policy:
    - Delete exports older than EXPORT_MAX_AGE_DAYS (30 days)
    - Keep maximum of EXPORT_MAX_COUNT (3) exports per association

    Args:
        sport_association_id: Optional UUID string to clean up for a specific association.
                             If None, cleans up for all associations.
    """
    import uuid
    from django.utils import timezone

    cutoff_date = timezone.now() - timedelta(days=EXPORT_MAX_AGE_DAYS)
    deleted_count = 0

    # Build base queryset for exports
    export_filter = {
        'document__filename__startswith': 'export_'
    }

    if sport_association_id:
        export_filter['sport_association_id'] = uuid.UUID(sport_association_id)
        associations = [SportAssociation.original_objects.get(
            sport_association_id=uuid.UUID(sport_association_id)
        )]
    else:
        # Get all associations with exports
        association_ids = SportAssociationDocumentsArchive.objects.filter(
            **export_filter
        ).values_list('sport_association_id', flat=True).distinct()
        associations = SportAssociation.original_objects.filter(
            sport_association_id__in=association_ids
        )

    for association in associations:
        # Get all exports for this association, ordered by date (newest first)
        exports = SportAssociationDocumentsArchive.objects.filter(
            sport_association=association,
            document__filename__startswith='export_'
        ).select_related('document').order_by('-date')

        # Delete exports older than cutoff date
        for export in exports:
            if export.document and export.document.creation_date:
                if export.document.creation_date < cutoff_date:
                    try:
                        document = export.document
                        # Delete file from storage
                        if document.filepath:
                            default_storage.delete(document.filepath)
                        export.delete()
                        document.delete()
                        deleted_count += 1
                        logger.info(
                            f"Deleted old export",
                            extra={
                                'document_id': str(document.document_id),
                                'sport_association_id': str(association.sport_association_id),
                                'reason': 'age_exceeded'
                            }
                        )
                    except Exception as e:
                        logger.error(f"Error deleting old export: {e}", exc_info=True)

        # Enforce max count limit (keep only EXPORT_MAX_COUNT most recent)
        remaining_exports = SportAssociationDocumentsArchive.objects.filter(
            sport_association=association,
            document__filename__startswith='export_'
        ).select_related('document').order_by('-date')

        exports_to_delete = list(remaining_exports[max_count:])
        for export in exports_to_delete:
            try:
                document = export.document
                # Delete file from storage
                if document and document.filepath:
                    default_storage.delete(document.filepath)
                export.delete()
                if document:
                    document.delete()
                deleted_count += 1
                logger.info(
                    f"Deleted excess export",
                    extra={
                        'document_id': str(document.document_id) if document else None,
                        'sport_association_id': str(association.sport_association_id),
                        'reason': 'count_exceeded'
                    }
                )
            except Exception as e:
                logger.error(f"Error deleting excess export: {e}", exc_info=True)

    return deleted_count


@shared_task(name="cleanup_old_exports")
def cleanup_old_exports_task():
    """
    Periodic task to clean up old exports for all associations.
    Should be scheduled to run daily.
    """
    logger.info("Starting cleanup_old_exports task", extra={'task_name': 'cleanup_old_exports'})
    try:
        deleted_count = cleanup_old_exports()
        logger.info(
            f"Completed cleanup_old_exports task",
            extra={'task_name': 'cleanup_old_exports', 'deleted_count': deleted_count}
        )
        print(f"[cleanup_old_exports] Deleted {deleted_count} old exports")
        return {'deleted_count': deleted_count}
    except Exception as e:
        logger.error("Error in cleanup_old_exports task", extra={'task_name': 'cleanup_old_exports'}, exc_info=True)
        print(f"[cleanup_old_exports] error {e}")
        return {'error': str(e)}


@shared_task(name="export_association_data")
def export_association_data(sport_association_id: str, user_id: str, include_files: bool = True):
    """
    Background task to export all association data to a ZIP file.

    Creates a SportAssociationDocumentsArchive entry when complete and
    sends an email notification to the user.

    Args:
        sport_association_id: UUID string of the association to export
        user_id: UUID string of the user who requested the export
        include_files: Ignored stale option; binary media is always included
    """
    import uuid
    from application.services.export_service import AssociationExportService

    logger.info(
        "Starting export_association_data task",
        extra={
            'task_name': 'export_association_data',
            'sport_association_id': sport_association_id
        }
    )

    try:
        # Get the user for notification
        user = User.objects.get(user_id=uuid.UUID(user_id))
        association = SportAssociation.original_objects.get(
            sport_association_id=uuid.UUID(sport_association_id)
        )

        # Clean up old exports before creating a new one
        # This enforces: max 30 days retention, max 3 exports per association
        cleanup_deleted = cleanup_old_exports(
            sport_association_id,
            max_count=EXPORT_MAX_COUNT - 1,
        )
        if cleanup_deleted > 0:
            logger.info(
                "Cleaned up old exports before new export",
                extra={
                    'sport_association_id': sport_association_id,
                    'deleted_count': cleanup_deleted
                }
            )

        # Create and run export service
        service = AssociationExportService(uuid.UUID(sport_association_id))
        document = service.export()

        # Send notification email
        subject = f"Export completato - {association.denomination}"
        message = f"""
Ciao {user.first_name or 'Utente'},

L'export dei dati della tua associazione "{association.denomination}" è stato completato con successo.

Puoi scaricare il file dalla sezione "Archivio Documenti" del tuo pannello di controllo.

Nome file: {document.filename}

Statistiche export:
"""
        for model_name, count in service.stats.items():
            message += f"  - {model_name}: {count}\n"

        if service.errors:
            message += "\nAvvisi:\n"
            for error in service.errors:
                message += f"  - {error}\n"

        message += f"""
Grazie,
Il team {WHITELABEL_NAME}
"""

        send_mail_async.delay(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            sport_association_id=str(sport_association_id),
        )

        logger.info(
            "Completed export_association_data task",
            extra={
                'task_name': 'export_association_data',
                'sport_association_id': sport_association_id,
                'document_id': str(document.document_id)
            }
        )
        print(f"[export_association_data] Export completed: {document.filename}")

        return {
            'success': True,
            'document_id': str(document.document_id),
            'filename': document.filename,
            'stats': service.stats,
        }

    except Exception as e:
        logger.error(
            "Error in export_association_data task",
            extra={'task_name': 'export_association_data'},
            exc_info=True
        )
        print(f"[export_association_data] error {e}")

        # Try to notify user of failure
        try:
            user = User.objects.get(user_id=uuid.UUID(user_id))
            send_mail_async.delay(
                subject="Errore export dati",
                message="Si è verificato un errore durante l'export dei dati. Riprova più tardi o contatta il supporto.",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                sport_association_id=sport_association_id,
            )
        except Exception:
            pass

        return {
            'success': False,
            'error': str(e),
        }
    finally:
        cache.delete(export_active_cache_key(sport_association_id))


@shared_task(name="import_association_data")
def import_association_data(
    zip_file_path: str,
    owner_email: str = '',
    owner_password: str = '',
    preserve_uuids: bool = True,
    skip_files: bool = False,
):
    """
    Background task to import association data from a ZIP export file.

    Args:
        zip_file_path: Path to the uploaded ZIP file in storage
        owner_email: Ignored stale option; archived owner email is retained
        owner_password: Recovery password when the archive has no supported owner password hash
        preserve_uuids: Ignored stale option; source UUIDs are always preserved
        skip_files: Ignored stale option; archive media is always imported
    """
    import os
    import tempfile
    from application.services.import_service import (
        AssociationImportService,
        ImportOptions,
    )

    logger.info(
        "Starting import_association_data task",
        extra={
            'task_name': 'import_association_data',
            'stale_owner_email': owner_email,
        }
    )

    temp_file = None
    try:
        # Download file from storage to temp location
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
        with default_storage.open(zip_file_path, 'rb') as src:
            temp_file.write(src.read())
        temp_file.close()

        # Create import options
        options = ImportOptions(
            owner_email=owner_email,
            owner_password=owner_password,
            dry_run=False,
        )

        # Create service and run import
        service = AssociationImportService(temp_file.name, options)

        # Validate first
        validation = service.validate()
        if not validation.is_valid:
            logger.error(
                "Import validation failed",
                extra={
                    'task_name': 'import_association_data',
                    'errors': validation.errors,
                }
            )
            return {
                'success': False,
                'error': f"Validazione fallita: {validation.errors}",
            }

        # Run the import
        association = service.import_all()

        logger.info(
            "Completed import_association_data task",
            extra={
                'task_name': 'import_association_data',
                'sport_association_id': str(association.sport_association_id),
                'denomination': association.denomination,
            }
        )

        return {
            'success': True,
            'sport_association_id': str(association.sport_association_id),
            'denomination': association.denomination,
            'owner_email': association.user.email,
            'stats': service.stats,
            'warnings': service.errors,
        }

    except Exception as e:
        logger.error(
            "Error in import_association_data task",
            extra={'task_name': 'import_association_data'},
            exc_info=True
        )

        return {
            'success': False,
            'error': str(e),
        }

    finally:
        try:
            default_storage.delete(zip_file_path)
        except Exception:
            pass
        # Clean up temp file
        if temp_file and os.path.exists(temp_file.name):
            os.remove(temp_file.name)
