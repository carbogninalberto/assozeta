"""
Instance configuration model for self-hosted deployments.
"""
import uuid
from copy import deepcopy
from django.db import models

from .defaults import DEFAULT_DISPLAY_SETTINGS, DEFAULT_PRIMARY_COLOR


class InstanceConfiguration(models.Model):
    """
    Singleton model for instance-wide configuration.
    Only one record should exist per instance.
    """

    SETUP_PROVENANCE_FRESH = 'fresh'
    SETUP_PROVENANCE_IMPORT = 'import'
    SETUP_PROVENANCE_LEGACY = 'legacy'
    SETUP_PROVENANCE_CHOICES = (
        (SETUP_PROVENANCE_FRESH, 'Fresh setup'),
        (SETUP_PROVENANCE_IMPORT, 'Imported backup'),
        (SETUP_PROVENANCE_LEGACY, 'Legacy setup'),
    )

    # Basic info
    domain = models.CharField(max_length=255, unique=True)
    configured_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Setup state
    setup_provenance = models.CharField(
        max_length=16,
        choices=SETUP_PROVENANCE_CHOICES,
        default=SETUP_PROVENANCE_LEGACY,
    )
    onboarding_completed_at = models.DateTimeField(null=True, blank=True)

    # OEM Branding
    name = models.CharField(max_length=255)
    abbreviation = models.CharField(max_length=50, blank=True, default='')
    primary_color = models.CharField(max_length=7, default=DEFAULT_PRIMARY_COLOR)
    support_email = models.EmailField(blank=True, default='')
    logo_path = models.CharField(max_length=255, blank=True, default='')

    # Empty settings preserve the host's existing email environment configuration.
    email_settings = models.JSONField(default=dict, blank=True)
    email_password_encrypted = models.TextField(default='', blank=True)
    email_revision = models.PositiveIntegerField(default=0)
    diagnostic_results = models.JSONField(default=dict, blank=True)
    # Per-provider revisions and encrypted overrides; absent overrides use environment.
    integration_settings = models.JSONField(default=dict, blank=True)

    # Display settings (JSON for flexibility)
    display_settings = models.JSONField(default=dict)

    # Feature flags
    is_reseller = models.BooleanField(default=False)
    self_hosted = models.BooleanField(default=True)
    support_multiple_associations = models.BooleanField(default=False)
    show_course_athletes = models.BooleanField(default=True)
    show_minor_exemption = models.BooleanField(default=True)
    show_tutorial = models.BooleanField(default=True)

    # OAuth (optional)
    google_client_id = models.CharField(max_length=255, blank=True, default='')
    apple_client_id = models.CharField(max_length=255, blank=True, default='')

    # Stripe (optional)
    stripe_public_key = models.CharField(max_length=255, blank=True, default='')
    stripe_secret_key = models.CharField(max_length=255, blank=True, default='')
    stripe_webhook_secret = models.CharField(max_length=255, blank=True, default='')
    stripe_pricing_table = models.CharField(max_length=255, blank=True, default='')
    stripe_client_portal = models.URLField(blank=True, default='')

    # Meta
    meta_title = models.CharField(max_length=255, blank=True, default='')
    meta_description = models.TextField(blank=True, default='')

    # Association created during setup
    primary_association = models.ForeignKey(
        'application.SportAssociation',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='instance_config'
    )

    class Meta:
        verbose_name = "Instance Configuration"
        verbose_name_plural = "Instance Configuration"

    def __str__(self):
        return f"Instance: {self.name} ({self.domain})"

    def save(self, *args, **kwargs):
        # Ensure only one instance exists (singleton pattern)
        if not self.pk and InstanceConfiguration.objects.exists():
            raise ValueError("Only one InstanceConfiguration can exist. Use get_config() to retrieve it.")

        # Set default display settings if empty
        if not self.display_settings:
            self.display_settings = deepcopy(DEFAULT_DISPLAY_SETTINGS)

        super().save(*args, **kwargs)

    @classmethod
    def get_config(cls):
        """Get the singleton instance configuration."""
        return cls.objects.first()

    @classmethod
    def is_configured(cls):
        """Check if instance has been configured."""
        return cls.objects.exists()

    def get_display_settings(self):
        """Get display settings with defaults for missing keys."""
        settings = deepcopy(DEFAULT_DISPLAY_SETTINGS)
        if self.display_settings:
            # Deep merge
            for key, value in self.display_settings.items():
                if key in settings and isinstance(settings[key], dict) and isinstance(value, dict):
                    settings[key].update(value)
                else:
                    settings[key] = value
        return settings


class DataRestore(models.Model):
    """Durable receipt for validated uploads and background replacement."""
    ACTIVE = ('queued', 'running')
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey('application.User', on_delete=models.PROTECT)
    association_id = models.UUIDField()
    state = models.CharField(max_length=16, default='review')
    stage = models.CharField(max_length=32, default='validated')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    upload_path = models.TextField(default='')
    sha256 = models.CharField(max_length=64)
    version = models.CharField(max_length=100, default='')
    preview = models.JSONField(default=dict)
    backup_path = models.TextField(default='')
    backup_sha256 = models.CharField(max_length=64, default='')
    media = models.JSONField(default=dict)
    error = models.TextField(default='')
    allow_missing_media = models.BooleanField(default=False)
    attempts = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['association_id'], condition=models.Q(state__in=['queued', 'running']),
            name='one_active_data_restore',
        )]


class DataRestoreUserDetachment(models.Model):
    """Local provenance for access revoked by replacement, consumed on recovery."""
    user = models.OneToOneField('application.User', on_delete=models.CASCADE, related_name='+')
    owner = models.ForeignKey('application.User', on_delete=models.CASCADE, related_name='+')
    association_id = models.UUIDField()
