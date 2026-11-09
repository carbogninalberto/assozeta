"""Owner page "Backup": the server backups, their actions and the schedule.

The independent runner owns the backup directory and runs every backup; the
API measures the data that the next backup will contain, relays the owner's
actions and archive downloads, and records each action in the audit log. A
download needs the owner's password again.
"""
import json
import logging
import re
from uuid import uuid4

from auditlog.models import LogEntry
from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.db import connection
from django.http import StreamingHttpResponse
from django.utils.http import content_disposition_header
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from application.utils.printing import _AsyncStreamingBody

from .models import InstanceConfiguration
from .permissions import IsInstanceOwner, is_instance_administrator
from .updater_client import UpdaterRejected, UpdaterUnavailable, call_runner, open_runner_stream

logger = logging.getLogger(__name__)

ARCHIVE = re.compile(r'assozeta-\d{8}T\d{6}Z(-\d+)?\.tar\.gz')
TOKEN_SALT = 'instance.backups.download'
TOKEN_MAX_AGE = 300
SIZES_KEY = 'instance:backup-data-sizes:v1'
SIZES_TIMEOUT = 600
PASSWORD_ATTEMPTS = 5
PASSWORD_WINDOW = 900
# The CLI budgets the uncompressed size, staged twice, plus this margin.
DEFAULT_MARGIN_MB = 1024


def actor_of(request):
    return getattr(request, 'authenticated_user', getattr(request, 'original_user', request.user))


def database_bytes():
    if connection.vendor != 'postgresql':
        return None
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_database_size(current_database())')
        return int(cursor.fetchone()[0])


def object_bytes():
    """Total size of the bucket that the backup mirrors, documents and files."""
    import boto3
    from botocore.config import Config
    client = boto3.client('s3', endpoint_url=settings.AWS_S3_ENDPOINT_URL,
                          aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                          aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                          region_name=settings.AWS_S3_REGION_NAME,
                          config=Config(connect_timeout=3, read_timeout=10, retries={'max_attempts': 1}))
    total = 0
    for page in client.get_paginator('list_objects_v2').paginate(Bucket=settings.AWS_STORAGE_BUCKET_NAME):
        total += sum(item['Size'] for item in page.get('Contents', ()))
    return total


def data_sizes(refresh=False):
    sizes = None if refresh else cache.get(SIZES_KEY)
    if sizes is None:
        sizes = {}
        for key, measure in (('database', database_bytes), ('objects', object_bytes)):
            try:
                sizes[key] = measure()
            except Exception:
                logger.warning('Could not measure %s size for the backup page', key, exc_info=True)
                sizes[key] = None
        cache.set(SIZES_KEY, sizes, timeout=SIZES_TIMEOUT)
    return sizes


def next_backup(sizes, free, margin_mb):
    if sizes['database'] is None or sizes['objects'] is None:
        return None
    estimated = sizes['database'] + sizes['objects']
    if not isinstance(margin_mb, int) or isinstance(margin_mb, bool) or margin_mb < 1:
        margin_mb = DEFAULT_MARGIN_MB
    required = estimated * 2 + margin_mb * 1024 * 1024
    return {'estimated': estimated, 'required': required, 'fits': free is None or free >= required}


class BackupOverviewView(APIView):
    permission_classes = [IsInstanceOwner]

    def get(self, request):
        try:
            inventory = call_runner('GET', '/backups')
        except UpdaterUnavailable as exc:
            return Response({'available': False, 'reason': str(exc)})
        except UpdaterRejected as exc:
            if exc.status == 404:
                return Response({'available': False, 'reason': 'Il servizio di aggiornamento richiede una versione più recente per mostrare i backup.'})
            return Response({'error': str(exc)}, status=exc.status if exc.status >= 500 else 503)
        sizes = data_sizes(refresh=request.query_params.get('refresh') == '1')
        disk = inventory.get('disk', {})
        return Response({
            'available': True,
            'archives': inventory.get('archives', []),
            'retention': inventory.get('retention', {}),
            'disk': {**disk, **sizes},
            'next_backup': next_backup(sizes, disk.get('free'), inventory.get('space_margin_mb')),
            # Older runners have neither: the page then stays read-only.
            'schedule': inventory.get('schedule'),
            'actions': inventory.get('actions', {}),
        })

    def post(self, request):
        """Start a manual backup; the runner stops the app while it copies the data."""
        actor = actor_of(request)
        response = relay('POST', '/backups', {'request_id': str(uuid4()), 'actor_id': str(actor.pk)}, success=202)
        if response.status_code == 202:
            audit(actor, 'backup_create', response.data['operation'].get('id', ''))
        return response


