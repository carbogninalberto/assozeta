"""
@ copyright: Bakney SRL
"""
import logging
import os
import posixpath
import re
import secrets
import string
from datetime import datetime, date
from io import BytesIO

from dateutil import parser

from dateutil.relativedelta import relativedelta
from django.core.exceptions import FieldDoesNotExist
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone
from django.utils.timezone import make_aware
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from application.models import CourseSubscription, CourseSubscriptionInstallment, Course
from application.exceptions import DuplicateSubscriptionError
from application.models.payment_models import Payment, PaymentCategory
from application.models.subscriptions_models import Signature, MedicalCertificate, SubscriptionMembership
from application.models.subscriptions_models import Subscription
from application.models.user_models import SportAssociation, User
from application.serializers.auth_serializers import UserSerializer, UserAccountSerializer
from application.serializers.courses_serializers import CourseSubscriptionSerializer, CourseEventsSerializer
from application.serializers.subscriptions_serializers import SignatureRequestSerializer, \
    SubscriptionSerializerAthleteOptimizedList
from application.serializers.user_serializers import AssociateSerializer
from application.tasks import send_email_template, print_document_subscription
from application.utils.api_utils import BalanceSheetData, check_email, generate_image_with_text
from application.utils.notification_utils import NotificationUtils
from application.views.auth_views import AuthUtils
from core import settings
from core.settings import STORAGE_DIR
from docmanager.models import Document
from notifications.services import NotificationService
from django.contrib.postgres.search import SearchVector, SearchQuery, SearchRank, TrigramSimilarity
from django.db.models import F, Value as V, CharField, Q, Case, When, FloatField, BigIntegerField
from django.db.models.functions import Concat, Cast

logger = logging.getLogger(__name__)


def cleanup_storage_keys(storage_keys):
    """Delete storage objects created by a failed subscription operation."""
    for storage_key in storage_keys:
        if not storage_key:
            continue
        try:
            default_storage.delete(storage_key)
        except Exception:
            logger.warning(
                "Failed to clean up storage object after subscription rollback",
                exc_info=True
            )


def _on_commit_robust(callback):
    transaction.on_commit(callback, robust=True)


def smart_search(queryset, search_term):
    """
    Intelligently chooses the appropriate search implementation based on the database backend.
    Falls back to SQLite-compatible search if PostgreSQL is not available.
    """
    from django.db import connection

    # Check if we're using PostgreSQL
    is_postgres = connection.vendor == 'postgresql'

    # if is_postgres:
    #     try:
    #         return advanced_postgres_search(queryset, search_term)
    #     except Exception as e:
    #         logger.error(f"PostgreSQL search failed: {e}")
    #         # Fallback to SQLite search if PostgreSQL search fails
    #         return sqlite_search(queryset, search_term)
    # else:
    return sqlite_search(queryset, search_term)


def sqlite_search(queryset, search_term, max_depth=4):
    """
    Improved search implementation with controlled field discovery.
    Only searches through explicitly defined relationships to avoid performance issues.

    Args:
        queryset: The initial queryset to search
        search_term: The search term entered by the user
        max_depth: Maximum depth for traversing related models (default: 2)
    """
    if not search_term:
        return queryset

    model = queryset.model
    search_term = search_term.strip().lower()
    search_words = re.findall(r'\w+', search_term)

    # Get searchable fields with controlled traversal
    fields_to_search = get_controlled_searchable_fields(model, max_depth=max_depth)

    # Create date variations for flexible date searching
    date_variations = get_date_variations(search_term)

    logger.info(f"Fields_to_search: {fields_to_search}")
    logger.info(f"Date_variations: {date_variations}")

    # Build optimized query
    query = Q()

    # Handle each word separately - each word must match at least one field
    for word in search_words:
        word_query = Q()

        # Try to match against each field
        for field in fields_to_search:
            word_query |= Q(**{f"{field}__icontains": word})

        # Add date variations if the word looks like a date component
        if re.match(r'^\d+$', word) or re.match(r'^\d{1,2}[./-]\d{1,2}[./-]\d{2,4}$', word):
            for date_variation in date_variations:
                for field in fields_to_search:
                    if 'date' in field.lower() or 'created' in field.lower():
                        word_query |= Q(**{f"{field}__icontains": date_variation})

        query &= word_query

    # Handle multi-word permutations (limited to avoid performance issues)
    if len(search_words) > 1:
        perm_query = Q()

        # Only try key permutations, not all
        key_permutations = [
            " ".join(reversed(search_words)),  # Reverse order
            " ".join(search_words)  # Original order
        ]

        for perm_term in key_permutations:
            for field in fields_to_search:
                # Focus on text fields for permutations
                if any(text_field in field.lower() for text_field in ['name', 'address', 'city', 'notes']):
                    perm_query |= Q(**{f"{field}__icontains": perm_term})

        query = query | perm_query

    # Apply the query
    queryset = queryset.filter(query)

    # Add relevance scoring with performance optimization
    queryset = add_relevance_scoring(queryset, search_term, fields_to_search)

    return queryset.distinct()

