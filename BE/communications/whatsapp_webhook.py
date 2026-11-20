from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .whatsapp import record_status
from .whatsapp_provider import CloudAPI


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def webhook(request):
    provider = CloudAPI()
    try:
        if request.method == 'GET':
            challenge = provider.verify_webhook(
                request.GET.get('hub.mode', ''), request.GET.get('hub.verify_token', ''),
                request.GET.get('hub.challenge', ''),
            )
            return HttpResponse(challenge, content_type='text/plain')
        body = request.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            return HttpResponse(status=400)
        events = provider.parse_status_callback(body, request.headers.get('X-Hub-Signature-256', ''))
    except PermissionError:
        return HttpResponse(status=403)
    except (ValueError, KeyError, TypeError, IndexError, AttributeError, OSError, OverflowError):
        return HttpResponse(status=400)
    for event in events:
        record_status(event)
    return HttpResponse(status=200)
