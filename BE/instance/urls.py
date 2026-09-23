"""
URL configuration for instance configuration endpoints.
"""
from django.urls import path
from .administration import InstanceAccessView, InstanceAdminView, OwnerLogoView, InstanceReleasesView, InstanceUpdatesView

from .diagnostics import DiagnosticsView
from .operations import EmailSettingsView, EmailTestView
from .integrations import IntegrationSettingsView, IntegrationTestView

from .views import (
    InstanceStatusView,
    InstanceSetupTokenValidateView,
    InstanceConfigView,
    InstanceSetupView,
    InstanceLogoUploadView,
    InstanceLogoServeView,
    InstanceManifestView,
    InstanceReconfigureView,
)

urlpatterns = [
    path('admin/integrations/<str:provider>/test', IntegrationTestView.as_view(), name='instance-integration-test'),
    path('admin/integrations/<str:provider>', IntegrationSettingsView.as_view(), name='instance-integration-settings'),
    path('admin/diagnostics', DiagnosticsView.as_view(), name='instance-diagnostics'),
    path('admin/email', EmailSettingsView.as_view(), name='instance-email-settings'),
    path('admin/email/test', EmailTestView.as_view(), name='instance-email-test'),
    path('access', InstanceAccessView.as_view(), name='instance-access'),
    path('admin', InstanceAdminView.as_view(), name='instance-admin'),
    path('admin/logo', OwnerLogoView.as_view(), name='instance-owner-logo'),
    path('admin/releases', InstanceReleasesView.as_view(), name='instance-releases'),
    path('admin/updates', InstanceUpdatesView.as_view(), name='instance-updates'),
    path('status', InstanceStatusView.as_view(), name='instance-status'),
    path('setup-token/validate', InstanceSetupTokenValidateView.as_view(), name='instance-setup-token-validate'),
    path('config', InstanceConfigView.as_view(), name='instance-config'),
    path('configure', InstanceSetupView.as_view(), name='instance-configure'),
    path('logo', InstanceLogoUploadView.as_view(), name='instance-logo-upload'),
    path('logo.png', InstanceLogoServeView.as_view(), name='instance-logo'),
    path('manifest.json', InstanceManifestView.as_view(), name='instance-manifest'),
    path('reconfigure', InstanceReconfigureView.as_view(), name='instance-reconfigure'),
]
