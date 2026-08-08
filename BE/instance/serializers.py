"""
Serializers for instance configuration endpoints.
"""
from rest_framework import serializers
from django.conf import settings

from .models import InstanceConfiguration
from .defaults import SUPPORTED_FEATURES


CANONICAL_LOGO_URL = '/api/instance/logo.png'
CANONICAL_MANIFEST_URL = '/api/instance/manifest.json'
DEFAULT_LOGO_URL = '/oem/assozeta/brand/logo.svg'


class InstanceStatusSerializer(serializers.Serializer):
    """Serializer for /instance/status endpoint."""
    configured = serializers.BooleanField()
    version = serializers.CharField()
    instance_name = serializers.CharField(allow_null=True)
    supported_features = serializers.ListField(child=serializers.CharField())


class OEMConfigSerializer(serializers.Serializer):
    """Nested serializer for OEM configuration in response."""
    name = serializers.CharField()
    abbreviation = serializers.CharField()
    logo = serializers.CharField(allow_null=True)
    supportEmail = serializers.CharField(source='support_email')
    primaryColor = serializers.CharField(source='primary_color')
    displaySettings = serializers.JSONField(source='display_settings')


class OAuthConfigSerializer(serializers.Serializer):
    """Nested serializer for OAuth configuration in response."""
    googleClientId = serializers.CharField(allow_null=True, allow_blank=True)
    appleClientId = serializers.CharField(allow_null=True, allow_blank=True)


class StripeConfigSerializer(serializers.Serializer):
    """Nested serializer for Stripe configuration in response."""
    publicKey = serializers.CharField(allow_null=True, allow_blank=True)
    pricingTable = serializers.CharField(allow_null=True, allow_blank=True)
    clientPortal = serializers.CharField(allow_null=True, allow_blank=True)


class MetaConfigSerializer(serializers.Serializer):
    """Nested serializer for meta/SEO configuration in response."""
    title = serializers.CharField()
    description = serializers.CharField()
    manifest = serializers.CharField()


class FeaturesConfigSerializer(serializers.Serializer):
    """Nested serializer for feature flags in response."""
    isReseller = serializers.BooleanField()
    selfHosted = serializers.BooleanField()
    supportMultipleAssociations = serializers.BooleanField()
    showCourseAthletes = serializers.BooleanField()
    showMinorExemption = serializers.BooleanField()
    showTutorial = serializers.BooleanField()


class InstanceConfigSerializer(serializers.ModelSerializer):
    """
    Serializer for /instance/config endpoint.
    Returns full instance configuration for frontend.
    """
    oem = serializers.SerializerMethodField()
    oauth = serializers.SerializerMethodField()
    stripe = serializers.SerializerMethodField()
    meta = serializers.SerializerMethodField()
    features = serializers.SerializerMethodField()
    setup = serializers.SerializerMethodField()

    class Meta:
        model = InstanceConfiguration
        fields = ['oem', 'oauth', 'stripe', 'meta', 'features', 'setup']

    def get_oem(self, obj):
        return {
            'name': obj.name,
            'abbreviation': obj.abbreviation,
            'logo': obj.logo_path or DEFAULT_LOGO_URL,
            'supportEmail': obj.support_email,
            'primaryColor': obj.primary_color,
            'displaySettings': obj.get_display_settings(),
        }

    def get_oauth(self, obj):
        return {
            'googleClientId': obj.google_client_id or None,
            'appleClientId': obj.apple_client_id or None,
        }

    def get_stripe(self, obj):
        return {
            'publicKey': getattr(settings, 'STRIPE_PUBLIC_KEY', '') or None,
            'pricingTable': None,
            'clientPortal': None,
        }

    def get_meta(self, obj):
        return {
            'title': obj.meta_title or obj.name,
            'description': obj.meta_description or 'Gestionale per associazioni sportive',
            'manifest': CANONICAL_MANIFEST_URL,
        }

    def get_features(self, obj):
        return {
            'isReseller': obj.is_reseller,
            'selfHosted': obj.self_hosted,
            'supportMultipleAssociations': obj.support_multiple_associations,
            'showCourseAthletes': obj.show_course_athletes,
            'showMinorExemption': obj.show_minor_exemption,
            'showTutorial': obj.show_tutorial,
        }

    def get_setup(self, obj):
        return {
            'provenance': obj.setup_provenance,
            'onboardingComplete': obj.onboarding_completed_at is not None,
        }


# Input serializers for setup

class OEMInputSerializer(serializers.Serializer):
    """Input serializer for OEM configuration."""
    name = serializers.CharField(max_length=255)
    abbreviation = serializers.CharField(max_length=50, required=False, allow_blank=True, default='')
    primaryColor = serializers.CharField(max_length=7, required=False, default='#351DC2')
    supportEmail = serializers.EmailField(required=False, allow_blank=True, default='')