def relay(method, path, payload, success=200):
    """Forward an owner action to the runner, keeping its Italian message on refusal."""
    try:
        return Response(call_runner(method, path, payload), status=success)
    except UpdaterUnavailable as exc:
        return Response({'error': str(exc)}, status=503)
    except UpdaterRejected as exc:
        if exc.status == 404 and path in ('/backups', '/backups/schedule'):
            return Response({'error': 'Il servizio di aggiornamento richiede una versione più recente.'}, status=409)
        return Response({'error': str(exc)}, status=exc.status if 400 <= exc.status < 500 else 503)


def audit(actor, action, value, **extra):
    LogEntry.objects.log_create(
        InstanceConfiguration.get_config(), action=LogEntry.Action.UPDATE, actor=actor,
        changes={action: ['', str(value)]}, additional_data={'actor_id': str(actor.pk), **extra}, force_log=True)


class BackupItemView(APIView):
    """Keep (exclude from rotation) or delete one archive."""
    permission_classes = [IsInstanceOwner]

    def patch(self, request, name):
        if not ARCHIVE.fullmatch(name):
            return Response({'error': 'Backup non trovato.'}, status=404)
        keep = request.data.get('keep') if isinstance(request.data, dict) else None
        if not isinstance(keep, bool):
            return Response({'error': 'Richiesta non valida.'}, status=400)
        response = relay('PATCH', f'/backups/{name}', {'keep': keep})
        if response.status_code == 200:
            audit(actor_of(request), 'backup_keep' if keep else 'backup_unkeep', name, backup=name)
        return response

    def delete(self, request, name):
        if not ARCHIVE.fullmatch(name):
            return Response({'error': 'Backup non trovato.'}, status=404)
        confirm = request.data.get('confirm', False) if isinstance(request.data, dict) else False
        if not isinstance(confirm, bool):
            return Response({'error': 'Richiesta non valida.'}, status=400)
        response = relay('DELETE', f'/backups/{name}', {'confirm': confirm})
        if response.status_code == 200:
            audit(actor_of(request), 'backup_delete', name, backup=name)
        return response


class ScheduleSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    frequency = serializers.ChoiceField(choices=['daily', 'weekly'])
    time = serializers.RegexField(r'\A([01]\d|2[0-3]):[0-5]\d\Z')
    weekday = serializers.IntegerField(min_value=0, max_value=6, default=6)
    keep = serializers.IntegerField(min_value=1, max_value=30)


class BackupScheduleView(APIView):
    permission_classes = [IsInstanceOwner]

    def put(self, request):
        serializer = ScheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        value = dict(serializer.validated_data)
        response = relay('PUT', '/backups/schedule', value)
        if response.status_code == 200:
            audit(actor_of(request), 'backup_schedule', json.dumps(value, sort_keys=True))
        return response


class DownloadTokenSerializer(serializers.Serializer):
    name = serializers.RegexField(r"\A" + ARCHIVE.pattern + r"\Z")
    password = serializers.CharField(max_length=256, trim_whitespace=False)


class BackupDownloadTokenView(APIView):
    permission_classes = [IsInstanceOwner]

    def post(self, request):
        serializer = DownloadTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        actor = actor_of(request)
        name = serializer.validated_data['name']
        attempts_key = f'instance:backup-download-attempts:{actor.pk}'
        if cache.get(attempts_key, 0) >= PASSWORD_ATTEMPTS:
            return Response({'error': 'Troppi tentativi. Riprova tra qualche minuto.'}, status=429)
        if not actor.check_password(serializer.validated_data['password']):
            cache.add(attempts_key, 0, timeout=PASSWORD_WINDOW)
            cache.incr(attempts_key)
            return Response({'error': 'Password non corretta.'}, status=400)
        cache.delete(attempts_key)
        LogEntry.objects.log_create(
            InstanceConfiguration.get_config(), action=LogEntry.Action.ACCESS, actor=actor,
            changes={'backup_download': ['', name]},
            additional_data={'backup': name, 'actor_id': str(actor.pk)}, force_log=True)
        token = signing.dumps({'name': name, 'actor': str(actor.pk), 'id': uuid4().hex}, salt=TOKEN_SALT)
        return Response({'download_token': token, 'expires_in': TOKEN_MAX_AGE})


