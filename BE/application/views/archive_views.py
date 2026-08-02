import base64
import os
from io import BytesIO

from django.core.files.storage import default_storage
from django.db import transaction
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, NotFound
from rest_framework.parsers import JSONParser
from rest_framework.response import Response

from application.utils.api_utils import KTDatatablePagination
from core.middleware import IsAuthenticated
from rest_framework import serializers

from application.models.user_models import Folder, SportAssociationDocumentsArchive, SportAssociation, \
    SportAssociationModuleTemplates
from application.serializers.user_serializers import FolderSerializer, DocumentArchiveSerializer, DocumentSerializer, \
    SportAssociationModuleTemplatesSerializer
from core.settings import STORAGE_DIR
from docmanager.models import Document

import logging


logger = logging.getLogger(__name__)


class FolderViewSet(viewsets.ModelViewSet):
    queryset = Folder.objects.all()
    serializer_class = FolderSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        sport_association = SportAssociation.objects.get(user=self.request.user)
        return Folder.objects.filter(sport_association=sport_association)

    def perform_create(self, serializer):
        sport_association = SportAssociation.objects.get(user=self.request.user)
        serializer.save(sport_association=sport_association)

    def add(self, request):
        sport_association = SportAssociation.objects.get(user=request.user)
        request.data['sport_association'] = sport_association.sport_association_id
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
        sport_association = SportAssociation.objects.get(user=request.user)
        folder_id = request.query_params.get('folder', None)
        queryset = self.get_queryset().filter(sport_association=sport_association)
        current_path = None

        if folder_id:
            # Get children of specific parent folder
            try:
                current_folder = Folder.objects.get(id=folder_id)
                # Verify folder belongs to same sport association
                if current_folder.sport_association_id != sport_association.sport_association_id:
                    raise PermissionDenied("Cannot access folder from different sport association.")
                queryset = queryset.filter(parent=current_folder)

                # Get ancestors excluding the current folder
                ancestors = list(current_folder.get_ancestors(include_self=True))

                if len(ancestors) > 1:
                    # Start with the innermost folder (current)
                    path = []
                    # Build nested structure from ancestors in reverse order
                    # Skip the last item since it's already included
                    for ancestor in ancestors:
                        path.append({'id': ancestor.id, 'name': ancestor.name})
                else:
                    path = [{'id': current_folder.id, 'name': current_folder.name}]

                current_path = path
            except Folder.DoesNotExist:
                raise NotFound("Parent folder not found.")
        elif folder_id == '':  # Empty string means root level
            queryset = queryset.filter(parent__isnull=True)
        else:
            # If no parent_id is provided, return root nodes (same as current behavior)
            queryset = queryset.filter(parent__isnull=True)

        serializer = self.get_serializer(queryset, many=True)
        return Response({"data": serializer.data, "path": current_path}, status=status.HTTP_200_OK)

    def move(self, request, pk=None):
        folder = self.get_object()
        sport_association = SportAssociation.objects.get(user=request.user)

        if folder.sport_association.sport_association_id != sport_association.sport_association_id:
            raise PermissionDenied("User not allowed.")

        new_parent_id = request.data.get('new_parent')
        if new_parent_id:
            new_parent = Folder.objects.get(id=new_parent_id)
            # Check if new parent belongs to same sport association
            if new_parent.sport_association.sport_association_id != sport_association.sport_association_id:
                raise PermissionDenied("Cannot move to folder from different sport association.")
            folder.move_to(new_parent)
        else:
            folder.move_to(None)  # Move to root
        return Response(status=status.HTTP_200_OK)