class OEMSetupInputSerializer(OEMInputSerializer):
    """OEM setup input including the canonical first-run logo path."""
    logo = serializers.CharField(max_length=255, required=False, allow_blank=True, allow_null=True)

    def validate_logo(self, value):
        if value in (None, ''):
            return ''
        if value != CANONICAL_LOGO_URL:
            raise serializers.ValidationError(f'Logo must be {CANONICAL_LOGO_URL}.')
        return value


class OAuthInputSerializer(serializers.Serializer):
    """Input serializer for OAuth configuration."""
    googleClientId = serializers.CharField(max_length=255, required=False, allow_blank=True, allow_null=True, default='')
    appleClientId = serializers.CharField(max_length=255, required=False, allow_blank=True, allow_null=True, default='')


class StripeInputSerializer(serializers.Serializer):
    """Input serializer for Stripe configuration."""
    publicKey = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    secretKey = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    webhookSecret = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    pricingTable = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    clientPortal = serializers.URLField(required=False, allow_blank=True, default='')


class InitializationInputSerializer(serializers.Serializer):
    """Input serializer for initialization configuration."""
    type = serializers.ChoiceField(choices=['fresh', 'import'])
    # For fresh initialization
    associationName = serializers.CharField(max_length=150, required=False)
    ownerEmail = serializers.EmailField(required=False)
    ownerPassword = serializers.CharField(max_length=128, required=False, write_only=True)
    # For import initialization
    importTaskId = serializers.CharField(max_length=255, required=False)

    def validate(self, data):
        init_type = data.get('type')

        if init_type == 'fresh':
            if not data.get('associationName'):
                raise serializers.ValidationError({'associationName': 'Required for fresh initialization.'})
            if not data.get('ownerEmail'):
                raise serializers.ValidationError({'ownerEmail': 'Required for fresh initialization.'})
            if not data.get('ownerPassword'):
                raise serializers.ValidationError({'ownerPassword': 'Required for fresh initialization.'})
            if len(data.get('ownerPassword', '')) < 8:
                raise serializers.ValidationError({'ownerPassword': 'Password must be at least 8 characters.'})

        elif init_type == 'import':
            if not data.get('importTaskId'):
                raise serializers.ValidationError({'importTaskId': 'Required for import initialization.'})

        return data


class InstanceSetupSerializer(serializers.Serializer):
    """
    Serializer for /instance/configure endpoint.
    Handles initial instance setup.
    """
    domain = serializers.CharField(max_length=255)
    oem = OEMSetupInputSerializer()
    oauth = OAuthInputSerializer(required=False, default=dict)
    stripe = StripeInputSerializer(required=False, default=dict)
    initialization = InitializationInputSerializer()

    def validate_domain(self, value):
        # Basic domain validation
        if not value or len(value) < 3:
            raise serializers.ValidationError('Domain must be at least 3 characters.')
        return value.lower().strip()


class InstanceReconfigureSerializer(serializers.ModelSerializer):
    """
    Serializer for /instance/reconfigure endpoint.
    Allows partial updates to instance configuration.
    """
    oem = OEMInputSerializer(required=False)
    oauth = OAuthInputSerializer(required=False)
    stripe = StripeInputSerializer(required=False)

    class Meta:
        model = InstanceConfiguration
        fields = [
            'oem', 'oauth', 'stripe',
            'display_settings', 'meta_title', 'meta_description',
            'show_course_athletes', 'show_minor_exemption', 'show_tutorial'
        ]
        extra_kwargs = {
            'display_settings': {'required': False},
            'meta_title': {'required': False},
            'meta_description': {'required': False},
            'show_course_athletes': {'required': False},
            'show_minor_exemption': {'required': False},
            'show_tutorial': {'required': False},
        }

    def update(self, instance, validated_data):
        # Handle nested OEM data
        oem_data = validated_data.pop('oem', None)
        if oem_data:
            instance.name = oem_data.get('name', instance.name)
            instance.abbreviation = oem_data.get('abbreviation', instance.abbreviation)
            instance.primary_color = oem_data.get('primaryColor', instance.primary_color)
            instance.support_email = oem_data.get('supportEmail', instance.support_email)

        # Handle nested OAuth data (convert null to empty string for DB)
        oauth_data = validated_data.pop('oauth', None)
        if oauth_data:
            instance.google_client_id = oauth_data.get('googleClientId') or ''
            instance.apple_client_id = oauth_data.get('appleClientId') or ''

        # Handle nested Stripe data
        stripe_data = validated_data.pop('stripe', None)
        if stripe_data:
            # Stripe runtime credentials are environment-owned in self-hosted deployments.
            # Keep legacy model fields for non-destructive compatibility, but do not let
            # configuration API input update runtime credential state.
            pass

        # Handle remaining fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()
        return instance
