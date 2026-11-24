from uuid import UUID

from auditlog.models import LogEntry
from rest_framework.response import Response
from rest_framework.views import APIView

from .backups import actor_of, password_confirmation
from .models import InstanceConfiguration
from .permissions import IsInstanceOwner
from .updater_client import UpdaterUnavailable, runner_response


class InstanceRuntimeView(APIView):
    permission_classes = [IsInstanceOwner]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'no-store'
        return response

    def relay(self, method, path, body=None):
        try:
            code, payload = runner_response(method, path, body, timeout=30)
            return Response(payload, status=code)
        except UpdaterUnavailable as exc:
            return Response({'error': str(exc)}, status=503)

    def get(self, request):
        return self.relay('GET', '/runtime')

    def post(self, request):
        data = request.data
        invalid = Response({'error': 'Richiesta non valida.'}, status=400)
        if not isinstance(data, dict) or set(data) != {'target', 'expected_generation', 'idempotency_key', 'password'}:
            return invalid
        target, generation = data['target'], data['expected_generation']
        if target not in ('django', 'go') or type(generation) is not int or generation < 1:
            return invalid
        try:
            key = str(UUID(data['idempotency_key']))
        except (ValueError, TypeError, AttributeError):
            return invalid
        refusal = password_confirmation(request)
        if refusal:
            return refusal
        actor = actor_of(request)
        response = self.relay('POST', '/runtime-switches', {
            'request_id': key, 'actor_id': str(actor.pk), 'target': target, 'expected_generation': generation})
        if response.status_code == 202 and response.data.get('replayed') is False:
            operation = response.data['operation']
            LogEntry.objects.log_create(
                InstanceConfiguration.get_config(), action=LogEntry.Action.UPDATE, actor=actor,
                changes={'runtime_switch': [operation['previous_runtime'], target]},
                additional_data={'actor_id': str(actor.pk), 'operation': operation['id'], 'expected_generation': generation},
                force_log=True)
        return response
