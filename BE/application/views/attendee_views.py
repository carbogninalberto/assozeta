"""
@ copyright: Bakney SRL
"""

from django.http import HttpResponse
from django.utils.dateparse import parse_datetime
from icalendar import Calendar, Event
from rest_framework.exceptions import ValidationError, NotFound
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes

from application.utils.attendance_utils import get_extended_prop_for_attendance_day, map_unlinked_attendance, \
    get_course_colors
from core.middleware import IsAuthenticated
from rest_framework.permissions import AllowAny

from application.models import AttendanceRegistry, AttendanceDay, GlobalCalendarEvents, Reminders
from application.models.carnet_models import CarnetSubscription
from application.models.courses_models import Course, CourseSubscription
from application.models.subscriptions_models import Subscription
from application.models.user_models import SportAssociation, User, Instructor
from datetime import datetime, timedelta

import logging

from application.permissions import IsProPlanAssociation, IsTeamsPlanAssociation, IsAthleteUser
from application.utils.api_utils import is_valid_uuid, ColorPalette, get_seconds_from_reminder_units, \
    REMINDER_UNITS_MAP_TEXT
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)


@api_view(['POST', 'DELETE'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def calendar_update(request, uid):
    """
    API endpoint to update the calendar of a course
    """


    is_valid_uuid(uid)

    logger.info("calendar_update -> init -> user: {}".format(request.user.user_id))

    if request.method == 'DELETE':
        logger.info("calendar_update -> delete -> user: {}".format(request.user.user_id))
        sport_association = SportAssociation.objects.get(user=request.user)
        course = Course.objects.filter(sport_association=sport_association, course_id=uid).first()
        course_attendance_registry = AttendanceRegistry.objects.filter(course=course).first()

        if course_attendance_registry is not None \
                and course_attendance_registry.status == AttendanceRegistry.PUBLISHED:
            events = AttendanceDay.objects.filter(attendance_registry=course_attendance_registry)

            if 'event_id' in request.data.keys() and request.data['event_id'] is not None:
                # get current event
                current_event = None
                for event in course_attendance_registry.events:
                    if event['event_id'] == request.data['event_id']:
                        current_event = event
                        course_attendance_registry.events.remove(event)
                        # delete also the registry event
                        for e in events:
                            # set event['start'] to datetime and tzinfo is UTC
                            current_event_date = datetime.strptime(
                                event['start'], '%Y-%m-%dT%H:%M:%S.%fZ',
                            ).replace(tzinfo=datetime.now().astimezone().tzinfo)
                            logger.info(f"current_event_date: {current_event_date}")
                            if e.title == event['title'] and e.date == current_event_date:
                                e.delete()
                                break
                        break
                course_attendance_registry.save()
                events_list = [e for e in course_attendance_registry.events]

                '''
                Update events based on before and groupId:
                - if groupId is not None and before is None, delete all the groupId events of the current event
                - if groupId is not None and before is true, delete all the groupId events before the current event
                - if groupId is not None and before is false, delete all the groupId events after the current event
                - if groupId is None, delete the current event
                '''
                if 'groupId' in request.data.keys() and request.data['groupId'] is not None:
                    # delete all the groupId events of the current event
                    if 'before' not in request.data.keys() or (
                            'before' in request.data.keys() and request.data['before'] is None):
                        for e in events_list:
                            if 'extendedProps' in e.keys() and \
                                    'groupId' in e['extendedProps'].keys() and \
                                    e['extendedProps']['groupId'] == request.data['groupId']:
                                course_attendance_registry.events.remove(e)
                                for ev in events:
                                    # set event['start'] to datetime and tzinfo is UTC
                                    current_event_date = datetime.strptime(
                                        e['start'], '%Y-%m-%dT%H:%M:%S.%fZ',
                                    ).replace(tzinfo=datetime.now().astimezone().tzinfo)
                                    logger.info(f"current_event_date: {current_event_date}")
                                    if ev.title == event['title'] and ev.date == current_event_date:
                                        ev.delete()
                                        break
                    # delete all the groupId events before the current event
                    elif request.data['before'] is True and current_event:
                        for e in events_list:
                            if 'extendedProps' in e.keys() and \
                                    'groupId' in e['extendedProps'].keys() and \
                                    e['extendedProps']['groupId'] == request.data['groupId'] and \
                                    datetime.strptime(
                                        e['start'], '%Y-%m-%dT%H:%M:%S.%fZ'
                                    ) <= datetime.strptime(
                                        current_event['start'], '%Y-%m-%dT%H:%M:%S.%fZ'
                                    ):
                                course_attendance_registry.events.remove(e)
                                for ev in events:
                                    # set event['start'] to datetime and tzinfo is UTC
                                    current_event_date = datetime.strptime(
                                        e['start'], '%Y-%m-%dT%H:%M:%S.%fZ',
                                    ).replace(tzinfo=datetime.now().astimezone().tzinfo)
                                    logger.info(f"current_event_date: {current_event_date}")
                                    if ev.title == event['title'] and ev.date == current_event_date:
                                        ev.delete()
                                        break

                    # delete all the groupId events after the current event
                    elif request.data['before'] is False and current_event:
                        for e in events_list:
                            if 'extendedProps' in e.keys() and \
                                    'groupId' in e['extendedProps'].keys() and \
                                    e['extendedProps']['groupId'] == request.data['groupId'] and \
                                    datetime.strptime(
                                        e['start'], '%Y-%m-%dT%H:%M:%S.%fZ'
                                    ) >= datetime.strptime(
                                        current_event['start'], '%Y-%m-%dT%H:%M:%S.%fZ'
                                    ):
                                course_attendance_registry.events.remove(e)
                                for ev in events:
                                    # set event['start'] to datetime and tzinfo is UTC
                                    current_event_date = datetime.strptime(
                                        e['start'], '%Y-%m-%dT%H:%M:%S.%fZ',
                                    ).replace(tzinfo=datetime.now().astimezone().tzinfo)
                                    logger.info(f"current_event_date: {current_event_date}")
                                    if ev.title == event['title'] and ev.date == current_event_date:
                                        ev.delete()
                                        break
                course_attendance_registry.save()

                return Response({"message": "Event deleted."}, status=status.HTTP_200_OK)
            else:
                for event in events:
                    event.delete()
                course_attendance_registry.delete()

                course_attendance_registry.events = []
                course_attendance_registry.status = AttendanceRegistry.DRAFT
                course_attendance_registry.save()

            return Response({"message": "Calendar updated."}, status=status.HTTP_200_OK)
        else:
            # cannot delete a draft
            return Response({'msg': 'cannot delete a draft.'}, status.HTTP_400_BAD_REQUEST)

    # getting body
    data = request.data

    if ('events' not in data.keys() or 'status' not in data.keys()) \
            and len(data.keys()) != 2:
        raise ValidationError("Missing or wrong events data.")

    if data['status'] not in [AttendanceRegistry.DRAFT, AttendanceRegistry.PUBLISHED]:
        raise ValidationError("Status not valid.")

    sport_association = SportAssociation.objects.get(user=request.user)
    course = Course.objects.filter(sport_association=sport_association, course_id=uid).first()

    if course is None:
        raise NotFound("Course not found.")

    course_attendance_registry = AttendanceRegistry.objects.filter(course=course).first()

    events_ids = [event['event_id'] for event in data['events']]
    reminders = Reminders.objects.filter(event_id__in=events_ids)
    # loop through events to update reminders
    for event in data['events']:
        current_reminder = reminders.filter(event_id=event['event_id']).first()
        if current_reminder is None and 'extendedProps' in event.keys() and \
                'reminder_enabled' in event['extendedProps'] and \
                event['extendedProps']['reminder_enabled']:
            start_date = datetime.strptime(event['start'], '%Y-%m-%dT%H:%M:%S.%fZ')
            subtract_seconds = get_seconds_from_reminder_units(
                event['extendedProps']['reminder_amount'],
                event['extendedProps']['reminder_unit']
            )
            send_at = start_date - timedelta(seconds=subtract_seconds)
            reminder_text = f"{event['extendedProps']['reminder_amount']} {REMINDER_UNITS_MAP_TEXT[event['extendedProps']['reminder_unit']]}"
            instructor = None
            if 'extendedProps' in event.keys() and 'instructor' in event['extendedProps']:
                try:
                    instructor_id = event['extendedProps']['instructor']['instructor_id']
                    instructor = Instructor.objects.filter(instructor_id=instructor_id).first()
                except Exception as e:
                    logger.error(f"Error getting instructor: {e}")
            reminder = Reminders.objects.create(
                event_id=event['event_id'],
                event_title=event['title'],
                event_description=event['extendedProps'][
                    'description'] if 'extendedProps' in event.keys() and 'description' in event[
                    'extendedProps'] else '',
                event_course_title=event['extendedProps'][
                    'course_title'] if 'extendedProps' in event.keys() and 'course_title' in event[
                    'extendedProps'] else None,
                event_reminder_text=reminder_text,
                send_at=send_at,
                user=request.user,
                instructor=instructor,
                sport_association=sport_association
            )
            reminder.save()
        elif current_reminder is not None and 'extendedProps' in event.keys() and \
                'reminder_enabled' in event['extendedProps'] and \
                not event['extendedProps']['reminder_enabled']:
            current_reminder.delete()
        # update reminder
        elif current_reminder is not None and 'extendedProps' in event.keys() and \
                'reminder_enabled' in event['extendedProps']:
            start_date = datetime.strptime(event['start'], '%Y-%m-%dT%H:%M:%S.%fZ')
            subtract_seconds = get_seconds_from_reminder_units(
                event['extendedProps']['reminder_amount'],
                event['extendedProps']['reminder_unit']
            )
            send_at = start_date - timedelta(seconds=subtract_seconds)
            instructor = None
            if 'extendedProps' in event.keys() and 'instructor' in event['extendedProps']:
                try:
                    instructor_id = event['extendedProps']['instructor']['instructor_id']
                    instructor = Instructor.objects.filter(instructor_id=instructor_id).first()
                except Exception as e:
                    logger.error(f"Error getting instructor: {e}")

            reminder_text = f"{event['extendedProps']['reminder_amount']} {REMINDER_UNITS_MAP_TEXT[event['extendedProps']['reminder_unit']]}"
            current_reminder.event_title = event['title']
            current_reminder.event_description = event['extendedProps'][
                'description'] if 'extendedProps' in event.keys() and 'description' in event['extendedProps'] else ''
            current_reminder.event_course_title = event['extendedProps'][
                'course_title'] if 'extendedProps' in event.keys() and 'course_title' in event[
                'extendedProps'] else None
            current_reminder.event_reminder_text = reminder_text
            current_reminder.send_at = send_at
            current_reminder.instructor = instructor
            current_reminder.save()

    if course_attendance_registry is None:
        course_attendance_registry = AttendanceRegistry.objects.create(
            course=course,
            status=data['status'],
            events=data['events']
        )
        course_attendance_registry.save()

        # generating the attendance rows
        if data['status'] == AttendanceRegistry.PUBLISHED:
            for event in data['events']:
                AttendanceDay.objects.create(
                    attendance_registry=course_attendance_registry,
                    title=event['title'],
                    date=event['start'],
                    associated_event=event['event_id'] if 'event_id' in event.keys() else None
                ).save()

        logger.info("calendar_update -> ended -> NEW REGISTRY -> user: {}".format(request.user.user_id))
        return Response({"message": "Calendar updated."}, status=status.HTTP_200_OK)

    # generating the attendance rows
    if course_attendance_registry.status == AttendanceRegistry.DRAFT:

        course_attendance_registry.events = data['events']
        course_attendance_registry.status = data['status']
        course_attendance_registry.save()

        if data['status'] == AttendanceRegistry.PUBLISHED:
            for event in data['events']:
                AttendanceDay.objects.create(
                    attendance_registry=course_attendance_registry,
                    title=event['title'],
                    date=event['start'],
                    associated_event=event['event_id'] if 'event_id' in event.keys() else None
                ).save()
        logger.info("calendar_update -> ended -> UPDATE REGISTRY -> user: {}".format(request.user.user_id))
        return Response({"message": "Calendar updated."}, status=status.HTTP_200_OK)

    if course_attendance_registry.status == AttendanceRegistry.PUBLISHED:

        # add only new events
        for event in data['events']:

            event_from_registry = None
            for idx, current_event in enumerate(course_attendance_registry.events):
                if current_event['event_id'] == event['event_id']:
                    event_from_registry = idx
                    break

            if event_from_registry is None:
                course_attendance_registry.events.append(event)
                AttendanceDay.objects.create(
                    attendance_registry=course_attendance_registry,
                    title=event['title'],
                    date=event['start'],
                    associated_event=event['event_id'] if 'event_id' in event.keys() else None
                ).save()
            else:
                # update event in db
                attendance_day = AttendanceDay.objects.filter(
                    attendance_registry=course_attendance_registry,
                    date=course_attendance_registry.events[event_from_registry]['start']).first()

                # update event
                course_attendance_registry.events[event_from_registry]['title'] = event['title']
                course_attendance_registry.events[event_from_registry]['start'] = str(event['start'])
                course_attendance_registry.events[event_from_registry]['end'] = str(event['end'])
                course_attendance_registry.events[event_from_registry]['allDay'] = event['allDay']
                # className if present
                if 'className' in event.keys():
                    course_attendance_registry.events[event_from_registry]['className'] = event['className']
                course_attendance_registry.events[event_from_registry]['extendedProps'] = event['extendedProps']

                if attendance_day is not None:
                    attendance_day.title = event['title']
                    attendance_day.date = event['start']
                    attendance_day.save()

        course_attendance_registry.events = sorted(course_attendance_registry.events, key=lambda k: k['start'])
        course_attendance_registry.save()
        logger.info("calendar_update -> ended -> UPDATE REGISTRY -> user: {}".format(request.user.user_id))
        return Response({"message": "Calendar updated."}, status=status.HTTP_200_OK)
    return Response({"message": "Calendar is already published."}, status=status.HTTP_412_PRECONDITION_FAILED)


@api_view(['GET'])
@permission_classes([AllowAny])
def calendar(request, uid):
    """
    API endpoint to get the calendar for the current course
    """


    is_valid_uuid(uid)

    course = Course.objects.filter(course_id=uid).first()

    if course is None:
        raise NotFound("Course not found.")

    course_attendance_registry = AttendanceRegistry.objects.filter(course=course).first()
    google_sync_enabled = False
    if request.user.is_authenticated:
        if request.user.is_collaborator is False:
            google_sync_enabled = request.user.google_sync_enabled
        else:
            try:
                request, has_permission = IsAuthenticated.has_permission_and_return_request(request=request)
                if request.original_user:
                        google_sync_enabled = request.original_user.google_sync_enabled
            except Exception as e:
                logger.info(e)
    data = {
        'status': AttendanceRegistry.DRAFT,
        'events': [],
        'course': {
            'title': course.title,
        },
        'sport_association': {
            'denomination': course.sport_association.denomination,
            'logo': course.sport_association.logo,
        },
        'google_calendar_id': course.google_calendar_id,
        'google_sync_enabled': google_sync_enabled,
    }

    if course_attendance_registry is not None:
        data['events'] = course_attendance_registry.events
        for event in data['events']:
            if 'extendedProps' in event.keys() and 'description' not in event['extendedProps'].keys():
                event['extendedProps']['description'] = f"{course_attendance_registry.course.title} - {event['title']}"
        data['status'] = course_attendance_registry.status
        return Response({'data': data}, status=status.HTTP_200_OK)

    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def attendees_update(request, uid, attendance_day_uid):
    """
    API endpoint to update an attendance registry event
    """


    is_valid_uuid(uid)
    is_valid_uuid(attendance_day_uid)

    logger.info("attendees_update -> init -> user: {}".format(request.user.user_id))
    # getting body
    data = request.data

    if 'attendees' not in data.keys() or len(data.keys()) != 1:
        raise ValidationError("Missing or wrong events data.")

    sport_association = SportAssociation.objects.get(user=request.user)
    course = Course.objects.filter(sport_association=sport_association, course_id=uid).first()

    if course is None:
        raise NotFound("Course not found.")

    course_attendance_registry = AttendanceRegistry.objects.filter(course=course).first()

    if course_attendance_registry is None:
        raise NotFound("Course Attendance Registry not found.")

    event_day = AttendanceDay.objects.filter(attendance_registry=course_attendance_registry,
                                             attendance_day_id=attendance_day_uid).first()
    if event_day is None:
        raise NotFound("Course Event day not found.")

    # check if attendees is None
    if event_day.attendees is None:
        event_day.attendees = []

    # iterate over the old attendees and reset the carnet if there is one
    for attendee in event_day.attendees:
        if attendee not in data['attendees']:
            # getting course subscription
            course_subscription = CourseSubscription.objects.all_objects().filter(
                course_subscription_id=attendee['course_subscription_id']).first()
            # getting the carnets of the course subscription
            carnets = CarnetSubscription.objects.filter(
                Q(course_subscription=course_subscription) |
                Q(course_subscription__isnull=True),
                subscription=course_subscription.subscription,
                disabled=False,
            ).order_by('-creation_date')
            carnet_found = False
            for c in carnets:
                # check if lessons is in the current carnet
                for lesson in c.meta['lessons_registry']:
                    if not 'attendance_day_id' in lesson.keys():
                        lesson['attendance_day_id'] = None
                    # found a lesson in the current carnet
                    if lesson['course']['id'] == str(course.course_id) \
                            and (
                            datetime.fromisoformat(lesson['date']).strftime('%Y/%m/%d') \
                            == event_day.date.strftime('%Y/%m/%d') \
                            or lesson['attendance_day_id'] == event_day.attendance_day_id):
                        c.meta['lessons_registry'].remove(lesson)
                        c.meta['lessons_left'] += 1
                        c.save()
                        carnet_found = True
                        break
                if carnet_found:
                    break

    final_attendees = data['attendees']

    # iterate over attendees and update the carnet if there is one
    for attendee in data['attendees']:
        # if already in the list, skip
        if attendee in event_day.attendees:
            continue

        # get course subscription
        course_subscription = CourseSubscription.objects.filter(
            course_subscription_id=attendee['course_subscription_id']).first()
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
            # check if there is a carnet with lessons left
            for c in carnets:
                if c.meta['lessons_left'] > 0:
                    if carnet is None or (
                            carnet is not None and
                            carnet.meta['lessons_left'] > c.meta['lessons_left']
                    ):
                        carnet = c
            # end enable when using postgresql in every environment

            if carnet:
                if carnet.meta['lessons_left'] == 0:
                    return Response({"msg": "Carnet finito."},
                                    status=status.HTTP_412_PRECONDITION_FAILED)
                if carnet.payment is not None and carnet.payment.paid is False:
                    return Response({"msg": "Il carnet non è ancora stato pagato."},
                                    status=status.HTTP_412_PRECONDITION_FAILED)
                # update meta
                carnet.meta['lessons_left'] -= 1
                carnet.meta['lessons_registry'].append({
                    "date": str(event_day.date),
                    "attendance_day_id": str(event_day.attendance_day_id),
                    "course": {
                        "id": str(course.course_id),
                        "title": course.title,
                    },
                    "title": event_day.title,
                })
                carnet.save()
            elif carnets.count() > 0:
                return Response({
                    "message": "Carnet is already finished.",
                    "associate_name": course_subscription.subscription.associate.get_full_name()
                }, status=status.HTTP_412_PRECONDITION_FAILED)

    event_day.attendees = final_attendees
    event_day.save()
    logger.info("attendees_update -> ended -> UPDATE REGISTRY -> user: {}".format(request.user.user_id))
    return Response({"message": "Attendance Registry updated."}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def attendees(request, uid):
    """
    API endpoint to get the attendance registry for the current course
    """


    is_valid_uuid(uid)

    logger.info("attendees -> init -> user: {}".format(request.user.user_id))
    sport_association = SportAssociation.objects.get(user=request.user)
    course = Course.objects.filter(sport_association=sport_association, course_id=uid).first()
    data = {
        'events': [],
    }

    if course is None:
        raise NotFound("Course not found.")

    course_attendance_registry = AttendanceRegistry.objects.filter(course=course).first()

    if course_attendance_registry is None:
        return Response({'data': data}, status=status.HTTP_200_OK)

    map_unlinked_attendance(course_attendance_registry)

    events = AttendanceDay.objects.filter(attendance_registry=course_attendance_registry).select_related('attendance_registry', 'attendance_registry__course').order_by('date')


    for event in events:
        event_attendees = []
        if event.attendees is not None:
            event_attendees = event.attendees
        # get expected absences and resolve associate full name
        if event.expected_absences is not None:
            for attendee in event.expected_absences:
                courseSubscription = CourseSubscription.objects.filter(
                    course_subscription_id=attendee['course_subscription_id']).first()
                if courseSubscription is not None:
                    attendee['associate_name'] = courseSubscription.subscription.associate.get_full_name()
        else:
            event.expected_absences = []

        events_data = event.attendance_registry.events
        extended_props = {}
        allDay = False
        start = None
        end = None
        # parse events data which is a jsonfield
        if events_data is not None:
            for e in events_data:
                # check same date
                if datetime.strptime(e['start'], '%Y-%m-%dT%H:%M:%S.%fZ').strftime('%Y/%m/%d %H:%M')[:-3] \
                        == event.date.strftime('%Y/%m/%d %H:%M')[:-3]:
                    if event.associated_event is not None and str(event.associated_event) != e['event_id']:
                        # if the event is not the same, skip
                        continue
                    if 'extendedProps' in e.keys():
                        extended_props = get_extended_prop_for_attendance_day(event)
                    if 'allDay' in e.keys():
                        allDay = e['allDay']
                    else:
                        allDay = False
                    if 'start' in e.keys():
                        start = e['start']
                    if 'end' in e.keys():
                        end = e['end']
                    break

        data['events'].append({
            "title": event.title,
            "allDay": allDay,
            "extended_props": extended_props,
            "attendance_day_id": event.attendance_day_id,
            "attendance_registry": event.attendance_registry.attendance_registry_id,
            "date": event.date,
            "start": start,
            "end": end,
            "attendees": event_attendees,
            "expected_absences": [a['associate_name'] if 'associate_name' in a else '<rimosso>' for a in event.expected_absences],
        })

    logger.info("attendees -> ended -> user: {}".format(request.user.user_id))
    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation | IsAthleteUser])
def subscription_attendance(request, uid):
    """
    API endpoint to get the attendance registry for the current course
    """

    is_valid_uuid(uid)

    # get course query string
    selected_course_id = request.query_params.get('course', 'all')

    logger.info("subscription_attendance -> init -> user: {}".format(request.user.user_id))
    if request.user.role == User.ATHLETE:
        subscription = Subscription.objects.filter(user=request.user, subscription_id=uid).first()
    else:
        sport_association = SportAssociation.objects.get(user=request.user)
        subscription = Subscription.objects.filter(sport_association=sport_association, subscription_id=uid).first()
    # get course subscriptions for the current subscription
    if selected_course_id == 'all':
        course_subscriptions = CourseSubscription.objects.all_objects().filter(subscription=subscription).select_related('subscription__associate')
    else:
        course_subscriptions = CourseSubscription.objects.all_objects().filter(subscription=subscription, course__course_id=selected_course_id).select_related('subscription__associate')
    # get courses from course subscriptions
    courses = []
    courses_detailed = []
    for c in course_subscriptions:
        if c.course is not None and c.course.course_id not in courses:
            courses.append(c.course.course_id)
            courses_detailed.append({
                "course_id": c.course.course_id,
                "title": c.course.title,
            })

    # get attendance registry for each course subscription
    attendance_registries = AttendanceRegistry.objects.filter(course_id__in=courses)
    # get attendance days for each attendance registry
    attendance_days_full = AttendanceDay.objects.filter(attendance_registry__in=attendance_registries).order_by('-date')
    attendance_days = []

    course_subscriptions_list = [str(x) for x in course_subscriptions.values_list('course_subscription_id', flat=True)]

    # filter attendance days by searching for course_subscription_id in attendees
    for att in attendance_days_full:
        if att.attendees:
            for c in att.attendees:
                if c['course_subscription_id'] in course_subscriptions_list:
                    attendance_days.append(att)

    data = {
        'attendance_days': [],
        'courses': courses_detailed,
        'stats': {
            "total_attendance_last_30_days": 0,
            "total_absences_last_30_days": 0,
            "total_attendance": len(attendance_days),
            "total_absences": 0,
        }
    }

    for attendance_day in attendance_days:
        data['attendance_days'].append({
            "title": attendance_day.title,
            "attendance_day_id": attendance_day.attendance_day_id,
            "attendance_registry": attendance_day.attendance_registry.attendance_registry_id,
            "course": {
                "course_id": attendance_day.attendance_registry.course.course_id,
                "title": attendance_day.attendance_registry.course.title,
            },
            "date": attendance_day.date,
        })
        # if attendance_day.date in the last 30 days increment the total_attendance_last_30_days
        if attendance_day.date >= timezone.now() - timedelta(days=30):
            data['stats']['total_attendance_last_30_days'] += 1

    logger.info("subscription_attendance -> ended -> user: {}".format(request.user.user_id))
    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation | IsAthleteUser])