def get_controlled_searchable_fields(model, max_depth=4):
    """
    Get searchable fields with controlled traversal based on model-specific rules.
    This prevents expensive recursive searches through all possible relationships.
    """
    model_name = model.__name__
    fields = []

    # Always get direct fields first
    for field in model._meta.get_fields():
        if not field.is_relation and not field.many_to_many:
            fields.append(field.name)

    # Add specific relationship fields based on model
    if model_name == 'Subscription':
        # Add associate fields if the relationship exists
        try:
            associate_field = model._meta.get_field('associate')
            if hasattr(associate_field, 'related_model') and associate_field.related_model:
                associate_model = associate_field.related_model

                # Add validated associate fields
                associate_fields = ['first_name', 'last_name', 'tax_code', 'email', 'phone',
                                    'phone_2', 'phone_3', 'phone_4', 'born_city', 'born_date', 'address_city',
                                    'address', 'notes', 'nationality', 'id_number']

                for field_name in associate_fields:
                    try:
                        associate_model._meta.get_field(field_name)
                        fields.append(f"associate__{field_name}")
                    except FieldDoesNotExist:
                        pass  # Field doesn't exist, skip it

                # Add tutor fields if we're not at max depth
                if max_depth > 1:
                    try:
                        tutors_field = associate_model._meta.get_field('tutors')
                        if hasattr(tutors_field, 'related_model'):
                            tutor_fields = ['first_name', 'last_name', 'tax_code', 'email', 'phone',
                                    'phone_2', 'phone_3', 'phone_4', 'born_city', 'born_date', 'address_city',
                                    'address', 'notes', 'nationality', 'id_number']
                            for field_name in tutor_fields:
                                try:
                                    associate_model._meta.get_field(field_name)  # tutors are also Associates
                                    fields.append(f"associate__tutors__{field_name}")
                                except FieldDoesNotExist:
                                    pass
                    except FieldDoesNotExist:
                        pass
        except FieldDoesNotExist:
            pass  # associate relationship doesn't exist

    elif model_name == 'Payment':
        # Add associate fields if the relationship exists
        try:
            associate_field = model._meta.get_field('associate')
            if hasattr(associate_field, 'related_model') and associate_field.related_model:
                associate_model = associate_field.related_model

                # Add validated associate fields
                associate_fields = ['first_name', 'last_name', 'tax_code', 'email', 'phone',
                                    'phone_2', 'phone_3', 'phone_4', 'born_city', 'born_date', 'address_city',
                                    'address', 'notes', 'nationality', 'id_number']

                for field_name in associate_fields:
                    try:
                        associate_model._meta.get_field(field_name)
                        fields.append(f"associate__{field_name}")
                    except FieldDoesNotExist:
                        pass  # Field doesn't exist, skip it

                # Add tutor fields if we're not at max depth
                if max_depth > 1:
                    try:
                        tutors_field = associate_model._meta.get_field('tutors')
                        if hasattr(tutors_field, 'related_model'):
                            tutor_fields = ['first_name', 'last_name', 'tax_code', 'email', 'phone',
                                            'phone_2', 'phone_3', 'phone_4', 'born_city', 'born_date', 'address_city',
                                            'address', 'notes', 'nationality', 'id_number']
                            for field_name in tutor_fields:
                                try:
                                    associate_model._meta.get_field(field_name)  # tutors are also Associates
                                    fields.append(f"associate__tutors__{field_name}")
                                except FieldDoesNotExist:
                                    pass
                    except FieldDoesNotExist:
                        pass
        except FieldDoesNotExist:
            pass  # associate relationship doesn't exist

        # Add supplier fields if the relationship exists
        try:
            supplier_field = model._meta.get_field('supplier')
            if hasattr(supplier_field, 'related_model') and supplier_field.related_model:
                supplier_model = supplier_field.related_model

                # Common fields that might exist in SupplierAndCustomers model
                supplier_fields = ['name', 'company_name', 'first_name', 'last_name', 'email', 'phone',
                                   'address', 'city', 'tax_code', 'vat_number', 'notes']

                for field_name in supplier_fields:
                    try:
                        supplier_model._meta.get_field(field_name)
                        fields.append(f"supplier__{field_name}")
                    except FieldDoesNotExist:
                        pass  # Field doesn't exist, skip it
        except FieldDoesNotExist:
            pass  # supplier relationship doesn't exist

        # Add instructor fields if the relationship exists
        try:
            instructor_field = model._meta.get_field('instructor')
            if hasattr(instructor_field, 'related_model') and instructor_field.related_model:
                instructor_model = instructor_field.related_model

                # Common fields that might exist in Instructor model
                instructor_fields = ['first_name', 'last_name', 'email', 'phone', 'tax_code',
                                     'address', 'city', 'notes']

                for field_name in instructor_fields:
                    try:
                        instructor_model._meta.get_field(field_name)
                        fields.append(f"instructor__{field_name}")
                    except FieldDoesNotExist:
                        pass  # Field doesn't exist, skip it
        except FieldDoesNotExist:
            pass  # instructor relationship doesn't exist

    elif model_name == 'Invoice':
        # Add selected_tutor fields if the relationship exists
        try:
            selected_tutor_field = model._meta.get_field('selected_tutor')
            if hasattr(selected_tutor_field, 'related_model') and selected_tutor_field.related_model:
                tutor_model = selected_tutor_field.related_model

                # Add validated tutor fields (selected_tutor is an Associate)
                tutor_fields = ['first_name', 'last_name', 'tax_code', 'email', 'phone',
                                'phone_2', 'phone_3', 'phone_4', 'born_city', 'born_date', 'address_city',
                                'address', 'notes', 'nationality', 'id_number']

                for field_name in tutor_fields:
                    try:
                        tutor_model._meta.get_field(field_name)
                        fields.append(f"selected_tutor__{field_name}")
                    except FieldDoesNotExist:
                        pass  # Field doesn't exist, skip it
            fields.extend([
                'payment__associate__first_name',
                'payment__associate__last_name',
                'payment__supplier__name',
                'payment__supplier__tax_code',
                'payment__supplier__vat_number',
                'payment__instructor__first_name',
                'payment__instructor__last_name',
            ])
        except FieldDoesNotExist:
            pass  # selected_tutor relationship doesn't exist

    elif model_name == 'Associate':
        # Add tutor fields if the relationship exists
        try:
            tutors_field = model._meta.get_field('tutors')
            if hasattr(tutors_field, 'related_model'):
                tutor_fields = ['first_name', 'last_name', 'tax_code', 'email', 'phone']
                for field_name in tutor_fields:
                    try:
                        model._meta.get_field(field_name)  # tutors are also Associates
                        fields.append(f"tutors__{field_name}")
                    except FieldDoesNotExist:
                        pass
        except FieldDoesNotExist:
            pass  # tutors relationship doesn't exist

    return fields


def add_relevance_scoring(queryset, search_term, fields_to_search):
    """
    Add relevance scoring with performance optimizations.
    """
    # Define important fields for each model
    IMPORTANT_FIELDS_MAP = {
        'Subscription': ['associate__first_name', 'associate__last_name', 'associate__tax_code'],
        'Associate': ['first_name', 'last_name', 'tax_code'],
        'Payment': ['associate__first_name', 'associate__last_name', 'associate__tax_code'],
        'Invoice': ['selected_tutor__first_name', 'selected_tutor__last_name', 'selected_tutor__tax_code'],
    }

    model_name = queryset.model.__name__
    important_fields = IMPORTANT_FIELDS_MAP.get(model_name, [])

    relevance_cases = []

    # Limit scoring to most important matches to improve performance
    for field in fields_to_search[:10]:  # Limit to first 10 fields
        # Exact matches get highest score
        if field in important_fields:
            relevance_cases.append(When(**{f"{field}__iexact": search_term}, then=V(10.0)))

        # Starts with matches (medium priority)
        if field in important_fields:
            relevance_cases.append(When(**{f"{field}__istartswith": search_term}, then=V(7.0)))

        # Contains matches (lower priority) - only for important fields
        if field in important_fields:
            relevance_cases.append(When(**{f"{field}__icontains": search_term}, then=V(4.0)))

    if relevance_cases:
        queryset = queryset.annotate(
            relevance=Case(
                *relevance_cases,
                default=V(1.0),
                output_field=FloatField()
            )
        ).order_by('-relevance')

    return queryset


