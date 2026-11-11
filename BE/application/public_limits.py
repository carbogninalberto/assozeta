"""Limits of the flows that are open without an account: request size and requests per address."""
from django.conf import settings
from rest_framework.throttling import AnonRateThrottle

from docmanager.views.document_view import medical_certificate_too_large_message


class PublicRequestThrottle(AnonRateThrottle):
    """Requests sent without an account, counted per address and endpoint in a fixed window."""
    scope = None

    def get_rate(self):
        return f'{settings.PUBLIC_REQUESTS_PER_WINDOW}/{settings.PUBLIC_REQUEST_WINDOW_SECONDS}s'

    def parse_rate(self, rate):
        count, seconds = rate.split('/')
        return int(count), int(seconds.rstrip('s'))


class PublicModuleResponseThrottle(PublicRequestThrottle):
    scope = 'public-module-response'


class PublicModuleAttachmentThrottle(PublicRequestThrottle):
    scope = 'public-module-attachment'


class PublicSubscriptionThrottle(PublicRequestThrottle):
    scope = 'public-subscription-add'


def upload_too_large_message(limit):
    return medical_certificate_too_large_message(limit)


def body_too_large(request):
    """Whether a request without an account declares a body above the upload limit."""
    if request.user.is_authenticated:
        return False
    try:
        declared = int(request.META.get('CONTENT_LENGTH') or 0)
    except ValueError:
        return False
    return declared > settings.PUBLIC_UPLOAD_MAX_BYTES
