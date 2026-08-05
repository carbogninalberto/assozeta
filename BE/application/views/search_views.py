"""
@ copyright: Bakney SRL
"""
import logging
from datetime import datetime

from django.utils import timezone
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import api_view

from application.utils.api_utils import compress_base64
from application.models import Carnet
from application.models.courses_models import Course, CourseSubscription, CourseTags
from application.models.user_models import SportAssociation, User
from application.serializers.auth_serializers import UserSerializerSearch, SportAssociationSearchProfileSerializer
from application.serializers.carnet_serializers import CarnetListInfoSerializer
from application.serializers.courses_serializers import CourseSerializer


logger = logging.getLogger(__name__)


@api_view(['GET'])
def search_profile(request, username):
    """
    Returns information about a certain profile
    """
    # check if in query there is module_info
    module_info = request.GET.get('module_info', None)

    logger.info("Searching profile", extra={'username': username, 'module_info': module_info is not None, 'authenticated': request.user.is_authenticated if hasattr(request, 'user') else False})
    u = User.objects.filter(username__iexact=username)

    data = {}
    if u.exists():
        if u.first().role is User.ATHLETE:
            data['user'] = UserSerializerSearch(u.first()).data
            data['user']['is_athlete'] = True
        elif module_info is not None:
            sport_association = SportAssociation.objects.get(user=u.first())
            data['user'] = UserSerializerSearch(u.first()).data
            data['user']['avatar_image'] = compress_base64(data['user']['avatar_image'])
            data['user']['sport_association'] = SportAssociationSearchProfileSerializer(sport_association).data
            if module_info is not None:
                data['user']['sport_association']['demand'] = sport_association.demand
                data['user']['sport_association']['regulation'] = sport_association.regulation
                data['user']['sport_association']['additional_sections'] = sport_association.additional_sections
                data['user']['sport_association']['show_regulation_to_members'] = sport_association.show_regulation_to_members
                data['user']['sport_association']['show_regulation_to_both'] = sport_association.show_regulation_to_both
                data['user']['sport_association']['show_regulation_to_athletes'] = sport_association.show_regulation_to_athletes
                data['user']['sport_association']['show_demand_to_members'] = sport_association.show_demand_to_members
                data['user']['sport_association']['show_demand_to_both'] = sport_association.show_demand_to_both
                data['user']['sport_association']['show_demand_to_athletes'] = sport_association.show_demand_to_athletes

                # Calculate if season is closing within 120 days
                user = u.first()
                today = timezone.now().date()

                # Get next season start date based on subscription_start_month and subscription_start_day
                current_year = today.year
                try:
                    next_season_start = datetime(current_year, user.subscription_start_month, user.subscription_start_day).date()
                    # If the next season start has already passed this year, use next year
                    if next_season_start <= today:
                        next_season_start = datetime(current_year + 1, user.subscription_start_month, user.subscription_start_day).date()
                except ValueError:
                    # Handle invalid dates (e.g., Feb 31)
                    next_season_start = None

                # Preregistration module is open from 120 days before season until the day before season starts
                if next_season_start:
                    days_until_next_season = (next_season_start - today).days
                    # Open if between 1 and 120 days (inclusive) until next season
                    is_open = 1 <= days_until_next_season <= 120
                    data['user']['sport_association']['preregistration_module_closed'] = not is_open
                    data['user']['sport_association']['remaining_preregistration_days'] = days_until_next_season if is_open else 0
                else:
                    data['user']['sport_association']['preregistration_module_closed'] = True
                    data['user']['sport_association']['remaining_preregistration_days'] = 0

            data['sport_association_id'] = sport_association.sport_association_id
        else:
            sport_association = SportAssociation.objects.get(user=u.first())
            sport_association_courses_unpinned = Course.objects.filter(
                sport_association=sport_association,
                status_flag=Course.ACTIVE,
                pinned=False
            ).order_by('-creation_date').iterator(chunk_size=100)

            sport_association_courses_pinned = Course.objects.filter(
                sport_association=sport_association,
                status_flag=Course.ACTIVE,
                pinned=True
            ).order_by('-creation_date').iterator(chunk_size=100)
            sport_association_carnets = Carnet.objects.filter(sport_association=sport_association).iterator(chunk_size=100)

            data['courses'] = []
            for idx, course in enumerate(sport_association_courses_unpinned):
                course_data = CourseSerializer(course).data
                course_data['total_subscriptions'] = CourseSubscription.objects.filter(course=course).count()
                data['courses'].append(course_data)

            data['courses_pinned'] = []
            for idx, course in enumerate(sport_association_courses_pinned):
                course_data = CourseSerializer(course).data
                course_data['total_subscriptions'] = CourseSubscription.objects.filter(course=course).count()
                data['courses_pinned'].append(course_data)

            data['carnets'] = CarnetListInfoSerializer(sport_association_carnets, many=True).data

            data['user'] = UserSerializerSearch(u.first()).data
            data['user']['avatar_image'] = compress_base64(data['user']['avatar_image'])
            data['user']['sport_association'] = SportAssociationSearchProfileSerializer(sport_association).data
            data['user']['is_athlete'] = False
            # data['user']['total_athletes'] = Subscription.objects.filter(sport_association=sport_association).count()
            data['sport_association_id'] = sport_association.sport_association_id

            # add CourseTags
            course_tags = CourseTags.objects.filter(sport_association=sport_association).iterator(chunk_size=100)
            data['courses_tags'] = [{'tag_id': tag.tag_id, 'tag_name': tag.tag_name} for tag in course_tags]

            # add active property to all the courses tags
            for course_tag in data['courses_tags']:
                course_tag['active'] = True

    else:
        raise NotFound(detail="user not found.")

    return Response({'data': data}, status=status.HTTP_200_OK)