def get_date_variations(search_term):
    """
    Generate variations of possible date formats from a search term.
    Handles formats: dd.mm.yy, dd.mm.yyyy, dd/mm/yy, dd/mm/yyyy, dd-mm-yyyy, dd-mm-yy

    Args:
        search_term: The search term that might contain a date

    Returns:
        List of possible date format variations
    """
    # Regular expressions to match different date formats
    date_patterns = [
        r'(\d{1,2})[.](\d{1,2})[.](\d{2,4})',  # dd.mm.yy or dd.mm.yyyy
        r'(\d{1,2})[/](\d{1,2})[/](\d{2,4})',  # dd/mm/yy or dd/mm/yyyy
        r'(\d{1,2})[-](\d{1,2})[-](\d{2,4})',  # dd-mm-yy or dd-mm-yyyy
    ]

    variations = []

    # Check each pattern
    for pattern in date_patterns:
        match = re.search(pattern, search_term)
        if match:
            day, month, year = match.groups()

            # Normalize year if it's in 2-digit format
            if len(year) == 2:
                # Assume 21st century for years less than current year's last two digits
                current_year = datetime.now().year % 100
                century = "20" if int(year) <= current_year else "19"
                full_year = f"{century}{year}"
            else:
                full_year = year

            # Generate variations with different separators and formats
            variations.extend([
                f"{day}.{month}.{year}",
                f"{day}.{month}.{full_year}",
                f"{day}/{month}/{year}",
                f"{day}/{month}/{full_year}",
                f"{day}-{month}-{year}",
                f"{day}-{month}-{full_year}",
                # Also include ISO format (yyyy-mm-dd) as it's common in databases
                f"{full_year}-{month.zfill(2)}-{day.zfill(2)}",
                # Include month/day variants for flexibility
                f"{month}.{day}.{year}",
                f"{month}/{day}/{year}",
                f"{month}-{day}-{year}",
            ])

            # If we found a date, no need to check other patterns
            break

    return variations

def check_if_subscription_exists(sport_association, associate_data, date_from, date_to, data):
    custom_data = data.get('custom_data', {})
    exists = Subscription.objects.subscription_exists(
        sport_association=sport_association,
        associate_data=associate_data.initial_data,
        start_date=date_from,
        end_date=date_to,
        custom_data=custom_data
    )

    # INFO
    # Why do we need to check if the subscription is renewable?
    # The reason is that the subscription can be available for some sport association across the seasons
    # like solar year from subscription date, but it can expired before
    # take this example:
    # - season year from 01/09 to 31/08
    # - subs is solar from 10/10 to 10/10 of the next year
    # - when we renew it on 11/10 the check will return true because there is the subscription for the current year
    #   but it's expired, so we need to check if it's actually expired and if there is no subscription created later
    if exists:
        # if exists a sub within this year, check if it's actually expired or not
        sub = Subscription.objects.get_subscription_if_it_exists(
            sport_association=sport_association,
            associate_data=associate_data.initial_data,
            start_date=date_from,
            end_date=date_to,
            custom_data=custom_data
        )

        # can be renewed if is not current
        renewal_available = not sub.is_current

        if renewal_available is True:
            # if it is renewable, make sure there is no subscription created later than current
            current_year_sub = Subscription.objects.filter(
                sport_association=sub.sport_association,
                start_date__gte=sub.end_date,
                archived=False,
                associate__tax_code__iexact=sub.associate.tax_code
            ).count()
            # can be renewed if there is no subscription created later than current
            renewal_available = current_year_sub == 0
        # if renewal is not available, then the subscription already exists
        return not renewal_available
    return exists


def extract_the_subscription_plan(plan_id, sport_association: SportAssociation):
    if plan_id is None:
        return None
    if sport_association is None:
        raise ValidationError({'msg': 'Sport association not found.', "code": 400})

    for plan in sport_association.subscription_fee_plans:
        if str(plan['id']) == str(plan_id):
            return plan

    return None


def get_subscription_fee(data, sport_association: SportAssociation, subscription_type):
    if subscription_type in [Subscription.ASSOCIATE_ONLY, Subscription.ASSOCIATE_AND_MEMBER]:
        if sport_association.multiple_subscription_fee is False:
            return sport_association.subscription_fee, None
        else:
            try:
                if 'plan_id' not in data.keys():
                    raise ValidationError({'msg': 'Plan id is required.', "code": 400})
                plan = extract_the_subscription_plan(data['plan_id'], sport_association)
                if plan is None:
                    raise ValidationError({'msg': 'Plan not found.', "code": 400})

                payment_meta = {
                    "subscription_data": {
                        "subscription_fee": plan['subscription_fee'],
                        "name": plan['name'],
                        "id": plan['id']
                    }
                }
            except Exception as e:
                logger.error(f"Error in plan extraction: {e}")
                return sport_association.subscription_fee, None

        return plan['subscription_fee'], payment_meta
    # in the case it's just a member subscription

    return 0, None


def extract_the_membership_plan(membership_plan_id, sport_association: SportAssociation):
    if membership_plan_id is None:
        return None
    if sport_association is None:
        raise ValidationError({'msg': 'Sport association not found.', "code": 400})

    # Refresh the sport_association from DB to ensure we have latest membership_fee_plans
    sport_association.refresh_from_db()

    logger.debug(f"Looking for membership_plan_id: {membership_plan_id}")
    logger.debug(f"Sport association: {sport_association.denomination}")
    logger.debug(f"Available plans: {[p.get('id') for p in sport_association.membership_fee_plans]}")

    for plan in sport_association.membership_fee_plans:
        if str(plan['id']) == str(membership_plan_id):
            return plan

    return None


