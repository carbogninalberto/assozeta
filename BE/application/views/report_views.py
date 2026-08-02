import base64
import logging

from django.http import HttpResponse
from rest_framework import viewsets, status
from rest_framework.response import Response

from core.middleware import IsAuthenticated
from application.models.report_models import SavedReport
from application.models.user_models import SportAssociation
from application.mcp_server.server import (
    tool_get_schema,
    tool_query_data,
    tool_count_data,
    tool_get_field_values,
    tool_export_data,
    tool_aggregate_data,
    tool_get_attendance_matrix,
    tool_export_multi_sheet,
)

logger = logging.getLogger(__name__)

TOOL_FUNCTIONS = {
    'get_schema': tool_get_schema,
    'query_data': tool_query_data,
    'count_data': tool_count_data,
    'get_field_values': tool_get_field_values,
    'export_data': tool_export_data,
    'aggregate_data': tool_aggregate_data,
    'get_attendance_matrix': tool_get_attendance_matrix,
    'export_multi_sheet': tool_export_multi_sheet,
}


def _get_sport_association(request):
    return SportAssociation.objects.get(user=request.user)


class SavedReportViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def list(self, request):
        sport_association = _get_sport_association(request)
        reports = SavedReport.objects.filter(
            sport_association=sport_association,
            created_by=request.user,
        )
        data = [
            {
                'saved_report_id': str(r.saved_report_id),
                'name': r.name,
                'description': r.description,
                'tool_name': r.tool_name,
                'created_at': r.created_at.isoformat(),
                'updated_at': r.updated_at.isoformat(),
            }
            for r in reports
        ]
        return Response({'data': data})

    def add(self, request):
        sport_association = _get_sport_association(request)
        data = request.data
        name = data.get('name', '').strip()
        tool_name = data.get('tool_name', '').strip()

        if not name or not tool_name:
            return Response(
                {'error': 'name and tool_name are required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if tool_name not in TOOL_FUNCTIONS:
            return Response(
                {'error': f'Unknown tool: {tool_name}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        report = SavedReport.objects.create(
            sport_association=sport_association,
            created_by=request.user,
            name=name,
            description=data.get('description', ''),
            tool_name=tool_name,
            params=data.get('params', {}),
            ui_config=data.get('ui_config', {}),
        )
        return Response(
            {
                'saved_report_id': str(report.saved_report_id),
                'name': report.name,
            },
            status=status.HTTP_201_CREATED,
        )

    def info(self, request, pk=None):
        sport_association = _get_sport_association(request)
        try:
            report = SavedReport.objects.get(
                saved_report_id=pk,
                sport_association=sport_association,
                created_by=request.user,
            )
        except SavedReport.DoesNotExist:
            return Response(
                {'error': 'Report not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response({
            'data': {
                'saved_report_id': str(report.saved_report_id),
                'name': report.name,
                'description': report.description,
                'tool_name': report.tool_name,
                'params': report.params,
                'ui_config': report.ui_config,
                'created_at': report.created_at.isoformat(),
                'updated_at': report.updated_at.isoformat(),
            }
        })

    def update(self, request, pk=None):
        sport_association = _get_sport_association(request)
        try:
            report = SavedReport.objects.get(
                saved_report_id=pk,
                sport_association=sport_association,
                created_by=request.user,
            )
        except SavedReport.DoesNotExist:
            return Response(
                {'error': 'Report not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        data = request.data
        if 'name' in data:
            report.name = data['name']
        if 'description' in data:
            report.description = data['description']
        if 'params' in data:
            report.params = data['params']
        if 'ui_config' in data:
            report.ui_config = data['ui_config']
        report.save()

        return Response({
            'saved_report_id': str(report.saved_report_id),
            'name': report.name,
        })

    def delete(self, request, pk=None):
        sport_association = _get_sport_association(request)
        try:
            report = SavedReport.objects.get(
                saved_report_id=pk,
                sport_association=sport_association,
                created_by=request.user,
            )
        except SavedReport.DoesNotExist:
            return Response(
                {'error': 'Report not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        report.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def run(self, request, pk=None):
        """Execute a saved report with optional parameter overrides.

        Returns a file download if the tool produces one, otherwise JSON.
        """
        sport_association = _get_sport_association(request)
        try:
            report = SavedReport.objects.get(
                saved_report_id=pk,
                sport_association=sport_association,
                created_by=request.user,
            )
        except SavedReport.DoesNotExist:
            return Response(
                {'error': 'Report not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        tool_fn = TOOL_FUNCTIONS.get(report.tool_name)
        if not tool_fn:
            return Response(
                {'error': f'Unknown tool: {report.tool_name}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Merge stored params with overrides from request body
        overrides = request.data.get('overrides', {})
        merged_params = {**report.params, **overrides}

        try:
            result = tool_fn(
                sport_association_id=str(sport_association.sport_association_id),
                **merged_params,
            )
        except Exception:
            logger.exception("Error running saved report %s", pk)
            return Response(
                {'error': 'Errore durante l\'esecuzione del report.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if 'error' in result:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)

        # If the result contains a file, return it as a download
        if 'data_base64' in result:
            file_bytes = base64.b64decode(result['data_base64'])
            content_type = result.get('content_type', 'application/octet-stream')
            filename = result.get('filename', 'report')
            response = HttpResponse(file_bytes, content_type=content_type)
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response

        return Response({'data': result})
