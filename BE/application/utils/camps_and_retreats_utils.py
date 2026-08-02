import locale

from django.db.models import Q

from application.models import CampsAndRetreatsPeriodsService, Payment, CampsAndRetreatsSubscriptionPeriod, \
    CampsAndRetreatsPeriod
import logging

from application.models.payment_models import PaymentCategory

logger = logging.getLogger(__name__)
locale.setlocale(locale.LC_ALL, 'it_IT.UTF-8')

def remove_all_periods_not_in_list(periods, subscription):
    """
    Remove all periods that are not in the list of periods
    """
    periods_subscription = CampsAndRetreatsSubscriptionPeriod.objects.filter(
        camps_and_retreats_subscription=subscription
    ).filter(
        Q(payment__paid=False) | Q(payment__isnull=True)
    )

    new_periods = [period['camps_and_retreats_period'] for period in periods]

    for period_subscription in periods_subscription:
        if str(period_subscription.camps_and_retreats_period.camps_and_retreats_period_id) not in new_periods:
            if period_subscription.payment is not None:
                period_subscription.payment.delete()
                period_subscription.payment = None
            period_subscription.delete()


def period_in_list(period, period_subscription):
    return period['camps_and_retreats_period'] == str(period_subscription.camps_and_retreats_period.camps_and_retreats_period_id)


def update_services(period, period_subscription):
    period_services = [
        service['camps_and_retreats_period_service_id']
        for service in period['services']
    ]
    current_services = [
        service.camps_and_retreats_period_service_id
        for service in period_subscription.camps_and_retreats_period_services.all()
    ]
    if set(period_services) != set(current_services):
        period_subscription.camps_and_retreats_period_services.clear()
        for service in period['services']:
            period_subscription.camps_and_retreats_period_services.add(
                CampsAndRetreatsPeriodsService.objects.get(
                    camps_and_retreats_period_service_id=service['camps_and_retreats_period_service_id']
                )
            )
        period_subscription.save()
        return True
    return False


def get_meta_payment_categories(period):
    return [
        {
            'payment_category_id': str(service.payment_category.payment_category_id),
            'amount': float(service.fee),
            'subject': 0,
            'title': service.title,
            'camps_and_retreats_period_service_id': str(service.camps_and_retreats_period_service_id),
        }
        for service in period.camps_and_retreats_period_services.all()
    ]


def create_period_notes(period, meta_payment_categories, subscription):
    notes = (f"Iscrizione al periodo {period.camps_and_retreats_period.title} "
             f"del campo {subscription.camps_and_retreats.title}")

    if description := period.camps_and_retreats_period.description:
        notes += (f"\nQuota di iscrizione: "
                  f"{locale.format_string('%.2f', period.camps_and_retreats_period.fee, grouping=True)}€\n")

    for category in meta_payment_categories:
        amount = locale.format_string('%.2f', float(category['amount']), grouping=True)
        notes += f"\n{category['title']}: {amount}€"

    return notes


def calculate_period_amount(period, meta_payment_categories):
    amount = sum(category['amount'] for category in meta_payment_categories)
    amount += float(period.camps_and_retreats_period.fee)
    return amount


def update_or_create_payment(period, period_subscription, meta_payment_categories, notes, amount):
    # update payment based on the payment category
    payment = Payment.objects.get_or_create(payment_id=period_subscription.payment.payment_id)
    payment.amount = amount
    payment.meta_payment_categories = meta_payment_categories
    payment.notes = notes
    payment.save()


def create_payment(**kwargs):
    """
    Create a payment object
    """
    amount = kwargs.get('amount')
    subscription = kwargs.get('subscription')
    notes = kwargs.get('notes')
    meta_payment_categories = kwargs.get('meta_payment_categories')

    payment = Payment.objects.create(
        expense=False,
        paid=False,
        amount=amount,
        subject=Payment.OTHER,
        type=Payment.DEFAULT,
        payment_category=PaymentCategory.objects.filter(
            name__iexact='entrate e proventi da attività tipiche').first(),
        meta_payment_categories=meta_payment_categories,
        notes=notes,
        sport_association=subscription.camps_and_retreats.sport_association,
        user=subscription.camps_and_retreats.sport_association.user,
        associate=subscription.subscription.associate,
    )

    return payment


