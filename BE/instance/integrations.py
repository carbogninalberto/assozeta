"""Owner-managed overrides for the integrations actually consumed by this app."""
import re

from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

from .integration_configuration import (PROVIDERS, SECRET_FIELDS, effective_integration,
                                        encrypt_secret, public_integration)
from .models import InstanceConfiguration
from .operations import OwnerOperationsView


class IntegrationSerializer(serializers.Serializer):
    revision = serializers.IntegerField(min_value=0)
    enabled = serializers.BooleanField()


class StripeSettingsSerializer(IntegrationSerializer):
    public_key = serializers.CharField(max_length=300, allow_blank=True)
    secret_key = serializers.CharField(max_length=500, required=False, allow_blank=True, write_only=True)
    webhook_secret = serializers.CharField(max_length=500, required=False, allow_blank=True, write_only=True)
    clear_secrets = serializers.ListField(child=serializers.ChoiceField(choices=SECRET_FIELDS['stripe']), default=list)

    def validate(self, data):
        patterns = {'public_key': r'pk_(test|live)_[A-Za-z0-9_]+',
                    'secret_key': r'(sk|rk)_(test|live)_[A-Za-z0-9_]+',
                    'webhook_secret': r'whsec_[A-Za-z0-9_]+'}
        for key, pattern in patterns.items():
            if data.get(key) and not re.fullmatch(pattern, data[key]):
                raise serializers.ValidationError({key: 'Formato della chiave non valido.'})
        if any(data.get(key) for key in data['clear_secrets']):
            raise serializers.ValidationError('Scegli se sostituire o rimuovere ciascuna credenziale.')
        return data


class LoginSettingsSerializer(IntegrationSerializer):
    client_id = serializers.CharField(max_length=255, allow_blank=True)

    def validate_client_id(self, value):
        if value and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', value):
            raise serializers.ValidationError('Inserisci un client ID senza spazi, URL o percorsi.')
        return value


class IntegrationSettingsView(OwnerOperationsView):
    def provider(self):
        provider = self.kwargs['provider']
        if provider not in PROVIDERS:
            raise NotFound()
        return provider

    def get(self, request, **kwargs):
        return Response(public_integration(self.provider(), InstanceConfiguration.get_config()))

    @transaction.atomic
    def put(self, request, **kwargs):
        provider = self.provider()
        serializer = (StripeSettingsSerializer if provider == 'stripe' else LoginSettingsSerializer)(data=request.data)
        serializer.is_valid(raise_exception=True)
        value = dict(serializer.validated_data)
        config = InstanceConfiguration.objects.select_for_update().first()
        current = effective_integration(provider, config, decrypt=False)
        if current['revision'] != value.pop('revision'):
            return Response({'error': 'Integrazione modificata in un’altra sessione. Ricarica prima di salvare.'}, status=409)
        clear = value.pop('clear_secrets', [])
        for key in SECRET_FIELDS[provider]:
            if key in clear:
                value[key] = ''
            elif key not in value:
                try:
                    # Preserve each omitted secret, including environment credentials.
                    from .integration_configuration import decrypt_secret
                    value[key] = decrypt_secret(current[key]) if current['source'] == 'instance' else current[key]
                except ImproperlyConfigured:
                    raise serializers.ValidationError({key: 'Reinserisci o rimuovi esplicitamente questa credenziale.'})
        if provider == 'stripe':
            # Validate retained environment credentials as well as replacements.
            StripeSettingsSerializer(data={**value, 'revision': current['revision']}).is_valid(raise_exception=True)
        if value['enabled']:
            if provider == 'stripe':
                missing = [key for key in PROVIDERS[provider] if not value.get(key)]
                if missing:
                    raise serializers.ValidationError({key: 'Campo richiesto per abilitare Stripe.' for key in missing})
                if not ((value['public_key'].startswith('pk_test_') and value['secret_key'].startswith(('sk_test_', 'rk_test_'))) or
                        (value['public_key'].startswith('pk_live_') and value['secret_key'].startswith(('sk_live_', 'rk_live_')))):
                    raise serializers.ValidationError('Chiave pubblica e segreta devono appartenere allo stesso ambiente Stripe.')
            elif not value['client_id']:
                raise serializers.ValidationError({'client_id': 'Client ID richiesto per abilitare l’integrazione.'})
            elif provider == 'google' and not value['client_id'].endswith('.apps.googleusercontent.com'):
                raise serializers.ValidationError({'client_id': 'Inserisci il client ID web fornito da Google.'})
        for key in SECRET_FIELDS[provider]:
            value[key] = encrypt_secret(value[key])
        self.persist(config, provider, current['revision'] + 1, value)
        return Response(public_integration(provider, config))

    @transaction.atomic
    def delete(self, request, **kwargs):
        provider = self.provider()
        revision = serializers.IntegerField(min_value=0).run_validation(request.data.get('revision'))
        config = InstanceConfiguration.objects.select_for_update().first()
        current = effective_integration(provider, config, decrypt=False)
        if current['revision'] != revision:
            return Response({'error': 'Integrazione modificata. Ricarica prima di ripristinare.'}, status=409)
        self.persist(config, provider, revision + 1, None)
        return Response(public_integration(provider, config))

    @staticmethod
    def persist(config, provider, revision, value):
        config.integration_settings = {**config.integration_settings, provider: {'revision': revision, 'settings': value}}
        config.save(update_fields=['integration_settings', 'updated_at'])
