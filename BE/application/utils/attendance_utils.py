from datetime import datetime
import logging

from application.models import AttendanceDay, Instructor
from application.models.courses_models import CourseSubscription, Course
from functools import lru_cache

from application.utils.api_utils import ColorPalette

logger = logging.getLogger(__name__)


@lru_cache(maxsize=128)
def get_course_colors(sport_association_id):
    courses = Course.objects.filter(sport_association_id=sport_association_id).values_list('course_id', flat=True)
    return {
        course: ColorPalette.colors[idx % len(ColorPalette.colors)]
        for idx, course in enumerate(courses)
    }


def map_unlinked_attendance(course_attendance_registry):
    logger.debug("Mapping unlinked attendance", extra={
        'attendance_registry_id': str(course_attendance_registry.attendance_registry_id)
    })

    def get_unlink_events(course_attendance_registry):
        return AttendanceDay.objects.filter(
            attendance_registry=course_attendance_registry,
            associated_event=None
        ).first()

    empty_event = get_unlink_events(course_attendance_registry)
    while empty_event is not None:
        # assure that event is assigned correctly
        events_data = empty_event.attendance_registry.events
        found_event_to_link = False
        if events_data is not None:
            for e in events_data:
                # check same date
                if datetime.strptime(e['start'], '%Y-%m-%dT%H:%M:%S.%fZ').strftime('%Y/%m/%d %H:%M')[:-3] \
                        == empty_event.date.strftime('%Y/%m/%d %H:%M')[:-3]:
                    if empty_event.associated_event is None and \
                            AttendanceDay.objects.filter(
                                attendance_registry=course_attendance_registry,
                                associated_event=e['event_id']
                            ).first() is None:
                        empty_event.associated_event = e['event_id']
                        empty_event.save()
                        logger.debug("Linked attendance event", extra={
                            'attendance_day_id': str(empty_event.attendance_day_id),
                            'event_id': e['event_id']
                        })
                        found_event_to_link = True
                        break
        if found_event_to_link:
            empty_event = get_unlink_events(course_attendance_registry)
        else:
            # avoid infinite loop
            empty_event = None

    logger.info("Unlinked attendance mapping completed", extra={
        'attendance_registry_id': str(course_attendance_registry.attendance_registry_id)
    })

def get_extended_prop_for_attendance_day(event: AttendanceDay):
    """
    Returns the extended properties for a given attendance day with optimized performance.

    Args:
        event: AttendanceDay object containing attendance information

    Returns:
        Dictionary containing processed attendance information and properties
    """
    logger.debug("Getting extended properties for attendance day", extra={
        'attendance_day_id': str(event.attendance_day_id),
        'course_id': str(event.attendance_registry.course.course_id)
    })

    # Initialize lists with empty defaults
    event_attendees = event.attendees or []
    event.expected_absences = event.expected_absences or []

    # Batch fetch all needed CourseSubscriptions
    all_subscription_ids = set()
    for attendee in event_attendees:
        all_subscription_ids.add(attendee['course_subscription_id'])
    for absence in event.expected_absences:
        all_subscription_ids.add(absence['course_subscription_id'])

    # Fetch all course subscriptions in one query with select_related
    subscription_map = {
        cs.course_subscription_id: cs
        for cs in CourseSubscription.objects.filter(
            course_subscription_id__in=all_subscription_ids
        ).select_related('subscription__associate')
    }

    # Process attendees and expected absences
    for attendee in event_attendees:
        if cs := subscription_map.get(attendee['course_subscription_id']):
            attendee['associate_name'] = cs.subscription.associate.get_full_name()

    expected_absences = []
    for absence in event.expected_absences:
        if cs := subscription_map.get(absence['course_subscription_id']):
            expected_absences.append({
                "course_subscription_id": absence['course_subscription_id'],
                "associate_name": absence.get('associate_name') or cs.subscription.associate.get_full_name()
            })

    # Find matching event data
    event_data = None
    if event.attendance_registry.events:
        event_date_str = event.date.strftime('%Y/%m/%d %H:%M')[:-3]
        for e in event.attendance_registry.events:
            e_date = datetime.strptime(e['start'], '%Y-%m-%dT%H:%M:%S.%fZ')
            if e_date.strftime('%Y/%m/%d %H:%M')[:-3] == event_date_str:
                if event.associated_event and str(event.associated_event) != e['event_id']:
                    continue

                if not event.associated_event:
                    try:
                        event.associated_event = e['event_id']
                        event.save()
                    except Exception:
                        pass

                event_data = e
                break

    # Get course subscriptions info efficiently
    course_subscriptions = CourseSubscription.objects.filter(
        course=event.attendance_registry.course
    ).select_related('subscription__associate')

    potential_attendees = [{
        "course_subscription_id": cs.course_subscription_id,
        "subscription": {
            "associate": {
                "first_name": cs.subscription.associate.first_name,
                "last_name": cs.subscription.associate.last_name,
                "tax_code": cs.subscription.associate.tax_code,
                "medical_label": cs.subscription.get_medical_label(),
            }
        }
    } for cs in course_subscriptions]

    total_course_subscriptions = len(potential_attendees)

    extended_props = event_data.get('extendedProps', {}) if event_data else {}
    if extended_props and 'instructor' in extended_props and extended_props['instructor'] and 'instructor_id' in extended_props['instructor']:
        instructor = Instructor.objects.filter(instructor_id=extended_props['instructor']['instructor_id']).first()
        if instructor:
            extended_props['instructor']['label'] = instructor.get_full_name()
    # check if it's a type of list of instructors
    elif extended_props and 'instructor' in extended_props and isinstance(extended_props['instructor'], list):
        for idx, instructor in enumerate(extended_props['instructor']):
            if 'instructor_id' in instructor:
                instructor_obj = Instructor.objects.filter(instructor_id=instructor['instructor_id']).first()
                if instructor_obj:
                    extended_props['instructor'][idx]['label'] = instructor_obj.get_full_name()

    logger.info("Extended properties retrieved", extra={
        'attendance_day_id': str(event.attendance_day_id),
        'total_attendees': len(event_attendees),
        'total_absences': len(event.expected_absences),
        'potential_attendees': total_course_subscriptions
    })

    return {
        "title": event.title,
        "allDay": event_data.get('allDay', False) if event_data else False,
        "extended_props": extended_props,
        "attendance_day_id": event.attendance_day_id,
        "attendance_registry": event.attendance_registry.attendance_registry_id,
        "date": event.date,
        "start": event_data.get('start') if event_data else None,
        "end": event_data.get('end') if event_data else None,
        "course_id": event.attendance_registry.course.course_id,
        "total_attendees": len(event_attendees),
        "total_expected_absences": len(event.expected_absences),
        "potential_attendees": potential_attendees,
        "total_expected_total": total_course_subscriptions - len(event.expected_absences),
        "attendees": event_attendees,
        "expected_absences": expected_absences,
    }
