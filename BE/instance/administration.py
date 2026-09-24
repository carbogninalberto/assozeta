"""Authenticated, owner-only instance administration, separate from public bootstrap."""
from django.conf import settings
from django.core.cache import cache
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import InstanceConfiguration
from .permissions import IsInstanceOwner, is_instance_owner, is_instance_administrator
from .serializers import InstanceConfigSerializer, InstanceReconfigureSerializer
from .views import InstanceLogoUploadView
from .release_catalog import ReleaseError, fetch_releases, fetch_release, release_summary, version_tuple
from .updater_client import call_runner, deployment_mode, UpdaterUnavailable, UpdaterRejected

CATALOG_KEY = 'instance:stable-releases:v1'


class InstanceAccessView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = getattr(request, 'authenticated_user', getattr(request, 'original_user', request.user))
        config = InstanceConfiguration.get_config()
        association = config.primary_association if config and config.primary_association_id else None
        allowed = is_instance_administrator(actor, config)
        return Response({
            'is_owner': is_instance_owner(actor, config),
            'is_administrator': allowed,
            'data_association': {'id': str(association.pk), 'name': association.denomination} if allowed and association else None,
        })


class OwnerLogoView(InstanceLogoUploadView):
    permission_classes = [IsInstanceOwner]


class InstanceAdminView(APIView):
    permission_classes = [IsInstanceOwner]

    def get(self, request):
        config = InstanceConfiguration.get_config()
        return Response({
            'config': InstanceConfigSerializer(config).data,
            'running_version': settings.RUNNING_VERSION,
            'configured_version': getattr(settings, 'ASSOZETA_CONFIGURED_VERSION', ''),
            'mode': deployment_mode(),
        })

    def put(self, request):
        if set(request.data) != {'oem'}:
            raise serializers.ValidationError('Only instance branding can be edited here.')
        config = InstanceConfiguration.get_config()
        serializer = InstanceReconfigureSerializer(config, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({'config': InstanceConfigSerializer(config).data})


class InstanceReleasesView(APIView):
    permission_classes = [IsInstanceOwner]

    def get(self, request):
        try:
            page = int(request.query_params.get('page', '1'))
            if page < 1:
                raise ValueError
        except ValueError:
            raise serializers.ValidationError('Invalid release history page.')
        releases = cache.get(CATALOG_KEY)
        refresh = request.query_params.get('refresh') == '1'
        try:
            # Bound manual refreshes without withholding pagination or cached notes.
            if releases is None or (refresh and cache.add(f'{CATALOG_KEY}:refresh', True, timeout=30)):
                releases = fetch_releases()
                cache.set(CATALOG_KEY, releases, timeout=600)
        except ReleaseError as exc:
            return Response({'error': str(exc)}, status=503)
        return Response(release_summary(releases, settings.RUNNING_VERSION, page))


class UpdateRequestSerializer(serializers.Serializer):
    release_id = serializers.IntegerField(min_value=1)
    tag = serializers.CharField(max_length=64)
    request_id = serializers.UUIDField()


class RestartRequestSerializer(serializers.Serializer):
    request_id = serializers.UUIDField()


class InstanceRestartsView(APIView):
    permission_classes = [IsInstanceOwner]

    def post(self, request):
        if set(request.data) != {'request_id'}:
            raise serializers.ValidationError('È richiesto soltanto l’identificativo della richiesta.')
        serializer = RestartRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if deployment_mode() != 'production':
            return Response({'error': 'Il riavvio dei container è disabilitato in sviluppo.'}, status=409)
        try:
            result = call_runner('POST', '/restarts', {
                'request_id': str(serializer.validated_data['request_id']),
                'actor_id': str(getattr(request, 'original_user', request.user).pk),
            })
            return Response(result, status=status.HTTP_202_ACCEPTED)
        except UpdaterUnavailable as exc:
            return Response({'error': str(exc)}, status=503)
        except UpdaterRejected as exc:
            return Response({'error': str(exc)}, status=exc.status)


class InstanceUpdatesView(APIView):
    permission_classes = [IsInstanceOwner]

    def get(self, request):
        try:
            result = call_runner('GET', '/status')
            if result.get('protocol') != 1:
                result = {'available': False, 'reason': 'Il servizio di aggiornamento richiede una versione compatibile.', 'active': None, 'history': []}
            return Response(result)
        except UpdaterUnavailable as exc:
            return Response({'available': False, 'reason': str(exc), 'active': None, 'history': []})
        except UpdaterRejected as exc:
            return Response({'error': str(exc)}, status=exc.status)

    def post(self, request):
        serializer = UpdateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if deployment_mode() != 'production':
            return Response({'error': 'Gli aggiornamenti reali sono disabilitati in sviluppo.'}, status=409)
        try:
            actor_id = str(getattr(request, 'original_user', request.user).pk)
            # A response may be lost immediately before the API is replaced.
            # Resolve retries before checking the now-changed installed version
            # or upstream release availability.
            runner_status = call_runner('GET', '/status')
            if runner_status.get('protocol') != 1:
                raise UpdaterUnavailable('Il servizio di aggiornamento richiede una versione compatibile.')
            records = ([runner_status['active']] if runner_status.get('active') else []) + runner_status.get('history', [])
            for operation in records:
                if operation.get('request_id') == str(data['request_id']):
                    if (operation.get('actor_id') != actor_id or operation.get('release_id') != data['release_id'] or
                            operation.get('tag') != data['tag']):
                        raise UpdaterRejected('Questo identificativo è già stato usato per un altro aggiornamento.')
                    return Response({'operation': operation}, status=status.HTTP_202_ACCEPTED)
            if not runner_status.get('available'):
                raise UpdaterUnavailable(runner_status.get('reason') or 'Il servizio di aggiornamento non è disponibile.')
            if runner_status.get('can_update') is False:
                raise UpdaterRejected(runner_status.get('reason') or 'La transazione precedente richiede un ripristino.')
            release = fetch_release(data['release_id'])
            current = version_tuple(settings.RUNNING_VERSION)
            if release['tag'] != data['tag'] or not release['artifacts_ready']:
                raise UpdaterRejected('La release selezionata non è pronta per l’aggiornamento.')
            if current is None or version_tuple(release['tag']) <= current:
                raise UpdaterRejected('La versione installata non consente questo aggiornamento.')
            result = call_runner('POST', '/updates', {
                'release_id': release['id'], 'tag': release['tag'],
                'request_id': str(data['request_id']),
                'actor_id': actor_id,
                'source_version': settings.RUNNING_VERSION,
            })
            return Response(result, status=status.HTTP_202_ACCEPTED)
        except ReleaseError as exc:
            return Response({'error': str(exc)}, status=503)
        except UpdaterUnavailable as exc:
            return Response({'error': str(exc)}, status=503)
        except UpdaterRejected as exc:
            return Response({'error': str(exc)}, status=exc.status)
