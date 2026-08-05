"""
Centralized permission registry for collaborator access control.

This module maps URL patterns to required permissions.
Permissions are enforced in the middleware for collaborators only.

Pattern matching:
- '*' matches any single path segment (UUID, ID, etc.)
- Exact string match for other segments
- HTTP methods can be specified as tuple: (method, path) -> permission

Example:
    'payment/*/update' matches 'payment/123e4567-e89b-12d3-a456-426614174000/update'
    'payment/list' matches exactly 'payment/list'
    ('POST', 'payment/add') matches POST to payment/add

Excluded from permission checks:
- OAuth endpoints (authentication)
- Stripe webhooks (external system callbacks)
- Health checks (monitoring)
- Superuser-only endpoints (already restricted)
"""

from rest_framework.exceptions import PermissionDenied

# URL pattern -> required permission
# Can be either:
#   'path/pattern': 'permission'  (any HTTP method)
#   ('METHOD', 'path/pattern'): 'permission'  (specific HTTP method)
PERMISSIONS_REGISTRY = {
    # ============================================
    # Dashboard & Profile
    # ============================================
    'statistic/dashboard': 'association.dashboard.read',
    'statistic/dashboard/layout': 'association.dashboard.read',
    'statistic/athlete-dashboard': 'association.dashboard.read',
    'statistic/report': 'association.report.read',

    # ============================================
    # Personas (Associates)
    # ============================================
    'personas/list': 'association.personas.read',
    'personas/all': 'association.personas.read',
    'personas/all-tutors': 'association.personas.read',
    'personas/add': 'association.personas.create',
    'personas/*/update': 'association.personas.update',
    'personas/*/delete': 'association.personas.delete',
    'personas/*/info': 'association.personas.read',
    'personas/*/recover': 'association.personas.update',
    'personas/bulk-delete': 'association.personas.delete',
    'personas-subscriptions/*/list': 'association.personas.read',

    # ============================================
    # Subscriptions (Members)
    # ============================================
    ('GET', 'subscription/list'): 'association.members.read',
    ('POST', 'subscription/list'): 'association.members.read',  # Filtering/search
    'subscription/list/all': 'association.members.read',
    'subscription/list/archived': 'association.members.archive.read',
    'subscription/list/export': 'association.members.read',
    'subscription/add': 'association.members.create',
    'subscription/renew': 'association.members.create',
    'subscription/*/info': 'association.members.read',
    'subscription/*/payments': 'association.members.read',
    ('GET', 'subscription/*/card'): 'association.members.read',
    ('POST', 'subscription/*/card'): 'association.members.update',  # Generating card
    'subscription/*/attendance': 'association.members.read',
    'subscription/*/calendar': 'association.members.read',
    'subscription/*/update': 'association.members.update',
    'subscription/*/edit': 'association.members.update',
    'subscription/*/approve': 'association.members.update',
    'subscription/*/reject': 'association.members.update',
    'subscription/*/delete': 'association.members.delete',
    'subscription/*/archive': 'association.members.archive.update',
    'subscription/*/transfer': 'association.members.update',
    'subscription/*/upload-document': 'association.members.update',
    'subscription/*/delete-document/*': 'association.members.update',
    'subscription/*/medical-certificate/upload': 'association.members.update',
    'subscription/*/medical-certificate/set-certificate-expiration': 'association.members.update',
    'subscription/*/medical-certificate/send-email-reminder': 'association.members.update',
    'subscription/*/medical-certificate/edit': 'association.members.update',
    'subscription/*/medical-appointments/list': 'association.members.read',
    'subscription/*/medical-appointments/add': 'association.members.update',
    'subscription/*/medical-appointments/*/delete': 'association.members.update',
    'subscription/import/upload': 'association.members.create',
    'subscription/import/status': 'association.members.read',
    'subscription/associates-draft/add': 'association.members.create',
    'subscription/associates-draft/list': 'association.members.read',
    'subscription/associates-draft/list/approve': 'association.members.create',
    'subscription/associates-draft/*/edit': 'association.members.update',
    'subscription/associates-draft/*/delete': 'association.members.delete',
    'subscription/associates-draft/bulk-delete': 'association.members.delete',
    'subscription/sign': 'association.members.update',
    'subscription/calculate-tax-code': 'association.members.read',

    # Subscription Memberships
    'subscription/memberships/list': 'association.members.read',
    'subscription/memberships/add': 'association.members.create',
    'subscription/memberships/*/update': 'association.members.update',
    'subscription/memberships/*/delete': 'association.members.delete',

    # Subscription Tags
    'subscription/tags/list': 'association.members.read',
    'subscription/tags/add': 'association.members.update',
    'subscription/tags/*/update': 'association.members.update',
    'subscription/tags/*/delete': 'association.members.update',
    'subscription/tags/*/assign/*': 'association.members.update',
    'subscription/tags/*/unassign/*': 'association.members.update',

    # ============================================
    # Courses
    # ============================================
    'course/list': 'association.courses.read',
    'course/add': 'association.courses.create',
    'course/*/update': 'association.courses.update',
    'course/*/enable': 'association.courses.update',
    'course/*/disable': 'association.courses.update',
    'course/*/delete': 'association.courses.delete',
    'course/*/pin': 'association.courses.update',
    ('GET', 'course/*/overview'): 'association.courses.read',
    ('POST', 'course/*/overview'): 'association.courses.update',  # Bulk operations
    'course/*/overview/*/delete': 'association.courses.update',
    'course/*/overview/*/add': 'association.courses.update',
    'course/*/overview/*/update': 'association.courses.update',
    'course/*/calendar': 'association.courses.read',
    'course/*/calendar/update': 'association.courses.update',
    'course/*/attendees': 'association.courses.attendance.read',
    'course/*/attendees/*/update': 'association.courses.attendance.update',

    # Course Subscriptions
    'course-subscriptions/list': 'association.courses.read',
    'course-subscriptions/add': 'association.courses.create',
    'course-subscriptions/*/update': 'association.courses.update',
    'course-subscriptions/*/delete': 'association.courses.delete',
    'course-subscriptions/bulk-delete': 'association.courses.delete',

    # Course Installments
    'course-installment/*/make-payment': 'association.courses.update',

    # Course Tags
    'course/tags/list': 'association.courses.read',
    'course/tags/add': 'association.courses.update',
    'course/tags/*/update': 'association.courses.update',
    'course/tags/*/delete': 'association.courses.update',
    'course/tags/*/assign/*': 'association.courses.update',
    'course/tags/*/unassign/*': 'association.courses.update',

    # Course Locations
    'course/locations/list': 'association.courses.read',
    'course/locations/add': 'association.courses.create',
    'course/locations/*/update': 'association.courses.update',
    'course/locations/*/delete': 'association.courses.delete',

    # ============================================
    # Camps and Retreats
    # ============================================
    'camps-and-retreats/list': 'association.campsandretreats.read',
    'camps-and-retreats/add': 'association.campsandretreats.create',
    'camps-and-retreats/*/update': 'association.campsandretreats.update',
    'camps-and-retreats/*/delete': 'association.campsandretreats.delete',
    'camps-and-retreats/*/info': 'association.campsandretreats.read',
    'camps-and-retreats/*/subscriptions/list': 'association.campsandretreats.read',
    'camps-and-retreats/*/subscriptions/add': 'association.campsandretreats.create',
    'camps-and-retreats/*/subscriptions/update': 'association.campsandretreats.update',
    'camps-and-retreats/*/subscriptions/delete': 'association.campsandretreats.delete',
    'camps-and-retreats/periods/add': 'association.campsandretreats.create',
    'camps-and-retreats/periods/*/update': 'association.campsandretreats.update',
    'camps-and-retreats/periods/*/delete': 'association.campsandretreats.delete',
    'camps-and-retreats/periods/*/info': 'association.campsandretreats.read',
    'camps-and-retreats/periods/services/add': 'association.campsandretreats.create',
    'camps-and-retreats/periods/services/*/update': 'association.campsandretreats.update',
    'camps-and-retreats/periods/services/*/delete': 'association.campsandretreats.delete',

    # ============================================
    # Carnet (Punch Cards)
    # ============================================
    'carnet/list': 'association.carnet.read',
    'carnet/add': 'association.carnet.create',
    'carnet/*/info': 'association.carnet.read',
    'carnet/*/update': 'association.carnet.update',
    'carnet/*/delete': 'association.carnet.delete',
    'carnet/*/assign/*': 'association.carnet.update',
    'carnet/*/replace/*': 'association.carnet.update',
    'carnet/*/unassign/*': 'association.carnet.update',
    'carnet-subscription/list': 'association.carnet.read',
    'carnet-subscription/*/enable': 'association.carnet.update',
    'carnet-subscription/*/disable': 'association.carnet.update',
    'carnet-subscription/*/update': 'association.carnet.update',
    'carnet-subscription/*/topup': 'association.carnet.update',
    'carnet-subscription/*/delete/*': 'association.carnet.delete',

    # ============================================
    # Instructors
    # ============================================
    'instructor/list': 'association.instructor.read',
    'instructor/report': 'association.instructor.read',
    'instructor/add': 'association.instructor.create',
    'instructor/*/info': 'association.instructor.read',
    'instructor/*/update': 'association.instructor.update',
    'instructor/*/delete': 'association.instructor.delete',
    'instructor/*/hours/list': 'association.instructor.hours.read',
    'instructor/*/hours/calculate': 'association.instructor.hours.read',
    'instructor/*/hours/add': 'association.instructor.hours.create',
    'instructor/*/hours/add/compensation': 'association.instructor.hours.create',
    'instructor/*/hours/*/update': 'association.instructor.hours.update',
    'instructor/*/hours/*/delete': 'association.instructor.hours.delete',

    # ============================================
    # Attendance
    # ============================================
    'attendance/*/mark': 'association.courses.attendance.update',
    'attendance-day/*/delete': 'association.courses.attendance.update',
    'attendance-day/*/mark-absent': 'association.courses.attendance.update',

    # ============================================
    # Calendar & Events
    # ============================================
    ('GET', 'calendar/events'): 'association.calendar.read',
    ('POST', 'calendar/events'): 'association.calendar.read',  # Creating events
    ('POST', 'calendar/events/update'): 'association.calendar.read',
    ('DELETE', 'calendar/events/update'): 'association.calendar.read',
    'calendar/events/export': 'association.calendar.read',

    # Course calendar
    ('POST', 'course/*/calendar/update'): 'association.courses.update',
    ('DELETE', 'course/*/calendar/update'): 'association.courses.update',

    # ============================================
    # Modules (Forms/Documents)
    # ============================================
    'modules/list': 'association.modules.read',
    'modules/add': 'association.modules.create',
    'modules/check-link': 'association.modules.read',
    'modules/*/update': 'association.modules.update',
    'modules/*/delete': 'association.modules.delete',
    'modules/*/info': 'association.modules.read',
    'modules/*/overview': 'association.modules.read',
    'modules/*/response/add': 'association.modules.create',
    'modules/*/export': 'association.modules.read',
    'modules/response/*/approve': 'association.modules.update',
    'modules/response/*/delete': 'association.modules.delete',
    ('POST', 'modules/response/*/add-attachment'): 'association.modules.update',
    ('DELETE', 'modules/response/*/add-attachment'): 'association.modules.update',

    # ============================================
    # Templates (Module Templates)
    # ============================================
    'templates/list': 'association.templates.read',
    'templates/add': 'association.templates.create',
    'templates/*/update': 'association.templates.update',
    'templates/*/delete': 'association.templates.delete',
    'templates/bulk-delete': 'association.templates.delete',

    # ============================================
    # Archive (Document Archive)
    # ============================================
    'folders/list': 'association.archive.read',
    'folders/add': 'association.archive.update',
    'folders/*/update': 'association.archive.update',
    'folders/*/delete': 'association.archive.update',
    'folders/*/move': 'association.archive.update',

    'documents/list': 'association.archive.read',
    'documents/add': 'association.archive.update',
    'documents/*/update': 'association.archive.update',
    'documents/*/delete': 'association.archive.update',
    'documents/*/move': 'association.archive.update',
    'documents/bulk-delete': 'association.archive.update',

    # ============================================
    # Payments
    # ============================================
    'payment/list': 'bookeeping.payments.read',
    'payment/stats': 'bookeeping.payments.read',
    'payment/add': 'bookeeping.payments.create',
    'payment/sign': 'bookeeping.payments.update',
    'payment/simulation/partial-quotes': 'bookeeping.payments.read',
    'payment/simulation/partial-quotes-apply': 'bookeeping.payments.update',
    'payment/list/export': 'bookeeping.payments.read',
    'payment/*/update': 'bookeeping.payments.update',
    'payment/*/request': 'bookeeping.payments.update',
    'payment/*/info': 'bookeeping.payments.read',
    'payment/*/approve': 'bookeeping.payments.update',
    'payment/*/cancel': 'bookeeping.payments.update',
    'payment/*/delete': 'bookeeping.payments.delete',
    'payment/*/archive': 'bookeeping.payments.archive.update',
    'payment/*/generate-invoice': 'bookeeping.documents.invoices.create',
    'payment-bulk/archive': 'bookeeping.payments.archive.update',
    'payment-bulk/delete': 'bookeeping.payments.delete',

    # Payment Categories
    'payment/category/list': 'bookeeping.payments.read',
    'payment/category/add': 'bookeeping.payments.update',
    'payment/category/*/update': 'bookeeping.payments.update',
    'payment/category/*/delete': 'bookeeping.payments.update',

    # ============================================
    # Invoices
    # ============================================
    'invoice/list': 'bookeeping.documents.invoices.read',
    'invoice/list/archived': 'bookeeping.documents.invoices.archive.read',
    'invoice/list/export': 'bookeeping.documents.invoices.read',
    'invoice/*/update': 'bookeeping.documents.invoices.update',
    'invoice/*/delete': 'bookeeping.documents.invoices.delete',
    'invoice/*/send': 'bookeeping.documents.invoices.update',
    'invoice-bulk/delete': 'bookeeping.documents.invoices.delete',
    'invoice-bulk/archive': 'bookeeping.documents.invoices.archive.update',

    # Supplier Invoices
    'invoice-suppliers/list': 'bookeeping.documents.supplierinvoices.read',
    'invoice-suppliers/stats': 'bookeeping.documents.supplierinvoices.read',
    'invoice-suppliers/add': 'bookeeping.documents.supplierinvoices.create',
    'invoice-suppliers/*/update': 'bookeeping.documents.supplierinvoices.update',
    'invoice-suppliers/*/delete': 'bookeeping.documents.supplierinvoices.delete',

    # Customer Invoices
    'invoice-customers/list': 'bookeeping.documents.clientinvoices.read',
    'invoice-customers/stats': 'bookeeping.documents.clientinvoices.read',
    'invoice-customers/add': 'bookeeping.documents.clientinvoices.create',
    'invoice-customers/*/update': 'bookeeping.documents.clientinvoices.update',
    'invoice-customers/*/delete': 'bookeeping.documents.clientinvoices.delete',

    # ============================================
    # Suppliers
    # ============================================
    'supplier/list': 'bookeeping.management.suppliers.read',
    'supplier/*/info': 'bookeeping.management.suppliers.read',
    'supplier/add': 'bookeeping.management.suppliers.create',
    'supplier/*/update': 'bookeeping.management.suppliers.update',
    'supplier/*/delete': 'bookeeping.management.suppliers.delete',
    'supplier/bulk-delete': 'bookeeping.management.suppliers.delete',

    # ============================================
    # Balance Sheet
    # ============================================
    ('GET', 'balance-sheet'): 'bookeeping.management.balancesheet.read',
    ('POST', 'balance-sheet'): 'bookeeping.management.balancesheet.update',
    ('DELETE', 'balance-sheet'): 'bookeeping.management.balancesheet.update',
    'balance-sheet/archived': 'bookeeping.management.balancesheet.read',
    'balance-sheet/accounts/list': 'bookeeping.management.accounts.read',
    'balance-sheet/accounts/add': 'bookeeping.management.accounts.create',
    'balance-sheet/accounts/*/update': 'bookeeping.management.accounts.update',
    'balance-sheet/accounts/*/delete': 'bookeeping.management.accounts.delete',
    'balance-sheet/accounts-transfer/list': 'bookeeping.management.accountstransfers.read',
    'balance-sheet/accounts-transfer/add': 'bookeeping.management.accountstransfers.create',
    'balance-sheet/accounts-transfer/*/update': 'bookeeping.management.accountstransfers.update',
    'balance-sheet/accounts-transfer/*/delete': 'bookeeping.management.accountstransfers.delete',

    # ============================================
    # Collaborators
    # ============================================
    'collaborators/list': 'other.users.collaborators.read',
    'collaborators/add': 'other.users.collaborators.create',
    'collaborators/*/update': 'other.users.collaborators.update',
    'collaborators/*/delete': 'other.users.collaborators.delete',

    # ============================================
    # Communication (Messages/Workflows)
    # ============================================
    # Note: These endpoints don't exist yet in urls.py but included for completeness
    # based on the permission structure from the frontend

    # ============================================
    # Profile & Settings
    # ============================================
    'profile/update': 'other.settings.update',
    ('GET', 'profile/associates'): 'other.users.connectedathletes.read',
    ('POST', 'profile/associates'): 'other.users.connectedathletes.read',  # Filtering
    'profile/associates/*/disable': 'other.users.connectedathletes.delete',
    'profile/associates/course/*': 'other.users.connectedathletes.read',
    'profile/associates/sport-association/*': 'other.users.connectedathletes.read',
    'profile/image/*': 'other.settings.update',
    'profile/update/password': 'other.settings.update',
    'profile/info': 'other.settings.read',
    ('GET', 'profile/settings'): 'other.settings.read',
    ('PATCH', 'profile/settings'): 'other.settings.update',
    'profile/settings/tables': 'other.settings.update',
    'profile/integrations': 'other.settings.read',
    'profile/update/subscription/template': 'other.settings.update',

    # Onboarding
    'onboarding/update': 'other.settings.update',

    # Testimonials
    'testimonials/add': 'other.settings.update',
    'testimonials/update': 'other.settings.update',

    # ============================================
    # Google Integration
    # ============================================
    'google/check': 'other.settings.read',
    'google/oauth2callback': 'other.settings.update',
    ('GET', 'google/calendar/config'): 'other.settings.read',
    ('DELETE', 'google/calendar/config'): 'other.settings.update',
    'google/calendar/list': 'association.calendar.read',
    'google/calendar/*/export': 'association.calendar.read',

    # ============================================
    # Printing/Documents
    # ============================================
    'printing/generate': 'association.members.read',  # Can read member data for documents

    # ============================================
    # Two-Factor Authentication
    # ============================================
    'two-fa/info': 'other.settings.read',
    'two-fa/setup': 'other.settings.update',
    'two-fa/update': 'other.settings.update',
}

