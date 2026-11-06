"""Owner page "Backup e ripristino": read-only list of the server backups.

The independent runner owns the backup directory; the API only measures the
data that the next backup will contain and relays archive downloads. Each
download needs the owner's password again and is recorded in the audit log.
"""
import logging
import re

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
        })


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
        token = signing.dumps({'name': name, 'actor': str(actor.pk)}, salt=TOKEN_SALT)
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