def subscription_calendar(request, uid):
    """
    API endpoint to get the attendance registry for the current course
    """


    is_valid_uuid(uid)

    logger.info("subscription_calendar -> init -> user: {}".format(request.user.user_id))
    if request.user.role == User.ATHLETE:
        subscription = Subscription.objects.filter(user=request.user, subscription_id=uid).first()
    else:
        sport_association = SportAssociation.objects.get(user=request.user)
        subscription = Subscription.objects.filter(sport_association=sport_association, subscription_id=uid).first()
    # get course subscriptions for the current subscription
    courses = CourseSubscription.objects.filter(subscription=subscription).values_list('course__course_id', flat=True)
    registries = AttendanceRegistry.objects.filter(course_id__in=courses)

    events = []
    for registry in registries:
        if registry.events:
            for event in registry.events:
                events.append({
                    "title": f"{event['title']} ({registry.course.title})",
                    "start": event['start'],
                    "end": event['end'],
                    "allDay": event['allDay'],
                })

    data = {
        'events': events,
    }

    logger.info("subscription_calendar -> ended -> user: {}".format(request.user.user_id))
    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def full_events_calendar(request):
    """
    API endpoint to get the attendance registry for the current course
    """
    if request.user.role == User.ATHLETE:
        return Response({'error': 'not allowed.'}, status=status.HTTP_403_FORBIDDEN)

    # get start and end query string or set default values to none
    start = request.query_params.get('start', None)
    end = request.query_params.get('end', None)
    get_lesson = request.query_params.get('get_lesson', 'true').lower() in ('true', 't', 'yes', 'y', '1')
    event_id = request.query_params.get('event_id', None)
    is_instructor = False

    if start is not None:
        start = datetime.fromisoformat(start)
    if end is not None:
        end = datetime.fromisoformat(end)

    sport_association = SportAssociation.objects.get(user=request.user)
    # get course subscriptions for the current subscription
    courses = Course.objects.filter(sport_association=sport_association).values_list('course_id', flat=True)

    courses_colors = get_course_colors(sport_association.sport_association_id)

    if hasattr(request, 'original_user') and request.original_user is not None and request.original_user.is_collaborator:
        # check if it's an instructor
        instructor = Instructor.objects.filter(associated_user_id=str(request.original_user.user_id)).first()
        instructor_id = str(instructor.instructor_id) if instructor is not None else None
        if instructor_id is not None:
            is_instructor = True
            attentance_registries = AttendanceRegistry.objects.filter(events__iregex=instructor_id)
            courses = attentance_registries.filter(
                course_id__in=courses
            ).values_list('course_id', flat=True)


    for idx, course in enumerate(courses):
        courses_colors[course] = ColorPalette.colors[idx] if idx < len(ColorPalette.colors) else ColorPalette.colors[0]
    registries = AttendanceRegistry.objects.filter(course_id__in=courses)
    attendace_days = AttendanceDay.objects.filter(attendance_registry__in=registries).select_related('attendance_registry', 'attendance_registry__course')

    events = []
    for registry in registries:
        if registry.events:
            for event in registry.events:
                if event_id is not None and event_id != event['event_id']:
                    continue
                # apply filter by start and end date if present
                if start is not None and end is not None:
                    # check if the event is in the range otherwise go to the next event
                    if datetime.strptime(event['start'], '%Y-%m-%dT%H:%M:%S.%fZ') < start or \
                            datetime.strptime(event['start'], '%Y-%m-%dT%H:%M:%S.%fZ') > end:
                        continue
                    # filter attendance days by date
                    # registry_attendance_day = attendace_days.filter(
                    #     date__in=[start, end]
                    # )
                registry_attendance_day = attendace_days.filter(
                    attendance_registry=registry,
                    title=event['title'],
                    date=event['start']
                ).first()

                if 'extendedProps' not in event.keys():
                    event['extendedProps'] = {}

                if 'className' not in event.keys():
                    event["className"] = courses_colors[registry.course.course_id],

                if registry_attendance_day is not None:
                    event['extendedProps']['attendace_day_id'] = registry_attendance_day.attendance_day_id
                    if get_lesson:
                        event['extendedProps']['lesson'] = get_extended_prop_for_attendance_day(registry_attendance_day)
                event['extendedProps']['course'] = registry.course.course_id
                # set in the description the prefix of the course
                if 'description' not in event['extendedProps'].keys():
                    event['extendedProps']['description'] = f"{registry.course.title} - {event['title']}"
                event['id'] = event['event_id']
                events.append(event)

    if not is_instructor:
        global_calendar_events, _ = GlobalCalendarEvents.objects.get_or_create(sport_association=sport_association)
        if global_calendar_events.events:
            for event in global_calendar_events.events:
                # compare if start date is before the end date and after the start date
                if start is not None and end is not None:
                    if start <= datetime.strptime(event['start'], '%Y-%m-%dT%H:%M:%S.%fZ') <= end:
                        event['id'] = event['event_id']
                        events.append(event)

    data = {
        'events': events,
    }

    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def full_events_calendar_update(request):
    data = request.data

    if 'events' not in data.keys():
        raise ValidationError("Missing or wrong events data.")

    sport_association = SportAssociation.objects.get(user=request.user)
    global_calendar_events, _ = GlobalCalendarEvents.objects.get_or_create(sport_association=sport_association)

    global_calendar_events.events = data['events']

    events_ids = [event['event_id'] for event in global_calendar_events.events]
    reminders = Reminders.objects.filter(event_id__in=events_ids)
    # loop through events to update reminders
    for event in global_calendar_events.events:
        current_reminder = reminders.filter(event_id=event['event_id']).first()
        if current_reminder is None and 'extendedProps' in event.keys() and \
                'reminder_enabled' in event['extendedProps'] and \
                event['extendedProps']['reminder_enabled']:
            start_date = datetime.strptime(event['start'], '%Y-%m-%dT%H:%M:%S.%fZ')
            subtract_seconds = get_seconds_from_reminder_units(
                event['extendedProps']['reminder_amount'],
                event['extendedProps']['reminder_unit']
            )
            send_at = start_date - timedelta(seconds=subtract_seconds)
            reminder_text = f"{event['extendedProps']['reminder_amount']} {REMINDER_UNITS_MAP_TEXT[event['extendedProps']['reminder_unit']]}"
            instructor = None
            if 'extendedProps' in event.keys() and 'instructor' in event['extendedProps']:
                try:
                    instructor_id = event['extendedProps']['instructor']['instructor_id']
                    instructor = Instructor.objects.filter(instructor_id=instructor_id).first()
                except Exception as e:
                    logger.error(f"Error getting instructor: {e}")

            reminder = Reminders.objects.create(
                event_id=event['event_id'],
                event_title=event['title'],
                event_description=event['extendedProps']['description'] if 'extendedProps' in event.keys() and 'description' in event['extendedProps'] else '',
                event_course_title=event['extendedProps']['course_title'] if 'extendedProps' in event.keys() and 'course_title' in event['extendedProps'] else None,
                event_reminder_text=reminder_text,
                send_at=send_at,
                user=request.user,
                instructor=instructor,
                sport_association=sport_association
            )
            reminder.save()
        elif current_reminder is not None and 'extendedProps' in event.keys() and \
                'reminder_enabled' in event['extendedProps'] and \
                not event['extendedProps']['reminder_enabled']:
            current_reminder.delete()
        # update reminder
        elif current_reminder is not None and 'extendedProps' in event.keys() and \
                'reminder_enabled' in event['extendedProps']:
            start_date = datetime.strptime(event['start'], '%Y-%m-%dT%H:%M:%S.%fZ')
            subtract_seconds = get_seconds_from_reminder_units(
                event['extendedProps']['reminder_amount'],
                event['extendedProps']['reminder_unit']
            )
            send_at = start_date - timedelta(seconds=subtract_seconds)
            instructor = None
            if 'extendedProps' in event.keys() and 'instructor' in event['extendedProps']:
                try:
                    instructor_id = event['extendedProps']['instructor']['instructor_id']
                    instructor = Instructor.objects.filter(instructor_id=instructor_id).first()
                except Exception as e:
                    logger.error(f"Error getting instructor: {e}")

            reminder_text = f"{event['extendedProps']['reminder_amount']} {REMINDER_UNITS_MAP_TEXT[event['extendedProps']['reminder_unit']]}"
            current_reminder.event_title = event['title']
            current_reminder.event_description = event['extendedProps']['description'] if 'extendedProps' in event.keys() and 'description' in event['extendedProps'] else ''
            current_reminder.event_course_title = event['extendedProps']['course_title'] if 'extendedProps' in event.keys() and 'course_title' in event['extendedProps'] else None
            current_reminder.event_reminder_text = reminder_text
            current_reminder.send_at = send_at
            current_reminder.instructor = instructor
            current_reminder.save()


    global_calendar_events.save()
    return Response({"message": "Events added."}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def full_events_calendar_export(request):
    """
    API endpoint to get the attendance registry for the current course
    """
    # get query param course_id
    course_id = request.GET.get('course_id', None)

    if course_id is not None:
        is_valid_uuid(course_id)

    if request.user.role == User.ATHLETE:
        return Response({'error: not allowed.'}, status.HTTP_403_FORBIDDEN)

    # get course subscriptions for the current subscription
    courses = Course.objects.filter(sport_association=request.user.sport_association).values_list('course_id', flat=True)
    # dict with random hex color for each course
    if course_id is not None:
        courses = courses.filter(course_id=course_id)

    registries = AttendanceRegistry.objects.filter(course_id__in=courses)

    cal = Calendar()
    for registry in registries:
        if registry.events:
            for event in registry.events:
                cal_event = Event()
                cal_event.add('summary', event['title'])
                start_dt = parse_datetime(event['start'])
                if event['end'] is not None and event['end'] != "None":
                    end_dt = parse_datetime(event['end'])
                else:
                    # For all day events or events without a specific end time, use the start time +1 hour or customize
                    end_dt = start_dt + timedelta(hours=1)
                cal_event.add('dtstart', start_dt)
                cal_event.add('dtend', end_dt)
                cal_event.add('dtstamp', datetime.now())
                cal_event['uid'] = event['event_id']  # Use event ID as UID for uniqueness
                if 'extendedProps' in event.keys():
                    if 'instructor' in event['extendedProps'] and \
                        isinstance(event['extendedProps']['instructor'], dict) and \
                        event['extendedProps']['instructor'] is not None and \
                        'label' in event['extendedProps']['instructor']:
                        cal_event.add('description', f"Instructor: {event['extendedProps']['instructor']['label']}")
                cal.add_component(cal_event)

    response = HttpResponse(cal.to_ical(), content_type="text/calendar")
    response['Content-Disposition'] = 'attachment; filename="events.ics"'
    return response
