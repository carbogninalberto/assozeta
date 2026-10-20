from rest_framework.permissions import BasePermission
from application.models import User


class IsAssociationUser(BasePermission):
    """Association business access; role restrictions are independent of billing.

    IsAuthenticated resolves collaborator/impersonation scope and enforces the
    existing collaborator permission registry before this permission is checked.
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and
                    request.user.role in (User.ASSOCIATION, User.COLLABORATOR))


class IsAthleteUser(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == User.ATHLETE)