# Endpoints that should NEVER require permission checks
# These are either public, external callbacks, or superuser-only
EXCLUDED_ENDPOINTS = [
    # Authentication (public)
    'oauth2/login',
    'oauth2/signup',
    'oauth2/refresh-token',
    'oauth2/reset',
    'oauth2/check/email',
    'oauth2/check/username',
    'oauth2/partial-signup',
    'oauth2/delete-account',

    # Stripe (external webhooks)
    'stripe/webhook',
    'stripe/on-boarding',
    'stripe/complete-on-boarding',
    'stripe/info',
    'stripe/pay/*',
    'stripe/multiple-pay',

    # Health & Monitoring
    'health',
    'check-inconsistencies',

    # Superuser-only endpoints
    'sport-associations/list',
    'sport-associations/*/admin-update',

    # Public endpoints for subscriptions (token-based)
    'subscription/generate-token-link',
    'subscription/validate-token-link-and-get-subscriptions',
    'subscription/get-associations-for-federation',

    # Search (social features)
    'search/all',
    'search/profile/*',

    # Billing (handled by plan-level permissions)
    'billing/active-plan',
    'billing/checkout',

    # Signature conversion (utility)
    'signature/convert/picture',

    # Data export (separate permission check in view)
    'export-all-data',
]


def check_collaborator_permission(request):
    """
    Check if the current collaborator has permission to access this endpoint.

    This function is called from middleware for every request.
    It only enforces permissions for users with role=COLLABORATOR.

    Args:
        request: Django request object with collaborator info set by middleware

    Raises:
        PermissionDenied: If collaborator lacks required permission

    Returns:
        None: If permission check passes or doesn't apply
    """
    # Skip if not a collaborator
    if not getattr(request, 'collaborator', False):
        return

    # Get the original collaborator user (before swap to connected_user)
    collaborator = request.original_user

    # FULL role (1) bypasses all permission checks
    if collaborator.collaborator_role == 1:  # User.FULL
        return

    # Normalize path (remove leading/trailing slashes, remove 'api/' prefix if present)
    path = request.path.strip('/')
    if path.startswith('api/'):  # pragma: no cover
        path = path[4:]

    # Check if endpoint is excluded from permission checks
    if _is_excluded_endpoint(path):
        return

    # Find required permission for this endpoint
    required_permission = _match_permission(path, request.method)

    if not required_permission:
        # No permission mapping found - deny by default for security
        # This catches new endpoints that haven't been added to registry
        raise PermissionDenied(
            f"Access denied: No permission mapping for endpoint '{path}'. "
            f"Contact administrator to grant access."
        )

    # Check if collaborator has the required permission
    user_permissions = set(collaborator.collaborator_permissions or [])

    if required_permission not in user_permissions:
        raise PermissionDenied(
            f"Missing permission: {required_permission}"
        )


