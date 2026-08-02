"""
@ copyright: Bakney SRL
"""
import logging

from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from core.middleware import IsAuthenticated
from application.models.payment_models import SupplierAndCustomers
from application.models.user_models import SportAssociation, User
from application.serializers.payment_serializers import SupplierSerializer
from application.utils.api_utils import is_valid_uuid, KTDatatablePagination, \
    filter_by_all_fields

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
# @cache_endpoint('supplier_list', timeout=60 * 60 * 24 * 7)
def supplier_list(request):
    """
    API endpoint to return a list of suppliers
    """
    logger.info("Retrieving suppliers list", extra={
        'user_id': str(request.user.user_id),
        'all': request.GET.get('all', None) is not None
    })

    paginator = KTDatatablePagination()
    general_search = request.GET.get('query[generalSearch]', None)
    sort_field = request.GET.get('sort[field]', None)
    sort_type = request.GET.get('sort[sort]', None)
    all = request.GET.get('all', None)

    if request.user.role == User.ATHLETE:
        logger.warning("Unauthorized supplier list access - athlete role", extra={
            'user_id': str(request.user.user_id),
            'role': request.user.role
        })
        # not allowed
        return Response(status=status.HTTP_403_FORBIDDEN)

    sport_association = SportAssociation.objects.get(user=request.user)
    suppliers = SupplierAndCustomers.objects.filter(sport_association=sport_association)

    if all:
        logger.info("Returning all suppliers", extra={
            'user_id': str(request.user.user_id),
            'count': suppliers.count()
        })
        return Response({'data': SupplierSerializer(suppliers, many=True).data}, status=status.HTTP_200_OK)

    if general_search:
        logger.debug("Filtering suppliers by search", extra={
            'user_id': str(request.user.user_id),
            'search_query': general_search[:50]
        })
        try:
            suppliers = filter_by_all_fields(suppliers, general_search)
        except Exception as e:
            logger.error("Error filtering suppliers/customers", extra={
                'user_id': str(request.user.user_id),
                'error': str(e)
            }, exc_info=True)
        # sort by field
    if sort_field:
        suppliers = suppliers.order_by(f"{'-' if sort_type == 'desc' else ''}{sort_field}")

    # add pagination
    suppliers = paginator.paginate_queryset(queryset=suppliers, request=request)

    data = SupplierSerializer(suppliers, many=True).data

    logger.info("Suppliers list retrieved successfully", extra={
        'user_id': str(request.user.user_id),
        'count': len(data),
        'total': paginator.page.paginator.count
    })
    return Response({'data': data, "meta": {
        "total": paginator.page.paginator.count,
        "page": paginator.page.number,
        "pages": paginator.page.paginator.num_pages,
        "perpage": paginator.page.paginator.per_page,
    }}, status=status.HTTP_200_OK)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def supplier_info(request, uid):
    """
    API endpoint to get detailed information about a specific supplier
    """
    logger.info("Retrieving supplier info", extra={'user_id': str(request.user.user_id), 'supplier_id': uid})
    is_valid_uuid(uid)

    if request.user.role == User.ATHLETE:
        logger.warning("Unauthorized supplier info access - athlete role", extra={
            'user_id': str(request.user.user_id),
            'role': request.user.role
        })
        return Response(status=status.HTTP_403_FORBIDDEN)

    sport_association = SportAssociation.objects.get(user=request.user)
    supplier = SupplierAndCustomers.objects.filter(
        supplier_id=uid,
        sport_association=sport_association
    ).first()

    if supplier is None:
        logger.warning("Supplier not found", extra={'user_id': str(request.user.user_id), 'supplier_id': uid})
        return Response({"msg": "Supplier not found."}, status=status.HTTP_404_NOT_FOUND)

    logger.info("Supplier info retrieved successfully", extra={
        'user_id': str(request.user.user_id),
        'supplier_id': uid,
        'supplier_name': supplier.name
    })
    data = SupplierSerializer(supplier).data
    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def supplier_add(request):
    """
    API endpoint to add a new supplier
    """
    logger.info("Creating new supplier", extra={'user_id': str(request.user.user_id)})

    if request.user.role == User.ATHLETE:
        logger.warning("Unauthorized supplier creation - athlete role", extra={
            'user_id': str(request.user.user_id),
            'role': request.user.role
        })
        # not allowed
        return Response(status=status.HTTP_403_FORBIDDEN)

    sport_association = SportAssociation.objects.get(user=request.user)
    data = request.data
    # put date in a serializer
    serializer = SupplierSerializer(data=data)

    if serializer.is_valid():
        supplier = serializer.save()
        supplier.sport_association = sport_association
        supplier.save()
        logger.info("Supplier created successfully", extra={
            'user_id': str(request.user.user_id),
            'supplier_id': str(supplier.supplier_id),
            'supplier_name': supplier.name
        })
        return Response(SupplierSerializer(supplier).data, status=status.HTTP_201_CREATED)
    else:
        logger.warning("Supplier validation failed", extra={
            'user_id': str(request.user.user_id),
            'errors': serializer.errors
        })
        return Response({"msg": f"validation errors: {serializer.errors}"}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def supplier_update(request, uid):
    """
    API endpoint to update a new supplier
    """
    logger.info("Updating supplier", extra={'user_id': str(request.user.user_id), 'supplier_id': uid})

    is_valid_uuid(uid)

    sport_association = SportAssociation.objects.get(user=request.user)
    supplier = SupplierAndCustomers.objects.filter(supplier_id=uid).first()

    if supplier.sport_association.sport_association_id != sport_association.sport_association_id:
        logger.warning("Unauthorized supplier update - ownership mismatch", extra={
            'user_id': str(request.user.user_id),
            'supplier_id': uid
        })
        raise PermissionDenied("User not allowed.")

    # update fields with serializer, if not valid return error
    serializer = SupplierSerializer(supplier, data=request.data, partial=True)
    if serializer.is_valid():
        serializer.save()
        logger.info("Supplier updated successfully", extra={
            'user_id': str(request.user.user_id),
            'supplier_id': uid,
            'supplier_name': supplier.name
        })
        return Response(SupplierSerializer(supplier).data, status=status.HTTP_200_OK)

    else:
        logger.warning("Supplier update validation failed", extra={
            'user_id': str(request.user.user_id),
            'supplier_id': uid,
            'errors': serializer.errors
        })
        return Response({"msg": "validation errors."}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def supplier_delete(request, uid):
    """
    API endpoint to delete a supplier
    """
    logger.info("Deleting supplier", extra={'user_id': str(request.user.user_id), 'supplier_id': uid})
    is_valid_uuid(uid)

    sport_association = SportAssociation.objects.get(user=request.user)
    supplier = SupplierAndCustomers.objects.filter(
        supplier_id=uid,
        sport_association=sport_association
    ).first()
    if supplier is None:
        logger.warning("Supplier not found for deletion or unauthorized", extra={
            'user_id': str(request.user.user_id),
            'supplier_id': uid
        })
        raise PermissionDenied("User not allowed.")

    supplier_name = supplier.name
    supplier.delete()
    logger.info("Supplier deleted successfully", extra={
        'user_id': str(request.user.user_id),
        'supplier_id': uid,
        'supplier_name': supplier_name
    })
    data = {"msg": "supplier deleted."}
    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def supplier_bulk_delete(request):
    """
    API endpoint to delete multiple suppliers at once
    """
    logger.info("Bulk deleting suppliers", extra={
        'user_id': str(request.user.user_id),
        'count': len(request.data.get('supplier_ids', []))
    })

    if request.user.role == User.ATHLETE:
        logger.warning("Unauthorized bulk supplier deletion - athlete role", extra={
            'user_id': str(request.user.user_id),
            'role': request.user.role
        })
        return Response(status=status.HTTP_403_FORBIDDEN)

    sport_association = SportAssociation.objects.get(user=request.user)
    supplier_ids = request.data.get('supplier_ids', [])

    if not supplier_ids:
        logger.warning("Bulk delete attempted with no IDs", extra={'user_id': str(request.user.user_id)})
        return Response({"msg": "No supplier IDs provided."}, status=status.HTTP_400_BAD_REQUEST)

    # Validate that all IDs are valid UUIDs
    try:
        for uid in supplier_ids:
            is_valid_uuid(uid)
    except Exception as e:
        logger.warning("Invalid UUID in bulk delete", extra={
            'user_id': str(request.user.user_id),
            'error': str(e)
        })
        return Response({"msg": "Invalid UUID format."}, status=status.HTTP_400_BAD_REQUEST)

    # Get all suppliers that belong to this sport association and match the provided IDs
    suppliers = SupplierAndCustomers.objects.filter(
        supplier_id__in=supplier_ids,
        sport_association=sport_association
    )

    # Count found suppliers
    found_count = suppliers.count()

    logger.debug("Deleting suppliers", extra={
        'user_id': str(request.user.user_id),
        'requested': len(supplier_ids),
        'found': found_count
    })

    # Delete all matching suppliers
    suppliers.delete()
    logger.info("Bulk delete completed successfully", extra={
        'user_id': str(request.user.user_id),
        'deleted_count': found_count
    })
    data = {"msg": f"{found_count} suppliers deleted."}
    return Response({'data': data}, status=status.HTTP_200_OK)