"""
Permissions for instance bootstrap/setup endpoints.
"""
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.utils.crypto import constant_time_compare
from rest_framework.permissions import BasePermission

from .models import InstanceConfiguration


def is_primary_association_owner_or_superuser(user, config=None):
    """Return True only for the configured instance owner or a superuser."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False

    if getattr(user, 'is_superuser', False):
        return True

    if config is None:
        config = InstanceConfiguration.objects.select_related('primary_association').first()

    if not config or not getattr(config, 'primary_association_id', None):
        return False

    try:
        association = config.primary_association
    except ObjectDoesNotExist:
        return False

    owner_id = getattr(association, 'user_id', None)
    user_id = getattr(user, 'pk', None)
    if owner_id is None or user_id is None:
        return False

    return str(owner_id) == str(user_id)


class SetupTokenOrAuthenticated(BasePermission):
    """
    Require the setup token before first-run configuration is complete.

    Once the instance is configured, these endpoints are restricted to the
    primary association owner or Django superusers only.
    """

    message = "Setup token missing or invalid."

    def has_permission(self, request, view):
        config = InstanceConfiguration.objects.select_related('primary_association').first()
        if config:
            return is_primary_association_owner_or_superuser(request.user, config)

        expected_token = str(getattr(settings, 'INSTANCE_SETUP_TOKEN', '') or '')
        if not expected_token:
            return False

        provided_token = str(request.headers.get('X-Setup-Token', '') or '')
        return constant_time_compare(provided_token, expected_token)