def get_membership_fee(data, sport_association: SportAssociation, subscription_type):

    # guard: no member fee if subscription is only associate
    if subscription_type == Subscription.ASSOCIATE_ONLY:
        return None, None

    # guard against multiple membership fees and no plan id passed
    if sport_association.multiple_membership_fee is True and 'membership_plan_id' not in data.keys():
        raise ValidationError({'msg': 'Membership plan id is required.', "code": 400})

    # guard against empty membership_plan_id
    if 'membership_plan_id' in data.keys() and data['membership_plan_id'] is None \
            and (sport_association.membership_fee is None or sport_association.membership_fee == 0):
        return None, None

    # guard against multiple membership fees and no plan found
    if sport_association.multiple_membership_fee is True and 'membership_plan_id' in data.keys():
        membership_plan = extract_the_membership_plan(data['membership_plan_id'], sport_association)
        if membership_plan is None:
            raise ValidationError({'msg': 'Membership plan not found.', "code": 400})

    # CASE 1: multiple_membership_fee is False
    if sport_association.multiple_membership_fee is False:
        if sport_association.membership_fee is None:
            return None, None
        if subscription_type == Subscription.MEMBER_ONLY:
            # Case subscription is member only
            # set the fee
            membership_fee = float(sport_association.membership_fee)
            return membership_fee, None
        elif subscription_type == Subscription.ASSOCIATE_AND_MEMBER:
            # Case subscription is associate and member
            # set the fee
            membership_fee = float(sport_association.membership_fee)
            # get categories

            if sport_association.user.default_payment_category is not None:
                membership_category = PaymentCategory.objects.filter(
                    payment_category_id=sport_association.user.default_payment_category.payment_category_id).first()
            else:
                membership_category = PaymentCategory.objects.filter(
                    name__iexact='entrate e proventi da attività tipiche').first()
            # create categories
            meta_payment_categories = [
                {
                    'payment_category_id': str(membership_category.payment_category_id),
                    'amount': float(membership_fee),
                    'subject': 0,
                    'title': 'Quota tesseramento',
                    'id': None
                }
            ]
            return membership_fee, meta_payment_categories

    # CASE 2: multiple_membership_fee is True
    if sport_association.multiple_membership_fee is True and 'membership_plan_id' in data.keys():
        if subscription_type == Subscription.ASSOCIATE_AND_MEMBER:
            # Case subscription is associate and member
            # get the plan for the meta_payment_categories
            membership_plan = extract_the_membership_plan(data['membership_plan_id'], sport_association)
            # get categories
            if sport_association.user.default_payment_category is not None:
                membership_category = PaymentCategory.objects.filter(
                    payment_category_id=sport_association.user.default_payment_category.payment_category_id).first()
            else:
                membership_category = PaymentCategory.objects.filter(
                    name__iexact='entrate e proventi da attività tipiche').first()
            # create categories
            meta_payment_categories = [
                {
                    'payment_category_id': str(membership_category.payment_category_id),
                    'amount': float(membership_plan['membership_fee']),
                    'subject': 0,
                    'title': membership_plan['name'],
                    'id': str(membership_plan['id'])
                }
            ]
            return float(membership_plan['membership_fee']), meta_payment_categories
        elif subscription_type == Subscription.MEMBER_ONLY:
            # Case subscription is member only
            # set the fee
            membership_plan = extract_the_membership_plan(data['membership_plan_id'], sport_association)
            return float(membership_plan['membership_fee']), None
    return None, None


def fill_data_preregisration(data):
    subscription = Subscription.objects.filter(subscription_id=data['subscription_id']).first()
    if subscription is not None:
        sub_type = data['associate_data']['type']
        data['associate_data'] = AssociateSerializer(subscription.associate).data
        tutor = subscription.associate.get_main_tutor()
        if tutor is not None:
            data['associate_tutor_data'] = AssociateSerializer(tutor).data
            if data['associate_tutor_data']['born_date'] is not None:
                data['associate_tutor_data']['born_date'] = tutor.born_date.strftime('%d/%m/%Y')
        # format born_date to DD/MM/YYYY
        data['associate_data']['born_date'] = subscription.associate.born_date.strftime('%d/%m/%Y')
        data['medical_certificate'] = {
            'medical_id': str(subscription.medical.medical_id) if subscription.medical is not None else None,
            'certificate_expring_date': subscription.medical.expiration_date.strftime('%d/%m/%Y') if subscription.medical is not None and subscription.medical.expiration_date is not None else None
        }
        data['additional_fields'] = subscription.additional_fields
        data['custom_data'] = subscription.custom_data
        data['associate_data']['type'] = sub_type
        data['associate_data']['role'] = subscription.role
        data['associate_data']['subscription_number'] = subscription.subscription_number

        data['subscription_membership'] = {
            'membership_number': subscription.subscription_number,
            'membership_type': subscription.subscription_type
        }

        data['signature'] = {
            'data': None,
            'signature': None,
            'there_is_signature': False
        }
        data['new_user_account'] = {
            'new_member': False
        }
        return data, subscription.user
    else:
        raise ValidationError({'msg': 'Subscription id is required.', "code": 400})


def safe_int(value, default=0):
    """Convert any value to int safely, returning default if conversion fails."""
    if value is None:
        return default

    try:
        if hasattr(value, 'subscription_number'):
            value = value.subscription_number

        str_value = str(value).strip()
        return int(str_value) if str_value else default
    except (ValueError, AttributeError):
        return default


def create_subscription(data, user, auth_token, is_athlete_request=False):
    created_storage_keys = []
    try:
        with transaction.atomic():
            return _create_subscription_impl(
                data,
                user,
                auth_token,
                is_athlete_request=is_athlete_request,
                created_storage_keys=created_storage_keys
            )
    except Exception:
        cleanup_storage_keys(created_storage_keys)
        raise


