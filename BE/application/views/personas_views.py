from django.db.models import OuterRef, Exists, Count
from rest_framework import viewsets, status

from application.utils.subscriptions_utils import smart_search
from core.middleware import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied

from application.models import Subscription
from application.models.user_models import Associate, AssociateTutorRelation
from application.serializers.personas_serializers import AssociateSerializer, AssociateSubscriptionSerializer, \
    AssociateSearchSerializer
from application.utils.api_utils import KTDatatablePagination
import logging

logger = logging.getLogger(__name__)


class AssociateViewSet(viewsets.ModelViewSet):
    queryset = Associate.objects.all()
    serializer_class = AssociateSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = KTDatatablePagination

    def get_queryset(self):
        from datetime import date

        # Calculate age in the database
        today = date.today()

        qs = Associate.objects.filter(
            sport_association=self.request.user.sport_association,
            deleted=False
        ).prefetch_related(
            # Prefetch tutor relations with their tutor data
            'tutor_relations__tutor'
        ).annotate(
            # Just annotate is_tutor and tutors_count for now
            # We'll handle age in the serializer with prefetched data
            # Annotate is_tutor instead of using property
            is_tutor_annotated=Exists(
                AssociateTutorRelation.objects.filter(tutor=OuterRef('pk'))
            ),
            tutors_count=Count('tutor_relations')
        )

        # Global search
        general_search = self.request.query_params.get('query[generalSearch]', None)
        if general_search:
            qs = smart_search(qs, general_search)

        # Is tutor filter
        is_tutor = self.request.query_params.get('query[is_tutor]', None)
        if is_tutor is not None and is_tutor != '':
            qs = qs.filter(is_tutor_annotated=is_tutor)


        associate_type = self.request.query_params.get('query[type]', None)
        if associate_type:
            types = associate_type.split(',')
            if 'athletes' in types and 'tutors' in types:
                # If both athlete and tutor are selected, we want all associates
                pass
            elif 'athletes' in types:
                # If only athlete is selected, filter out tutors
                qs = qs.filter(is_tutor_annotated=False)
            elif 'tutors' in types:
                # If only tutor is selected, filter for tutors
                qs = qs.filter(is_tutor_annotated=True)

        # Age filter maxAge and minAge
        min_age = self.request.query_params.get('query[minAge]', None)
        max_age = self.request.query_params.get('query[maxAge]', None)
        if min_age is not None or max_age is not None:
            if min_age is not None:
                qs = qs.filter(born_date__lte=today.replace(year=today.year - int(min_age)))
            if max_age is not None:
                qs = qs.filter(born_date__gte=today.replace(year=today.year - int(max_age)))


        # Sorting
        sort_field = self.request.query_params.get('sort[field]', None)
        sort_sort = self.request.query_params.get('sort[sort]', None)
        if sort_field and sort_sort:
            if sort_field == 'tutors':
                sort_field = 'tutors_count'
                sort_sort = 'asc' if sort_sort == 'asc' else 'desc'
            elif sort_field == 'age':
                sort_field = 'born_date'  # Sort by birth date instead
                sort_sort = 'desc' if sort_sort == 'asc' else 'asc'
            elif sort_field == 'full_name':
                # Sort by full name using Concat
                sort_field = 'last_name'
                sort_sort = 'asc' if sort_sort == 'asc' else 'desc'

            if sort_sort == 'asc':
                qs = qs.order_by(sort_field)
            else:
                qs = qs.order_by(f'-{sort_field}')

        return qs

    def perform_create(self, serializer):
        self.sport_association = self.request.user.sport_association
        serializer.save(user=self.request.user, sport_association=self.sport_association)

    def add(self, request):
        logger.info("Creating new associate", extra={'user_id': str(request.user.user_id), 'sport_association_id': str(request.user.sport_association.sport_association_id)})
        serializer = self.get_serializer(
            data=request.data,
            context={'tutors_data': request.data.pop('tutors_data', [])}
        )
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        logger.info("Associate created successfully", extra={'associate_id': str(serializer.data['associate_id']), 'first_name': serializer.data.get('first_name'), 'last_name': serializer.data.get('last_name')})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def delete(self, request, pk=None):
        instance = self.get_object()
        logger.info("Deleting associate (soft delete)", extra={'user_id': str(request.user.user_id), 'associate_id': str(pk)})
        logger.debug("Checking associate ownership", extra={'associate_sport_association': str(instance.sport_association.sport_association_id), 'user_sport_association': str(request.user.sport_association.sport_association_id)})
        if instance.sport_association.sport_association_id != request.user.sport_association.sport_association_id:
            logger.warning("Unauthorized associate deletion attempt", extra={'user_id': str(request.user.user_id), 'associate_id': str(pk)})
            raise PermissionDenied("User not allowed.")
        instance.deleted = True
        instance.save()
        logger.info("Associate deleted successfully", extra={'associate_id': str(pk), 'first_name': instance.first_name, 'last_name': instance.last_name})
        return Response(status=status.HTTP_204_NO_CONTENT)

    def bulk_delete(self, request):
        associate_ids = request.data.get('associate_ids', [])
        logger.info("Bulk deleting associates", extra={'user_id': str(request.user.user_id), 'count': len(associate_ids)})
        associates = Associate.objects.filter(
            associate_id__in=associate_ids,
            sport_association=request.user.sport_association
        )
        deleted_count = associates.count()
        associates.update(deleted=True)
        logger.info("Associates bulk deleted successfully", extra={'count': deleted_count})
        return Response(status=status.HTTP_204_NO_CONTENT)

    def update(self, request, pk=None):
        instance = self.get_object()
        logger.info("Updating associate", extra={'user_id': str(request.user.user_id), 'associate_id': str(pk)})
        logger.debug("Checking associate ownership", extra={'associate_sport_association': str(instance.sport_association.sport_association_id), 'user_sport_association': str(request.user.sport_association.sport_association_id)})
        if instance.sport_association.sport_association_id != request.user.sport_association.sport_association_id:
            logger.warning("Unauthorized associate update attempt", extra={'user_id': str(request.user.user_id), 'associate_id': str(pk)})
            raise PermissionDenied("User not allowed.")
        serializer = self.get_serializer(
            instance,
            data=request.data,
            context={'tutors_data': request.data.get('tutors_data', [])},
            partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        # return the refreshed object
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        logger.info("Associate updated successfully", extra={'associate_id': str(pk), 'first_name': instance.first_name, 'last_name': instance.last_name})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def info(self, request, pk=None):
        instance = self.get_object()
        if instance.sport_association.sport_association_id != request.user.sport_association.sport_association_id:
            raise PermissionDenied("User not allowed.")
        serializer = self.get_serializer(instance)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def all_tutors(self, request):
        tutors = Associate.objects.filter(
            sport_association=request.user.sport_association,
            deleted=False
        ).values('associate_id', 'first_name', 'last_name')
        return Response(tutors, status=status.HTTP_200_OK)

    def list(self, request):
        queryset = self.get_queryset()
        paginator = self.paginator
        page = paginator.paginate_queryset(queryset, request)

        if page is not None:
            serializer = AssociateSearchSerializer(page, many=True)
            data = serializer.data

            meta = {
                "total": paginator.page.paginator.count,
                "page": paginator.page.number,
                "pages": paginator.page.paginator.num_pages,
                "perpage": paginator.page.paginator.per_page,
                "rowIds": [associate.associate_id for associate in page]
            }

            return Response({
                "data": data,
                "meta": meta
            }, status=status.HTTP_200_OK)

        serializer = AssociateSearchSerializer(queryset, many=True)
        return Response({"data": serializer.data}, status=status.HTTP_200_OK)

    def all(self, request):
        personas = Associate.objects.filter(
            sport_association=self.request.user.sport_association,
            deleted=False
        )
        all_personas = []

        for persona in personas:
            all_personas.append({
                "associate_id": persona.associate_id,
                "first_name": persona.first_name,
                "last_name": persona.last_name,
            })
        return Response({"data": all_personas}, status=status.HTTP_200_OK)

    def all_with_subscriptions(self, request):
        """
        Returns all active (current) subscriptions with their associated persona data.
        Single query for optimal performance, keyed by subscription_id.
        """
        from datetime import date

        today = date.today()

        # Single query: get all current subscriptions (start_date <= today <= end_date)
        subscriptions = Subscription.objects.filter(
            sport_association=self.request.user.sport_association,
            deleted=False,
            archived=False,
            associate__isnull=False,
            associate__deleted=False,
            start_date__lte=today,
            end_date__gte=today
        ).select_related('associate').values(
            'subscription_id', 'status_flag', 'type',
            'associate_id', 'associate__first_name', 'associate__last_name',
            'associate__email', 'associate__tax_code'
        )

        # Build response dict keyed by associate_id
        result = {
            str(s['associate_id']): {
                'subscription_id': s['subscription_id'],
                'associate_id': s['associate_id'],
                'first_name': s['associate__first_name'],
                'last_name': s['associate__last_name'],
                'email': s['associate__email'],
                'tax_code': s['associate__tax_code'],
                'subscription_status': s['status_flag'],
                'type': s['type'],
            }
            for s in subscriptions
        }

        return Response({"data": result}, status=status.HTTP_200_OK)

    def recover(self, request, pk=None):
        logger.info("Recovering deleted associate", extra={'user_id': str(request.user.user_id), 'associate_id': str(pk)})
        instance = Associate.objects.all_objects().filter(pk=pk).first()
        if not instance:
            logger.warning("Associate not found for recovery", extra={'associate_id': str(pk)})
            return Response(status=status.HTTP_404_NOT_FOUND)

        logger.debug("Checking associate ownership for recovery", extra={'associate_sport_association': str(instance.sport_association.sport_association_id), 'user_sport_association': str(request.user.sport_association.sport_association_id)})
        if instance.sport_association.sport_association_id != request.user.sport_association.sport_association_id:
            logger.warning("Unauthorized associate recovery attempt", extra={'user_id': str(request.user.user_id), 'associate_id': str(pk)})
            raise PermissionDenied("User not allowed.")
        instance.deleted = False
        instance.save()
        logger.info("Associate recovered successfully", extra={'associate_id': str(pk), 'first_name': instance.first_name, 'last_name': instance.last_name})
        return Response({"mgs": "Ripristinata con successo"}, status=status.HTTP_200_OK)


class AssociateSubscriptionViewSet(viewsets.ModelViewSet):
    queryset = Subscription.objects.all()
    serializer_class = AssociateSubscriptionSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = KTDatatablePagination

    def get_queryset(self):
        qs = Subscription.objects.filter(
            sport_association=self.request.user.sport_association,
            deleted=False
        ).order_by('-creation_date')
        return qs

    def list(self, request, pk=None):
        if pk:
            queryset = self.get_queryset().filter(associate_id=pk)
        else:
            queryset = self.get_queryset()
        paginator = self.paginator
        page = paginator.paginate_queryset(queryset, request)

        if page is not None:
            serializer = self.get_serializer(page, many=True)
            data = serializer.data

            meta = {
                "total": paginator.page.paginator.count,
                "page": paginator.page.number,
                "pages": paginator.page.paginator.num_pages,
                "perpage": paginator.page.paginator.per_page,
                "rowIds": [associate.associate_id for associate in page]
            }

            return Response({
                "data": data,
                "meta": meta
            }, status=status.HTTP_200_OK)

        serializer = self.get_serializer(queryset, many=True)
        return Response({"data": serializer.data}, status=status.HTTP_200_OK)

    def delete(self, request, pk=None):
        instance = self.get_object()
        logger.info("Deleting associate subscription (soft delete)", extra={'user_id': str(request.user.user_id), 'subscription_id': str(pk)})
        logger.debug("Checking subscription ownership", extra={'subscription_sport_association': str(instance.sport_association.sport_association_id), 'user_sport_association': str(request.user.sport_association.sport_association_id)})
        if instance.sport_association.sport_association_id != request.user.sport_association.sport_association_id:
            logger.warning("Unauthorized subscription deletion attempt", extra={'user_id': str(request.user.user_id), 'subscription_id': str(pk)})
            raise PermissionDenied("User not allowed.")
        instance.deleted = True
        instance.save()
        logger.info("Associate subscription deleted successfully", extra={'subscription_id': str(pk)})
        return Response(status=status.HTTP_204_NO_CONTENT)