class DocumentArchiveViewSet(viewsets.ModelViewSet):
    queryset = SportAssociationDocumentsArchive.objects.all()
    serializer_class = DocumentArchiveSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = (JSONParser,)

    def get_queryset(self):
        sport_association = SportAssociation.objects.get(user=self.request.user)
        return SportAssociationDocumentsArchive.objects.filter(sport_association=sport_association)

    def perform_create(self, serializer):
        sport_association = SportAssociation.objects.get(user=self.request.user)
        serializer.save(sport_association=sport_association)

    def add(self, request):
        files_data = request.data.get('files', [])
        folder_id = request.data.get('folder', None)
        if folder_id is not None:
            folder = Folder.objects.get(id=folder_id)
        else:
            folder = None
        sport_association = SportAssociation.objects.get(user=request.user)

        created_archives = []

        for file_data in files_data:
            try:
                # Create and save the document
                if 'document' in file_data and 'filename' in file_data:
                    decoded_file = base64.b64decode(file_data['document'])
                    document = Document.objects.create(
                        filename=file_data['filename']
                    )

                    # Save the file
                    storing_path = os.path.join(STORAGE_DIR, str(document.creation_date.timestamp()),
                                                str(document.document_id))
                    file_path = os.path.join(storing_path, document.filename)
                    file_like = BytesIO(decoded_file)
                    default_storage.save(file_path, file_like)

                    # Create archive entry
                    archive = SportAssociationDocumentsArchive.objects.create(
                        sport_association=sport_association,
                        folder=folder,
                        document=document
                    )

                    # Serialize the document first
                    document_serializer = DocumentSerializer(document)
                    # Then serialize the archive using our manually created data
                    archive_data = {
                        'sport_association_documents_archive_id': archive.sport_association_documents_archive_id,
                        'sport_association': archive.sport_association.sport_association_id,
                        'document': document_serializer.data,  # Use the serialized document data
                        'date': archive.date.isoformat() if hasattr(archive, 'date') else None,
                        'folder': archive.folder.id if archive.folder else None
                    }

                    created_archives.append(archive_data)
                else:
                    raise serializers.ValidationError({'exception': 'document or filename key not present'})

            except Exception as e:
                # Clean up any created documents if there's an error
                if 'document' in locals():
                    document.delete()
                raise serializers.ValidationError({'exception': str(e)})

        return Response({"data": created_archives}, status=status.HTTP_201_CREATED)

    def delete(self, request, pk=None):
        instance = self.get_object()
        sport_association = SportAssociation.objects.get(user=request.user)

        if instance.sport_association.sport_association_id != sport_association.sport_association_id:
            raise PermissionDenied("User not allowed.")

        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['DELETE'])
    def bulk_delete(self, request):
        instances = request.data.get('files', [])
        if not instances:
            raise serializers.ValidationError({'exception': 'No instances provided'})

        sport_association = SportAssociation.objects.get(user=request.user)

        # Get all archives at once and verify permissions
        archives_to_delete = SportAssociationDocumentsArchive.objects.filter(
            sport_association_documents_archive_id__in=instances
        )

        # Check if all requested instances exist
        if len(archives_to_delete) != len(instances):
            raise serializers.ValidationError({'exception': 'Some instances not found'})

        # Verify all instances belong to the user's sport association
        unauthorized = archives_to_delete.exclude(
            sport_association__sport_association_id=sport_association.sport_association_id
        ).exists()

        if unauthorized:
            raise PermissionDenied("User not allowed to delete some of these instances.")

        try:
            with transaction.atomic():
                # Get all document IDs before deletion for cleanup
                document_ids = list(archives_to_delete.values_list('document_id', flat=True))

                # Delete the archives
                archives_to_delete.delete()

                # Clean up the associated documents
                Document.objects.filter(document_id__in=document_ids).delete()

            return Response(status=status.HTTP_204_NO_CONTENT)

        except Exception as e:
            raise serializers.ValidationError({'exception': str(e)})

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
        sport_association = SportAssociation.objects.get(user=request.user)
        folder_id = request.query_params.get('folder', None)
        page = int(request.query_params.get('page', 0))  # Default to page 0 if not provided
        items_per_page = 40

        queryset = self.get_queryset()

        if folder_id:
            folder = Folder.objects.get(id=folder_id)
            # Verify folder belongs to same sport association
            if folder.sport_association.sport_association_id != sport_association.sport_association_id:
                raise PermissionDenied("Cannot access folder from different sport association.")
            queryset = queryset.filter(folder=folder)
        else:  # Empty string means root level
            queryset = queryset.filter(folder__isnull=True)

        # Calculate pagination
        start = page * items_per_page
        end = start + items_per_page
        total_count = queryset.count()

        # Slice the queryset for pagination
        paginated_queryset = queryset[start:end]

        # Manually construct the data with properly serialized documents
        response_data = []
        for archive in paginated_queryset:
            document_serializer = DocumentSerializer(archive.document)
            archive_data = {
                'sport_association_documents_archive_id': archive.sport_association_documents_archive_id,
                'sport_association': archive.sport_association.sport_association_id,
                'document': document_serializer.data,
                'date': archive.date.isoformat() if hasattr(archive, 'date') else None,
                'folder': archive.folder.id if archive.folder else None
            }
            response_data.append(archive_data)

        return Response({
            "data": response_data,
            "pagination": {
                "page": page,
                "total_pages": -(-total_count // items_per_page),  # Ceiling division
                "total_items": total_count,
                "has_next": end < total_count
            }
        }, status=status.HTTP_200_OK)

    def move_to_folder(self, request, pk=None):
        document = self.get_object()
        sport_association = SportAssociation.objects.get(user=request.user)

        if document.sport_association.sport_association_id != sport_association.sport_association_id:
            raise PermissionDenied("User not allowed.")

        new_folder_id = request.data.get('folder')
        if new_folder_id:
            new_folder = Folder.objects.get(id=new_folder_id)
            # Verify new folder belongs to same sport association
            if new_folder.sport_association.sport_association_id != sport_association.sport_association_id:
                raise PermissionDenied("Cannot move to folder from different sport association.")
            document.folder_id = new_folder_id
        else:
            document.folder = None
        document.save()
        return Response(status=status.HTTP_200_OK)


class SportAssociationModuleTemplatesViewSet(viewsets.ModelViewSet):
    queryset = SportAssociationModuleTemplates.objects.all()
    serializer_class = SportAssociationModuleTemplatesSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = KTDatatablePagination

    def get_queryset(self):
        sport_association = SportAssociation.objects.get(user=self.request.user)
        return SportAssociationModuleTemplates.objects.filter(sport_association=sport_association)

    def perform_create(self, serializer):
        sport_association = SportAssociation.objects.get(user=self.request.user)
        serializer.save(sport_association=sport_association)

    def create(self, request):
        sport_association = SportAssociation.objects.get(user=request.user)
        request.data['sport_association'] = sport_association.sport_association_id
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

    def bulk_delete(self, request):
        instances = request.data.get('sport_association_module_templates_ids', [])
        if not instances:
            raise serializers.ValidationError({'exception': 'No instances provided'})

        sport_association = SportAssociation.objects.get(user=request.user)

        # Get all templates at once and verify permissions
        templates_to_delete = SportAssociationModuleTemplates.objects.filter(
            sport_association_module_templates_id__in=instances
        )

        # Only delete templates that belong to the user's sport association
        templates_to_delete = templates_to_delete.filter(
            sport_association__sport_association_id=sport_association.sport_association_id
        )

        try:
            with transaction.atomic():
                # Delete the templates
                templates_to_delete.delete()

            return Response(status=status.HTTP_204_NO_CONTENT)

        except Exception as e:
            raise serializers.ValidationError({'exception': str(e)})

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
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)