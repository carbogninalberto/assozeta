"""
Invoice Service
Handles invoice-related business logic
"""
import hashlib
from datetime import datetime

from django.db import connection

from application.models.invoices_models import Invoice
from application.utils.api_utils import BalanceSheetData


class InvoiceService:
    """Service class for invoice-related operations"""

    @staticmethod
    def _acquire_invoice_lock(sport_association_id):
        """Acquire a PostgreSQL advisory lock scoped to the current transaction.

        This serializes all concurrent invoice number generation for the same
        sport association, preventing duplicate progressive numbers during
        bulk payment approval.
        """
        # pg_advisory_xact_lock takes a bigint; derive a stable key from the UUID
        lock_key = int(hashlib.md5(str(sport_association_id).encode()).hexdigest()[:15], 16)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_key])

    @staticmethod
    def get_next_invoice_number(sport_association, reference_date, user, use_payment_date=False):
        """
        Calculate the next invoice number based on the balance sheet date range and user settings.

        Args:
            sport_association: SportAssociation instance
            reference_date: datetime - The date to use for determining the fiscal year range
            user: User instance with balance sheet settings
            use_payment_date: bool - If True, use payment_date field; if False, use creation_date

        Returns:
            int: The next invoice number to use
        """
        # Serialize concurrent callers for this sport association (requires transaction.atomic)
        InvoiceService._acquire_invoice_lock(sport_association.sport_association_id)

        # Get the fiscal year date range based on user settings
        date_from, _ = BalanceSheetData.get_range_from_year_and_starting_date(
            date=datetime.now(),
            starting_day=user.balance_sheet_start_day,
            starting_month=user.balance_sheet_start_month
        )

        # Query for the last invoice based on the reference date
        if reference_date < date_from:
            # For dates before the current fiscal year, look at old invoices
            if use_payment_date:
                last_invoice_queryset = Invoice.objects.filter(
                    sport_association=sport_association,
                    archived=False,
                    payment__payment_date__lt=date_from,
                    cancelled=False,
                    deleted=False
                )
            else:
                last_invoice_queryset = Invoice.objects.filter(
                    sport_association=sport_association,
                    archived=False,
                    creation_date__lt=date_from
                )
        else:
            # For dates in the current fiscal year
            if use_payment_date:
                last_invoice_queryset = Invoice.objects.filter(
                    sport_association=sport_association,
                    archived=False,
                    payment__payment_date__gte=date_from,
                    cancelled=False,
                    deleted=False
                )
            else:
                last_invoice_queryset = Invoice.objects.filter(
                    sport_association=sport_association,
                    archived=False,
                    payment__creation_date__gte=date_from
                )

        # Extract the latest invoice number
        try:
            last_invoice = last_invoice_queryset.order_by('-number').first()
            latest_invoice_number = int(last_invoice.number if last_invoice else 0)
        except (TypeError, ValueError):
            latest_invoice_number = 0

        # Get the user's starting invoice number with error handling
        try:
            user_start_invoice_number = int(user.starting_number_invoices)
        except (TypeError, ValueError):
            user_start_invoice_number = 0

        # The new invoice number is the maximum of the latest invoice and user's starting number, plus 1
        max_invoice_number = max(latest_invoice_number, user_start_invoice_number)
        return max_invoice_number + 1
