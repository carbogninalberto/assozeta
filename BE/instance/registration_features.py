"""Instance-wide registration choices, independent of customer entitlements."""
from django.conf import settings


def registration_features():
    # Keep the existing profile wire names for current clients and old links.
    # Enabling these UI workflows never grants role or data-access permissions.
    choices = (
        ('FAMILY_REGISTRATION_ENABLED', 'Iscrizione per famiglia'),
        ('FEDERATION_REGISTRATION_ENABLED', 'Iscrizioni per Enti e Federazioni'),
        ('MULTIPLE_REGISTRATION_ENABLED', 'Iscrizioni multiple per federazioni'),
    )
    return [{'name': name, 'description': ''} for setting, name in choices
            if getattr(settings, setting)]