def _create_subscription_impl(data, user, auth_token, is_athlete_request=False, created_storage_keys=None):
    if created_storage_keys is None:
        created_storage_keys = []
    preregistration = False
    preregistration_user = user
    if 'preregistration' in data.keys() and data['preregistration'] is not None and \
            data['preregistration'] is True:
        if 'subscription_id' in data.keys() and data['subscription_id'] is not None:
            data, preregistration_user = fill_data_preregisration(data)
        preregistration = True

    now = date.today()
    age = relativedelta(now, parser.parse(str(data['associate_data']['born_date'])).date()).years

    if 'associate_tutor_data' in data.keys() and data['associate_tutor_data'] is not None:
        # smart check if associate_data has & associate_tutor_data have
        # address_city, address_cap with values, if not set them to what the other has
        # check field per field to avoid overwriting
        if 'address_city' not in data['associate_data'].keys() and 'address_city' in data[
            'associate_tutor_data'].keys():
            data['associate_data']['address_city'] = data['associate_tutor_data']['address_city']

        elif age < 18 and 'address_city' in data['associate_data'].keys() and 'address_city' not in data[
            'associate_tutor_data'].keys():
            data['associate_tutor_data']['address_city'] = data['associate_data']['address_city']

        if 'address_cap' not in data['associate_data'].keys() and 'address_cap' in data['associate_tutor_data'].keys():
            data['associate_data']['address_cap'] = data['associate_tutor_data']['address_cap']
        elif age < 18 and 'address_cap' in data['associate_data'].keys() and 'address_cap' not in data[
            'associate_tutor_data'].keys():
            data['associate_tutor_data']['address_cap'] = data['associate_data']['address_cap']

        if 'address_cap' in data['associate_tutor_data'] and data['associate_tutor_data']['address_cap'] is not None \
            and data['associate_tutor_data']['address_cap'] == '':
            data['associate_tutor_data']['address_cap'] = None


        # check if email is valid for both associate and tutor (if present)
        # in the case they aren't, then remove them from the request

        if 'email' in data['associate_data'].keys() and data['associate_data']['email'] is not None:
            try:
                data['associate_data']['email'] = data['associate_data']['email'].strip().replace(' ', '').replace('..', '.')
                check_email(data['associate_data']['email'])
            except ValidationError:
                del data['associate_data']['email']

        if 'email' in data['associate_tutor_data'].keys() and data['associate_tutor_data']['email'] is not None:
            try:
                data['associate_tutor_data']['email'] = data['associate_tutor_data']['email'].strip().replace(' ', '').replace('..', '.')
                check_email(data['associate_tutor_data']['email'])
            except ValidationError:
                del data['associate_tutor_data']['email']

        # check sex and set uppercase for both associate and tutor
        if data['associate_data']['sex'] is not None:
            data['associate_data']['sex'] = data['associate_data']['sex'].upper()
        if data['associate_tutor_data']['sex'] is not None:
            data['associate_tutor_data']['sex'] = data['associate_tutor_data']['sex'].upper()

    # instantiate new variables for each field to make code more readable
    new_user_account = UserAccountSerializer(data=data['new_user_account'])
    associate_data = AssociateSerializer(data=data['associate_data'])
    associate_tutor_data = AssociateSerializer(data=data['associate_tutor_data']) if 'associate_tutor_data' in data.keys() and \
                                                                                   data['associate_tutor_data'] is not None else None
    signature = SignatureRequestSerializer(data=data['signature'])


    if 'medical_certificate' in data.keys() and \
            data['medical_certificate'] is not None and \
            'medical_id' in data['medical_certificate'].keys():
        medical_certificate = data['medical_certificate']['medical_id']
    else:
        medical_certificate = None

    # if request coming from athlete must get sport association from body
    if is_athlete_request:
        logger.debug('athlete subscription')
        sport_association_user = User.objects.get(username=data['sport_association'])
        sport_association = SportAssociation.objects.get(user=sport_association_user)
    else:
        logger.debug('sport association subscription')
        sport_association = user.sport_association
        # If user doesn't have a sport_association (e.g., superuser) or if the request
        # specifies a different sport_association, use the one from the request data
        if (sport_association is None or 'sport_association' in data) and 'sport_association' in data:
            try:
                sport_association_user = User.objects.get(username=data['sport_association'])
                sport_association = SportAssociation.objects.get(user=sport_association_user)
                logger.debug(f'Using sport association from request data: {sport_association.denomination}')
            except (User.DoesNotExist, SportAssociation.DoesNotExist):
                pass  # Keep the original user.sport_association

    # set sport_association to associate_data & associate_tutor_data
    associate_data.initial_data['sport_association'] = sport_association.sport_association_id
    associate_data.initial_data['user'] = user.user_id if not is_athlete_request else sport_association.user_id
    if associate_tutor_data is not None and associate_tutor_data.initial_data is not None:
        associate_tutor_data.initial_data['sport_association'] = sport_association.sport_association_id
        associate_tutor_data.initial_data['user'] = user.user_id if not is_athlete_request else sport_association.user_id

    date_from, date_to = BalanceSheetData.get_range_from_year_and_starting_date(
        date=datetime.now(),
        starting_day=sport_association.user.subscription_start_day,
        starting_month=sport_association.user.subscription_start_month,
        user=user
    )

    if preregistration:
        date_from, date_to = BalanceSheetData.get_range_from_year_and_starting_date(
            date=date_to + relativedelta(days=1),
            starting_day=sport_association.user.subscription_start_day,
            starting_month=sport_association.user.subscription_start_month,
            user=user
        )

    SportAssociation.objects.select_for_update().filter(
        sport_association_id=sport_association.sport_association_id
    ).first()

    exists = check_if_subscription_exists(sport_association, associate_data, date_from, date_to, data)
    if exists is True:
        raise DuplicateSubscriptionError()

    # add new member if present
    if new_user_account.is_valid() and new_user_account.save().new_member:
        # extract the new user data and validate id
        logger.debug('new user account')
        new_member_info = UserSerializer(data=data['new_user_account']['new_member_info'])
        # try getting the user
        user = User.objects.filter(email=new_member_info.initial_data['email']).first()
        if user is None:
            new_member_info.is_valid(raise_exception=True)
            user = new_member_info.save()
            alphabet = string.ascii_letters + string.digits
            user.username = user.first_name[:3] + user.last_name[:3] + ''.join(
                secrets.choice(alphabet) for i in range(8)).upper()
            password = ''.join(secrets.choice(alphabet) for i in range(8))
            user.set_password(password)
            user.save()
            _on_commit_robust(lambda user=user, password=password: AuthUtils.send_password_welcome_email(
                user,
                password,
                sport_association
            ))

    # creating the associate
    if associate_data.is_valid(raise_exception=True):
        associate = associate_data.save()
    # creating associate Tutor if associate is a minor
    if associate.is_minor and associate_tutor_data is not None and associate_tutor_data.is_valid():
        associate_tutor = associate_tutor_data.save()
        associate_tutor.user = user
        associate_tutor.save()
        # check if tutor is already added to the associate
        if associate_tutor not in associate.tutors.all():
            # get the current main tutor
            primary_tutor = associate.get_main_tutor()
            # if the primary tutor is not the same as the associate tutor, add the associate tutor
            if primary_tutor is not None and associate_tutor.associate_id != primary_tutor.associate_id:
                associate.tutors.add(primary_tutor)
            elif primary_tutor is None:
                associate.add_main_tutor(associate_tutor)
    associate.user = user
    associate.save()

    if signature.is_valid(raise_exception=True):
        signature = signature.save()
        # save new signature if present
        if signature.there_is_signature:
            new_signature = Signature.objects.create(
                signature=signature.data,
                user=user
            )
            new_signature.save()

    medical = None
    if medical_certificate is not None:
        medical = MedicalCertificate.objects.filter(medical_id=medical_certificate)

        if medical.exists():
            medical = medical.latest('creation_date')
            if medical.user is None:
                medical.user = sport_association.user
            # adding the expiration date to the medical certificate if exsists
            try:
                if 'certificate_expring_date' in data['medical_certificate'].keys():
                    expiration_date = data['medical_certificate']['certificate_expring_date']

                    try:
                        # understand the format of the date and convert it to datetime YYYY-MM-DD or DD/MM/YYYY
                        if '/' in expiration_date:
                            # convert
                            medical.expiration_date = datetime.strptime(expiration_date, '%d/%m/%Y')
                        else:
                            # convert
                            medical.expiration_date = datetime.strptime(expiration_date, '%Y-%m-%d')
                    except Exception as e:
                        logger.debug(f"Error in date conversion: {e}")
            except Exception as e:
                logger.debug(f"No expiration date for medical certificate: {e}")
            medical.save()
        else:
            medical = None
    elif 'medical_certificate' in data and 'certificate_expring_date' in data['medical_certificate'].keys():
        # it means that there is the certificate_expring_date in the request but no medical_certificate, we need to
        # create a new medical certificate
        expiration_date = data['medical_certificate']['certificate_expring_date']
        document = None
        saved_file = None

        try:
            # understand the format of the date and convert it to datetime YYYY-MM-DD or DD/MM/YYYY
            if '/' in expiration_date:
                # convert
                expiration_date = datetime.strptime(expiration_date, '%d/%m/%Y')
            else:
                # convert
                expiration_date = datetime.strptime(expiration_date, '%Y-%m-%d')
            # generate a fake image document with the medical certificate expiration date and data
            filename = f"medical_certificate_{associate_data.initial_data['first_name']}_{associate_data.initial_data['last_name']}.png"

            # skip creating certificate if expiration date is today (essentially expired)
            if expiration_date.strftime('%Y-%m-%d') == datetime.now().strftime('%Y-%m-%d'):
                logger.debug('Skipping medical certificate creation - expiration date is today')
                raise Exception('Skipping - expiration date is today')
            # create document
            image_png = generate_image_with_text(
                    f'''
            {associate_data.initial_data['first_name']} {associate_data.initial_data['last_name']}\n
            \n
            Questa certificazione è valida fino al {make_aware(expiration_date)}\n
            \n
            \n
            Attenzione: questo è un placeholder, il certificato medico deve essere caricato in seguito.
                        '''
                )

            document = Document.objects.create(filename=filename)
            document.save()

            storing_path = posixpath.join(
                (STORAGE_DIR or '').strip('/'),
                str(document.creation_date.timestamp()),
                str(document.document_id)
            )
            file = posixpath.join(storing_path, document.filename).lstrip('/')

            # store image_png in file
            # image png is Image from PIL
            f = BytesIO()
            image_png.save(f, format='PNG')
            f.seek(0)

            django_file = ContentFile(f.read(), name=filename)

            # save the file
            saved_file = default_storage.save(file, django_file)

            medical = MedicalCertificate.objects.create(
                expiration_date=expiration_date,
                user=sport_association.user,
                document=document
            )
            created_storage_keys.append(saved_file)

        except Exception as e:
            if saved_file:
                cleanup_storage_keys([saved_file])
            if document and document.pk:
                document.delete()
            logger.debug(f"Error in empty certificate creation: {e}")

    # get type of subscription
    subscription_type = Subscription.ASSOCIATE_AND_MEMBER
    fee_amount = 0
    membership_fee = 0

    # subscription type
    if 'type' in data['associate_data'] and data['associate_data'] and data['associate_data']['type'] is not None:
        subscription_type = int(str(data['associate_data']['type']).strip()) if data['associate_data']['type'] != '' \
            else Subscription.ASSOCIATE_AND_MEMBER

    if sport_association.enable_quotes_management:
        fee_amount, payment_meta = get_subscription_fee(data, sport_association, subscription_type)

        # get membership_fee if present
        membership_fee, meta_payment_categories = get_membership_fee(data, sport_association, subscription_type)
        fee_amount = float(fee_amount) + float(membership_fee) if membership_fee is not None else float(fee_amount)

        subject = Payment.SUBSCRIPTION

        if sport_association.user.default_payment_category is not None:
            payment_category = PaymentCategory.objects.filter(
                payment_category_id=sport_association.user.default_payment_category.payment_category_id).first()
        else:
            payment_category = PaymentCategory.objects.filter(
                name__iexact='entrate e proventi da attività tipiche').first()

        if fee_amount > 0 or sport_association.user.show_zero_payments:
            payment_date = timezone.now().date()

            # Check for recent duplicate payment (within same day, same amount, unpaid)
            existing_payment = Payment.objects.filter(
                associate=associate,
                sport_association=sport_association,
                subject=Payment.SUBSCRIPTION,
                paid=False,
                amount=fee_amount,
                payment_date=payment_date
            ).order_by('-creation_date').first()

            if existing_payment:
                # Reuse existing payment to avoid duplicates
                payment = existing_payment
            else:
                payment = Payment.objects.create(
                    user=user,
                    associate=associate,
                    amount=fee_amount,
                    subject=subject,
                    payment_category=payment_category,
                    sport_association=sport_association,
                    meta_payment_categories=meta_payment_categories,
                    meta=payment_meta,
                    payment_date=payment_date
                )
        else:
            payment = None
    else:
        payment = None

    # changing status if signed
    subscription_status = Subscription.PENDING if signature.there_is_signature else Subscription.NOT_SIGNED

    # get highest number so far
    latest_membership_number = Subscription.objects.filter(
        sport_association=sport_association,
        subscription_number__isnull=False,
        subscription_number__regex=r'^\d+$'  # Only purely numeric values
    ).annotate(
        subscription_number_int=Cast('subscription_number', BigIntegerField())
    ).order_by('-subscription_number_int').first()

    membership_number = max(
        safe_int(sport_association.user.membership_starting_number),
        safe_int(latest_membership_number) if latest_membership_number else 0
    ) + 1

    subscription_role = Subscription.SOCIO_ORDINARIO
    custom_data = None
    additional_fields = None

    # subscription role
    if 'role' in data['associate_data'] and data['associate_data'] and data['associate_data']['role'] is not None:
        subscription_role = int(str(data['associate_data']['role']).strip()) if data['associate_data']['role'] != '' \
            else Subscription.SOCIO_ORDINARIO

    # membership data
    if 'subscription_number' in data['associate_data'] and data['associate_data']['subscription_number'] is not None:
        membership_number = int(str(data['associate_data']['subscription_number']).strip())

    if 'custom_data' in data and data['custom_data'] is not None:
        custom_data = data['custom_data']

        if membership_number is None and 'membership_number' in custom_data and custom_data['membership_number'] is not None:
            try:
                membership_number = int(custom_data['membership_number'])
            except Exception as e:
                logger.error(f"Error in membership number conversion: {e}")

    if 'additional_fields' in data and data['additional_fields'] is not None:
        additional_fields = data['additional_fields']

    # creating the actual subscription
    if 'plan_id' not in data.keys() or data['plan_id'] is None:
        data['plan_id'] = None
    if 'membership_plan_id' not in data.keys() or data['membership_plan_id'] is None:
        data['membership_plan_id'] = None

    # try to resolve the plan_name and membership_plan_name
    plan_name = None
    membership_plan_name = None

    if data['plan_id'] is not None:
        plan = extract_the_subscription_plan(data['plan_id'], sport_association)
        if plan is not None:
            plan_name = plan['name']
    # try to resolve the membership plan name
    if data['membership_plan_id'] is not None:
        membership_plan = extract_the_membership_plan(data['membership_plan_id'], sport_association)
        if membership_plan is not None:
            membership_plan_name = membership_plan['name']

    current_date_from, current_date_to = BalanceSheetData.get_range_from_year_and_starting_date(
        date=timezone.now(),
        starting_day=user.subscription_start_day,
        starting_month=user.subscription_start_month,
        user=user
    )

    if preregistration:
        current_date_from, current_date_to = BalanceSheetData.get_range_from_year_and_starting_date(
            date=current_date_to + relativedelta(days=1),
            starting_day=user.subscription_start_day,
            starting_month=user.subscription_start_month,
            user=user
        )

    # Prepare the initial data for subscription creation
    subscription_data = {
        'sport_association': sport_association,
        'status_flag': subscription_status,
        'associate': associate,
        'type': subscription_type,
        'role': subscription_role,
        'user': user,
        'medical': medical,
        'payment': payment,
        'meta': {
            'plan_id': data['plan_id'],
            'plan_name': plan_name,
            'membership_plan_id': data['membership_plan_id'],
            'membership_plan_name': membership_plan_name
        },
        'subscription_number': membership_number,
        'subscription_type': sport_association.user.default_membership_type,
        'custom_data': custom_data,
        'additional_fields': additional_fields,
    }

    if preregistration:
        subscription_data['creation_date'] = date_from # the actual start date
        subscription_data['start_date'] = current_date_from
        subscription_data['end_date'] = current_date_to
        subscription_data['user'] = preregistration_user

    try:
        # Create the subscription with all data in a single query
        subscription = Subscription.objects.create(**subscription_data)
    except DuplicateSubscriptionError:
        raise DuplicateSubscriptionError()

    # Upload signature to storage if present
    if signature.there_is_signature and signature.data:
        subscription.set_signature_from_base64(signature.data)
        if subscription.signature_storage_key:
            created_storage_keys.append(subscription.signature_storage_key)
        subscription.save(update_fields=['signature_url', 'signature_storage_key'])

    if subscription.type in [Subscription.ASSOCIATE_AND_MEMBER, Subscription.MEMBER_ONLY]:
        membership_start_date = None
        membership_end_date = None
        membership_type = sport_association.user.default_membership_type

        if (membership_type is None or membership_type == '') and membership_plan_name is not None:
            membership_type = membership_plan_name
        elif membership_type is None or membership_type == '':
            membership_type = f"Tesseramento {current_date_from.year}/{current_date_to.year}"

        # create the SubscriptionMembership
        membership_duration = sport_association.user.membership_duration
        if membership_duration == User.FULL_YEAR:
            membership_start_date = current_date_from.date()
            membership_end_date = current_date_from.date() + relativedelta(years=1) - relativedelta(days=1)
        elif membership_duration == User.LIKE_SEASON_YEAR:
            membership_start_date = current_date_from.date()
            membership_end_date = current_date_to.date()
        elif membership_duration == User.LIKE_SEASON_YEAR_FROM_TIME:
            membership_start_date = subscription.creation_date.date()
            membership_end_date = current_date_to.date()

        try:
            subscription_membership = SubscriptionMembership.objects.create(
                associate=subscription.associate,
                sport_association=sport_association,
                subscription=subscription,
                membership_number=membership_number,
                membership_type=membership_type,
                start_date=membership_start_date,
                end_date=membership_end_date,
                price=membership_fee,
                payment=payment,
            )
            subscription_membership.save()
        except Exception as e:
            logger.error(e)
            raise

    _on_commit_robust(lambda: print_document_subscription.delay(str(subscription.subscription_id), auth_token))

    messages = [
        {
            "type": NotificationUtils.SUBSCRIPTION,
            "msg": "Nuova Iscrizione aggiunta per {}.".format(associate.get_full_name())
        }
    ]

    _on_commit_robust(lambda: NotificationService.send_notification(user, messages))

    if user.role == User.ATHLETE:
        data = {
            'athlete_first_name': subscription.associate.first_name,
            'athlete_last_name': subscription.associate.last_name,
            'sport_association': {
                "denomination": subscription.sport_association.denomination,
            },
            'app_host': settings.APP_URL,
            'settings': {
                'WHITELABEL_NAME': settings.WHITELABEL_NAME,
                'IS_WHITELABEL': settings.IS_WHITELABEL
            }
        }

        _on_commit_robust(lambda: send_email_template.delay(
            recipient_list=[subscription.sport_association.user.email],
            subject=f"[{subscription.sport_association.denomination}] Ricordati di approvare l\'iscrizione di "
                    f"{subscription.associate.first_name} {subscription.associate.last_name}",
            template="email/account/email_new_subscription.html",
            data=data,
            sport_association_id=subscription.sport_association.sport_association_id
        ))
    subscription._created_storage_keys = list(created_storage_keys)
    return True, subscription


