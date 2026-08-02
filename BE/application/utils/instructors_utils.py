import base64
import io
from functools import reduce

import pytz
from django.db.models import Value
from django.db.models.functions import Concat

from application.models import AttendanceDay, AttendanceRegistry, Instructor, CourseSubscription
import logging
import pandas as pd
from typing import Optional, Union

logger = logging.getLogger(__name__)


def generate_xlsx_report_from_data(data: list, schema: dict) -> Optional[bytes]:
    """
    Generate an Excel file from a list of dictionaries.

    Args:
        data: List of dictionaries containing the data
        schema: Dictionary containing the schema of the data

    Returns:
        The Excel file as bytes or None if error
    """
    df = pd.DataFrame(data)
    df = df.rename(columns=schema)

    # Create BytesIO object to hold the Excel file
    excel_file = io.BytesIO()

    # Create ExcelWriter object with xlsxwriter engine
    writer = pd.ExcelWriter(excel_file, engine='xlsxwriter')

    # Write the dataframe to Excel
    df.to_excel(writer, sheet_name='Sheet1', index=False)

    # Auto-adjust columns width
    worksheet = writer.sheets['Sheet1']
    for i, col in enumerate(df.columns):
        column_len = max(df[col].astype(str).str.len().max(), len(col)) + 2
        worksheet.set_column(i, i, column_len)

    # Close the writer to save the file
    writer.close()
    excel_file.seek(0)

    return excel_file.getvalue()


def generate_csv_report_from_data(data: list, schema: dict) -> Optional[str]:
    """
    Generate a CSV file from a list of dictionaries.

    Args:
        data: List of dictionaries containing the data
        schema: Dictionary containing the schema of the data

    Returns:
        The CSV as a string or None if error
    """
    df = pd.DataFrame(data)
    df = df.rename(columns=schema)
    return df.to_csv(index=False)


def generate_report_from_data(data: list, schema: dict, type='xlsx') -> Optional[Union[bytes, str]]:
    """
    Generate a report from data.

    Args:
        data: List of dictionaries containing the data
        schema: Dictionary containing the schema of the data
        type: Type of report ('xlsx' or 'csv')

    Returns:
        Bytes for xlsx, String for csv, or None if error
    """
    try:
        if type == 'xlsx':
            return generate_xlsx_report_from_data(data, schema)
        elif type == 'csv':
            return generate_csv_report_from_data(data, schema)
        else:
            logger.error(f"Unsupported report type: {type}")
            return None
    except Exception as e:
        logger.error(f"Error generating report: {str(e)}")
        return None

def get_value_by_path(item: dict, path: str):
    """
    Get a value from a dictionary using a dot-separated path.

    Args:
        item: The dictionary to search in
        path: Dot-separated path to the value (e.g. "user.address.city")

    Returns:
        The value if found, None if any part of the path is missing

    Example:
        >>> data = {"user": {"address": {"city": "Rome"}}}
        >>> get_value_by_path(data, "user.address.city")
        'Rome'
    """
    try:
        return reduce(lambda d, key: d.get(key) if isinstance(d, dict) else None,
                      path.split('.'),
                      item)
    except (AttributeError, TypeError):
        return None

