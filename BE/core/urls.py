"""core URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.http import HttpResponse, HttpResponseForbidden
from django.urls import path, include
from django_ratelimit.exceptions import Ratelimited

from core.settings import DEBUG
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView


def handler403(request, exception=None):
    if isinstance(exception, Ratelimited):
        return HttpResponse('Too many requests, blocked.', status=429)
    return HttpResponseForbidden('Forbidden')


urlpatterns = [
    # path(ADMIN_URL, admin.site.urls),
    path('', include('application.urls')),
    path('', include('application.chat.urls')),
    path('', include('docmanager.urls')),
    path('', include('communications.urls')),
    path('instance/', include('instance.urls')),
    # path('api-auth/', include('rest_framework.urls'))
    path('schema/', SpectacularAPIView.as_view(), name='schema'),
    # Optional UI:
    path('schema/swagger-ui/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('schema/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
]

if DEBUG:
    urlpatterns += [path('silk/', include('silk.urls', namespace='silk'))]
    # add also handling of static files in debug mode
    from django.conf import settings
    from django.conf.urls.static import static
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
