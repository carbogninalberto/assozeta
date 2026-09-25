import logging
import django
import django_ratelimit
import requests

from django.http import JsonResponse
from django.middleware.gzip import GZipMiddleware as DjangoGZipMiddleware
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError, PermissionDenied, NotAcceptable
from rest_framework.permissions import BasePermission
from application.models import User

logger = logging.getLogger()


class GZipMiddleware(DjangoGZipMiddleware):
    def process_response(self, request, response):
        if getattr(response, 'disable_gzip', False):
            return response
        return super().process_response(request, response)


class ConditionalSessionMiddleware:
    """
    Session middleware that only processes sessions for routes that need them.

    ALWAYS uses sessions for:
    - OAuth2 routes (/oauth2/, /auth/, /accounts/) - needed for authentication flow
    - Django admin routes (/admin/)
    - Any non-API routes

    SKIPS session processing for:
    - Pure API routes (/api/) WITH Bearer token authorization
    - This saves 3-8ms per API request by avoiding Redis session lookups

    Sessions are required by Django's AuthenticationMiddleware for OAuth2 flows.
    """
    def __init__(self, get_response):
        self.get_response = get_response
        # Import the real SessionMiddleware
        from django.contrib.sessions.middleware import SessionMiddleware
        self.session_middleware = SessionMiddleware(get_response)

    def __call__(self, request):
        # Always use sessions for OAuth2/auth routes (required for authentication flow)
        needs_session = (
            request.path.startswith('/oauth2/') or
            request.path.startswith('/auth/') or
            request.path.startswith('/accounts/') or
            request.path.startswith('/admin/')
        )

        if needs_session:
            # Always use session processing for authentication routes
            return self.session_middleware(request)

        # For /api/ routes with Bearer token, skip sessions (they use OAuth2 tokens)
        is_api_with_token = (
            request.path.startswith('/api/') and
            request.headers.get('authorization', '').startswith('Bearer ')
        )

        if is_api_with_token:
            # Skip session processing for API requests with tokens
            return self.get_response(request)
        else:
            # Use normal session processing for all other routes
            return self.session_middleware(request)


class IsAuthenticated(BasePermission):
    """Apply the shared identity scope, including manually authenticated requests."""

    def has_permission(self, request, view):
        from application.impersonation import resolve_request_identity
        user = resolve_request_identity(request)
        return bool(user and user.is_authenticated)

    @staticmethod
    def has_permission_and_return_request(request):
        from application.impersonation import resolve_request_identity
        user = resolve_request_identity(request)
        return request, bool(user and user.is_authenticated)


class ExceptionHandlerMiddleware:
    """
    Middleware for centralized exception handling.

    Identity scoping is handled by authentication and the shared permission resolver.
    Django's new-style middleware doesn't call process_view() without MiddlewareMixin.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            '''
            We need to read the request body before calling the get_response method, because the body is a stream
            and if we are not doing so, if in the api we do request._request to call another endpoint
            there is the error "You cannot access body after reading from request's data stream" and the request
            is not sent.

            The reason behind this funky behavior is that the request body is a stream, and once it's read, it's gone.
            So, if you read it once, you can't read it again.
            The solution is to read the body before calling the get_response method.

            OPTIMIZATION: Only read body for methods that actually have a body (POST, PUT, PATCH, DELETE)
            This saves 5-10ms per GET request by avoiding unnecessary I/O

            Multipart requests are excluded so Django's upload handlers can stream large files to disk.
            '''
            if (
                request.method in ['POST', 'PUT', 'PATCH', 'DELETE']
                and not (request.content_type or '').startswith('multipart/')
            ):
                request.body
            response = self.get_response(request)

            return response

        except django_ratelimit.exceptions.Ratelimited as e:
            logger.error("Rate limit exceeded", extra={'path': request.path}, exc_info=True)
            return JsonResponse({"exception": e.detail}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        except django.core.exceptions.RequestDataTooBig as e:
            logger.warning("Request body too large", extra={'path': request.path})
            return JsonResponse({'exception': str(e)}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

        except FileNotFoundError as e:
            logger.error("File not found", extra={'path': request.path}, exc_info=True)
            return JsonResponse({"exception": str(e)}, status=status.HTTP_404_NOT_FOUND)

        except NotAcceptable as e:
            logger.error("Not acceptable", extra={'path': request.path}, exc_info=True)
            return JsonResponse({"exception": e.detail}, status=status.HTTP_409_CONFLICT)

        except PermissionDenied as e:
            logger.error("Permission denied", extra={'path': request.path, 'user_id': str(request.user.user_id) if hasattr(request.user, 'user_id') else None}, exc_info=True)
            return JsonResponse({"exception": e.detail}, status=status.HTTP_403_FORBIDDEN)

        except User.DoesNotExist as e:
            logger.error("User does not exist", extra={'path': request.path}, exc_info=True)
            return JsonResponse({"exception": "No user found."}, status=status.HTTP_404_NOT_FOUND)

        except requests.RequestException as e:
            logger.error("Request exception", extra={'path': request.path}, exc_info=True)
            return JsonResponse({'exception': str(e)}, status=status.HTTP_408_REQUEST_TIMEOUT)

        except NotFound as e:
            logger.error("Not found", extra={'path': request.path}, exc_info=True)
            return JsonResponse({"exception": e.detail}, status=status.HTTP_404_NOT_FOUND)

        except ValueError as e:
            logger.error("Value error", extra={'path': request.path}, exc_info=True)
            return JsonResponse({"exception": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        except TypeError as e:
            logger.error("Type error", extra={'path': request.path}, exc_info=True)
            return JsonResponse({'exception': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        except django.core.exceptions.ValidationError as e:
            logger.error("Django validation error", extra={'path': request.path}, exc_info=True)
            return JsonResponse({"exception": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        except ValidationError as e:
            logger.error("DRF validation error", extra={'path': request.path}, exc_info=True)
            return JsonResponse({"exception": "validation errors."}, status=status.HTTP_400_BAD_REQUEST)

        except django.core.exceptions.ObjectDoesNotExist as e:
            logger.error("Object does not exist", extra={'path': request.path}, exc_info=True)
            return JsonResponse({'exception': str(e)}, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            logger.error("Unhandled exception", extra={'path': request.path}, exc_info=True)
            return JsonResponse({'exception': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
