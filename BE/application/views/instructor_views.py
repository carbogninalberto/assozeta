"""
@ copyright: Bakney srl
"""
import datetime
from decimal import Decimal

from dateutil import parser
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes

from application.models.courses_models import Course, CourseSubscription, CourseSubscriptionInstallment
from application.models.payment_models import PaymentCategory, Payment
from application.utils.instructors_utils import get_report
from core.middleware import IsAuthenticated
from application.models.user_models import SportAssociation, Instructor, InstructorHours, User

import logging

from application.serializers.user_serializers import InstructorSerializer, InstructorHoursSerializer
from application.utils.api_utils import is_valid_uuid, KTDatatablePagination

from application.printing_tasks import print_document_compensation

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
# @cache_endpoint('instructors_list', timeout=60 * 60 * 24 * 7)
def instructor_list(request):

    user = request.user

    instructors = Instructor.objects.filter(user=user).select_related(
        'user__sportassociation'
    )

    data = InstructorSerializer(instructors, many=True).data

    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def instructor_report(request):

    user: User = request.user
    sport_association = user.sport_association

    start_date = request.GET.get('start_date', None)
    end_date = request.GET.get('end_date', None)

    if start_date is None or end_date is None:
        return Response({'msg': 'Entrambe le date sono richieste.'}, status=status.HTTP_400_BAD_REQUEST)

    # convert the date to datetime format is DD/MM/YYYY
    if start_date is not None:
        start_date = parser.parse(start_date, dayfirst=True)
        # convert to datetime 00:00:00
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    if end_date is not None:
        end_date = parser.parse(end_date, dayfirst=True)
        # convert to datetime 23:59:59
        end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=0)


    results, report_file = get_report(
        str(user.user_id),
        start_date,
        end_date,
        str(sport_association.sport_association_id)
    )

    return Response({'data': {
        'results': results,
        'report_file': report_file
    }}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def instructor_add(request):
    logger.info("Creating new instructor", extra={
        'user_id': str(request.user.user_id),
        'action': 'instructor_add'
    })

    sport_association = SportAssociation.objects.get(user=request.user)
    if not sport_association:
        logger.error("Sport association not found for instructor creation", extra={
            'user_id': str(request.user.user_id)
        })
        raise ValidationError('Sport association not found.')

    data = request.data
    data['user'] = request.user.user_id
    if 'associated_user_id' in data and data['associated_user_id'] == '':
        data['associated_user_id'] = None
    # create instructor using serializer
    serializer = InstructorSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    instructor = serializer.save()

    logger.info("Instructor created successfully", extra={
        'user_id': str(request.user.user_id),
        'instructor_id': str(instructor.instructor_id)
    })

    return Response({'msg': 'instructor created'}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def instructor_info(request, uid):

    logger.info("instructor_info -> init -> user: {}".format(request.user.user_id))

    date_range = request.GET.get('date_range', None)

    if not is_valid_uuid(uid):
        return Response({'error': 'invalid instructor id'}, status=status.HTTP_400_BAD_REQUEST)

    instructor = Instructor.objects.filter(instructor_id=uid).first()

    if not instructor:
        return Response({'error': 'instructor not found'}, status=status.HTTP_404_NOT_FOUND)

    data = InstructorSerializer(instructor).data

    stats = {
        "hours": 0,
        "total_amount": 0,
        "total_amount_paid": 0,
        "total_amount_to_pay": 0,
    }

    if date_range is not None or date_range == '':
        # if payment range split pattern: 'YYYY/MM/DD al YYYY/MM/DD'
        date_from, date_to = date_range.split(' al ')
        try:
            date_from = datetime.datetime.strptime(date_from, '%Y/%m/%d')
            date_to = datetime.datetime.strptime(date_to, '%Y/%m/%d')
        except Exception as e:
            try:
                # try to parse the date in the format 'DD/MM/YYYY'
                date_from = datetime.datetime.strptime(date_from, '%d/%m/%Y')
                date_to = datetime.datetime.strptime(date_to, '%d/%m/%Y')
            except Exception as e:
                logger.error("Failed to parse date range", extra={
                    'user_id': str(request.user.user_id)
                }, exc_info=True)
                date_from, date_to = datetime.datetime.now() - timezone.timedelta(
                    days=365), datetime.datetime.now() + timezone.timedelta(days=1)
        start_date = date_from
        end_date = date_to
    else:
        # extract hours of this month
        start_date = datetime.datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_date = datetime.datetime.now().replace(day=1, month=datetime.datetime.now().month + 1, hour=0, minute=0, second=0, microsecond=0)
    # format date to be YYYY-MM-DD
    start_date = start_date.strftime('%Y-%m-%d')
    end_date = end_date.strftime('%Y-%m-%d')

    instructor_hours = InstructorHours.objects.filter(
        instructor=instructor,
        date__gte=start_date,
        date__lte=end_date
    )

    if len(instructor_hours) > 0:
        stats['hours'] = sum([x.amount for x in instructor_hours])
        stats['total_amount'] = sum([x.amount for x in instructor_hours])
        stats['total_amount_paid'] = sum([x.amount for x in instructor_hours if x.paid])
        stats['total_amount_to_pay'] = sum([x.amount for x in instructor_hours if not x.paid])

    logger.info("instructor_info -> ended -> user: {}".format(str(request.user.user_id)))

    return Response({'data': data, 'stats': stats}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def instructor_hours_list(request, uid):
    is_valid_uuid(uid)
    paginator = KTDatatablePagination()

    date_range = request.GET.get('query[date_range]', None)
    query = request.GET.get('query[generalSearch]', None)

    instructors = InstructorHours.objects.filter(instructor__user=request.user, instructor=uid).order_by('-date', '-creation_date')

    if date_range is not None and date_range != '':
        # if payment range split pattern: 'YYYY/MM/DD al YYYY/MM/DD'
        date_from, date_to = date_range.split(' al ')
        try:
            date_from = datetime.datetime.strptime(date_from, '%Y/%m/%d')
            date_to = datetime.datetime.strptime(date_to, '%Y/%m/%d')
        except Exception as e:
            try:
                # try to parse the date in the format 'DD/MM/YYYY'
                date_from = datetime.datetime.strptime(date_from, '%d/%m/%Y')
                date_to = datetime.datetime.strptime(date_to, '%d/%m/%Y')
            except Exception as e:
                logger.error("Failed to parse date range", extra={
                    'user_id': str(request.user.user_id)
                }, exc_info=True)
                date_from, date_to = datetime.datetime.now() - timezone.timedelta(
                    days=365), datetime.datetime.now() + timezone.timedelta(days=1)
        instructors = instructors.filter(date__range=[date_from, date_to])

    if query is not None:
        instructors = instructors.filter(
            Q(date__icontains=query) |
            Q(hours__icontains=query) |
            Q(amount__icontains=query) |
            Q(notes__icontains=query)
        )

    instructors = paginator.paginate_queryset(queryset=instructors, request=request)

    # TODO: move this to an event once the payment is paid
    # loop through the hours and check if they are paid
    for instructor in instructors:
        if instructor.payment and instructor.payment.paid:
            instructor.paid = True
            instructor.save()
        elif instructor.payment is None and instructor.document is not None:
            instructor.document.delete()

        if instructor.payment is not None and instructor.document is None:
            print_document_compensation.apply(
                args=[str(instructor.payment.payment_id), request.headers.get('authorization')])
    data = InstructorHoursSerializer(instructors, many=True).data

    return Response({'data': data, 'meta': {
        "total": paginator.page.paginator.count,
        "page": paginator.page.number,
        "pages": paginator.page.paginator.num_pages,
        "perpage": paginator.page.paginator.per_page,
        "rowIds": [instructor_hour.instructor_hours_id for instructor_hour in instructors]
    }}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def instructor_hours_calculate(request, uid):
    is_valid_uuid(uid)

    # Validate required inputs
    payment_range = request.data.get('period')
    courses = request.data.get('courses')
    percentage = request.data.get('percentage')

    if not courses:
        return Response({'error': 'courses is required'}, status=status.HTTP_400_BAD_REQUEST)

    # Validate instructor exists
    instructor = Instructor.objects.filter(instructor_id=uid).first()
    if not instructor:
        return Response({'error': 'instructor not found'}, status=status.HTTP_404_NOT_FOUND)

    # Validate course_id exists in each course object
    try:
        course_ids = [x['course_id'] for x in courses]
    except (KeyError, TypeError):
        return Response({'error': 'Invalid courses format'}, status=status.HTTP_400_BAD_REQUEST)

    courses_objs = Course.objects.filter(course_id__in=course_ids)

    # Parse payment range
    payment_from, payment_to = None, None
    if payment_range is not None and payment_range != '':
        try:
            payment_from, payment_to = payment_range.split(' al ')
        except ValueError:
            return Response({'error': 'Invalid period format'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            payment_from = datetime.datetime.strptime(payment_from, '%Y/%m/%d')
            payment_to = datetime.datetime.strptime(payment_to, '%Y/%m/%d')
        except ValueError:
            try:
                payment_from = datetime.datetime.strptime(payment_from, '%d/%m/%Y')
                payment_to = datetime.datetime.strptime(payment_to, '%d/%m/%Y')
            except ValueError as e:
                logger.exception(e)
                return Response({'error': 'Invalid date format'}, status=status.HTTP_400_BAD_REQUEST)

    if payment_from is None or payment_to is None:
        return Response({'msg': 'Periodo di pagamento non valido'}, status=status.HTTP_400_BAD_REQUEST)

    # Make timezone aware
    payment_from = payment_from.replace(tzinfo=timezone.get_current_timezone())
    payment_to = payment_to.replace(tzinfo=timezone.get_current_timezone())

    # Validate percentage
    if percentage is not None:
        try:
            percentage = float(percentage)
            if percentage < 0 or percentage > 100:
                return Response({'error': 'Percentage must be 0-100'}, status=status.HTTP_400_BAD_REQUEST)
        except (ValueError, TypeError):
            return Response({'error': 'Invalid percentage'}, status=status.HTTP_400_BAD_REQUEST)

    data = {
        "amount": 0,
        "calculation_data": []
    }

    for course in courses_objs:
        # Optimize with select_related to avoid N+1 queries
        course_subscriptions = CourseSubscription.objects.filter(
            course=course,
        ).select_related('subscription__associate')

        if course.course_type == Course.MEMBERSHIP_TYPE:
            for course_subscription in course_subscriptions:
                membership_payments = course_subscription.membership_payments.filter(
                    payment_date__range=[payment_from, payment_to],
                    paid=True,
                )
                for membership_payment in membership_payments:
                    data['amount'] += membership_payment.amount
                    data['calculation_data'].append({
                        'course': course.title,
                        'course_subscription': course_subscription.course_subscription_id,
                        'full_athlete_name': course_subscription.subscription.associate.get_full_name(),
                        'amount': membership_payment.amount,
                        'payment_date': membership_payment.payment_date
                    })
        elif course.multi_payments is True:
            for course_subscription in course_subscriptions:
                course_subscription_installments = CourseSubscriptionInstallment.objects.filter(
                    course_subscription=course_subscription,
                    payment__payment_date__range=[payment_from, payment_to],
                    payment__paid=True
                ).select_related('payment')
                for course_subscription_installment in course_subscription_installments:
                    data['amount'] += course_subscription_installment.payment.amount
                    data['calculation_data'].append({
                        'course': course.title,
                        'course_subscription': course_subscription.course_subscription_id,
                        'full_athlete_name': course_subscription.subscription.associate.get_full_name(),
                        'course_subscription_installment': course_subscription_installment.course_subscription_installment_id,
                        'amount': course_subscription_installment.payment.amount,
                        'payment_date': course_subscription_installment.payment.payment_date
                    })
        else:
            # Single payment courses
            for course_subscription in course_subscriptions:
                payment = course_subscription.payment
                if payment is None or not payment.paid:
                    continue

                if payment.payment_date and payment.amount > 0 and \
                        payment.payment_date >= payment_from and payment.payment_date <= payment_to:
                    data['amount'] += payment.amount
                    data['calculation_data'].append({
                        'course': course.title,
                        'course_subscription': course_subscription.course_subscription_id,
                        'full_athlete_name': course_subscription.subscription.associate.get_full_name(),
                        'amount': payment.amount,
                        'payment_date': payment.payment_date
                    })

    # Apply percentage if provided
    if percentage is not None:
        data['amount'] = float(data['amount']) * (percentage / 100)

    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def instructor_hours_add_compensation(request, uid):
    is_valid_uuid(uid)

    data = request.data
    if 'hours' not in data:
        return Response({'error': 'hours not found'}, status=status.HTTP_400_BAD_REQUEST)

    if 'payment_date' in data:
        try:
            data['payment_date'] = data['payment_date'].split('+')[0]
            # drop the timezone from the payment date
            data['payment_date'] = datetime.datetime.strptime(data['payment_date'], '%Y-%m-%dT%H:%M:%S.%f').replace(tzinfo=None)
        except Exception as e:
            data['payment_date'] = timezone.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            logger.exception(e)
    else:
        # set payment date to now if not set
        data['payment_date'] = timezone.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")


    # validate instructor
    instructor = Instructor.objects.filter(instructor_id=uid).first()
    if not instructor:
        return Response({'error': 'instructor not found'}, status=status.HTTP_404_NOT_FOUND)

    hours = InstructorHours.objects.filter(
        instructor=instructor,
        instructor_hours_id__in=data['hours'],
        payment__isnull=True,
        paid=False
    )

    # create the payment
    payment_category = PaymentCategory.objects.filter(name='Compensi e Rimborsi Spese').first()
    amount = sum([x.amount for x in hours])
    payment = Payment.objects.create(
        user=request.user,
        expense=True,
        amount=amount,
        description=f"Compensi e Rimborsi Spese per {instructor.first_name} {instructor.last_name}",
        paid=False,
        payment_date=data['payment_date'],
        payment_category=payment_category,
        instructor=instructor,
        sport_association=request.user.sport_association
    )
    notes = ''

    for i, hour in enumerate(hours):
        hour.payment = payment
        hour.save()
        if hour.compensation_type == 'hourly':
            notes += f"Compenso per il giorno {hour.date}, ore {hour.hours} con importo totale di {hour.amount}€\n"
        else:
            notes += f"Compenso per il giorno {hour.date}, importo di {hour.amount}€\n"
            notes += f"Note: {hour.notes if hour.notes and hour.notes != '' else '-'}\n"
            notes += "Nel dettaglio:\n\n"
            # get the calculation data from the compensation
            for idx, calculation_data in enumerate(hour.calculation_data):
                # get amount and convert to Decimal for calculation
                amount = Decimal(str(calculation_data['amount']))
                # calculate the amount based on the percentage
                percentage_amount = amount * (hour.percentage_billing / 100) if hour.percentage_billing else amount
                notes += (f"{idx+1}° Pagamento\nAtleta: {calculation_data['full_athlete_name']} \n"
                          f"Corso: {calculation_data['course']}\n"
                          f"Importo: {amount} € * {hour.percentage_billing} % = {percentage_amount} €\n\n")
        if i != len(hours) - 1:
            notes += '\n ---------------- \n\n'

    payment.notes = notes
    payment = payment.save()

    if payment:
        logger.info("Generating compensation document", extra={
            'user_id': str(request.user.user_id),
            'payment_id': str(payment.payment_id),
            'instructor_id': uid
        })
        print_document_compensation.apply(args=[str(payment.payment_id), request.headers.get('authorization')])

    logger.info("Compensation created successfully", extra={
        'user_id': str(request.user.user_id),
        'instructor_id': uid,
        'amount': float(amount)
    })
    return Response({'msg': 'compensation created.'}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def instructor_hours_add(request, uid):
    is_valid_uuid(uid)

    data = request.data

    # validate instructor
    instructor = Instructor.objects.filter(instructor_id=uid).first()
    if not instructor:
        return Response({'error': 'instructor not found'}, status=status.HTTP_404_NOT_FOUND)

    data['instructor'] = uid

    # validate with serializer
    serializer = InstructorHoursSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    serializer.save()

    return Response({'msg': 'hours added.'}, status=status.HTTP_200_OK)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def instructor_hours_update(request, uid, instructor_hours_id):
    is_valid_uuid(uid)
    is_valid_uuid(instructor_hours_id)

    # check that instructor is of the user
    instructor = Instructor.objects.filter(instructor_id=uid, user=request.user).first()

    instructor_hours = InstructorHours.objects.filter(
        instructor=instructor,
        instructor_hours_id=instructor_hours_id).first()
    if not instructor_hours:
        return Response({'error': 'hours not found'}, status=status.HTTP_404_NOT_FOUND)

    instructor_hours = InstructorHoursSerializer(instructor_hours, data=request.data, partial=True)

    if instructor_hours.is_valid(raise_exception=True):
        instructor_hours.save()
        return Response({'success': True}, status.HTTP_200_OK)

    return Response({'msg': 'hours updated.'}, status=status.HTTP_200_OK)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def instructor_hours_delete(request, uid, instructor_hours_id):
    is_valid_uuid(uid)
    is_valid_uuid(instructor_hours_id)

    # check that instructor is of the user
    instructor = Instructor.objects.filter(instructor_id=uid, user=request.user).first()

    instructor_hours = InstructorHours.objects.filter(
        instructor=instructor,
        instructor_hours_id=instructor_hours_id).first()
    if not instructor_hours:
        return Response({'error': 'hours not found'}, status=status.HTTP_404_NOT_FOUND)

    if instructor_hours.document:
        instructor_hours.document.delete()
    instructor_hours.delete()

    return Response({'msg': 'hours deleted.'}, status=status.HTTP_200_OK)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def instructor_update(request, uid):
    logger.info("Updating instructor", extra={
        'user_id': str(request.user.user_id),
        'instructor_id': uid,
        'action': 'instructor_update'
    })

    if not is_valid_uuid(uid):
        return Response({'error': 'invalid instructor id'}, status=status.HTTP_400_BAD_REQUEST)

    instructor = Instructor.objects.filter(instructor_id=uid).first()

    if not instructor:
        logger.error("Instructor not found for update", extra={
            'user_id': str(request.user.user_id),
            'instructor_id': uid
        })
        return Response({'error': 'instructor not found'}, status=status.HTTP_404_NOT_FOUND)

    data = request.data

    # check if empty user
    if not data.get('user'):
        data['user'] = request.user.user_id

    # TODO: custom users must be implemented

    # update instructor using serializer
    serializer = InstructorSerializer(instructor, data=data)
    serializer.is_valid(raise_exception=True)
    serializer.save()

    logger.info("Instructor updated successfully", extra={
        'user_id': str(request.user.user_id),
        'instructor_id': uid
    })

    return Response({'msg': 'instructor updated'}, status=status.HTTP_200_OK)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def instructor_delete(request, uid):
    logger.info("Deleting instructor", extra={
        'user_id': str(request.user.user_id),
        'instructor_id': uid,
        'action': 'instructor_delete'
    })

    if not is_valid_uuid(uid):
        return Response({'error': 'invalid instructor id'}, status=status.HTTP_400_BAD_REQUEST)

    instructor = Instructor.objects.filter(instructor_id=uid).first()

    if not instructor:
        logger.error("Instructor not found for deletion", extra={
            'user_id': str(request.user.user_id),
            'instructor_id': uid
        })
        return Response({'error': 'instructor not found'}, status=status.HTTP_404_NOT_FOUND)

    instructor.delete()

    logger.info("Instructor deleted successfully", extra={
        'user_id': str(request.user.user_id),
        'instructor_id': uid
    })

    return Response({'msg': 'instructor deleted'}, status=status.HTTP_200_OK)