def generate_payment_for_periods(subscription):
    periods = CampsAndRetreatsSubscriptionPeriod.objects.filter(
        camps_and_retreats_subscription=subscription
    ).filter(
        Q(payment__paid=False) | Q(payment__isnull=True)
    ).select_related('camps_and_retreats_period')

    if not periods:
        return
    meta_payment_categories = []
    notes = ""
    amount = 0
    for period in periods:
        period_categories = get_meta_payment_categories(period)
        meta_payment_categories.extend(period_categories)
        notes += create_period_notes(period, period_categories, subscription) + "\n\n"
        amount += calculate_period_amount(period, period_categories)

    payment = create_payment(amount=amount, subscription=subscription, notes=notes, meta_payment_categories=meta_payment_categories)

    # update the payment for each period
    for period in periods:
        period.payment = payment
        period.save()


def patch_existing_periods_and_delete_periods(periods, subscription):
    '''
    patch existing periods and payments if they are not paid and changed
    peridos: new list
    periods_subscription: old list
    subscription: the subscription
    '''

    periods_subscription = CampsAndRetreatsSubscriptionPeriod.objects.filter(
        camps_and_retreats_subscription=subscription
    ).filter(
        Q(payment__paid=False) | Q(payment__isnull=True)
    )

    # periods payment to update
    for period_subscription in periods_subscription:
        if period_subscription.payment is not None:
            period_subscription.payment.delete()
        # check if the period is in the new list and update it if it is changed
        for period in periods:
            if period_in_list(period, period_subscription):
                # update the period services if they are changed
                if not update_services(period, period_subscription):
                    logger.info("FYI: services not updated")

                # if update_services(period, period_subscription):
                #     # update the payment if it is changed
                #     meta_payment_categories = get_meta_payment_categories(period)
                #
                #     # create the notes for the payment
                #     notes += create_period_notes(period, meta_payment_categories, subscription) + "\n\n"
                #     # amount = calculate_period_amount(period, meta_payment_categories)
                #
                #     # update_or_create_payment(period, period_subscription, meta_payment_categories, notes, amount)
                # # break # TODO: WHY DID I ADD A BREAK HERE?


def delete_unpaid_periods(subscription):
    periods_subscription = CampsAndRetreatsSubscriptionPeriod.objects.filter(
        camps_and_retreats_subscription=subscription
    ).filter(
        Q(payment__paid=False) | Q(payment__isnull=True)
    )

    for period_subscription in periods_subscription:
        try:
            period_subscription.payment.delete()
        except AttributeError:
            pass
        except Exception as e:
            logger.error(f"Error deleting payment: {e}")

        period_subscription.delete()


def add_services_to_period(period, period_instance):
    if 'services' not in period:
        return

    service_ids = [service['camps_and_retreats_period_service_id'] for service in period['services']]

    existing_services = CampsAndRetreatsPeriodsService.objects.filter(
        camps_and_retreats_period_service_id__in=service_ids
    )

    period_instance.camps_and_retreats_period_services.add(*existing_services)
    period_instance.save()


def create_period_instance(period, subscription):
    return CampsAndRetreatsSubscriptionPeriod.objects.create(
        camps_and_retreats_period=CampsAndRetreatsPeriod.objects.get(
            camps_and_retreats_period_id=period['camps_and_retreats_period']
        ),
        camps_and_retreats_subscription=subscription,
        payment=None,
    )


def add_new_periods(periods, subscription):
    # Fetch unpaid subscription periods
    paid_periods = CampsAndRetreatsSubscriptionPeriod.objects.filter(
        camps_and_retreats_subscription=subscription,
        payment__paid=True
    )

    # Extract period IDs as strings
    existing_periods = [
        str(period.camps_and_retreats_period.camps_and_retreats_period_id)
        for period in paid_periods
    ]

    for period in periods:
        if period['camps_and_retreats_period'] in existing_periods:
            continue

        period_instance = create_period_instance(period, subscription)
        # add services to the period
        add_services_to_period(period, period_instance)
