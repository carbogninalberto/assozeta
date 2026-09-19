"""Owner-only operational settings. Secrets never enter API responses or audit logs."""
import ipaddress
import re

from django.db import transaction
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from .email_configuration import effective_email, encrypt_password, public_email
from .operation_limits import claim_operation
from .models import InstanceConfiguration
from .permissions import IsInstanceOwner
from .probe_runner import run_probe


class EmailSettingsSerializer(serializers.Serializer):
    revision = serializers.IntegerField(min_value=0)
    host = serializers.CharField(max_length=253)
    port = serializers.IntegerField(min_value=1, max_value=65535)
    security = serializers.ChoiceField(choices=['ssl', 'tls', 'none'])
    username = serializers.CharField(max_length=254, allow_blank=True)
    password = serializers.CharField(max_length=4096, required=False, trim_whitespace=False, write_only=True)
    clear_password = serializers.BooleanField(default=False)
    from_email = serializers.EmailField(max_length=254)
    sender_name = serializers.CharField(max_length=100, allow_blank=True)

    def validate_host(self, value):
        try:
            ipaddress.ip_address(value)
        except ValueError:
            labels = value.rstrip('.').split('.')
            if not all(re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', label) for label in labels):
                raise serializers.ValidationError('Inserisci un nome host o indirizzo IP senza protocollo o percorso.')
        return value

    def validate_sender_name(self, value):
        if '\r' in value or '\n' in value:
            raise serializers.ValidationError('Il nome mittente deve essere su una sola riga.')
        return value

    def validate(self, data):
        if data.get('password') and data['clear_password']:
            raise serializers.ValidationError('Scegli se sostituire o rimuovere la password.')
        if data['security'] == 'none' and (data['username'] or data.get('password')):
            raise serializers.ValidationError('Usa TLS o SSL per inviare credenziali SMTP.')
        return data


class OwnerOperationsView(APIView):
    permission_classes = [IsInstanceOwner]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'no-store'
        return response


class EmailSettingsView(OwnerOperationsView):
    permission_classes = [IsInstanceOwner]

    def get(self, request):
        config = InstanceConfiguration.get_config()
        last_test = (config.diagnostic_results or {}).get('email_test')
        if last_test and last_test.get('revision') != config.email_revision:
            last_test = None
        return Response({**public_email(config), 'last_test': last_test})

    @transaction.atomic
    def put(self, request):
        serializer = EmailSettingsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        config = InstanceConfiguration.objects.select_for_update().first()
        if config.email_revision != data.pop('revision'):
            return Response({'error': 'Configurazione modificata in un’altra sessione. Ricarica prima di salvare.'}, status=409)
        clear = data.pop('clear_password')
        password = data.pop('password', None)
        if password is None and not clear:
            # Retain the effective password when switching from environment to DB.
            # Do not silently discard an unreadable saved credential.
            try:
                password = effective_email(config)['password']
            except Exception:
                return Response({'error': 'Reinserisci o rimuovi esplicitamente la password salvata.'}, status=400)
        password = '' if clear else password
        if data['security'] == 'none' and password:
            return Response({'error': 'Rimuovi la password oppure abilita TLS o SSL.'}, status=400)
        config.email_settings = data
        config.email_password_encrypted = encrypt_password(password)
        self.persist(config)
        return Response(public_email(config))

    @transaction.atomic
    def delete(self, request):
        revision = serializers.IntegerField(min_value=0).run_validation(request.data.get('revision'))
        config = InstanceConfiguration.objects.select_for_update().first()
        if config.email_revision != revision:
            return Response({'error': 'Configurazione modificata. Ricarica prima di ripristinare.'}, status=409)
        config.email_settings = {}
        config.email_password_encrypted = ''
        self.persist(config)
        return Response(public_email(config))

    @staticmethod
    def persist(config):
        config.email_revision += 1
        # Old email checks must not remain green after changing credentials.
        results = dict(config.diagnostic_results or {})
        results['checks'] = [item for item in results.get('checks', []) if item.get('id') != 'email']
        results.pop('email_test', None)
        config.diagnostic_results = results
        config.save(update_fields=['email_settings', 'email_password_encrypted', 'email_revision', 'diagnostic_results', 'updated_at'])


class EmailTestSerializer(serializers.Serializer):
    revision = serializers.IntegerField(min_value=0)
    action = serializers.ChoiceField(choices=['connect', 'send'])
    recipient = serializers.EmailField(required=False, max_length=254)

    def validate(self, data):
        if data['action'] == 'send' and not data.get('recipient'):
            raise serializers.ValidationError({'recipient': 'Inserisci il destinatario del messaggio di prova.'})
        return data


class EmailTestView(OwnerOperationsView):
    permission_classes = [IsInstanceOwner]

    def post(self, request):
        serializer = EmailTestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        value = serializer.validated_data
        config = InstanceConfiguration.get_config()
        if config.email_revision != value['revision']:
            return Response({'error': 'La configurazione è cambiata. Ricarica prima di eseguire il test.'}, status=409)
        if not claim_operation('email_test', 20):
            return Response({'error': 'Un test è già in corso o è appena terminato. Attendi fino a 20 secondi e riprova.'}, status=429, headers={'Retry-After': '20'})
        result = run_probe('email_' + value['action'], value)
        with transaction.atomic():
            config = InstanceConfiguration.objects.select_for_update().first()
            if config.email_revision != value['revision']:
                return Response({'error': 'Configurazione cambiata durante il test. Ricarica le impostazioni.' + (' Il messaggio potrebbe essere stato inviato: verifica il destinatario prima di riprovare.' if value['action'] == 'send' else '')}, status=409)
            results = dict(config.diagnostic_results or {})
            results['email_test'] = {**result, 'revision': config.email_revision, 'action': value['action']}
            config.diagnostic_results = results
            config.save(update_fields=['diagnostic_results'])
        return Response(result)
