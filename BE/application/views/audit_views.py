"""
Copyright: Bakney S.r.l.
Audit Log API views.
"""
import logging
from datetime import datetime

from django.db.models import Q, Count
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from core.middleware import IsAuthenticated
from auditlog.models import LogEntry
from django.contrib.contenttypes.models import ContentType

from application.models.audit_models import AuditLogIndex
from application.models.user_models import SportAssociation
from application.serializers.audit_serializers import (
    AuditLogEntrySerializer,
    AuditLogListMinimalSerializer
)
from application.utils.api_utils import KTDatatablePagination

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def audit_log_list(request):
    """
    API endpoint to list audit logs for the current sport association.

    Query parameters:
    - pagination[page]: Page number (default: 1)
    - pagination[perpage]: Items per page (default: 10)
    - query[generalSearch]: Search in object_repr, actor name/email
    - query[action]: Filter by action type (0=CREATE, 1=UPDATE, 2=DELETE)
    - query[model]: Filter by model name(s), comma-separated
    - query[actor_id]: Filter by actor user ID
    - query[object_id]: Filter by specific object ID (UUID) - shows all changes to that object
    - query[date_from]: Filter from date (YYYY-MM-DD)
    - query[date_to]: Filter to date (YYYY-MM-DD)
    - sort[field]: Sort field (timestamp, action, model)
    - sort[sort]: Sort direction (asc, desc)
    - minimal: If "true", return minimal fields only (default: false)
    """
    logger.info("Audit log list request", extra={'user_id': str(request.user.user_id)})

    # Get sport association
    try:
        sport_association = SportAssociation.objects.get(user=request.user)
    except SportAssociation.DoesNotExist:
        return Response(
            {"error": "Sport association not found"},
            status=status.HTTP_404_NOT_FOUND
        )

    # Get filter parameters
    general_search = request.GET.get('query[generalSearch]', None)
    action = request.GET.get('query[action]', None)
    model_name = request.GET.get('query[model]', None)
    actor_id = request.GET.get('query[actor_id]', None)
    object_id = request.GET.get('query[object_id]', None)
    date_from = request.GET.get('query[date_from]', None)
    date_to = request.GET.get('query[date_to]', None)
    sort_field = request.GET.get('sort[field]', 'timestamp')
    sort_direction = request.GET.get('sort[sort]', 'desc')
    minimal = request.GET.get('minimal', 'false').lower() == 'true'

    # Get log entry IDs for this sport association via index
    indexed_log_ids = AuditLogIndex.objects.filter(
        sport_association=sport_association
    ).values_list('log_entry_id', flat=True)

    # Base queryset
    queryset = LogEntry.objects.filter(
        pk__in=indexed_log_ids
    ).select_related('actor', 'content_type')

    # Exclude noise from audit logs:
    # 1. accesstoken logs (OAuth tokens aren't meaningful audit data)
    # 2. User logs with last_login changes (login events aren't meaningful)
    # 3. Logs without an actor (automated tasks/Celery jobs)
    queryset = queryset.exclude(
        content_type__model='accesstoken'
    ).exclude(
        content_type__model='user',
        changes__has_key='last_login'
    )

    # Apply filters
    if general_search:
        queryset = queryset.filter(
            Q(object_repr__icontains=general_search) |
            Q(actor__email__icontains=general_search) |
            Q(actor__first_name__icontains=general_search) |
            Q(actor__last_name__icontains=general_search)
        )

    if action is not None and action != '':
        try:
            action_int = int(action)
            queryset = queryset.filter(action=action_int)
        except ValueError:
            pass

    if model_name:
        # Support comma-separated model names
        model_names = [m.strip().lower() for m in model_name.split(',')]
        content_types = ContentType.objects.filter(model__in=model_names)
        queryset = queryset.filter(content_type__in=content_types)

    if actor_id:
        queryset = queryset.filter(actor_id=actor_id)

    if object_id:
        queryset = queryset.filter(object_pk=object_id)

    if date_from:
        try:
            from_date = datetime.strptime(date_from, '%Y-%m-%d')
            from_date = timezone.make_aware(from_date) if timezone.is_naive(from_date) else from_date
            queryset = queryset.filter(timestamp__gte=from_date)
        except ValueError:
            pass

    if date_to:
        try:
            to_date = datetime.strptime(date_to, '%Y-%m-%d')
            to_date = to_date.replace(hour=23, minute=59, second=59)
            to_date = timezone.make_aware(to_date) if timezone.is_naive(to_date) else to_date
            queryset = queryset.filter(timestamp__lte=to_date)
        except ValueError:
            pass

    # Apply sorting
    sort_mapping = {
        'timestamp': 'timestamp',
        'action': 'action',
        'model': 'content_type__model',
    }
    sort_field_db = sort_mapping.get(sort_field, 'timestamp')
    if sort_direction == 'asc':
        queryset = queryset.order_by(sort_field_db)
    else:
        queryset = queryset.order_by(f'-{sort_field_db}')

    # Paginate
    paginator = KTDatatablePagination()
    page = paginator.paginate_queryset(queryset, request)

    # Choose serializer based on minimal flag
    serializer_class = AuditLogListMinimalSerializer if minimal else AuditLogEntrySerializer

    if page is not None:
        serializer = serializer_class(page, many=True)

        # Return in expected format
        return Response({
            'data': serializer.data,
            'meta': {
                'total': paginator.page.paginator.count,
                'page': paginator.page.number,
                'pages': paginator.page.paginator.num_pages,
                'perpage': paginator.page_size
            }
        })

    serializer = serializer_class(queryset, many=True)
    return Response({'data': serializer.data})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def audit_log_detail(request, log_id):
    """
    API endpoint to get detailed information about a single audit log entry.
    """
    logger.info("Audit log detail request", extra={
        'user_id': str(request.user.user_id),
        'log_id': log_id
    })

    # Get sport association
    try:
        sport_association = SportAssociation.objects.get(user=request.user)
    except SportAssociation.DoesNotExist:
        return Response(
            {"error": "Sport association not found"},
            status=status.HTTP_404_NOT_FOUND
        )

    # Verify access through index
    try:
        index = AuditLogIndex.objects.select_related('log_entry').get(
            log_entry_id=log_id,
            sport_association=sport_association
        )
    except AuditLogIndex.DoesNotExist:
        return Response(
            {"error": "Audit log not found"},
            status=status.HTTP_404_NOT_FOUND
        )

    # Fetch full log entry with related data
    log_entry = LogEntry.objects.select_related('actor', 'content_type').get(pk=log_id)
    serializer = AuditLogEntrySerializer(log_entry)

    return Response({'data': serializer.data})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def audit_log_models(request):
    """
    API endpoint to list available model types for filtering.
    Returns models that have audit logs for the current sport association.
    """
    try:
        sport_association = SportAssociation.objects.get(user=request.user)
    except SportAssociation.DoesNotExist:
        return Response(
            {"error": "Sport association not found"},
            status=status.HTTP_404_NOT_FOUND
        )

    # Get distinct content types from indexed logs
    indexed_log_ids = AuditLogIndex.objects.filter(
        sport_association=sport_association
    ).values_list('log_entry_id', flat=True)

    content_type_ids = LogEntry.objects.filter(
        pk__in=indexed_log_ids
    ).values_list('content_type_id', flat=True).distinct()

    content_types = ContentType.objects.filter(pk__in=content_type_ids)

    models_list = []
    for ct in content_types:
        verbose_name = AuditLogEntrySerializer.MODEL_VERBOSE_NAMES.get(
            ct.model,
            ct.model.replace('_', ' ').title()
        )
        models_list.append({
            'name': ct.model,
            'label': verbose_name
        })

    # Sort by label
    models_list.sort(key=lambda x: x['label'])

    return Response({'data': models_list})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def audit_log_stats(request):
    """
    API endpoint to get audit log statistics for the current sport association.
    """
    try:
        sport_association = SportAssociation.objects.get(user=request.user)
    except SportAssociation.DoesNotExist:
        return Response(
            {"error": "Sport association not found"},
            status=status.HTTP_404_NOT_FOUND
        )

    indexed_log_ids = AuditLogIndex.objects.filter(
        sport_association=sport_association
    ).values_list('log_entry_id', flat=True)

    total_logs = indexed_log_ids.count()

    # Count by action
    action_counts = LogEntry.objects.filter(
        pk__in=indexed_log_ids
    ).values('action').annotate(count=Count('id'))

    actions = {
        'create': 0,
        'update': 0,
        'delete': 0
    }
    for ac in action_counts:
        if ac['action'] == 0:
            actions['create'] = ac['count']
        elif ac['action'] == 1:
            actions['update'] = ac['count']
        elif ac['action'] == 2:
            actions['delete'] = ac['count']

    return Response({
        'data': {
            'total': total_logs,
            'by_action': actions
        }
    })