def _is_excluded_endpoint(path):
    """Check if endpoint is excluded from permission checks."""
    for pattern in EXCLUDED_ENDPOINTS:
        if _match_pattern(path, pattern):
            return True
    return False


def _match_permission(path, method='GET'):
    """
    Find the required permission for a given URL path and HTTP method.

    Args:
        path: URL path without leading slash (e.g., 'payment/123/update')
        method: HTTP method (GET, POST, PATCH, DELETE, etc.)

    Returns:
        str: Required permission or None if no match found
    """
    # First, try to find method-specific match
    for pattern, permission in PERMISSIONS_REGISTRY.items():
        if isinstance(pattern, tuple):
            # Method-specific pattern: ('POST', 'path/pattern')
            pattern_method, pattern_path = pattern
            if pattern_method == method and _match_pattern(path, pattern_path):
                return permission

    # Then, try to find generic match (any method)
    for pattern, permission in PERMISSIONS_REGISTRY.items():
        if isinstance(pattern, str):
            # Generic pattern: 'path/pattern'
            if _match_pattern(path, pattern):
                return permission

    return None


def _match_pattern(path, pattern):
    """
    Match URL path against pattern with wildcard support.

    Wildcards:
        '*' matches any single path segment (UUID, ID, slug, etc.)

    Examples:
        'payment/*/update' matches 'payment/123e4567/update'
        'payment/list' matches exactly 'payment/list'

    Args:
        path: Actual URL path
        pattern: Pattern with optional wildcards

    Returns:
        bool: True if path matches pattern
    """
    # Reject empty paths
    if not path or not pattern:
        return False

    # Split and filter out empty segments (handles double slashes)
    path_parts = [p for p in path.split('/') if p]
    pattern_parts = [p for p in pattern.split('/') if p]

    # Must have same number of segments
    if len(path_parts) != len(pattern_parts):
        return False

    # Check each segment
    for path_part, pattern_part in zip(path_parts, pattern_parts):
        if pattern_part == '*':
            # Wildcard matches anything
            continue
        if path_part != pattern_part:
            # Exact match required
            return False

    return True
