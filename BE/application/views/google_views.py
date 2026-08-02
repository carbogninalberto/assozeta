
import pytz
import requests
from django.http import HttpResponse
from django.utils.dateparse import parse_datetime
from rest_framework.decorators import api_view, permission_classes
from application.models import User, AttendanceRegistry
from application.models.courses_models import Course
from core.middleware import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from googleapiclient.discovery import build

import google_auth_oauthlib
import core.settings as settings
import logging

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def google_check(request):
    user = request.user
    if request.collaborator:
        user = request.original_user

    return Response({'google_sync_enabled': user.google_sync_enabled}, status.HTTP_200_OK)


@api_view(['GET', 'DELETE'])
@permission_classes([IsAuthenticated])
def google_calendar_config(request):

    if request.method == 'DELETE':
        user = request.user
        if request.collaborator:
            user = request.original_user

        logger.info("Revoking Google Calendar integration", extra={'user_id': str(user.user_id)})
        # revoke the token
        credentials = user.get_google_credentials()
        # Revoke a token
        requests.post('https://oauth2.googleapis.com/revoke',
                      params={'token': credentials.token},
                      headers={'content-type': 'application/x-www-form-urlencoded'})

        user.integration_google_state = None
        user.integration_google_credentials = None
        user.integration_google_auto_sync = False
        user.save()
        logger.info("Google Calendar integration revoked", extra={'user_id': str(user.user_id)})
        return Response({'msg': 'deleted'}, status.HTTP_200_OK)

    # Create flow instance to manage the OAuth 2.0 Authorization Grant Flow steps.
    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
        settings.GOOGLE_CALENDAR_CLIENT_SECRET_FILE, scopes=settings.GOOGLE_CALENDAR_SCOPES)

    # The URI created here must exactly match one of the authorized redirect URIs
    # for the OAuth 2.0 client, which you configured in the API Console. If this
    # value doesn't match an authorized URI, you will get a 'redirect_uri_mismatch'
    # error.
    flow.redirect_uri = settings.GOOGLE_CALENDAR_REDIRECT_URL

    authorization_url, state = flow.authorization_url(
        # Enable offline access so that you can refresh an access token without
        # re-prompting the user for permission. Recommended for web server apps.
        access_type='offline',
        # Enable incremental authorization. Recommended as a best practice.
        include_granted_scopes='true')

    # Store the state so the callback can verify the auth server response.
    request.session['state'] = state

    if request.collaborator:
        request.original_user.integration_google_state = state
        request.original_user.save()
    else:
        request.user.integration_google_state = state
        request.user.save()

    return Response({"authorization_url": authorization_url})


