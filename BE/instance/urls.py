"""
URL configuration for instance configuration endpoints.
"""
from django.urls import path

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
    path('status', InstanceStatusView.as_view(), name='instance-status'),
    path('setup-token/validate', InstanceSetupTokenValidateView.as_view(), name='instance-setup-token-validate'),
    path('config', InstanceConfigView.as_view(), name='instance-config'),
    path('configure', InstanceSetupView.as_view(), name='instance-configure'),
    path('logo', InstanceLogoUploadView.as_view(), name='instance-logo-upload'),
    path('logo.png', InstanceLogoServeView.as_view(), name='instance-logo'),
    path('manifest.json', InstanceManifestView.as_view(), name='instance-manifest'),
    path('reconfigure', InstanceReconfigureView.as_view(), name='instance-reconfigure'),
]