def get_optimized_subscriptions(user):
    # get tax_code of the user
    tax_codes = Subscription.objects.filter(user=user).values_list('associate__tax_code', flat=True).distinct()
    if len(tax_codes) == 0:
        []
    subscriptions = Subscription.objects.filter(Q(user=user) | Q(associate__tax_code__in=tax_codes)).prefetch_related('medical', 'medical__document', 'associate', 'sport_association').order_by('-creation_date')
    return SubscriptionSerializerAthleteOptimizedList(subscriptions, many=True).data


def add_course_to_subscription(course, sub, sport_association, data=None, is_athlete=False):
    if data is None:
        data = {}

    # get multiple_quote if present and course_type == 2
    multiple_quote = None
    if course.course_type == 2:
        try:
            if 'multiple_quote' not in data.keys():
                # get the first quote
                data['multiple_quote'] = course.multiple_quotes[0]
            else:
                # find the quote in course.multiple_quotes based on value
                for q in course.multiple_quotes:
                    if q['quote_id'] == data['multiple_quote']['value']:
                        data['multiple_quote'] = q
                        break
                multiple_quote = data['multiple_quote']
        except Exception as e:
            logger.error(f"Error in multiple quote extraction: {e}")
            multiple_quote = None

    if CourseSubscription.objects.filter(
            course=course,
            subscription=sub
    ).first() is not None:
        return Response({'msg': 'Utente già nel corso.'}, status=status.HTTP_400_BAD_REQUEST)

    course_subscription = CourseSubscription.objects.create(
        course=course,
        subscription=sub,
        multiple_quote=multiple_quote,
    )

    if is_athlete and course.course_type == Course.MEMBERSHIP_TYPE:
        # prefill the default values for auto-renewal and membership_fee
        course_subscription.auto_renewal = course.auto_renewal
        course_subscription.membership_fee = course.fee
        course_subscription.billed_frequency = course.billed_frequency
        if course.billed_duration_is_sport_season:
            season_date_from, season_date_to = BalanceSheetData.get_range_from_year_and_starting_date(
                date=timezone.now(),
                starting_day=course.sport_association.user.subscription_start_day,
                starting_month=course.sport_association.user.subscription_start_month,
                user=course.sport_association.user
            )
            # get sport association initial date currently
            billed_from = season_date_from.strftime('%d/%m/%Y')
            # get billed to date
            billed_until = season_date_to.strftime('%d/%m/%Y')
            course_subscription.billed_from = billed_from
            course_subscription.billed_until = billed_until
            course_subscription.billed_from_day_of_month = course.sport_association.user.subscription_start_day
        elif course.billed_from_subscription_date:
            course_subscription.billed_from = course_subscription.creation_date
            course_subscription.billed_until = course_subscription.creation_date + relativedelta(
                months=course.billed_frequency)
        else:
            course_subscription.billed_from = datetime.strptime(
                f"{course.billed_from_day_of_month}/{timezone.now().month}/{timezone.now().year}", "%d/%m/%Y"
            )
            course_subscription.billed_until = course_subscription.billed_from + relativedelta(
                months=course.billed_frequency)

        course_subscription.save()

    payment = None

    if sport_association.user.default_payment_category_courses is not None:
        payment_category = PaymentCategory.objects.filter(
            payment_category_id=sport_association.user.default_payment_category_courses.payment_category_id).first()
    else:
        payment_category = PaymentCategory.objects.filter(
            name__iexact='entrate e proventi da attività tipiche').first()

    if 'one_fee_payment' in data and data['one_fee_payment'] and\
            course.one_fee > 0:
        # generate payment for the course subscription
        payment = Payment.objects.create(
            user=sub.user,
            associate=sub.associate,
            amount=course.one_fee,
            subject=Payment.COURSE,
            sport_association=sport_association,
            payment_category=payment_category,
            course={
                "label": course.title,
                "value": str(course.course_id)
            },
            meta={
                'one_fee': 'true'
            }
        )
        payment.save()
    elif course.course_type == 2 and multiple_quote is not None:
        if 'payment_category' in multiple_quote.keys() \
                and 'value' in multiple_quote['payment_category']:
            try:
                payment_category_multiple = PaymentCategory.objects.filter(
                    payment_category_id=multiple_quote['payment_category']['value']
                ).first()
                payment_category = payment_category_multiple
            except Exception as e:
                logger.error(f"Error in payment category extraction: {e}")

        # get multiple_quote if present and course_type == 2
        payment = Payment.objects.create(
            user=sub.user,
            associate=sub.associate,
            amount=float(multiple_quote['amount'].replace(',', '.')),
            subject=Payment.COURSE,
            sport_association=sport_association,
            payment_category=payment_category,
            course={
                "label": course.title,
                "value": str(course.course_id)
            },
            meta={
                'multiple_quote': multiple_quote
            }
        )
        payment.save()
    else:
        # check if there are installments
        if course.multi_payments:
            course_subscription.multi_payments = True
            course_subscription.save()

            # generate installments for the course subscription
            for e in course.events:
                course_event = CourseEventsSerializer(data=e)
                course_event.is_valid(raise_exception=True)
                # parse the event
                event = course_event.validated_data
                # generate installment if date is not yet passed
                if event['payment_date'] >= course_subscription.creation_date.date() or \
                        sport_association.user.full_installments_plan:
                    # generate payment for the installment
                    payment = Payment.objects.create(
                        user=sub.user,
                        associate=sub.associate,
                        amount=event['amount'],
                        subject=Payment.COURSE,
                        payment_category=payment_category,
                        creation_date=event['payment_date'],
                        sport_association=sport_association,
                        course={
                            "label": course.title,
                            "value": str(course.course_id)
                        },
                    )
                    payment.save()

                    installment = CourseSubscriptionInstallment.objects.create(
                        course_subscription=course_subscription,
                        amount=event['amount'],
                        id=event['id'],
                        payment_date=event['payment_date'],
                        payment=payment
                    )
                    installment.save()
        elif course.fee > 0:
            payment = Payment.objects.create(
                user=sub.user,
                associate=sub.associate,
                amount=course.fee,
                subject=Payment.COURSE,
                payment_category=payment_category,
                sport_association=sport_association,
                course={
                    "label": course.title,
                    "value": str(course.course_id)
                },
            )
            payment.save()

    if payment is not None and payment.amount > 0:
        course_subscription.payment = payment
        course_subscription.save()

    data = {
        "msg": "associate added.",
        "payment": None,
        "course_subscription": CourseSubscriptionSerializer(course_subscription).data
    }

    if payment is not None:
        data['payment'] = {
            "payment_id": payment.payment_id,
            "amount": payment.amount,
            "paid": payment.paid,
            "creation_date": payment.creation_date,
        }
    return data


