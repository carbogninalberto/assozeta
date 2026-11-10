"""Owner page "Copia remota": encrypted off-site copies of the server backups.

The runner keeps the S3 settings and the public key, encrypts and uploads.
These views check the owner, validate, relay, and audit every change. The
secret key, the recovery key and the confirmation characters are never
logged, audited or returned (except the recovery key, once, when generated).
Saving the settings and setting up the key need the owner's password again.

The storage address must be https:// (http:// only when
ASSOZETA_BACKUP_REMOTE_ALLOW_HTTP is set, for a storage on the local network)
and may not name this machine, link-local or cloud instance addresses. Private
network addresses stay allowed: a MinIO on the LAN is a common choice. Names
are checked as written; the runner resolves them when it connects.
"""
import ipaddress
import json
import re
from uuid import uuid4

from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from .backups import ARCHIVE, actor_of, audit, instance_setting, password_confirmation, password_refusal
from .permissions import IsInstanceOwner
from .updater_client import UpdaterRejected, UpdaterUnavailable, call_runner

# S3 round trips take longer than the runner's usual instant answers.
SLOW = 30
SETTINGS_FIELDS = ('endpoint', 'region', 'bucket', 'prefix', 'access_key', 'path_style', 'retention', 'enabled')

INVALID_ENDPOINT = 'Inserisci un indirizzo valido, ad esempio https://s3.example.com.'
HTTPS_ENDPOINT = 'Usa un indirizzo https://.'
BLOCKED_ENDPOINT = 'Questo indirizzo non può essere usato per la copia remota.'
# The runner's form (https?://host[/path]) without user information, query or fragment.
ENDPOINT = re.compile(r'(https?)://([^/?#@ \t\n\r\f\v]+)(/[^?# \t\n\r\f\v]*)?')
AUTHORITY = re.compile(r'(?:\[([0-9A-Fa-f:.]+)\]|([^:\[\]]+))(?::([0-9]{1,5}))?')
DOTTED_QUAD = re.compile(r'(?:0|[1-9][0-9]{0,2})(?:\.(?:0|[1-9][0-9]{0,2})){3}')
# A last label inet_aton reads as a number (decimal, octal or hex).
NUMERIC_LABEL = re.compile(r'[0-9]+|0[xX][0-9A-Fa-f]*')
BLOCKED_NAMES = {'localhost', 'metadata', 'metadata.google.internal', 'instance-data'}
BLOCKED_NETWORKS = [ipaddress.ip_network(value) for value in (
    '0.0.0.0/8', '127.0.0.0/8', '169.254.0.0/16', '224.0.0.0/3', '100.100.100.200/32', '168.63.129.16/32',
    '::/128', '::1/128', 'fe80::/10', 'fec0::/10', 'ff00::/8', 'fd00:ec2::254/128')]
EMBEDDED_IPV4 = [ipaddress.ip_network(value) for value in ('::ffff:0:0/96', '64:ff9b::/96')]
TRUE_SETTING = {'1', 'true', 'yes', 'on'}


def ascii_lower(value):
    return ''.join(chr(ord(char) + 32) if 'A' <= char <= 'Z' else char for char in value)


def blocked_address(address):
    for network in EMBEDDED_IPV4:
        if address in network:
            address = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return any(address in network for network in BLOCKED_NETWORKS if network.version == address.version)


def endpoint_problem(value):
    """None when the storage address may be used, else the message for the owner."""
    if value == '':
        return None
    form = ENDPOINT.fullmatch(value)
    authority = AUTHORITY.fullmatch(form.group(2)) if form else None
    if not authority or authority.group(3) is not None and not 0 < int(authority.group(3)) <= 65535:
        return INVALID_ENDPOINT
    if authority.group(1) is not None:
        try:
            address = ipaddress.IPv6Address(authority.group(1))
        except ValueError:
            return INVALID_ENDPOINT
    else:
        host = ascii_lower(authority.group(2)).rstrip('.')
        if not host:
            return INVALID_ENDPOINT
        address = None
        if NUMERIC_LABEL.fullmatch(host.rsplit('.', 1)[-1]):
            if not DOTTED_QUAD.fullmatch(host) or any(int(part) > 255 for part in host.split('.')):
                return INVALID_ENDPOINT
            address = ipaddress.IPv4Address(host)
        elif host in BLOCKED_NAMES or host.endswith('.localhost'):
            return BLOCKED_ENDPOINT
    if address is not None and blocked_address(address):
        return BLOCKED_ENDPOINT
    if form.group(1) == 'http' and ascii_lower(str(instance_setting('ASSOZETA_BACKUP_REMOTE_ALLOW_HTTP')).strip(' \t\r\n')) not in TRUE_SETTING:
        return HTTPS_ENDPOINT
    return None


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

    def validate_endpoint(self, value):
        problem = endpoint_problem(value)
        if problem:
            raise serializers.ValidationError(problem)
        return value


class RemoteSettingsChangeSerializer(RemoteSettingsSerializer):
    password = serializers.CharField(max_length=256, trim_whitespace=False, write_only=True)


class RemoteBackupSettingsView(APIView):
    permission_classes = [IsInstanceOwner]

    def get(self, request):
        return relay('GET', '/backups/remote')

    def put(self, request):
        serializer = RemoteSettingsChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        value = dict(serializer.validated_data)
        refusal = password_refusal(actor_of(request), value.pop('password'))
        if refusal:
            return refusal
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
        refusal = password_confirmation(request)
        if refusal:
            return refusal
        response = relay('POST', '/backups/remote/key', {}, success=201)
        if response.status_code == 201:
            audit(actor_of(request), 'backup_remote_key_generated', response.data.get('key_id', ''))
        return response


class KeyConfirmSerializer(serializers.Serializer):
    key_id = serializers.RegexField(r'\A[0-9a-f]{8}\Z')
    tail = serializers.CharField(min_length=6, max_length=6)
    password = serializers.CharField(max_length=256, trim_whitespace=False, write_only=True)


class RemoteBackupKeyConfirmView(APIView):
    permission_classes = [IsInstanceOwner]

    def post(self, request):
        serializer = KeyConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        value = dict(serializer.validated_data)
        refusal = password_refusal(actor_of(request), value.pop('password'))
        if refusal:
            return refusal
        key_id = value['key_id']
        response = relay('POST', '/backups/remote/key/confirm', value)
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
