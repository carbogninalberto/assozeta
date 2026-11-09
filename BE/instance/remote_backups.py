"""Owner page "Copia remota": encrypted off-site copies of the server backups.

The runner keeps the S3 settings and the public key, encrypts and uploads.
These views check the owner, validate, relay, and audit every change. The
secret key, the recovery key and the confirmation characters are never
logged, audited or returned (except the recovery key, once, when generated).
"""
import json
from uuid import uuid4

from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from .backups import ARCHIVE, actor_of, audit
from .permissions import IsInstanceOwner
from .updater_client import UpdaterRejected, UpdaterUnavailable, call_runner

# S3 round trips take longer than the runner's usual instant answers.
SLOW = 30
SETTINGS_FIELDS = ('endpoint', 'region', 'bucket', 'prefix', 'access_key', 'path_style', 'retention', 'enabled')


def relay(method, path, payload=None, success=200, timeout=5):
    try:
        response = Response(call_runner(method, path, payload, timeout=timeout), status=success)
    except UpdaterUnavailable as exc:
        return Response({'error': str(exc)}, status=503)
    except UpdaterRejected as exc:
        if exc.status == 404 and str(exc) == 'Unknown endpoint.':
            return Response({'error': 'Il servizio di aggiornamento richiede una versione più recente.'}, status=409)
        return Response({'error': str(exc)}, status=exc.status if 400 <= exc.status < 500 else 503)
    response['Cache-Control'] = 'no-store'
    return response


class RemoteSettingsSerializer(serializers.Serializer):
    endpoint = serializers.CharField(max_length=255, allow_blank=True, required=False)
    region = serializers.CharField(max_length=64, allow_blank=True, required=False)
    bucket = serializers.CharField(max_length=63, allow_blank=True, required=False)
    prefix = serializers.CharField(max_length=200, allow_blank=True, required=False)
    access_key = serializers.CharField(max_length=128, allow_blank=True, required=False)
    secret_key = serializers.CharField(max_length=256, allow_blank=True, required=False, write_only=True)
    path_style = serializers.BooleanField(required=False)
    retention = serializers.IntegerField(min_value=1, max_value=30, required=False)
    enabled = serializers.BooleanField(required=False)


class RemoteBackupSettingsView(APIView):
    permission_classes = [IsInstanceOwner]

    def get(self, request):
        return relay('GET', '/backups/remote')

    def put(self, request):
        serializer = RemoteSettingsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        value = dict(serializer.validated_data)
        response = relay('PUT', '/backups/remote', value)
        if response.status_code == 200:
            # Record what changed, never the secret itself.
            change = {key: value[key] for key in SETTINGS_FIELDS if key in value}
            if value.get('secret_key'):
                change['secret_key'] = 'changed'
            audit(actor_of(request), 'backup_remote_settings', json.dumps(change, sort_keys=True))
        return response


class RemoteBackupTestView(APIView):
    permission_classes = [IsInstanceOwner]

    def post(self, request):
        serializer = RemoteSettingsSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        value = {key: item for key, item in serializer.validated_data.items() if key != 'enabled'}
        return relay('POST', '/backups/remote/test', value, timeout=SLOW)


class RemoteBackupKeyView(APIView):
    """Generate a key pair: the recovery key is in this response only."""
    permission_classes = [IsInstanceOwner]

    def post(self, request):
        response = relay('POST', '/backups/remote/key', {}, success=201)
        if response.status_code == 201:
            audit(actor_of(request), 'backup_remote_key_generated', response.data.get('key_id', ''))
        return response


class KeyConfirmSerializer(serializers.Serializer):
    key_id = serializers.RegexField(r'\A[0-9a-f]{8}\Z')
    tail = serializers.CharField(min_length=6, max_length=6)


class RemoteBackupKeyConfirmView(APIView):
    permission_classes = [IsInstanceOwner]

    def post(self, request):
        serializer = KeyConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key_id = serializer.validated_data['key_id']
        response = relay('POST', '/backups/remote/key/confirm', dict(serializer.validated_data))
        if response.status_code == 200:
            audit(actor_of(request), 'backup_remote_key_confirmed', key_id)
        return response


class FetchSerializer(serializers.Serializer):
    name = serializers.RegexField(r'\A' + ARCHIVE.pattern + r'\Z')
    recovery_key = serializers.CharField(max_length=4096, trim_whitespace=False)


class RemoteBackupFetchView(APIView):
    """Bring a remote-only copy back to the server; the key is held in memory only."""
    permission_classes = [IsInstanceOwner]

    def post(self, request):
        serializer = FetchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        actor = actor_of(request)
        name = serializer.validated_data['name']
        payload = {'name': name, 'recovery_key': serializer.validated_data['recovery_key'],
                   'request_id': str(uuid4()), 'actor_id': str(actor.pk)}
        response = relay('POST', '/backups/remote/fetch', payload, success=202, timeout=SLOW)
        if response.status_code == 202:
            audit(actor, 'backup_remote_fetch', name, backup=name)
        return response
