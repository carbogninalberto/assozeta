from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET


@require_GET
def health(request):
    result = {"status": "ok", "version": settings.RUNNING_VERSION}
    if settings.MANUAL_RUN_ID:
        result['manual_capture'] = {'run_id': settings.MANUAL_RUN_ID,
                                    'application_revision': settings.MANUAL_APPLICATION_REVISION,
                                    'reference_time': timezone.now().isoformat()}
    return JsonResponse(result)


@require_GET
def readiness(request):
    checks = {"database": False, "redis": False}

    try:
        connection.ensure_connection()
        checks["database"] = True
    except Exception:
        pass

    try:
        cache.set("assozeta-readiness", "ok", timeout=10)
        checks["redis"] = cache.get("assozeta-readiness") == "ok"
    except Exception:
        pass

    ready = all(checks.values())
    return JsonResponse(
        {"status": "ready" if ready else "unavailable", "checks": checks},
        status=200 if ready else 503,
    )
