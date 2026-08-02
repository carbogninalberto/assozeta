# Description: This file contains the utility functions for generating balance sheet.
import logging

from django.db.models import Q

from application.models import Payment
from application.models.payment_models import PaymentCategory

logger = logging.getLogger(__name__)


def find_category_index(category_list, category_id):
    """Find the index of a category in a list by its ID."""
    for i, item in enumerate(category_list):
        try:
            if item['id'] == str(category_id):
                return i
        except KeyError:
            pass
    return -1


def reset_balance_sheet_to_zero(default_bs, payments):
    logger.debug("Resetting balance sheet to zero", extra={'payments_count': len(list(payments))})
    for payment in payments:
        key = "generalExpenses" if payment.expense else "generalIncome"
        direction_key = "outgoing" if payment.expense else "incoming"

        # find the index of the category in the balance sheet
        category_id = payment.payment_category.payment_category_id if payment.payment_category else "senza_causale"

        # check if it is commercial
        is_commercial = False
        if payment.payment_category and payment.payment_category:
            is_commercial = payment.payment_category.type == PaymentCategory.COMMERCIAL

        # check if the category is commercial or institutional
        type_key = 'commercial' if is_commercial else 'institutional'

        # find the index of the category in the balance sheet
        category_index = find_category_index(default_bs[direction_key][key], category_id)

        if category_index == -1:
            continue
        else:
            default_bs[direction_key][key][category_index][type_key] = 0

        # check if payment has meta categories
        if payment.meta_payment_categories is not None and \
                len(payment.meta_payment_categories) > 0:
            for meta in payment.meta_payment_categories:
                menta_category_id = 'senza_causale'
                if 'payment_category_id' in meta:
                    menta_category_id = meta['payment_category_id']
                elif 'payment_category' in meta:
                    menta_category_id = meta['payment_category']

                category = PaymentCategory.objects.filter(payment_category_id=menta_category_id).first()
                amount = 0
                if category is None:
                    menta_category_id = 'senza_causale'
                else:
                    if 'amount' in meta:
                        amount = float(meta['amount'])
                    elif 'fee' in meta:
                        amount = float(meta['fee'])

                if amount == 0:
                    # avoid adding empty payments
                    continue

                meta_is_commercial = False
                if category:
                    meta_is_commercial = category.type == PaymentCategory.COMMERCIAL

                category_index = find_category_index(default_bs[direction_key][key], menta_category_id)
                if category_index == -1:
                    continue
                else:
                    # check if the category is commercial or institutional
                    meta_type_key = 'commercial' if meta_is_commercial else 'institutional'
                    default_bs[direction_key][key][category_index][meta_type_key] = 0

    logger.debug("Balance sheet reset to zero completed")
    return default_bs


def generate_balance_sheet(sport_association, date_from, date_to, default_bs) -> dict:
    logger.info("Generating balance sheet", extra={
        'sport_association_id': str(sport_association.sport_association_id),
        'date_from': str(date_from),
        'date_to': str(date_to)
    })

    date_to = date_to.replace(hour=23, minute=59, second=59)
    # truncate tz from date_to and date_from
    date_from = date_from.replace(tzinfo=None)
    date_to = date_to.replace(tzinfo=None)
    # get all the payments within the date range that are paid
    payments = Payment.objects.filter(
        sport_association=sport_association,
        paid=True,
        amount__gt=0,
        deleted=False,
        archived=False
    ).filter(
        (Q(payment_date__range=(date_from, date_to)) & Q(payment_date__isnull=False)) |
        (Q(creation_date__range=(date_from, date_to)) & Q(payment_date__isnull=True))
    ).select_related('payment_category')

    payments_count = payments.count()
    logger.debug("Found payments for balance sheet", extra={
        'sport_association_id': str(sport_association.sport_association_id),
        'payments_count': payments_count
    })

    default_bs = reset_balance_sheet_to_zero(default_bs, payments)

    # iterate through the payments and update the balance sheet
    for payment in payments:
        key = "generalExpenses" if payment.expense else "generalIncome"
        direction_key = "outgoing" if payment.expense else "incoming"

        # find the index of the category in the balance sheet
        category_id = payment.payment_category.payment_category_id if payment.payment_category else "senza_causale"
        category_index = find_category_index(default_bs[direction_key][key], category_id)

        # check if it is commercial
        is_commercial = False
        if payment.payment_category and payment.payment_category:
            is_commercial = payment.payment_category.type == PaymentCategory.COMMERCIAL

        # check if the category is commercial or institutional
        type_key = 'commercial' if is_commercial else 'institutional'

        # if the category is not found, add it to the balance sheet
        if category_index == -1:
            to_add = {
                "description": payment.payment_category.name if payment.payment_category else "Senza causale",
                "institutional": float(payment.amount) if not is_commercial else 0,
                "commercial": float(payment.amount) if is_commercial else 0,
                "id": str(payment.payment_category.payment_category_id) if payment.payment_category else "senza_causale",
                "editable": False,
                "default": True,
            }
            default_bs[direction_key][key].append(to_add)
            category_index = len(default_bs[direction_key][key]) - 1
        else:
            default_bs[direction_key][key][category_index][type_key] += float(payment.amount)


        # check if payment has meta categories
        if payment.meta_payment_categories is not None and \
                len(payment.meta_payment_categories) > 0:
            for meta in payment.meta_payment_categories:
                menta_category_id = 'senza_causale'
                if 'payment_category_id' in meta:
                    menta_category_id = meta['payment_category_id']
                elif 'payment_category' in meta:
                    menta_category_id = meta['payment_category']

                category = PaymentCategory.objects.filter(payment_category_id=menta_category_id).first()
                amount = 0
                if category is None:
                    menta_category_id = 'senza_causale'
                else:
                    if 'amount' in meta:
                        amount = float(meta['amount'])
                    elif 'fee' in meta:
                        amount = float(meta['fee'])

                if amount == 0:
                    # avoid adding empty payments
                    continue

                meta_is_commercial = False
                if category:
                    meta_is_commercial = category.type == PaymentCategory.COMMERCIAL

                meta_category_index = find_category_index(default_bs[direction_key][key], menta_category_id)
                if meta_category_index == -1:
                    to_add = {
                        "description": category.name if category else "Senza causale",
                        "institutional": amount if not meta_is_commercial else 0,
                        "commercial": amount if meta_is_commercial else 0,
                        "id": str(category.payment_category_id) if category else "senza_causale",
                        "editable": False,
                        "default": True,
                    }
                    default_bs[direction_key][key].append(to_add)
                else:
                    # check if the category is commercial or institutional
                    meta_type_key = 'commercial' if meta_is_commercial else 'institutional'
                    default_bs[direction_key][key][meta_category_index][meta_type_key] += amount

                # remove the amount from the general category
                default_bs[direction_key][key][category_index][type_key] -= amount

    logger.info("Balance sheet generated successfully", extra={
        'sport_association_id': str(sport_association.sport_association_id),
        'payments_processed': payments_count
    })
    return default_bs