def add_course_subscription(course_subscription: CourseSubscription, sport_association, data=None):
    """
    Handle payment creation for course subscriptions based on course type and payment options

    Args:
        course_subscription: CourseSubscription instance
        sport_association: SportAssociation instance
        data: Optional dictionary containing additional data like one_fee_payment and multiple_quote
    """
    payment = None
    course = course_subscription.course
    sub = course_subscription.subscription

    if sport_association.user.default_payment_category_courses is not None:
        payment_category = PaymentCategory.objects.filter(
            payment_category_id=sport_association.user.default_payment_category_courses.payment_category_id).first()
    else:
        payment_category = PaymentCategory.objects.filter(
            name__iexact='entrate e proventi da attività tipiche').first()

    if course.course_type == Course.DEFAULT_TYPE:
        if data and data.get('one_fee_payment') and course.one_fee > 0:
            payment = Payment.objects.create(
                user=sport_association.user,
                associate=sub.associate,
                amount=course.one_fee,
                subject=Payment.COURSE,
                sport_association=sport_association,
                payment_category=payment_category,
                course={
                    "label": course.title,
                    "value": str(course.course_id)
                },
                meta={
                    'one_fee': 'true',
                    'description': f"Pagamento in un'unica soluzione per {course.title}",
                    'course_id': str(course.course_id),
                    'course_title': str(course.title),
                    'course_type': str(course.course_type),
                    'payment_date': str(course_subscription.creation_date.date())
                }
            )
        elif course.multi_payments:
            course_subscription.multi_payments = True
            course_subscription.save()
        elif course.fee > 0:
            payment = Payment.objects.create(
                user=sport_association.user,
                associate=sub.associate,
                amount=course.fee,
                subject=Payment.COURSE,
                sport_association=sport_association,
                payment_category=payment_category,
                course={
                    'label': str(course.title),
                    'value': str(course.course_id)
                },
                meta= {
                    'description': f"Quota corso del {course_subscription.creation_date.strftime('%d/%m/%y')} {course.title}",
                    'course_id': str(course.course_id),
                    'course_title': str(course.title),
                    'course_type': str(course.course_type),
                    'payment_date': str(course_subscription.creation_date.date())
                }
            )

    if course.course_type == Course.MULTIPLE_QUOTES_TYPE and data and data.get('multiple_quote'):
        multiple_quote = data['multiple_quote']

        if 'payment_category' in multiple_quote.keys() \
                and 'value' in multiple_quote['payment_category']:
            try:
                payment_category_multiple = PaymentCategory.objects.filter(
                    payment_category_id=multiple_quote['payment_category']['value']
                ).first()
                payment_category = payment_category_multiple
            except Exception as e:
                logger.error(f"Error in payment category extraction: {e}")

        payment = Payment.objects.create(
            user=sport_association.user,
            associate=sub.associate,
            amount=float(multiple_quote['amount'].replace(',', '.')),
            subject=Payment.COURSE,
            sport_association=sport_association,
            payment_category=payment_category,
            course={
                "label": course.title,
                "value": str(course.course_id)
            },
            meta={
                'multiple_quote': multiple_quote,
                'description': f"Quota {multiple_quote['title']} a {course.title}",
                'course_id': str(course.course_id),
                'course_title': str(course.title),
                'course_type': str(course.course_type),
            }
        )


    if payment and payment.amount > 0:
        course_subscription.payment = payment
        course_subscription.save()

    return course_subscription
