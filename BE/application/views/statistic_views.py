"""
@ copyright: Bakney SRL
"""
import logging
from datetime import date, timedelta, datetime
from statistics import mean

import pytz
from dateutil.relativedelta import relativedelta
from django.db.models import Count, Sum, Q, Case, When, IntegerField, F
from django.db.models.functions import TruncMonth, TruncDate, Coalesce
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes

from application.models.carnet_models import CarnetSubscription
from application.utils.attendance_utils import get_extended_prop_for_attendance_day
from core.middleware import IsAuthenticated
from rest_framework.response import Response
from application.models import AttendanceRegistry, AttendanceDay
from application.models.courses_models import Course, CourseSubscription
from application.models.invoices_models import Invoice
from application.models.payment_models import Payment
from application.models.subscriptions_models import Subscription
from application.models.user_models import SportAssociation, Associate, User, Instructor
from django.conf import settings

from application.permissions import IsProPlanAssociation, IsTeamsPlanAssociation
from application.serializers.user_serializers import AssociateSerializer
from application.utils.api_utils import BalanceSheetData, is_valid_uuid
from communications.models import MessageTransaction, Message
from instance.models import InstanceConfiguration

logger = logging.getLogger(__name__)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def statistic_dashboard_layout(request):

    sport_association = SportAssociation.objects.get(user=request.user)

    if sport_association is None:
        return Response({'error: sport association not found.'}, status.HTTP_404_NOT_FOUND)

    dashboard_layout = request.data.get('dashboard_layout', None)

    if request.user.is_collaborator:
        request.original_user.dashboard_layout = dashboard_layout
        request.original_user.save()
    else:
        request.user.dashboard_layout = dashboard_layout
        request.user.save()

    return Response({'data': 'dashboard layout updated'}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def statistic_dashboard(request):

    sport_association = SportAssociation.objects.get(user=request.user)

    if sport_association is None:
        return Response({'error: sport association not found.'}, status.HTTP_404_NOT_FOUND)

    widget = request.GET.get('widget', None)

    if widget is None:
        return Response({'error: widget is required.'}, status.HTTP_400_BAD_REQUEST)

    # Get statistics for the widget

    if widget == 'subscriptions':
        today = timezone.now()

        # Single query that gets both total count and status breakdown
        subscription_stats = Subscription.objects.filter(
            sport_association=sport_association,
            status_flag__in=[Subscription.ACCEPTED, Subscription.PENDING, Subscription.NOT_SIGNED,
                             Subscription.REJECTED],
            archived=False,
            type__in=[Subscription.ASSOCIATE_AND_MEMBER, Subscription.MEMBER_ONLY],
            start_date__lte=today,
            end_date__gte=today
        ).select_related(
            'associate',
            'medical',
            'payment'
        ).aggregate(
            total_subscriptions=Count('subscription_id'),
            status_1_count=Count(Case(
                When(status_flag=1, then=1),
                output_field=IntegerField(),
            )),
            status_2_count=Count(Case(
                When(status_flag=2, then=1),
                output_field=IntegerField(),
            )),
            status_3_count=Count(Case(
                When(status_flag=3, then=1),
                output_field=IntegerField(),
            )),
            status_4_count=Count(Case(
                When(status_flag=4, then=1),
                output_field=IntegerField(),
            ))
        )

        pie_subscriptions = [
            subscription_stats['status_1_count'],
            subscription_stats['status_2_count'],
            subscription_stats['status_3_count'],
            subscription_stats['status_4_count']
        ]

        return Response({
            'data': {
                "pie_subscriptions": pie_subscriptions,
                "total_subscriptions": subscription_stats['total_subscriptions'],
            }
        }, status=status.HTTP_200_OK)

    elif widget == 'payments':
        # Calculate date range
        end_date = timezone.now()
        start_date = end_date - timedelta(days=30)

        # Get all payments for the last 31 days in a single query
        # Group by date and sum the amounts
        daily_payments = Payment.objects.filter(
            amount__gt=0,
            paid=True,
            sport_association=sport_association,
            creation_date__range=(start_date, end_date)
        ).annotate(
            date=TruncDate('creation_date')
        ).values('date').annotate(
            daily_total=Sum('amount')
        ).order_by('date')

        # Create a dictionary of date: amount pairs
        payment_dict = {
            payment['date']: payment['daily_total']
            for payment in daily_payments
        }

        # Create a list of daily totals, filling in zeros for days without payments
        current_month_payments = []
        current_date = start_date.date()

        while current_date <= end_date.date():
            daily_amount = payment_dict.get(current_date, 0)
            current_month_payments.append(float(daily_amount))
            current_date += timedelta(days=1)

        # Calculate total in a single operation
        total_payments = sum(current_month_payments)

        return Response({
            'data': {
                "current_month_payments": current_month_payments,
                "total_payments": total_payments,
            }
        }, status=status.HTTP_200_OK)

    elif widget == 'associates':
        # Calculate date range
        end_date = timezone.now()
        start_date = end_date - timedelta(days=30)

        # Get daily counts in a single query
        daily_associates = Subscription.objects.filter(
            sport_association=sport_association,
            creation_date__range=(start_date, end_date)
        ).annotate(
            date=TruncDate('creation_date')
        ).values('date').annotate(
            daily_count=Count('subscription_id')
        ).order_by('date')

        # Create a dictionary of date: count pairs
        associates_dict = {
            entry['date']: entry['daily_count']
            for entry in daily_associates
        }

        # Create a list of daily counts, filling in zeros for days without new associates
        current_month_associates = []
        current_date = start_date.date()

        while current_date <= end_date.date():
            daily_count = associates_dict.get(current_date, 0)
            current_month_associates.append(daily_count)
            current_date += timedelta(days=1)

        # Calculate total in a single operation
        total_associates = sum(current_month_associates)

        return Response({
            'data': {
                "current_month_associates": current_month_associates,
                "total_associates": total_associates,
            }
        }, status=status.HTTP_200_OK)

    elif widget == 'bestcourses':
        end_date = timezone.now()
        start_date = end_date - timedelta(days=7)

        # Get all active courses with their subscription counts in a single query
        courses_with_counts = Course.objects.filter(
            sport_association=sport_association,
            status_flag=Course.ACTIVE
        ).annotate(
            subscriptions=Count('coursesubscription')
        ).values(
            'course_id',
            'title',
            'description',
            'subscriptions'
        ).order_by('-subscriptions')[:3]

        # Convert to list of dictionaries for best courses
        best_courses = list(courses_with_counts)

        # Get daily subscription counts for the last week in a single query
        daily_subscriptions = CourseSubscription.objects.filter(
            course__sport_association=sport_association,
            creation_date__range=(start_date, end_date)
        ).annotate(
            date=TruncDate('creation_date')
        ).values('date').annotate(
            daily_count=Count('course_subscription_id')
        ).order_by('date')

        # Create a dictionary of date: count pairs
        subscription_dict = {
            entry['date']: entry['daily_count']
            for entry in daily_subscriptions
        }

        # Create list of daily counts, filling in zeros for days without subscriptions
        current_week_course_associates = []
        current_date = start_date.date()

        while current_date <= end_date.date():
            daily_count = subscription_dict.get(current_date, 0)
            current_week_course_associates.append(daily_count)
            current_date += timedelta(days=1)

        # Get total course associates in a single query
        total_subscriptions = CourseSubscription.objects.filter(
            course__sport_association=sport_association
        ).count()

        return Response({
            'data': {
                "current_week_course_associates": current_week_course_associates,
                "total_course_associates": total_subscriptions,
                "best_courses": best_courses,
            }
        }, status=status.HTTP_200_OK)

    elif widget == 'todaylessons':
        # get all today lessons in range of today
        rome_today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(minutes=121)
        rome_today_end = datetime.now().replace(hour=23, minute=59, second=59, microsecond=0) + timedelta(minutes=121)

        is_instructor = False
        if hasattr(request, 'original_user') and request.original_user is not None and request.original_user.is_collaborator:
            # check if it's an instructor
            instructor = Instructor.objects.filter(associated_user_id=str(request.original_user.user_id)).first()
            instructor_id = str(instructor.instructor_id) if instructor is not None else None
            if instructor_id is not None:
                is_instructor = True
                attentance_registries = AttendanceRegistry.objects.filter(events__iregex=instructor_id)
                # Optimized query with select_related and prefetch_related
                attendance_days = AttendanceDay.objects.filter(
                    date__range=(rome_today_start, rome_today_end),
                    attendance_registry__course__sport_association=sport_association,
                    attendance_registry__in=attentance_registries
                ).select_related(
                    'attendance_registry',
                    'attendance_registry__course'
                ).order_by('date').iterator(chunk_size=100)

        if not is_instructor:
            # Optimized query with select_related
            attendance_days = AttendanceDay.objects.filter(
                date__range=(rome_today_start, rome_today_end),
                attendance_registry__course__sport_association=sport_association,
            ).select_related(
                'attendance_registry',
                'attendance_registry__course'
            ).order_by('date').iterator(chunk_size=100)

        lessons = []

        for attendance_day in attendance_days:
            lessons.append(get_extended_prop_for_attendance_day(attendance_day))

        return Response({'data': {
            "lessons": lessons,
        }}, status=status.HTTP_200_OK)

    elif widget == 'expiringcarnets':
        thirty_days_ago = timezone.now() - timedelta(days=30)

        # Base query without JSON-specific operations
        carnet_subscriptions = CarnetSubscription.objects.filter(
            subscription__sport_association__user=request.user,
        ).select_related(
            'subscription',
            'subscription__associate',
            'carnet_id'
        )

        # Handle JSON filtering based on database backend
        if 'postgres' in settings.DATABASES['default']['ENGINE']:
            # PostgreSQL specific JSON filtering
            carnet_subscriptions = carnet_subscriptions.filter(
                meta__has_key='lessons_left'
            ).filter(
                meta__lessons_left__lt=4
            ).exclude(
                Q(meta__lessons_left=0) &
                ~Q(meta__lessons_registry__exists=True)
            )
        else:
            # SQLite fallback - fetch all and filter in Python
            carnet_subscriptions = carnet_subscriptions.all()

        def process_carnet(carnet):
            try:
                meta = carnet.meta
                if not isinstance(meta, dict):
                    return None

                lessons_left = meta.get('lessons_left')
                if lessons_left is None or not isinstance(lessons_left, (int, float)):
                    return None

                # Filter out carnets with too many lessons (SQLite only)
                if 'postgres' not in settings.DATABASES['default']['ENGINE']:
                    if lessons_left >= 4:
                        return None

                # Check for recent activity if no lessons left
                if lessons_left == 0:
                    lessons_registry = meta.get('lessons_registry', [])
                    if not any(
                            isinstance(lesson, dict) and
                            'date' in lesson and
                            datetime.strptime(lesson['date'], '%Y-%m-%d %H:%M:%S%z') > thirty_days_ago
                            for lesson in lessons_registry
                    ):
                        return None

                return {
                    "carnet_subscription_id": carnet.carnet_subscription_id,
                    "subscription": {
                        "subscription_id": carnet.subscription.subscription_id,
                        "associate": {
                            "associate_id": carnet.subscription.associate.associate_id,
                            "first_name": carnet.subscription.associate.first_name,
                            "last_name": carnet.subscription.associate.last_name
                        }
                    },
                    "carnet": {
                        "carnet_id": carnet.carnet_id.carnet_id,
                        "title": carnet.carnet_id.title,
                        "description": carnet.carnet_id.description,
                        "lessons_number": carnet.carnet_id.lessons_number,
                        "fee": carnet.carnet_id.fee,
                        "creation_date": carnet.creation_date,
                        "public": carnet.carnet_id.public,
                        "meta": meta
                    }
                }
            except (KeyError, TypeError, ValueError):
                return None

        # Process carnets and filter out None values
        expiring_carnets = [
            result for result in map(process_carnet, carnet_subscriptions)
            if result is not None
        ]

        # Sort by lessons_left
        expiring_carnets.sort(key=lambda x: x['carnet']['meta'].get('lessons_left', 0))

        return Response({
            'data': {
                "expiring_carnets": expiring_carnets,
            }
        }, status=status.HTTP_200_OK)
    elif widget == 'expiringmedicalcertificates':

        today = timezone.now().date()
        expiring_30_days = today + timedelta(days=30)

        current_date_from, current_date_to = BalanceSheetData.get_range_from_year_and_starting_date(
            date=timezone.now(),
            starting_day=request.user.balance_sheet_start_day,
            starting_month=request.user.balance_sheet_start_month
        )

        # Optimize query by combining filters and selecting only needed fields
        subscriptions = (
            Subscription.objects
            .filter(
                sport_association=sport_association,
                archived=False,
                medical__isnull=False,
                medical__expiration_date__isnull=False,
                medical__expiration_date__lte=expiring_30_days,
                medical__expiration_date__gte=today,
                creation_date__range=(current_date_from, current_date_to)
            )
            .select_related('medical', 'associate')
            .values(
                'subscription_id',
                'medical__expiration_date',
                'associate__associate_id',
                'associate__first_name',
                'associate__last_name'
            )
        )

        # Process results using list comprehension instead of loop
        expiring_medical_certificates = [
            {
                "subscription_id": sub['subscription_id'],
                "days_left": (sub['medical__expiration_date'] - today).days if sub['medical__expiration_date'] else None,
                "associate": {
                    "associate_id": sub['associate__associate_id'],
                    "first_name": sub['associate__first_name'],
                    "last_name": sub['associate__last_name']
                },
                "medical": {
                    "expiration_date": sub['medical__expiration_date'],
                }
            }
            for sub in subscriptions
        ]

        return Response(
            {'data': {"expiring_medical_certificates": sorted(
                [x for x in expiring_medical_certificates if x['days_left'] is not None],
                key=lambda x: x['days_left']
            )}},
            status=status.HTTP_200_OK
        )

    elif widget == 'expiredmedicalcertificates':

        today = timezone.now().date()

        current_date_from, current_date_to = BalanceSheetData.get_range_from_year_and_starting_date(
            date=timezone.now(),
            starting_day=request.user.balance_sheet_start_day,
            starting_month=request.user.balance_sheet_start_month
        )

        # Optimize query by combining filters and selecting only needed fields
        subscriptions = (
            Subscription.objects
            .filter(
                sport_association=sport_association,
                medical__isnull=False,
                medical__expiration_date__isnull=False,
                medical__expiration_date__lt=today,
                creation_date__range=(current_date_from, current_date_to)
            )
            .select_related('medical', 'associate')
            .values(
                'subscription_id',
                'medical__expiration_date',
                'associate__associate_id',
                'associate__first_name',
                'associate__last_name'
            )
        )

        # Process results using list comprehension
        expired_medical_certificates = [
            {
                "subscription_id": sub['subscription_id'],
                "days_left": (sub['medical__expiration_date'] - today).days if sub['medical__expiration_date'] else None,
                "associate": {
                    "associate_id": sub['associate__associate_id'],
                    "first_name": sub['associate__first_name'],
                    "last_name": sub['associate__last_name']
                },
                "medical": {
                    "expiration_date": sub['medical__expiration_date'],
                }
            }
            for sub in subscriptions
        ]

        return Response(
            {'data': {"expired_medical_certificates": sorted(
                [x for x in expired_medical_certificates if x['days_left'] is not None],
                key=lambda x: x['days_left']
            )}},
            status=status.HTTP_200_OK
        )

    elif widget == 'subscriptionstoapprove':
        limit = 100
        today = timezone.now().date()

        # Optimize query by combining filters, limiting results, and selecting only needed fields
        subscriptions = (
            Subscription.objects
            .filter(
                sport_association=sport_association,
                status_flag__in=[Subscription.PENDING, Subscription.NOT_SIGNED],
                archived=False
            ).filter(
                Q(start_date__lte=today) &
                Q(end_date__gte=today)
            )
            .select_related('associate')
            .order_by('-creation_date', '-status_flag')
            .values(
                'creation_date',
                'subscription_id',
                'status_flag',
                'associate__associate_id',
                'associate__first_name',
                'associate__last_name',
            )[:limit]
        )

        subscriptions_to_approve = []
        for sub in subscriptions:
            subscriptions_to_approve.append(
                {
                    "creation_date": sub['creation_date'],
                    "subscription_id": sub['subscription_id'],
                    "status": sub['status_flag'],
                    "associate": {
                        "associate_id": sub['associate__associate_id'],
                        "first_name": sub['associate__first_name'],
                        "last_name": sub['associate__last_name']
                    }
                }
            )

        return Response(
            {'data': {"subscriptions_to_approve": subscriptions_to_approve}},
            status=status.HTTP_200_OK
        )

    elif widget == 'incomeAndExpenses':

        # Set up Rome timezone and dates
        rome_tz = pytz.timezone('Europe/Rome')
        rome_now = datetime.utcnow().replace(tzinfo=pytz.utc).astimezone(rome_tz)

        # Calculate date ranges
        today_start = rome_now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        last_month_start = today_start - relativedelta(days=31)
        year_start = today_start.replace(month=1, day=1)

        # Base queryset
        base_queryset = Payment.objects.filter(
            sport_association=sport_association,
            archived=False,
            payment_date__isnull=False,
            paid=True
        )

        # Calculate metrics using annotations
        def get_period_totals(queryset, start_date, end_date):
            return queryset.filter(
                payment_date__range=(start_date, end_date)
            ).aggregate(
                income=Sum('amount', filter=Q(expense=False)) or 0,
                expenses=Sum('amount', filter=Q(expense=True)) or 0
            )

        # Get totals for each period
        today_totals = get_period_totals(base_queryset, today_start, today_end)
        month_totals = get_period_totals(base_queryset, last_month_start, today_end)
        year_totals = get_period_totals(base_queryset, year_start, today_end)

        return Response({
            'data': {
                'today_income': today_totals['income'],
                'today_expenses': today_totals['expenses'],
                'last_month_income': month_totals['income'],
                'last_month_expenses': month_totals['expenses'],
                'total_income': year_totals['income'],
                'total_expenses': year_totals['expenses']
            }
        }, status=status.HTTP_200_OK)

    elif widget == 'expiredPayments':

        days_before = 30
        days_after = 7
        limit = 100
        today = timezone.now().date()
        date_from = timezone.now() - timedelta(days=days_before)
        date_to = timezone.now() + timedelta(days=days_after)

        # Calculate the effective date (payment_date if exists, otherwise creation_date)
        effective_date = Coalesce('payment_date', 'creation_date')

        # Get payments with prefetched subscriptions
        payments = (
            Payment.objects
            .filter(
                paid=False,
                archived=False,
                associate__isnull=False,
                sport_association=sport_association,
                expense=False,
                amount__gt=0,
                creation_date__range=(date_from, date_to)
            )
            .select_related('associate')
            .annotate(
                effective_date=effective_date
            )
            .order_by('-creation_date', '-payment_date')[:limit]
        )

        # Calculate expired days in Python
        payments = list(payments)  # Materialize the queryset
        for payment in payments:
            payment.expired_days = (today - payment.creation_date.date()).days

        # Fetch all relevant subscriptions in a single query and store them in a dictionary
        subscriptions = Subscription.objects.filter(
            archived=False,
            sport_association=sport_association,
            associate__in=[p.associate_id for p in payments],
            start_date__lte=F('associate__payment__payment_date'),
            end_date__gte=F('associate__payment__payment_date')
        ).order_by('-creation_date')

        # Create a mapping with subscription objects
        subscription_map = {
            (sub.associate_id, sub.start_date, sub.end_date): sub
            for sub in subscriptions
        }

        # Process results
        expired_payments = []
        for payment in payments:
            payment_date = payment.payment_date or payment.creation_date
            payment_date = payment_date.date() if hasattr(payment_date, 'date') else payment_date

            # Find matching subscription
            matching_subscription = None
            for (assoc_id, start, end), subscription in subscription_map.items():
                if (assoc_id == payment.associate_id and
                        start <= payment_date <= end):
                    matching_subscription = subscription
                    break

            if matching_subscription:
                expired_payments.append({
                    "payment_id": payment.payment_id,
                    "expired_days": (today - payment.creation_date.date()).days,
                    "amount": payment.amount,
                    "payment_date": payment.payment_date,
                    "creation_date": payment.creation_date,
                    "title": payment.get_course_carnet_name(),
                    "date": (payment.payment_date or payment.creation_date).strftime('%d/%m/%Y'),
                    "associate": {
                        "associate_id": payment.associate.associate_id,
                        "first_name": payment.associate.first_name,
                        "last_name": payment.associate.last_name
                    },
                    "subscription_id": matching_subscription.subscription_id,
                })

        return Response({
            'data': {
                "expired_payments": sorted(expired_payments, key=lambda x: x['expired_days'])
            }
        }, status=status.HTTP_200_OK)
    else:
        pass

    return Response({'error: widget not found.'}, status.HTTP_404_NOT_FOUND)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def statistic_athlete_dashboard(request):
    logger.info(
        "Athlete dashboard request started",
        extra={
            "user_id": str(request.user.user_id),
            "username": request.user.username,
            "role": request.user.role,
        }
    )

    # check if user is athlete
    if request.user.role != User.ATHLETE:
        logger.warning(
            "Unauthorized dashboard access attempt",
            extra={
                "user_id": str(request.user.user_id),
                "role": request.user.role,
                "expected_role": User.ATHLETE,
            }
        )
        return Response({'error: forbidden.'}, status.HTTP_403_FORBIDDEN)

    try:
        instance_config = InstanceConfiguration.objects.select_related(
            'primary_association__user'
        ).first()
        if instance_config is None or instance_config.primary_association is None:
            logger.error(
                "Athlete dashboard unavailable: primary association is not configured",
                extra={"user_id": str(request.user.user_id)},
            )
            return Response(
                {'error': 'Primary sport association is not configured.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        sport_association = instance_config.primary_association
        user_subscriptions = Subscription.objects.filter(
            user=request.user,
            sport_association=sport_association,
        )
        logger.debug(
            "User subscriptions retrieved",
            extra={
                "user_id": str(request.user.user_id),
                "sport_association_id": str(sport_association.sport_association_id),
                "subscription_count": user_subscriptions.count(),
            }
        )

        # Get unique tax codes from associates of user's subscriptions
        tax_codes = user_subscriptions.filter(
            associate__tax_code__isnull=False
        ).values_list('associate__tax_code', flat=True).distinct()

        # Family subscriptions remain visible, but only inside this self-hosted association.
        subscriptions = Subscription.objects.filter(
            sport_association=sport_association,
        ).filter(
            Q(user=request.user) |
            Q(associate__user=request.user) |
            Q(associate__tax_code__in=tax_codes)
        ).distinct()

        logger.debug(
            "All related subscriptions retrieved",
            extra={
                "user_id": str(request.user.user_id),
                "total_subscriptions": subscriptions.count(),
                "tax_codes_found": len(tax_codes),
            }
        )

        active_subscriptions = [subscription for subscription in subscriptions if subscription.active]
        communication_message_transaction_posts = MessageTransaction.objects.filter(
            recipient=sport_association.sport_association_id,
            sent_on__gte=timezone.now() - timedelta(days=14),
            message__type=Message.INSIDE_APP
        ).order_by('-sent_on')[:5]

        data = [{
            "sport_association": {
                "denomination": sport_association.denomination,
                "sport_association_id": sport_association.sport_association_id,
                "user": {
                    "user_id": sport_association.user.user_id,
                    "avatar_image": sport_association.user.avatar_image,
                    "first_name": sport_association.user.first_name,
                    "last_name": sport_association.user.last_name,
                    "username": sport_association.user.username,
                },
                "review_url": sport_association.review_url,
                "review_url_enabled": sport_association.review_url_enabled,
                "communications": [
                    {
                        "message": communication.message.message,
                        "sent_on": communication.sent_on,
                    } for communication in communication_message_transaction_posts
                ]
            },
            "subscriptions": len(active_subscriptions)
        }]

        upcoming_lessons = []
        subscriptions_with_lessons = subscriptions.filter(archived=False)
        logger.debug(
            "Processing upcoming lessons",
            extra={
                "user_id": str(request.user.user_id),
                "active_subscriptions": subscriptions_with_lessons.count(),
            }
        )

        for subscription in subscriptions_with_lessons:
            # get upcoming lessons for each course
            course_subscriptions = CourseSubscription.objects.filter(
                subscription=subscription
            )
            for course_subscription in course_subscriptions:
                # get AttendanceRegistry for each course_subscription
                attendance_registries = AttendanceRegistry.objects.filter(
                    course=course_subscription.course)
                for attendance_registry in attendance_registries:
                    # get the next 7 days upcoming lessons
                    events = attendance_registry.events
                    for event in events:
                        try:
                            event_start = datetime.strptime(event['start'], '%Y-%m-%dT%H:%M:%S.%fZ')
                            now = datetime.now()
                            next_week = now + timedelta(days=7)

                            # get the next 7 days upcoming lessons
                            if now <= event_start <= next_week:
                                # let's get the attendance day
                                attendance_day = AttendanceDay.objects.filter(
                                    attendance_registry=attendance_registry,
                                    date__date=event_start.date(),
                                    auto_marked=False
                                ).first()
                                if attendance_day is None:
                                    continue

                                is_absent = False
                                is_present = False
                                if attendance_day.expected_absences and any(
                                        str(course_subscription.course_subscription_id) == attendee['course_subscription_id']
                                        for attendee in attendance_day.expected_absences):
                                    is_absent = True
                                if attendance_day.attendees and any(
                                        str(course_subscription.course_subscription_id) == attendee['course_subscription_id']
                                        for attendee in attendance_day.attendees):
                                    is_present = True

                                upcoming_lessons.append({
                                    "event_id": event['event_id'],
                                    "is_absent": is_absent,
                                    "is_present": is_present,
                                    "attendance_day_id": attendance_day.attendance_day_id,
                                    "title": event['title'],
                                    "start": event_start.date().strftime('%d/%m/%Y'),
                                    "course_subscription_id": course_subscription.course_subscription_id,
                                    "course_id": course_subscription.course.course_id,
                                    "course_title": course_subscription.course.title,
                                    "subscription": {
                                        "subscription_id": subscription.subscription_id,
                                        "associate": {
                                            "associate_id": subscription.associate.associate_id,
                                            "first_name": subscription.associate.first_name,
                                            "last_name": subscription.associate.last_name
                                        }
                                    }
                                })
                        except (ValueError, KeyError) as e:
                            logger.warning(
                                "Invalid event data in attendance registry",
                                extra={
                                    "user_id": str(request.user.user_id),
                                    "event_id": event.get('event_id', 'unknown'),
                                    "error": str(e),
                                    "error_type": type(e).__name__,
                                }
                            )
                            continue

        upcoming_lessons.sort(key=lambda x: datetime.strptime(x['start'], '%d/%m/%Y'))

        logger.info(
            "Athlete dashboard request completed successfully",
            extra={
                "user_id": str(request.user.user_id),
                "sport_associations_count": 1,
                "upcoming_lessons_count": len(upcoming_lessons),
            }
        )

        return Response({'data': data, 'upcoming_lessons': upcoming_lessons}, status=status.HTTP_200_OK)

    except Exception as e:
        logger.error(
            "Unexpected error in athlete dashboard",
            extra={
                "user_id": str(request.user.user_id),
                "error": str(e),
                "error_type": type(e).__name__,
            },
            exc_info=True
        )
        return Response(
            {'error': 'An error occurred while loading the dashboard'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def attendance_mark(request, uid):
    is_valid_uuid(uid)

    # get attendance_day_id if in the body
    attendance_day_id = request.data.get('attendance_day_id', None)


    # get subscription
    subscription = Subscription.objects.filter(
        subscription_id=uid).first()
    if subscription is None:
        return Response({"msg": "Iscrizione non trovata."}, status.HTTP_404_NOT_FOUND)

    available_attendance_days = []
    already_checked_in = False
    # today django timezone aware
    today = timezone.now()
    # make it rome
    today = today.astimezone(pytz.timezone('Europe/Rome'))
    course_sub = None

    if attendance_day_id is None:
        # get course_subscription
        course_subscriptions = CourseSubscription.objects.filter(
            subscription=subscription
        ).select_related('course').iterator(chunk_size=100)

        for course_subscription in course_subscriptions:
            course_sub = course_subscription
            # get the AttendanceRegistry
            attendance_registry = AttendanceRegistry.objects.filter(
                course=course_subscription.course
            ).first()

            if attendance_registry is None:
                continue

            # get the attendance day
            attendance_day = AttendanceDay.objects.filter(
                attendance_registry=attendance_registry,
                date__date=today.date()
            ).select_related('attendance_registry', 'attendance_registry__course').first()

            if attendance_day is None:
                continue

            if attendance_day.attendees and any(
                    str(course_subscription.course_subscription_id) == attendee['course_subscription_id']
                    for attendee in attendance_day.attendees):
                already_checked_in = True
                continue

            available_attendance_days.append(attendance_day)
    else:
        attendance_day = AttendanceDay.objects.filter(
            attendance_day_id=attendance_day_id
        ).select_related('attendance_registry', 'attendance_registry__course').first()

        if attendance_day is None:
            return Response({"msg": "Giorno di presenza non trovato."}, status.HTTP_404_NOT_FOUND)

        if attendance_day.attendees and any(
                str(subscription.course_subscription_id) == attendee['course_subscription_id']
                for attendee in attendance_day.attendees):
            already_checked_in = True
        available_attendance_days.append(attendance_day)
        course_sub = CourseSubscription.objects.filter(
            course=attendance_day.attendance_registry.course,
            subscription=subscription
        ).first()

    if len(available_attendance_days) == 0:
        if already_checked_in:
            return Response({"msg": f"Ciao {subscription.associate.get_full_name()}, hai già effettuato il check-in."}, status.HTTP_200_OK)
        return Response({"msg": f"Ciao {subscription.associate.get_full_name()}, non hai corsi oggi."}, status.HTTP_404_NOT_FOUND)

    # if there is only one attendance_day then we check-in
    if len(available_attendance_days) == 1:
        attendance_day = available_attendance_days[0]
        # append the course_subscription_id to the attendees
        if attendance_day.attendees is None:
            attendance_day.attendees = []

        carnets = CarnetSubscription.objects.filter(
            Q(course_subscription=course_sub) |
            Q(course_subscription__isnull=True),
            subscription=course_sub.subscription,
            disabled=False,
        ).order_by('-creation_date')
        carnet = None
        # check if there is a carnet with lessons left
        for c in carnets:
            if c.meta['lessons_left'] > 0:
                if carnet is None or (
                        carnet is not None and
                        carnet.meta['lessons_left'] > c.meta['lessons_left']
                ):
                    carnet = c

        if carnet is not None:
            if carnet.meta['lessons_left'] == 0:
                return Response({
                    "msg": f"Caro {subscription.associate.get_full_name()}, hai finito il carnet.",
                }, status=status.HTTP_412_PRECONDITION_FAILED)

            if carnet.payment is not None and carnet.payment.paid is False:
                return Response({
                    "msg": f"Caro {subscription.associate.get_full_name()}, non hai ancora pagato il carnet.",
                }, status=status.HTTP_412_PRECONDITION_FAILED)
            # update meta
            carnet.meta['lessons_left'] -= 1
            carnet.meta['lessons_registry'].append({
                "date": str(attendance_day.date),
                "attendance_day_id": str(attendance_day.attendance_day_id),
                "course": {
                    "id": str(course_sub.course.course_id),
                    "title": course_sub.course.title,
                },
                "title": attendance_day.title,
            })
            carnet.save()
        elif carnet is None and carnets.count() > 0:
            return Response({
                "msg": f"Caro {subscription.associate.get_full_name()}, hai finito il carnet.",
            }, status=status.HTTP_412_PRECONDITION_FAILED)

        attendance_day.attendees.append({
            "course_subscription_id": str(course_sub.course_subscription_id)
        })

        attendance_day.save()

        return Response({"msg": f"Check-in effettuato! Ciao {subscription.associate.get_full_name()}."}, status.HTTP_200_OK)

    data = []
    for attendance_day in available_attendance_days:
        data.append({
            "attendance_day_id": attendance_day.attendance_day_id,
            "date": attendance_day.date,
            "course": {
                "course_id": attendance_day.attendance_registry.course.course_id,
                "title": attendance_day.attendance_registry.course.title
            },
        })
    # if there are more than one attendance_day then we need to ask the user to select one, return 207 Multi-Status
    # with the available attendance_days
    return Response({"msg": f"Seleziona il corso per il check-in.", "data": data}, status.HTTP_207_MULTI_STATUS)




@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def attendance_day_delete(request, uid):

    is_valid_uuid(uid)

    # OLD filter
    # attendance_day = AttendanceDay.objects.filter(
    #     attendance_day_id=uid, auto_marked=False, date__gt=datetime.now()).first()
    attendance_day = AttendanceDay.objects.filter(attendance_day_id=uid).first()
    # check if attendance day exists
    if attendance_day is None:  # pragma: no cover
        return Response({'error: attendance day not found.'}, status.HTTP_404_NOT_FOUND)

    attendance_day.delete()

    data = {"msg": "attendance day deleted"}

    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def attendance_day_mark_absent(request, uid):

    is_valid_uuid(uid)

    attendance_day = AttendanceDay.objects.filter(
        attendance_day_id=uid).first()
    # check if attendance day exists
    if attendance_day is None:  # pragma: no cover
        return Response({'error: attendance day not found.'}, status.HTTP_404_NOT_FOUND)

    # get from request body the course_subscription_id
    course_subscription_id = request.data.get('course_subscription_id', None)
    if course_subscription_id is None:  # pragma: no cover
        return Response({'error: course_subscription_id is required.'}, status.HTTP_400_BAD_REQUEST)

    # check is valid uuid
    is_valid_uuid(course_subscription_id)

    absent = request.data.get('absent', True)

    if absent:
        # remove from attendees if there
        if attendance_day.attendees and any(
                str(course_subscription_id) == attendee['course_subscription_id']
                for attendee in attendance_day.attendees):
            attendance_day.attendees = [
                attendee for attendee in attendance_day.attendees
                if str(course_subscription_id) != attendee['course_subscription_id']
            ]

        # now add the course_subscription_id to the expected_absences if not already there
        if attendance_day.expected_absences and any(
                str(course_subscription_id) == attendee['course_subscription_id']
                for attendee in attendance_day.expected_absences):
            return Response({'error: course_subscription_id already in expected_absences.'}, status.HTTP_200_OK)

        # append the course_subscription_id to the expected_absences
        if attendance_day.expected_absences is None:
            attendance_day.expected_absences = []
        attendance_day.expected_absences.append({
            "course_subscription_id": course_subscription_id
        })
        attendance_day.save()

        data = {"msg": "added to expected_absences and removed from attendees."}
    else:
        # remove from expected_absences if there
        if attendance_day.expected_absences and any(
                str(course_subscription_id) == attendee['course_subscription_id']
                for attendee in attendance_day.expected_absences):
            attendance_day.expected_absences = [
                attendee for attendee in attendance_day.expected_absences
                if str(course_subscription_id) != attendee['course_subscription_id']
            ]

        # now add the course_subscription_id to the attendees if not already there
        if attendance_day.attendees and any(
                str(course_subscription_id) == attendee['course_subscription_id']
                for attendee in attendance_day.attendees):
            return Response({'error: course_subscription_id already in attendees.'}, status.HTTP_200_OK)

        # append the course_subscription_id to the attendees
        if attendance_day.attendees is None:
            attendance_day.attendees = []

        attendance_day.attendees.append({
            "course_subscription_id": course_subscription_id
        })
        attendance_day.save()
        data = {"msg": "removed from expected_absences and added to attendees."}

    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def statistic_report(request):


    if request.user.role != User.ASSOCIATION:
        return Response({'error: forbidden.'}, status.HTTP_403_FORBIDDEN)


    # get currentDate from query params
    # todo: currentDate value if specified should be checked
    current_date = request.query_params.get('currentDate', None)
    if current_date:
        # format is '2022-11-10T00:00:00Z'
        current_date = datetime.strptime(current_date, '%Y-%m-%d')
    else:
        current_date = datetime.now()
    if isinstance(current_date, str):
        # fixme: this crashes when , just select another year
        current_date = datetime.strptime(current_date, '%Y-%m-%d')
    elif isinstance(current_date, datetime):
        # remove time from datetime
        current_date = datetime.strptime(current_date.strftime('%Y-%m-%d'), '%Y-%m-%d')

    # calculate previous year
    last_date_from, _ = BalanceSheetData.get_range_from_year_and_starting_date(
        date=datetime.strptime(datetime.now().strftime('%Y-%m-%d'), '%Y-%m-%d'),
        starting_day=request.user.balance_sheet_start_day,
        starting_month=request.user.balance_sheet_start_month
    )

    # todo: this should return all the years not only this, otherwise we will have only two years
    #  and always relative from the requested one??
    available_years = [
        {
            'year': last_date_from.year - 1,
            'start_date': last_date_from.replace(year=last_date_from.year - 1)
        },
        {
            'year': last_date_from.year,
            'start_date': last_date_from
        }
    ]

    bs_month = request.user.balance_sheet_start_month
    months, months_number = BalanceSheetData.get_range_from_year_and_starting_date_in_months(bs_month)
    from_date, to_date = BalanceSheetData.get_range_from_year_and_starting_date(
        current_date, starting_day=request.user.balance_sheet_start_day, starting_month=bs_month)
    sport_association = SportAssociation.objects.get(user=request.user)

    # Subscriptions charts
    subscriptions_extract = \
        Subscription.objects.filter(
            sport_association=sport_association,
            archived=False,
            creation_date__range=(from_date, to_date),
        )\
        .annotate(month=TruncMonth('creation_date'))\
        .values('month')\
        .annotate(c=Count('subscription_id'))\
        .values('month', 'c')

    subscriptions_extract_approved = \
        Subscription.objects.filter(
            sport_association=sport_association,
            archived=False,
            creation_date__range=(from_date, to_date),
            status_flag=Subscription.ACCEPTED,
        ) \
            .annotate(month=TruncMonth('creation_date')) \
            .values('month') \
            .annotate(c=Count('subscription_id')) \
            .values('month', 'c')

    subscriptions_grouped = []
    for i in months_number:
        if subscriptions_extract.filter(month__month=i).exists():
            subscriptions_grouped.append(subscriptions_extract.filter(month__month=i)[0]['c'])
        else:
            subscriptions_grouped.append(0)

    subscriptions_grouped_approved = []
    for i in months_number:
        if subscriptions_extract_approved.filter(month__month=i).exists():
            subscriptions_grouped_approved.append(subscriptions_extract_approved.filter(month__month=i)[0]['c'])
        else:
            subscriptions_grouped_approved.append(0)

    # Courses charts
    count_courses = Course.objects.filter(
        sport_association=sport_association,
        status_flag=Course.ACTIVE,
        creation_date__range=(from_date, to_date),
    ).count()
    courses_id = Course.objects.filter(
        sport_association=sport_association,
        status_flag=Course.ACTIVE,
        creation_date__range=(from_date, to_date),
    ).values_list('course_id', flat=True)

    courses_extract = CourseSubscription.objects.filter(
        course__in=courses_id,
    ).values('course__title')\
        .annotate(c=Count('course_subscription_id'), revenue=Sum('course__fee'))\
        .values('course__title', 'c', 'revenue').iterator(chunk_size=100)

    courses_grouped = [0 for i in range(count_courses)]
    courses_revenue_grouped = [0 for i in range(count_courses)]
    courses = ['' for i in range(count_courses)]
    idx = 0
    for c in courses_extract:
        courses[idx] = c['course__title']
        courses_grouped[idx] = c['c']
        courses_revenue_grouped[idx] = c['revenue']
        idx += 1

    pie_subscriptions = [0, 0, 0, 0]
    subscriptions = Subscription.objects.filter(
        sport_association=sport_association,
        status_flag__lte=4
    ).values('status_flag').annotate(
        count=Count('subscription_id')
    ).order_by('status_flag')

    for subscription in subscriptions:
        pie_subscriptions[subscription['status_flag'] - 1] = subscription['count']

    # Payment charts - keeping original date behavior
    payments_extract = Payment.objects.filter(
        sport_association=sport_association,
        archived=False,
        expense=False,
        paid=True,
    ).filter(
        (Q(payment_date__range=(from_date, to_date)) & Q(payment_date__isnull=False)) |
        (Q(creation_date__range=(from_date, to_date)) & Q(payment_date__isnull=True))
    ).annotate(
        month=TruncMonth('creation_date')
    ).values('month').annotate(
        total=Sum('amount')
    ).order_by('month')

    # Convert to dictionary for O(1) lookup, but use month number as key
    payments_by_month = {
        payment['month'].month: payment['total']
        for payment in payments_extract
    }

    # Fill in the months array
    payments_grouped = [
        payments_by_month.get(month, 0)
        for month in months_number
    ]

    # Pie payments - no changes needed here
    pie_payments = Payment.objects.filter(
        sport_association=sport_association,
        archived=False,
        expense=False,
    ).filter(
        (Q(payment_date__range=(from_date, to_date)) & Q(payment_date__isnull=False)) |
        (Q(creation_date__range=(from_date, to_date)) & Q(payment_date__isnull=True))
    ).aggregate(
        paid_amount=Sum('amount', filter=Q(paid=True)),
        unpaid_amount=Sum('amount', filter=Q(paid=False))
    )

    pie_payments = [
        pie_payments['paid_amount'] or 0,
        pie_payments['unpaid_amount'] or 0
    ]

    # Subscriptions KPI
    average_subscriptions_month = mean(subscriptions_grouped_approved)
    total_subscriptions_associates = sum(subscriptions_grouped_approved)

    subscriptions_querylist = Subscription.objects.filter(
        sport_association=sport_association,
        archived=False,
        status_flag=Subscription.ACCEPTED
    ).select_related('associate')
    if subscriptions_querylist is not None and len(subscriptions_querylist) > 0:
        average_age = mean([sub.get_age() for sub in subscriptions_querylist])
        female = sum([1 if sub.associate.sex == Associate.FEMALE else 0 for sub in subscriptions_querylist])
        male = sum([1 if sub.associate.sex == Associate.MALE else 0 for sub in subscriptions_querylist])
        other = sum([1 if sub.associate.sex == Associate.OTHER else 0 for sub in subscriptions_querylist])
    else:
        average_age = 0
        female = 0
        male = 0
        other = 0

    # Payments KPI
    # TODO: refactor extracting the data from payments
    # total_subscriptions = Invoice.objects.filter(
    #     sport_association=sport_association,
    #     archived=False,
    #     creation_date__range=(from_date, to_date)
    # ).aggregate(Sum('membership_fee'))['membership_fee__sum']
    total_subscriptions = Payment.objects.filter(
        sport_association=sport_association,
        archived=False,
        subject=Payment.SUBSCRIPTION,
        paid=True
    ).filter(
        (Q(payment_date__range=(from_date, to_date)) & Q(payment_date__isnull=False)) |
        (Q(creation_date__range=(from_date, to_date)) & Q(payment_date__isnull=True))
    ).aggregate(Sum('amount'))['amount__sum']
    ## end refactor

    total_payments = Payment.objects.filter(
        sport_association=sport_association,
        archived=False,
    ).filter(
        (Q(payment_date__range=(from_date, to_date)) & Q(payment_date__isnull=False)) |
        (Q(creation_date__range=(from_date, to_date)) & Q(payment_date__isnull=True))
    ).count()

    # TODO: refactor extracting data form payments
    # total_activities = Invoice.objects.filter(
    #     sport_association=sport_association,
    #     archived=False,
    #     creation_date__range=(from_date, to_date)
    # ).aggregate(Sum('activity_fee'))['activity_fee__sum']
    total_activities = Payment.objects.filter(
        sport_association=sport_association,
        archived=False,
        subject=Payment.COURSE,
        paid=True,
    ).filter(
        (Q(payment_date__range=(from_date, to_date)) & Q(payment_date__isnull=False)) |
        (Q(creation_date__range=(from_date, to_date)) & Q(payment_date__isnull=True))
    ).aggregate(Sum('amount'))['amount__sum']
    ## end refactor


    total_invoices = Invoice.objects.filter(
        sport_association=sport_association,
        archived=False,
        creation_date__range=(from_date, to_date)
    ).count()

    data = {
        'available_years': available_years,
        "subscriptions": {
            "x": months,
            "y": subscriptions_grouped,
        },
        "kpi_subscriptions": {
            "average_subscriptions_month": "{:.2f}".format(average_subscriptions_month),
            "total_subscriptions": total_subscriptions_associates,
            "average_age": "{:.2f}".format(average_age),
            "female": female,
            "male": male,
            "other": other
        },
        "kpi_payments": {
            "total_subscriptions": total_subscriptions,
            "total_payments": total_payments,
            "total_activities": total_activities,
            "total_invoices": total_invoices
        },
        "pie_subscriptions": {
            "y": pie_subscriptions,
            "x": ["Non firmata", "In attesa", "Rifiutate", "Accettate"]
        },
        "payments": {
            "x": months,
            "y": payments_grouped,
        },
        "pie_payments": {
            "y": pie_payments,
            "x": ["Pagato", "Non pagato"],
        },
        "courses": {
            "y": courses_grouped,
            "x": courses,
            "revenue": {
                "y": courses_revenue_grouped,
            }
        }
    }

    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['GET'])
def health(request):
    # with configure_scope() as scope:
    #     if scope.transaction:
    #         scope.transaction.sampled = False
    return Response({'msg': 'I am good', 'version': settings.RUNNING_VERSION}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def check_inconsistencies(request):
    associates = Associate.objects.filter(user_id=request.user.user_id)

    missing_tutors = []

    for associate in associates:
        if associate.main_tutor is None:
            now = date.today()
            associate_date = str(associate.born_date or '')
            # get format of the date DD/MM/YYYY or YYYY-MM-DD
            if associate_date != '':
                if associate_date.find('/') != -1:
                    age = relativedelta(now, datetime.strptime(associate_date, '%d/%m/%Y')).years
                else:
                    age = relativedelta(now, datetime.strptime(associate_date, '%Y-%m-%d')).years
                if age < 18:
                    associate_data = AssociateSerializer(associate).data
                    associate_data['associate_id'] = associate.associate_id
                    missing_tutors.append(associate_data)
    data = {
        "inconsistencies": True if len(missing_tutors) > 0 else False,
        "missing_tutors": missing_tutors
    }
    return Response(data, status=status.HTTP_200_OK)
