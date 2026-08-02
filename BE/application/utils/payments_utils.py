from datetime import datetime
import logging

import pytz
from django.db.models import Q

from application.models import SportAssociation, Payment, CourseSubscription, CourseSubscriptionInstallment
from application.models.carnet_models import CarnetSubscription
from application.models.subscriptions_models import Subscription, SubscriptionMembership
from application.utils.api_utils import BalanceSheetData
from core import settings

logger = logging.getLogger(__name__)


class PaymentUtils:
    CASH = 'cash'
    TRANSFER = 'transfer'
    ONLINE = 'online'
    SEPA_TRANSFER = 'sepa-transfer'

    PAYMENT_INVOICE_DESCRIPTION = {
        0: "Ricevuta di pagamento con causale <b>{}</b> per l'associazione <b>{}</b>.",
        1: "Ricevuta di pagamento per l'iscrizione {}di <b>{}</b> all'associazione <b>{}</b>.",
        2: "Ricevuta di pagamento di <b>{}</b> per il corso <b>{}</b> dell'associazione <b>{}</b>.",
    }


def generate_invoice_description(payment: Payment, sport_association: SportAssociation):
    logger.debug("Generating invoice description", extra={
        'payment_id': str(payment.payment_id),
        'subject': payment.subject,
        'sport_association_id': str(sport_association.sport_association_id)
    })
    description = PaymentUtils.PAYMENT_INVOICE_DESCRIPTION[payment.subject]
    if payment.subject is Payment.SUBSCRIPTION:
        # subscription name
        sub_name = ""
        period_and_sub_label = ""
        sub = Subscription.objects.filter(payment=payment).first()
        if sub:
            period_and_sub_label = f"{sub.get_period()} "

        if payment.meta is not None and payment.meta["subscription_data"]:
            sub_name = f"{period_and_sub_label}({payment.meta['subscription_data']['name']}) "

        sub_memebership = SubscriptionMembership.objects.filter(subscription=sub).first()
        if sub_memebership:
            sub_name += f" e tesseramento, {sub_memebership.get_period()} ({sub_memebership.membership_type}) "

        description = description.format(sub_name, payment.associate.get_full_name(),
                                         sport_association.denomination)
    elif payment.subject is Payment.OTHER:
        description = description.format(payment.payment_category.name,
                                         sport_association.denomination)
    else:
        course_payment = CourseSubscription.objects.all_objects().filter(payment=payment).first()
        if course_payment is None:
            course_payment = CourseSubscriptionInstallment.objects.all_objects().filter(payment=payment).first()
            if course_payment:
                description = description.format(payment.associate.get_full_name(),
                                                 course_payment.course_subscription.course.title,
                                                 sport_association.denomination)
            else:
                course_payment = CarnetSubscription.objects.filter(payment=payment).first()
                if course_payment:
                    description = description.format(payment.associate.get_full_name(),
                                                     course_payment.carnet_id.title,
                                                     sport_association.denomination)
                else:
                    description = payment.description
        else:
            description = description.format(payment.associate.get_full_name(),
                                             course_payment.course.title,
                                             sport_association.denomination)
    logger.info("Invoice description generated", extra={
        'payment_id': str(payment.payment_id),
        'description_length': len(description)
    })
    return description


def calculate_simulation(date=None, sport_association=None):
    logger.info("Starting payment simulation calculation", extra={
        'date': date,
        'sport_association_id': str(sport_association) if sport_association else None
    })
    subscription_plans_considered = []
    quotes_to_emit = []
    try:
        # get today datetime in UTC
        if date is None:
            current_ts = datetime.now().astimezone(pytz.utc)
        else:
            # date is a string in the format YYYY-MM-DD
            current_ts = datetime.strptime(date, "%Y-%m-%d")

        # convert to date
        current_date = current_ts.date()

        if sport_association is None:
            logger.debug("Processing all sport associations with subscription fee plans")
            # get all the sport associations that have subscription fee plans
            sport_associations = SportAssociation.objects.filter(
                ~Q(subscription_fee_plans__isnull=True) &
                ~Q(subscription_fee_plans=[])
            ).filter(
                multiple_subscription_fee=True,
                subscription_fee_plans__icontains='"advanced_options": true'
            ).iterator(chunk_size=50)
        else:
            logger.debug("Processing specific sport association", extra={
                'sport_association_id': str(sport_association)
            })
            sport_associations = SportAssociation.objects.filter(
                sport_association_id=sport_association
            )

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

            logger.debug("Found active subscription fee plans", extra={
                'sport_association_id': str(sport_association.sport_association_id),
                'active_plans_count': len(active_subscription_fee_plans)
            })

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
            ).exclude(meta__exact='{"plan_id": null}').select_related('payment', 'associate')

            subscriptions_count = subscriptions.count()
            logger.info("Found subscriptions for quote assignment", extra={
                'sport_association_id': str(sport_association.sport_association_id),
                'subscriptions_count': subscriptions_count
            })

            subscription_plans_considered = [plan for plan in active_subscription_fee_plans]

            for plan in active_subscription_fee_plans:
                # for each plan we should extract the quote of the last time and see if it is paid
                if plan['previous_subscription_fee_plan'] == '':
                    continue

                logger.debug("Processing subscription fee plan", extra={
                    'sport_association_id': str(sport_association.sport_association_id),
                    'plan_name': plan['name'],
                    'plan_id': plan['id']
                })

                # get who had the previous payment
                previous_subs_to_check = subscriptions.filter(
                    payment__meta__icontains=plan['previous_subscription_fee_plan']
                )

                previous_subs_count = previous_subs_to_check.count()
                logger.info("Found subscriptions with previous plan", extra={
                    'sport_association_id': str(sport_association.sport_association_id),
                    'plan_id': plan['id'],
                    'previous_subscriptions_count': previous_subs_count
                })

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
                        logger.debug("Payment already exists, skipping", extra={
                            'associate_id': str(subscription.associate.associate_id),
                            'plan_id': plan['id']
                        })
                        continue


                    payment_meta = {
                        "subscription_data": {
                            "subscription_fee": plan['subscription_fee'],
                            "name": plan['name'],
                            "id": plan['id']
                        }
                    }

                    payment = {
                        "user": subscription.user.get_full_name(),
                        "associate": {
                            "first_name": subscription.associate.first_name,
                            "last_name": subscription.associate.last_name
                        },
                        "amount": plan['subscription_fee'],
                        "subject": Payment.SUBSCRIPTION,
                        "meta": payment_meta,
                        "message": None
                    }

                    logger.info("Creating new quote payment", extra={
                        'sport_association_id': str(sport_association.sport_association_id),
                        'associate_id': str(subscription.associate.associate_id),
                        'plan_id': plan['id'],
                        'amount': plan['subscription_fee']
                    })

                    if subscription.user != sport_association.user:
                        logger.debug("Sending quote notification email", extra={
                            'user_email': subscription.user.email,
                            'associate_id': str(subscription.associate.associate_id)
                        })

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
                            payment['message'] = message

                    quotes_to_emit.append(payment)

    except Exception as e:
        logger.error("Error during payment simulation calculation", extra={
            'error': str(e),
            'date': date,
            'sport_association_id': str(sport_association) if sport_association else None
        }, exc_info=True)

    logger.info("Payment simulation calculation completed", extra={
        'plans_considered': len(subscription_plans_considered),
        'quotes_to_emit': len(quotes_to_emit)
    })
    return subscription_plans_considered, quotes_to_emit