class BackupDownloadView(APIView):
    """Browser navigation cannot send the JWT; a short signed token stands in."""
    permission_classes = [AllowAny]
    authentication_classes = []
    http_method_names = ['get', 'options']

    def get(self, request, name):
        try:
            claims = signing.loads(request.query_params.get('download_token', ''), salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE)
        except (signing.BadSignature, TypeError):
            claims = None
        if not isinstance(claims, dict) or claims.get('name') != name or not ARCHIVE.fullmatch(name):
            return Response({'error': 'Link di download non valido o scaduto.'}, status=403)
        from application.models import User
        actor = User.objects.filter(pk=claims.get('actor')).first()
        if not is_instance_administrator(actor, InstanceConfiguration.get_config()):
            return Response({'error': 'Download non autorizzato.'}, status=403)
        # Single use: a link copied from a log or history cannot be replayed.
        if not isinstance(claims.get('id'), str) or not cache.add(f'instance:backup-download-used:{claims["id"]}', True, timeout=TOKEN_MAX_AGE + 60):
            return Response({'error': 'Link di download già usato. Conferma di nuovo la password.'}, status=403)
        try:
            size, body = open_runner_stream(f'/backups/{name}')
        except UpdaterRejected as exc:
            return Response({'error': 'Backup non trovato.' if exc.status == 404 else str(exc)},
                            status=404 if exc.status == 404 else 503)
        except UpdaterUnavailable as exc:
            return Response({'error': str(exc)}, status=503)
        response = StreamingHttpResponse(_AsyncStreamingBody(body), content_type='application/gzip')
        response['Content-Length'] = str(size)
        response['Content-Disposition'] = content_disposition_header(True, name)
        response['Cache-Control'] = 'no-store'
        response['Referrer-Policy'] = 'no-referrer'
        response.disable_gzip = True
        return response


FAILURE_NOTIFIED = 'instance:backup-failure-notified:{}'
FAILURE_WINDOW_HOURS = 48


def _failures(status, inventory):
    """Failed backups of the last two days, as (key, when, reason)."""
    from datetime import timedelta
    from django.utils import timezone
    from django.utils.dateparse import parse_datetime
    cutoff = timezone.now() - timedelta(hours=FAILURE_WINDOW_HOURS)
    for operation in status.get('history', []):
        when = parse_datetime(operation.get('updated_at') or operation.get('created_at') or '')
        if operation.get('kind') == 'backup' and operation.get('status') == 'failed' and when and when >= cutoff:
            yield operation['id'], when, operation.get('error') or ''
    last = (inventory.get('schedule') or {}).get('last_run') or {}
    when = parse_datetime(last.get('at') or '')
    # A scheduled run that could not start has no operation of its own.
    if last.get('status') == 'failed' and not last.get('operation') and when and when >= cutoff:
        yield f'schedule:{last["at"]}', when, last.get('error') or ''


def notify_failures():
    """Email the owner once about each failed backup."""
    from django.core.mail import send_mail
    from django.utils import timezone
    from .updater_client import deployment_mode
    if deployment_mode() != 'production':
        return 0
    try:
        status = call_runner('GET', '/status')
        inventory = call_runner('GET', '/backups')
    except (UpdaterUnavailable, UpdaterRejected):
        return 0
    config = InstanceConfiguration.objects.select_related('primary_association__user').first()
    owner = getattr(getattr(config, 'primary_association', None), 'user', None)
    if not owner or not owner.email:
        return 0
    sent = 0
    for key, when, reason in _failures(status, inventory):
        if not cache.add(FAILURE_NOTIFIED.format(key), True, timeout=7 * 86400):
            continue
        local = timezone.localtime(when).strftime('%d/%m/%Y %H:%M')
        link = f'https://{config.domain}/#/profile?page=self-instance&tab=backups' if config.domain else ''
        body = f'Il backup del {local} non è riuscito.\n\n'
        if reason:
            body += f'Motivo: {reason}\n\n'
        body += f'Controlla in Istanza > Backup{": " + link if link else ""}.\n'
        try:
            send_mail('Backup non riuscito', body, None, [owner.email])
            sent += 1
        except Exception:
            cache.delete(FAILURE_NOTIFIED.format(key))
            logger.warning('Could not send the backup failure email', exc_info=True)
    return sent