def get_report(user_id: str, start_date, end_date, sport_association_id: str):

    logger.info(f"Generating report for user {user_id} from {start_date} to {end_date} for sport association {sport_association_id}")
    results = []

    # get instructors
    instructors = Instructor.objects.filter(
       user_id=user_id,
    )

    instructors_ids = [str(instructor.instructor_id) for instructor in instructors]

    # get the attendance registry
    registries = AttendanceRegistry.objects.filter(
        course__sport_association_id=sport_association_id,
    ).select_related('course').iterator(chunk_size=100)

    # At the start of get_report function:
    rome_tz = pytz.timezone('Europe/Rome')
    utc_tz = pytz.UTC

    # Convert start_date and end_date from Rome to UTC
    start_date = rome_tz.localize(start_date).astimezone(utc_tz)
    end_date = rome_tz.localize(end_date).astimezone(utc_tz)

    course_subscriptions_ids = set()

    for registry in registries:
        events = registry.events
         # Get the attendance days
        attendance_days = AttendanceDay.objects.filter(
            attendance_registry=registry,
            date__range=[start_date, end_date]
        ).order_by('date').iterator(chunk_size=100)

        for attendance_day in attendance_days:
            # log the day date
            logger.info(f"Processing attendance day {attendance_day.date} for registry {registry.attendance_registry_id}")

            # find the associated event
            event = None
            for e in events:
                if e['event_id'] == str(attendance_day.associated_event):
                    event = e
                    break
            if event:
                # check the path exists 'extendedProps.instructor.instructor_id'
                instructor_obj = get_value_by_path(event, 'extendedProps.instructor')
                description = get_value_by_path(event, 'extendedProps.description')
                logger.info(f"Found instructor_id {instructor_obj} for event {event['title']}")
                # check if instructor is a dict or a list
                if instructor_obj is not None:
                    if isinstance(instructor_obj, list):
                        instructor_id_list = [inst.get('instructor_id') for inst in instructor_obj]
                    else:
                        instructor_id_list = [str(instructor_obj.get('instructor_id'))]

                    for instructor_id in instructor_id_list:
                        if instructor_id in instructors_ids:
                            instructor = instructors.filter(instructor_id=instructor_id).first()
                            # set the date to format 'Europe/Rome', 'DD/MM/YYYY HH24:MI'
                            attendees = []
                            expected_absences = []

                            if attendance_day.attendees is not None:
                                for attendee in attendance_day.attendees:
                                    attendees.append(attendee['course_subscription_id'])
                                    course_subscriptions_ids.add(attendee['course_subscription_id'])
                            if attendance_day.expected_absences is not None:
                                for expected_absence in attendance_day.expected_absences:
                                    expected_absences.append(expected_absence['course_subscription_id'])
                                    course_subscriptions_ids.add(expected_absence['course_subscription_id'])
                            # make attendance_day.date to 'Europe/Rome', 'DD/MM/YYYY HH24:MI'
                            timezone_date = attendance_day.date.astimezone(pytz.timezone('Europe/Rome')) #.strftime('%d/%m/%Y %H:%M')
                            results.append({
                                'first_name': instructor.first_name,
                                'last_name': instructor.last_name,
                                'course': registry.course.title,
                                'title': attendance_day.title,
                                'description': description if description else '',
                                'date': timezone_date,
                                'attendees_count': len(attendance_day.attendees) if attendance_day.attendees else 0,
                                'attendees': attendees,
                                'expected_absences': expected_absences,
                            })

    course_subscriptions = CourseSubscription.objects.filter(
        course_subscription_id__in=course_subscriptions_ids
    ).annotate(
        full_name=Concat(
            'subscription__associate__first_name',
            Value(' '),
            'subscription__associate__last_name'
        )
    ).values_list('course_subscription_id', 'full_name')

    subscription_lookup = {
        str(cs[0]): cs[1]
        for cs in course_subscriptions
    }

    for result in results:
        result['attendees'] = ", ".join([
            subscription_lookup.get(str(cs_id), 'Atleta cancellato')
            for cs_id in result['attendees']
        ])
        result['expected_absences'] = ", ".join([
            subscription_lookup.get(str(cs_id), 'Atleta cancellato')
            for cs_id in result['expected_absences']
        ])

    # sort by first_name, last_name, date from older date to most recent date
    results.sort(key=lambda x: (x['first_name'], x['last_name'], x['date']))

    # convert all the dates to string
    for result in results:
        result['date'] = result['date'].strftime('%d/%m/%Y %H:%M')

    report = generate_report_from_data(results, {
        'first_name': 'Nome',
        'last_name': 'Cognome',
        'course': 'Corso',
        'title': 'Titolo',
        'description': 'Descrizione',
        'date': 'Data',
        'attendees_count': 'Numero di partecipanti',
        'attendees': 'Partecipanti',
        'expected_absences': 'Assenti previsti',
    }, type='xlsx')

    if report is not None:
        if isinstance(report, bytes):  # Excel file
            report = base64.b64encode(report).decode('utf-8')

    return results, report