@api_view(['GET'])
def google_oauth2callback(request):
    logger.info("Google OAuth2 callback received")
    # if cancel the flow redirect to the home page
    if 'error' in request.query_params:
        logger.warning("Google OAuth2 flow cancelled by user")
        html_body = """
            <html>
                <head>
                    <script type="text/javascript">
                        window.close();
                    </script>            
                </head>
                <body>
                    <p>Richiesta cancellata. Per favore chiudi la pagina.</p>
                </body>
            </html>
        """

        return HttpResponse(html_body, content_type='text/html')

    user = User.objects.filter(integration_google_state=request.query_params['state']).first()
    if user is None:
        logger.error("No user found for Google OAuth state", extra={'state': request.query_params.get('state')})
        return Response({'msg': 'No user found for the integration flow.'}, status.HTTP_400_BAD_REQUEST)

    logger.info("Fetching Google OAuth token", extra={'user_id': str(user.user_id)})
    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
        settings.GOOGLE_CALENDAR_CLIENT_SECRET_FILE, scopes=settings.GOOGLE_CALENDAR_SCOPES)
    flow.redirect_uri = settings.GOOGLE_CALENDAR_REDIRECT_URL

    flow.fetch_token(code=request.query_params['code'])

    credentials = flow.credentials
    user.integration_google_credentials = credentials.to_json()
    if user.is_collaborator is False:
        user.integration_google_auto_sync = True
    user.save()
    logger.info("Google Calendar credentials stored", extra={'user_id': str(user.user_id), 'auto_sync': user.integration_google_auto_sync})

    html_body = """
            <html>
                <head>
                    <script type="text/javascript">
                        window.close();
                    </script>            
                </head>
                <body>
                    <p>Autorizzato. Per favore chiudi la pagina.</p>
                </body>
            </html>
        """

    return HttpResponse(html_body, content_type='text/html')


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def google_calendar_list(request):
    user = request.user
    if request.collaborator:
        user = request.original_user

    logger.info("Fetching Google Calendar list", extra={'user_id': str(user.user_id)})
    # use the code for API call request of the calendar list
    service = build('calendar', 'v3', credentials=user.get_google_credentials())

    page_token = None
    calendars = []
    while True:
        calendar_list = service.calendarList().list(pageToken=page_token).execute()
        for calendar_list_entry in calendar_list['items']:
            calendars.append(calendar_list_entry)
        page_token = calendar_list.get('nextPageToken')
        if not page_token:
            break

    logger.info("Google Calendar list fetched", extra={'user_id': str(user.user_id), 'calendar_count': len(calendars)})
    return Response({'data': {
        'calendars': calendars
    }}, status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def google_calendar_export_course(request, course_id):
    if course_id is None:
        return Response({'msg': 'Course id is required.'}, status.HTTP_400_BAD_REQUEST)

    course = Course.objects.filter(course_id=course_id).first()

    if course is None:
        return Response({'msg': 'Course not found.'}, status.HTTP_404_NOT_FOUND)

    user = request.user
    if request.collaborator:
        user = request.original_user

    logger.info("Exporting course to Google Calendar", extra={'user_id': str(user.user_id), 'course_id': str(course_id), 'course_title': course.title})
    # use the code for API call request of the calendar list
    service = build('calendar', 'v3', credentials=user.get_google_credentials())

    if course.google_calendar_id is not None:
        logger.debug("Deleting existing Google Calendar", extra={'calendar_id': course.google_calendar_id, 'course_id': str(course_id)})
        try:
            # delete the calendar
            service.calendars().delete(calendarId=course.google_calendar_id).execute()
        except Exception as e:
            logger.warning("Failed to delete existing Google Calendar", extra={'calendar_id': course.google_calendar_id, 'error': str(e)})

    # Create a new calendar
    calendar_data = {
        'description': "Il calendario viene sincronizzato in automatico, non modificarlo.",#str(course.course_id),
        'summary': course.title,
        'timeZone': 'Europe/Rome'
    }
    logger.info("Creating new Google Calendar", extra={'course_id': str(course_id), 'title': course.title})
    try:
        calendar = service.calendars().insert(body=calendar_data).execute()
        logger.info("Google Calendar created", extra={'calendar_id': calendar['id'], 'course_id': str(course_id)})
    except Exception as e:
        # check is this case:
        if 'quotaExceeded' in str(e):
            logger.error("Google Calendar quota exceeded", extra={'user_id': str(user.user_id), 'course_id': str(course_id)}, exc_info=True)
            return Response({'msg': 'quotaExceeded'}, status.HTTP_403_FORBIDDEN)
        logger.error("Error creating Google Calendar", extra={'course_id': str(course_id), 'error': str(e)}, exc_info=True)
        return Response({'msg': 'Error creating calendar.'}, status.HTTP_400_BAD_REQUEST)
    if user.is_collaborator is False:
        course.google_calendar_id = calendar['id']
    course.save()

    def foreground_color(background_color):
        '''
        background_color: hex color with #, example: #ffffff
        return: hex color with #, example: #ffffff
        '''
        r = int(background_color[1:3], 16)
        g = int(background_color[3:5], 16)
        b = int(background_color[5:7], 16)
        yiq = ((r * 299) + (g * 587) + (b * 114)) / 1000
        return '#000000' if yiq >= 128 else '#ffffff'

    # we need also to insert the calendar in it list
    calendar_list_entry = {
        'id': calendar['id'],  # Use the ID of the newly created calendar
        'backgroundColor': course.google_background_color,
        'foregroundColor': foreground_color(course.google_background_color),
    }
    # delete and insert the calendar in the list
    try:
        service.calendarList().delete(calendarId=calendar['id']).execute()
    except Exception as e:
        pass
    service.calendarList().insert(body=calendar_list_entry).execute()

    registry = AttendanceRegistry.objects.filter(course_id=course.course_id).first()

    if registry is None:
        return Response({'msg': 'Attendance Registry not found.'}, status.HTTP_404_NOT_FOUND)

    # export events to the calendar
    for event in registry.events:
        # convert event_id to base32hex for google event id
        event['event_id'] = event['event_id'].replace('-', '')
        # convert event to google event compatible
        start_dt = parse_datetime(event['start'])
        end_dt = None
        all_day = False
        description = ''
        if 'extendedProps' in event.keys():
            if 'instructor' in event['extendedProps'] and \
                    isinstance(event['extendedProps']['instructor'], dict) and \
                    event['extendedProps']['instructor'] is not None and \
                    'label' in event['extendedProps']['instructor']:
                description = f"Istruttore: {event['extendedProps']['instructor']['label']}"
        if event['end'] is not None and event['end'] != "None":
            end_dt = parse_datetime(event['end'])
        else:
            start_dt = parse_datetime(event['start'])
            all_day = True
            # end_date default is start date day until 23:59
            end_dt = start_dt.replace(hour=23, minute=59)
        event_body = {
            'extendedProperties': {
                'private': {
                    'course_id': str(course.course_id),
                    'event_id': event['event_id']
                }
            },
            'calendar_id': calendar['id'],
            'summary': event['title'],
            'description': description,
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'email', 'minutes': 24 * 60},
                    {'method': 'popup', 'minutes': 10},
                ],
            },
        }

        # assume both start and end are in UTC and convert them to Europe/Rome
        rome_tz = pytz.timezone('Europe/Rome')
        start_dt_utc = start_dt.replace(tzinfo=pytz.UTC)
        end_dt_utc = end_dt.replace(tzinfo=pytz.UTC)
        start_dt = start_dt_utc.astimezone(rome_tz)
        end_dt = end_dt_utc.astimezone(rome_tz)

        if all_day:
            event_body['start'] = {
                'date': start_dt.strftime('%Y-%m-%d'),
                'timeZone': 'Europe/Rome',
            }
            event_body['end'] = {
                'date': end_dt.strftime('%Y-%m-%d'),
                'timeZone': 'Europe/Rome',
            }
        else:
            event_body['start'] = {
                'dateTime': start_dt.strftime('%Y-%m-%dT%H:%M:%S'),
                'timeZone': 'Europe/Rome',
            }
            event_body['end'] = {
                'dateTime': end_dt.strftime('%Y-%m-%dT%H:%M:%S'),
                'timeZone': 'Europe/Rome',
            }

        try:
            service.events().insert(calendarId=calendar['id'], body=event_body).execute()
        except Exception as e:
            logger.error("Error inserting Google Calendar event", extra={'event_id': event['event_id'], 'calendar_id': calendar['id'], 'error': str(e)}, exc_info=True)

    logger.info("Course exported to Google Calendar successfully", extra={'course_id': str(course_id), 'calendar_id': course.google_calendar_id, 'event_count': len(registry.events)})
    return Response({'msg': 'exported.', 'google_calendar_id': course.google_calendar_id}, status.HTTP_200_OK)


