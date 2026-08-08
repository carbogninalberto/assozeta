"""
@ copyright: Bakney SRL
"""
from collections import defaultdict
from datetime import datetime

from dateutil.relativedelta import relativedelta
from django.db.models import Q, Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.timezone import make_aware
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.response import Response
from rest_framework import status, viewsets
from rest_framework.decorators import api_view, permission_classes

from application.models import AttendanceRegistry
from application.serializers.payment_serializers import PaymentSerializer
from application.signals import membership_created
from application.utils.printing import current_view_course, current_view_course_subscription
from application.utils.subscriptions_utils import add_course_to_subscription, add_course_subscription
from core.middleware import IsAuthenticated

from application.models.courses_models import Course, CourseSubscription, CourseSubscriptionInstallment, CourseTags, \
    CourseLocation
from application.models.payment_models import Payment
from application.models.subscriptions_models import Subscription
from application.models.user_models import SportAssociation, User, Instructor
from application.serializers.courses_serializers import CourseSerializer, CourseAddSerializer, CourseSubscriptionOverviewSerializer, CourseEventsSerializer, \
    CourseLocationSerializer

import logging

from application.utils.api_utils import is_valid_uuid, BalanceSheetData, KTDatatablePagination

logger = logging.getLogger(__name__)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def course_add(request):
    """
    API endpoint to add a new course
    :param request: data: {}
    :return: token, refresh token
    """
    logger.info("Creating new course", extra={
        'user_id': str(request.user.user_id),
        'action': 'course_add'
    })

    # getting body
    data = request.data

    # checking body for security/correctness
    if 'new_course' in data.keys() and \
            'subscriptions' in data.keys() and \
            len(data.keys()) == 2:

        sport_association = SportAssociation.objects.get(user=request.user)
        new_course_data = CourseAddSerializer(data=data['new_course'])
        subscriptions = data['subscriptions']

        if new_course_data.is_valid(raise_exception=True):
            # extract the new user data and validate id
            logger.debug('Validating new course data', extra={
                'user_id': str(request.user.user_id),
                'subscriptions_count': len(subscriptions)
            })
            # sanitize the data
            # sanitize input for XSS attacks and other security issues
            new_course_data.validated_data['title'] = new_course_data.validated_data['title'].replace("<", "&lt;").replace(">", "&gt;")
            # sanitize input for XSS attacks and other security issues
            new_course_data.validated_data['description'] = new_course_data.validated_data['description'].replace("<", "&lt;").replace(">", "&gt;")
            course = new_course_data.save()

            course.sport_association = sport_association
            course.save()

            logger.info("Course created successfully", extra={
                'user_id': str(request.user.user_id),
                'course_id': str(course.course_id),
                'course_title': course.title
            })

            if 'locations' in data['new_course'].keys():
                # add only the new locations
                for l in data['new_course']['locations']:
                    # get course location l
                    course_location = CourseLocation.objects.filter(course_location_id=l['course_location_id']).first()
                    if course_location is not None:
                        course.locations.add(course_location)

            for subscription in subscriptions:
                sub = Subscription.objects.get(subscription_id=subscription)
                course_subscription = CourseSubscription.objects.create(
                    course=course,
                    subscription=sub,
                    multi_payments=course.multi_payments
                )

                # check if there are installments
                if course.multi_payments:
                    # generate installments for the course subscription
                    for e in course.events:
                        course_event = CourseEventsSerializer(data=e)
                        course_event.is_valid(raise_exception=True)
                        # parse the event
                        event = course_event.validated_data

                        # generate installment if date is not yet passed
                        if event['payment_date'] >= course_subscription.creation_date.date() or \
                                request.user.full_installments_plan:
                            # generate payment for the installment
                            payment = Payment.objects.create(
                                user=sub.user,
                                associate=sub.associate,
                                amount=event['amount'],
                                subject=Payment.COURSE,
                                creation_date=event['payment_date'],
                                sport_association=sport_association,
                            )
                            payment.save()

                            installment = CourseSubscriptionInstallment.objects.create(
                                course_subscription=course_subscription,
                                amount=event['amount'],
                                id=event['id'],
                                payment_date=event['payment_date'],
                                payment=payment,
                            )
                            installment.save()
                else:
                    payment = Payment.objects.create(
                        user=sub.user,
                        associate=sub.associate,
                        amount=course.fee,
                        subject=Payment.COURSE,
                        sport_association=sport_association,
                    )
                    payment.save()

                    course_subscription.payment = payment
                course_subscription.save()
        else:
            raise ValidationError({"msg:": "not valid course information.", "details": new_course_data.errors})

        return Response({"status": "success"}, status=status.HTTP_200_OK)
    else:
        return Response({"msg": "validation errors."}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def course_update(request, uid):
    """
    API endpoint to update a course
    :param request: data: {}
    :return: token, refresh token
    """
    logger.info("Updating course", extra={
        'user_id': str(request.user.user_id),
        'course_id': uid,
        'action': 'course_update'
    })

    # validate the uid
    if not is_valid_uuid(uid):
        raise ValidationError({"msg:": "not valid course uid."})

    # getting body
    data = request.data

    course = Course.objects.filter(course_id=uid).first()
    if course is None:
        logger.error("Course not found for update", extra={
            'user_id': str(request.user.user_id),
            'course_id': uid
        })
        raise ValidationError({"msg:": "course not found."})

    # updating the course each field separately if it is in the body
    if 'title' in data.keys():
        # sanitize input for XSS attacks and other security issues
        data['title'] = data['title'].replace("<", "&lt;").replace(">", "&gt;")
        course.title = data['title']
    if 'description' in data.keys():
        # sanitize input for XSS attacks and other security issues
        data['description'] = data['description'].replace("<", "&lt;").replace(">", "&gt;")
        course.description = data['description']
    if 'one_fee' in data.keys() and course.one_fee_payment:
        course.one_fee = data['one_fee']
    if 'fee' in data.keys():
        course.fee = data['fee']
    if 'locations' in data.keys():
        # load the locations
        course.locations.clear()
        # add only the new locations
        for l in data['locations']:
            # get course location l
            course_location = CourseLocation.objects.filter(course_location_id=l['course_location_id']).first()
            if course_location is not None:
                course.locations.add(course_location)
    if 'multiple_quotes' in data.keys():
        course.multiple_quotes = data['multiple_quotes']
    if 'events' in data.keys() and course.multi_payments:
        # load the events
        course.events = []
        # add only the new events
        for e in data['events']:
            course_event = CourseEventsSerializer(data=e)
            course_event.is_valid(raise_exception=True)
            # parse the event
            event = course_event.validated_data
            # convert the date to string format DD/MM/YYYY
            event['payment_date'] = event['payment_date'].strftime("%d/%m/%Y")
            # conver the amount to string format 0.00
            event['amount'] = "{:.2f}".format(event['amount'])
            course.events.append(dict(event))

    if 'billed_duration_is_sport_season' in data.keys():
        course.billed_duration_is_sport_season = data['billed_duration_is_sport_season']
    if 'billed_frequency' in data.keys():
        course.billed_frequency = data['billed_frequency']
    if 'billed_from_subscription_date' in data.keys():
        course.billed_from_subscription_date = data['billed_from_subscription_date']
    if 'billed_from_day_of_month' in data.keys():
        course.billed_from_day_of_month = data['billed_from_day_of_month']
    if 'auto_renewal' in data.keys():
        course.auto_renewal = data['auto_renewal']
    if 'start_date' in data.keys():
        course.start_date = data['start_date'] if data['start_date'] is not None else None
    if 'end_date' in data.keys():
        course.end_date = data['end_date'] if data['end_date'] is not None else None

    course.save()

    logger.info("Course updated successfully", extra={
        'user_id': str(request.user.user_id),
        'course_id': uid
    })

    return Response({"status": "success"}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def course_tags_list(request):
    """
    API endpoint to get the list of tags for the subscriptions
    :param request: None
    :return: list of tags
    """

    tags = CourseTags.objects.filter(
        sport_association=request.user.sport_association
    ).order_by('tag_name')

    return Response(
        {
            'tags': [{'tag_id': tag.tag_id, 'tag_name': tag.tag_name} for tag in tags]
        },
        status=status.HTTP_200_OK
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def course_tags_add(request):
    """
    API endpoint to add a new tag for the subscriptions
    :param request: tag_name
    :return: list of tags
    """

    data = request.data
    tag_name = data.get('tag_name', None)

    if tag_name is None:
        return Response(
            {
                'msg': 'tag_name is required'
            },
            status=status.HTTP_400_BAD_REQUEST
        )

    tag = CourseTags.objects.create(
        tag_name=tag_name,
        sport_association=request.user.sport_association
    )

    return Response(
        {
            'tag_id': tag.tag_id,
            'tag_name': tag.tag_name
        },
        status=status.HTTP_200_OK
    )


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def course_tags_update(request, tag_id):
    """
    API endpoint to update a tag for the subscriptions
    :param request: tag_name
    :return: list of tags
    """

    data = request.data
    tag_name = data.get('tag_name', None)

    if tag_name is None:
        return Response(
            {
                'msg': 'tag_name is required'
            },
            status=status.HTTP_400_BAD_REQUEST
        )

    tag = CourseTags.objects.filter(
        tag_id=tag_id,
        sport_association=request.user.sport_association
    ).first()

    if tag is None:
        return Response(
            {
                'msg': 'tag not found'
            },
            status=status.HTTP_404_NOT_FOUND
        )

    tag.tag_name = tag_name
    tag.save()

    return Response(
        {
            'tag_id': tag.tag_id,
            'tag_name': tag.tag_name
        },
        status=status.HTTP_200_OK
    )


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def course_tags_delete(request, tag_id):
    """
    API endpoint to delete a tag for the subscriptions
    :param request: tag_name
    :return: list of tags
    """

    tag = CourseTags.objects.filter(
        tag_id=tag_id,
        sport_association=request.user.sport_association
    ).first()

    if tag is None:
        return Response(
            {
                'msg': 'tag not found'
            },
            status=status.HTTP_404_NOT_FOUND
        )

    tag.delete()

    return Response(
        {
            'msg': 'tag deleted'
        },
        status=status.HTTP_200_OK
    )


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def course_tags_assign(request, tag_id, course_id):
    """
    API endpoint to update a tag for the subscriptions
    :param request: tag_id, subscription_id
    :return: list of tags
    """

    data = request.data

    tag = CourseTags.objects.filter(
        tag_id=tag_id,
        sport_association=request.user.sport_association
    ).first()

    if tag is None:
        return Response(
            {
                'msg': 'tag not found'
            },
            status=status.HTTP_404_NOT_FOUND
        )

    course = Course.objects.filter(
        course_id=course_id,
        sport_association=request.user.sport_association
    ).first()

    if course is None:
        return Response(
            {
                'msg': 'course not found'
            },
            status=status.HTTP_404_NOT_FOUND
        )

    course.tags.add(tag)
    course.save()

    return Response(
        {
            'msg': 'tag assigned'
        },
        status=status.HTTP_200_OK
    )


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def course_tags_unassign(request, tag_id, course_id):
    """
    API endpoint to update a tag for the subscriptions
    :param request: tag_id, subscription_id
    :return: list of tags
    """

    data = request.data

    tag = CourseTags.objects.filter(
        tag_id=tag_id,
        sport_association=request.user.sport_association
    ).first()

    if tag is None:
        return Response(
            {
                'msg': 'tag not found'
            },
            status=status.HTTP_404_NOT_FOUND
        )

    course = Course.objects.filter(
        course_id=course_id,
        sport_association=request.user.sport_association
    ).first()

    if course is None:
        return Response(
            {
                'msg': 'course not found'
            },
            status=status.HTTP_404_NOT_FOUND
        )

    course.tags.remove(tag)
    course.save()

    return Response(
        {
            'msg': 'tag assigned'
        },
        status=status.HTTP_200_OK
    )


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def course_list(request):
    if request.user.role == User.ATHLETE:
        sport_association_id = request.GET.get('sport_association_id', None)
        if sport_association_id is None:
            raise PermissionDenied("Missing sport_association_id query param.")
        is_valid_uuid(sport_association_id)

        courses = Course.objects.filter(
            sport_association_id=sport_association_id,
            status_flag=Course.ACTIVE
        ).select_related('sport_association').prefetch_related(
            'tags', 'locations'
        ).order_by('-creation_date')

        data = CourseSerializer(courses, many=True).data
        return Response({'data': data}, status=status.HTTP_200_OK)

    paginator = KTDatatablePagination()
    subscription_id = request.GET.get('subscription_id', None)
    general_search = request.GET.get('query[generalSearch]', None)
    tags = request.GET.get('query[tags]', None)
    tags_and = request.GET.get('query[tags_and]', None)
    status_flag = request.GET.get('query[status_flag]', None)
    sort_field = request.GET.get('sort[field]', None)
    sort_type = request.GET.get('sort[sort]', None)
    all = request.GET.get('all', None)
    optimized = request.GET.get('optimized', None)

    sport_association = SportAssociation.objects.get(user=request.user)

    # Optimize with select_related and prefetch_related
    courses = Course.objects.select_related('sport_association').prefetch_related(
        Prefetch(
            'coursesubscription_set',
            queryset=CourseSubscription.objects.select_related(
                'subscription__associate'
            )
        )
    ).filter(sport_association=sport_association).order_by('-creation_date')

    if subscription_id:
        sub = Subscription.objects.get(subscription_id=subscription_id)
        # Cache course subscriptions query
        course_subs = list(CourseSubscription.objects.filter(subscription=sub))
        courses = courses.exclude(course_id__in=[c.course_id for c in course_subs])

    if general_search:
        courses = courses.filter(
            Q(title__icontains=general_search) |
            Q(description__icontains=general_search)
        )

    if tags:
        tags_ids = tags.split(',')
        logger.info(f"tags_ids: {tags_ids} | tags_and: {tags_and == '1'}, {tags_and}")
        if tags_and == '1':
            for tag_id in tags_ids:
                courses = courses.filter(tags__tag_id=tag_id)
        else:
            courses = courses.filter(tags__tag_id__in=tags_ids)

    if status_flag:
        courses = courses.filter(status_flag=status_flag)

    if sort_field and sort_type:
        if sort_type == 'asc':
            courses = courses.order_by(sort_field)
        else:
            courses = courses.order_by(f'-{sort_field}')

    if all is not None and all == '1':
        if optimized is not None and optimized == '1':
            return Response({'data': courses.values('course_id', 'title')}, status=status.HTTP_200_OK)
        return Response({'data': CourseSerializer(courses, many=True).data}, status=status.HTTP_200_OK)

    if request.method == 'POST':
        file, filename = current_view_course(sport_association, request.data, courses,
                                                    title="Corsi filtrati")
        return Response({
            "file": file,
            "filename": filename
        }, status=status.HTTP_200_OK)

    original_user = request.original_user if hasattr(request, 'original_user') else None
    if original_user and original_user.role == User.COLLABORATOR:
        attentance_registries = AttendanceRegistry.objects.filter(
            course__in=courses,
        )
        instructor = Instructor.objects.filter(associated_user_id=original_user.user_id).first()
        if instructor:
            instructor_id = str(instructor.instructor_id)
            # Cast JSONField to text and search within it
            attentance_registries = attentance_registries.filter(events__iregex=instructor_id)
            courses = courses.filter(attendanceregistry__in=attentance_registries)

    courses = paginator.paginate_queryset(queryset=courses, request=request)


    current_date_from, current_date_to = BalanceSheetData.get_range_from_year_and_starting_date(
        date=timezone.now(),
        starting_day=request.user.balance_sheet_start_day,
        starting_month=request.user.balance_sheet_start_month
    )

    data = {}
    meta = {
        "total": paginator.page.paginator.count,
        "page": paginator.page.number,
        "pages": paginator.page.paginator.num_pages,
        "perpage": paginator.page.paginator.per_page,
        "rowIds": [course.course_id for course in courses]
    }
    courses = CourseSerializer(courses, many=True).data

    # Cache athletes query results
    athletes_by_course = defaultdict(list)
    course_ids = [course['course_id'] for course in courses]

    all_athletes = CourseSubscription.objects.filter(
        course_id__in=course_ids
    ).select_related('subscription__associate')

    for athlete in all_athletes:
        athletes_by_course[str(athlete.course_id)].append(athlete)

    for idx, course in enumerate(courses):
        data[idx] = course
        course_creation_date = make_aware(datetime.strptime(course['creation_date'].split('T')[0], "%Y-%m-%d"))
        data[idx]['current_year'] = 1 if current_date_from <= course_creation_date <= current_date_to else 0
        data[idx]['athletes'] = {}

        try:
            for idx_athlete, athlete in enumerate(athletes_by_course[course['course_id']]):
                data[idx]['athletes'][idx_athlete] = {
                    "paid": athlete.paid,
                    "creation_date": athlete.creation_date
                }
                if athlete.subscription:
                    data[idx]['athletes'][idx_athlete]['subscription'] = {
                        "subscription_id": athlete.subscription.subscription_id,
                        "associate": {
                            "first_name": athlete.subscription.associate.first_name,
                            "last_name": athlete.subscription.associate.last_name,
                            "born_date": athlete.subscription.associate.born_date,
                            "sex": athlete.subscription.associate.sex,
                        }
                    }
        except Exception as e:
            logger.error("Error in getting athletes", extra={
                'user_id': str(request.user.user_id),
                'course_id': course['course_id']
            }, exc_info=True)

    logger.info("course_list -> ended -> user: {}".format(request.user.user_id))
    return Response({'data': data, 'meta': meta}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def course_enable(request, uid):

    is_valid_uuid(uid)

    logger.info("course_enable")
    sport_association = SportAssociation.objects.get(user=request.user)
    course = Course.objects.filter(course_id=uid).first()
    if course is None:
        return Response({'msg': 'Course not found.'}, status=status.HTTP_404_NOT_FOUND)
    if course.sport_association.sport_association_id != sport_association.sport_association_id:  # pragma: no cover
        raise PermissionDenied("User not allowed.")

    if course.status_flag != Course.ACTIVE:
        course.status_flag = course.ACTIVE
        course.save()
    else:  # pragma: no cover
        raise PermissionDenied("User cannot change the status of the subscription.")

    data = {"msg": "course enabled."}

    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def course_disable(request, uid):

    is_valid_uuid(uid)

    logger.info("course_disable")
    sport_association = SportAssociation.objects.get(user=request.user)
    course = Course.objects.filter(course_id=uid).first()
    if course is None:
        return Response({'msg': 'Course not found.'}, status=status.HTTP_404_NOT_FOUND)
    if course.sport_association.sport_association_id != sport_association.sport_association_id:  # pragma: no cover
        raise PermissionDenied("User not allowed.")

    if course.status_flag != Course.DRAFT:
        course.status_flag = course.DRAFT
        course.save()
    else:  # pragma: no cover
        raise PermissionDenied("User cannot change the status of the subscription.")

    data = {"msg": "course disabled."}

    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def course_pin(request, uid):

    is_valid_uuid(uid)

    sport_association = SportAssociation.objects.get(user=request.user)
    course = Course.objects.filter(course_id=uid).first()
    if course is None:
        return Response({'msg': 'Course not found.'}, status=status.HTTP_404_NOT_FOUND)
    if course.sport_association.sport_association_id != sport_association.sport_association_id:  # pragma: no cover
        raise PermissionDenied("User not allowed.")

    course.pinned = not course.pinned
    course.save()

    msg = "corso messo in evidenza." if course.pinned else "corso tolto dall'evidenza."
    data = {f"msg": msg}

    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def course_delete(request, uid):

    is_valid_uuid(uid)

    logger.info("course_delete")
    sport_association = SportAssociation.objects.get(user=request.user)
    course = Course.objects.filter(course_id=uid).first()
    if course is None:
        return Response({'msg': 'Course not found.'}, status=status.HTTP_404_NOT_FOUND)
    if course.sport_association.sport_association_id != sport_association.sport_association_id:  # pragma: no cover
        raise PermissionDenied("User not allowed.")

    course.delete()

    data = {"msg": "course deleted."}

    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def course_overview(request, uid):

    is_valid_uuid(uid)

    logger.info("course_disable")
    course = Course.objects.select_related(
        'sport_association'
    ).prefetch_related(
        'tags', 'locations'
    ).filter(
        course_id=uid,
        sport_association__user=request.user
    ).first()

    if not course:
        raise PermissionDenied("Course not found")

    data = {
        "course": CourseSerializer(course).data,
    }

    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def course_overview_delete(request, uid, uid_subscription):

    is_valid_uuid(uid)
    is_valid_uuid(uid_subscription)

    logger.info("course_overview_delete")
    sport_association = SportAssociation.objects.get(user=request.user)
    course = Course.objects.filter(course_id=uid).first()
    if course.sport_association.sport_association_id != sport_association.sport_association_id:  # pragma: no cover
        raise PermissionDenied("User not allowed.")

    subscription = Subscription.objects.filter(subscription_id=uid_subscription).first()
    course_sub = CourseSubscription.objects.filter(
        course=course,
        subscription=subscription
    ).first()

    # get installments from request.data and delete them
    data = request.data
    if 'installments' in data:
        installments = data['installments']
        for installment in installments:
            inst_to_del = CourseSubscriptionInstallment.objects.filter(
                course_subscription=course_sub,
                course_subscription_installment_id=installment
            ).first()
            if inst_to_del is not None:
                # delete the payment if it exists
                if inst_to_del.payment is not None and not inst_to_del.payment.paid:
                    try:
                        inst_to_del.payment.delete()
                    except Exception as e:  # pragma: no cover
                        logger.error("Error in deleting payment", extra={
                            'user_id': str(request.user.user_id),
                            'installment_id': str(inst_to_del.course_subscription_installment_id)
                        }, exc_info=True)
                inst_to_del.delete()
    else:
        # delete all the unpaid installments and payments
        installments = CourseSubscriptionInstallment.objects.filter(
            course_subscription=course_sub,
            paid=False,
        )
        for installment in installments:
            if installment.payment is not None and not installment.payment.paid:
                try:
                    installment.payment.delete()
                except Exception as e:
                    logger.error("Error in deleting payment", extra={
                        'user_id': str(request.user.user_id),
                        'installment_id': str(installment.course_subscription_installment_id)
                    }, exc_info=True)
            installment.delete()

    course_sub.delete()

    data = {"msg": "associate deleted."}

    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def course_overview_add(request, uid, uid_subscription):
    """
    API endpoint to add a new list of users to a course
    :param request: data: {}
    :param uid: course_id
    :param uid_subscription: subscription_id
    :return: token, refresh token
    """


    is_valid_uuid(uid)
    is_valid_uuid(uid_subscription)

    data = request.data

    logger.info("course_overview_add")
    sport_association = SportAssociation.objects.filter(user=request.user).first()
    course = Course.objects.filter(course_id=uid).first()
    sub = Subscription.objects.filter(subscription_id=uid_subscription).first()
    is_athlete = request.user.role == User.ATHLETE
    if sport_association is not None and \
            course.sport_association.sport_association_id != sport_association.sport_association_id:  # pragma: no cover
        raise PermissionDenied("User not allowed.")
    else:
        sport_association = sub.sport_association
        if sport_association is None:  # pragma: no cover
            raise Exception("Error in adding user.")

    data = add_course_to_subscription(course, sub, sport_association, data=data, is_athlete=is_athlete)

    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def course_installment_make_payment(request, uid):
    is_valid_uuid(uid)

    # get the course installment
    installment = CourseSubscriptionInstallment.objects.filter(
        course_subscription_installment_id=uid
    ).first()

    if installment is None:
        raise Exception("Installment not found.")

    # get the course subscription
    course_subscription = installment.course_subscription
    course = course_subscription.course

    # make the payment
    payment = Payment.objects.create(
        user=course_subscription.subscription.user,
        associate=course_subscription.subscription.associate,
        amount=installment.amount,
        subject=Payment.COURSE,
        creation_date=installment.payment_date,
        sport_association=course.sport_association,
    )

    payment.save()

    # update the installment
    installment.payment = payment
    installment.save()

    serialized_payment = PaymentSerializer(payment).data

    # return the payment
    return Response({'data': serialized_payment}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def course_overview_update(request, uid, uid_subscription):
    """
    API endpoint to update a user in a course
    :param request: data: {}
    :param uid: course_id
    :param uid_subscription: subscription_id
    :return: token, refresh token
    """

    is_valid_uuid(uid)
    is_valid_uuid(uid_subscription)

    data = request.data

    logger.info("course_overview_update")
    sport_association = SportAssociation.objects.filter(user=request.user).first()
    course = Course.objects.filter(course_id=uid).first()
    course_subscription = CourseSubscription.objects.filter(
        subscription__subscription_id=uid_subscription,
        course__course_id=uid
    ).first()
    sub = Subscription.objects.filter(subscription_id=uid_subscription).first()
    if course_subscription is None:  # pragma: no cover
        raise Exception("Error in updating user.")

    is_athlete = request.user.role == User.ATHLETE
    if is_athlete is False and sport_association is not None and \
            course.sport_association.sport_association_id != sport_association.sport_association_id:  # pragma: no cover
        raise PermissionDenied("User not allowed.")

    if is_athlete and sub.user.user_id != request.user.user_id:  # pragma: no cover
        raise PermissionDenied("User not allowed.")

    if is_athlete:
        sport_association = sub.sport_association

    if 'multi_payments' in data and data['multi_payments']:
        # if the course has multi payments
        if course_subscription.multi_payments:
            # delete all upcoming unpaid installments and payments
            installments = CourseSubscriptionInstallment.objects.filter(
                course_subscription=course_subscription
            ).filter(
                Q(payment__paid=False) | Q(payment__isnull=True),
            )
            for installment in installments:
                if installment.payment is not None:
                    installment.payment.delete()
                    installment.payment = None
                installment.delete()


        # check if the course has multi payments
        if not course_subscription.multi_payments:
            # delete unpaid payment otherwise they become orphan payments
            if course_subscription.payment is not None and \
                    not course_subscription.payment.paid:
                course_subscription.payment.delete()
            course_subscription.payment = None
            # change to multi payments and generate installments
            course_subscription.multi_payments = True
            course_subscription.one_fee_payment = False
            course_subscription.save()

        # generate installments for the course subscription
        for idx, e in enumerate(course.events or []):
            course_event = CourseEventsSerializer(data=e)
            try:
                course_event.is_valid(raise_exception=True)
            except Exception as e:
                logger.error("Error in validating course event", extra={
                    'user_id': str(request.user.user_id),
                    'course_id': str(course.course_id),
                    'event_index': idx
                }, exc_info=True)
                continue
            # parse the event
            event = course_event.validated_data
            # generate installment if date is not yet passed
            if event['payment_date'] >= course_subscription.creation_date.date() \
                    or data['all'] is True:
                # generate payment for the installment
                payment = Payment.objects.create(
                    user=sub.user,
                    associate=sub.associate,
                    amount=event['amount'],
                    subject=Payment.COURSE,
                    creation_date=event['payment_date'],
                    description=f"Rata n.{int(idx) + 1} del {event['payment_date'].strftime('%d/%m/%y')} ({course.title})",
                    sport_association=sport_association,
                    meta={
                        'description': f"Rata n.{int(idx) + 1} del {event['payment_date'].strftime('%d/%m/%y')} ({course.title})",
                        'course_id': str(course.course_id),
                        'course_title': str(course.title),
                        'course_subscription_id': str(course_subscription.course_subscription_id),
                        'payment_date': str(event['payment_date']),
                    }
                )
                payment.save()

                installment = CourseSubscriptionInstallment.objects.create(
                    course_subscription=course_subscription,
                    amount=event['amount'],
                    id=event['id'],
                    payment_date=event['payment_date'],
                    payment=payment,
                )
                installment.save()

    elif 'one_fee_payment' in data and data['one_fee_payment']:
        # check if the course has multi payments
        if course_subscription.multi_payments:
            # delete all upcoming unpaid installments and payments
            installments = CourseSubscriptionInstallment.objects.filter(
                course_subscription=course_subscription,
            ).filter(
                Q(payment__paid=False) | Q(payment__isnull=True),
            )
            for installment in installments:
                if installment.payment is not None:
                    installment.payment.delete()
                    installment.payment = None
                installment.delete()

            # set course to one fee
            course_subscription.multi_payments = False
            course_subscription.one_fee_payment = True

            # generate payment for the course subscription
            payment = Payment.objects.create(
                user=sub.user,
                associate=sub.associate,
                amount=course.one_fee,
                subject=Payment.COURSE,
                sport_association=sport_association,
                meta={
                    'one_fee': 'true',
                    'description': f"Pagamento in un'unica soluzione per {course.title}",
                    'course_id': str(course.course_id),
                    'course_title': str(course.title),
                    'course_type': str(course.course_type),
                    'payment_date': str(course_subscription.creation_date.date())
                }
            )
            payment.save()

            course_subscription.payment = payment
            course_subscription.save()

    data = {"msg": "course subscription update."}

    return Response({'data': data}, status=status.HTTP_200_OK)


class CourseLocationViewSet(viewsets.ModelViewSet):
    queryset = CourseLocation.objects.all()
    serializer_class = CourseLocationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Filter queryset by sport association
        sport_association = SportAssociation.objects.get(user=self.request.user)
        return CourseLocation.objects.filter(sport_association=sport_association)

    def perform_create(self, serializer):
        sport_association = SportAssociation.objects.get(user=self.request.user)
        serializer.save(sport_association=sport_association)

    def add(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def delete(self, request, pk=None):
        instance = self.get_object()
        sport_association = SportAssociation.objects.get(user=request.user)

        if instance.sport_association.sport_association_id != sport_association.sport_association_id:
            raise PermissionDenied("User not allowed.")

        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def update(self, request, pk=None):
        instance = self.get_object()
        sport_association = SportAssociation.objects.get(user=request.user)

        if instance.sport_association.sport_association_id != sport_association.sport_association_id:
            raise PermissionDenied("User not allowed.")

        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def list(self, request):
        queryset = self.get_queryset()  # This will use the filtered queryset
        serializer = self.get_serializer(queryset, many=True)
        return Response({"data": serializer.data}, status=status.HTTP_200_OK)


class CourseSubscriptionViewSet(viewsets.ModelViewSet):
    queryset = CourseSubscription.objects.all()
    serializer_class = CourseSubscriptionOverviewSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self, course_id=None, sport_association=None, subscription_id=None, include_deleted=False):
        """
        Filter queryset based on the authenticated user's subscription
        and exclude soft-deleted records unless specified
        """
        base_query = CourseSubscription.objects

        if not include_deleted:
            base_query = base_query.filter(deleted=False)
        else:
            base_query = base_query.all_objects()

        if course_id is not None:
            base_query = base_query.filter(course_id=course_id)

        if subscription_id is not None:
            base_query = base_query.filter(subscription_id=subscription_id)

        return base_query.filter(
            course__sport_association=sport_association
        )

    def get_object(self):
        """
        Override get_object to include deleted objects when performing delete operations
        """
        queryset = self.get_queryset(
            sport_association=self.request.user.sport_association,
            include_deleted=self.action == 'delete'
        )

        # Get the object from the filtered queryset
        obj = get_object_or_404(queryset, pk=self.kwargs["pk"])

        # May raise a permission denied
        self.check_object_permissions(self.request, obj)

        return obj

    def perform_create(self, serializer, is_athlete=False):
        """
        Override perform_create to handle payment creation
        """
        instances = []
        is_many = isinstance(serializer.validated_data, list)

        if is_many:
            for item in serializer.validated_data:
                course = item['course']
                if course.course_type in [Course.DEFAULT_TYPE, Course.MULTIPLE_QUOTES_TYPE]:
                    instance = serializer.child.create(item)
                    instance = add_course_subscription(
                        course_subscription=instance,
                        sport_association=self.request.user.sport_association,
                        data=item  # Pass individual item data instead of request.data
                    )
                    instances.append(instance)
                else:
                    instance = serializer.child.create(item)
                    instances.append(instance)
                    # emit signal membership_created
                    membership_created.send(
                        sender=self.__class__,
                        instance=instance,
                    )
        else:
            course = serializer.validated_data['course']
            if course.course_type in [Course.DEFAULT_TYPE, Course.MULTIPLE_QUOTES_TYPE]:
                instance = serializer.create(serializer.validated_data)
                instance = add_course_subscription(
                    course_subscription=instance,
                    sport_association=self.request.user.sport_association,
                    data=serializer.validated_data
                )
                instances.append(instance)
            else:
                if is_athlete:
                    # get course
                    course = Course.objects.get(course_id=serializer.validated_data['course'])
                    # prefill the default values for auto-renewal and membership_fee
                    serializer.validated_data['auto_renewal'] = course.auto_renewal
                    serializer.validated_data['membership_fee'] = course.fee
                    serializer.validated_data['billed_frequency'] = course.billed_frequency
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
                        serializer.validated_data['billed_from'] = billed_from
                        serializer.validated_data['billed_until'] = billed_until
                        serializer.validated_data['billed_from_day_of_month'] = course.sport_association.user.subscription_start_day
                    elif course.billed_from_subscription_date:
                        serializer.validated_data['billed_from'] = serializer.validated_data['creation_date']
                        serializer.validated_data['billed_until'] = serializer.validated_data['creation_date'] + relativedelta(months=course.billed_frequency)
                    else:
                        serializer.validated_data['billed_from'] = datetime.strptime(
                            f"{course.billed_from_day_of_month}/{timezone.now().month}/{timezone.now().year}", "%d/%m/%Y"
                        )
                        serializer.validated_data['billed_until'] = serializer.validated_data['billed_from'] + relativedelta(months=course.billed_frequency)

                instance = serializer.create(serializer.validated_data)
                instances.append(instance)
                # emit signal membership_created
                membership_created.send(
                    sender=self.__class__,
                    instance=instance,
                )

        return instances

    def add(self, request):
        """
        Create a new course subscription
        """
        is_many = isinstance(request.data, list)
        # check if request user is sport association
        serializer = self.get_serializer(data=request.data, many=is_many)
        serializer.is_valid(raise_exception=True)
        instances = self.perform_create(serializer, is_athlete=request.user.role == User.ATHLETE)
        response_serializer = self.get_serializer(instances, many=True)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

    def bulk_delete(self, request):
        """
        Soft delete multiple course subscriptions
        """
        data = request.data
        course_subscription_ids = data.get('course_subscription_ids', [])

        CourseSubscription.objects.filter(
            course_subscription_id__in=course_subscription_ids,
            course__sport_association=request.user.sport_association
        ).delete()

        return Response(status=status.HTTP_204_NO_CONTENT)

    def delete(self, request, pk=None):
        """
        Soft delete a course subscription
        """
        instance = self.get_object()
        if instance.course.sport_association != request.user.sport_association:
            raise PermissionDenied("User not allowed to delete this course subscription")
        instance.delete()  # This calls the model's custom delete method for soft delete


        return Response(status=status.HTTP_204_NO_CONTENT)

    def update(self, request, pk=None):
        """
        Update a course subscription
        """
        instance = self.get_object()
        old_billed_From = instance.billed_from
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        # update the related payment for instances of type membership
        if instance.type == CourseSubscription.MEMBERSHIP_TYPE:
            for payment in instance.membership_payments.all():
                if not payment.paid and \
                    str(payment.meta.get('billed_from')) == old_billed_From.strftime('%Y-%m-%d'):
                    payment.amount = serializer.validated_data['membership_fee']
                    payment.creation_date = serializer.validated_data['billed_from']
                    payment.payment_date = serializer.validated_data['billed_from']
                    payment.description = f"Abbonamento: {serializer.validated_data['course'].title} dal {serializer.validated_data['billed_from'].strftime('%d/%m/%Y')} al {serializer.validated_data['billed_until'].strftime('%d/%m/%Y')}"
                    payment.meta = {
                        'description': f"Abbonamento: {serializer.validated_data['course'].title} dal {serializer.validated_data['billed_from'].strftime('%d/%m/%Y')} al {serializer.validated_data['billed_until'].strftime('%d/%m/%Y')}",
                        'course_id': str(serializer.validated_data['course'].course_id),
                        'course_title': serializer.validated_data['course'].title,
                        'course_subscription_id': str(serializer.validated_data['course_subscription_id']),
                        'billed_from': serializer.validated_data['billed_from'].strftime('%Y-%m-%d'),
                        'billed_until': serializer.validated_data['billed_until'].strftime('%Y-%m-%d'),
                        'amount': str(serializer.validated_data['membership_fee'])

                    }
                    payment.save()
                    break

        return Response(serializer.data, status=status.HTTP_200_OK)

    def list(self, request):
        """
        List all course subscriptions for the authenticated user
        """
        deleted = request.GET.get('include_deleted', 'false').lower() == 'true'
        queryset = self.get_queryset(
            request.GET.get('course_id'),
            request.user.sport_association,
            request.GET.get('subscription_id'),
            include_deleted=deleted
        )

        # check if post
        if request.method == 'POST':
            file, filename = current_view_course_subscription(request.user.sport_association, request.data, queryset, title="Iscrizioni ai corsi")
            return Response({
                "file": file,
                "filename": filename
            }, status=status.HTTP_200_OK)

        serializer = self.get_serializer(queryset, many=True)
        return Response({"data": serializer.data}, status=status.HTTP_200_OK)