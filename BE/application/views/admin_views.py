from django.db.models import Q
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError

from application.models import User
from application.impersonation import active_admin, begin, end, resolve_target, SESSION_TTL, actor_association, eligible_users


class IsAdministrator(BasePermission):
    def has_permission(self, request, view):
        return active_admin(getattr(request, 'authenticated_user', request.user))


class CanManageImpersonation(BasePermission):
    def has_permission(self, request, view):
        actor = getattr(request, 'authenticated_user', request.user)
        if request.path.strip('/').removeprefix('api/').startswith('administration/'):
            return active_admin(actor)
        if request.method == 'DELETE':
            return bool(actor and actor.is_authenticated and actor.is_active and not actor.deleted)
        actor_association(actor)
        return True


def describe(user, association=None):
    owner = user.connected_user if user.is_collaborator else user
    association = association or getattr(owner, 'sportassociation', None)
    return {'user_id': str(user.pk), 'username': user.username, 'email': user.email,
            'first_name': user.first_name, 'last_name': user.last_name, 'last_login': user.last_login,
            'role': {1: 'association', 2: 'athlete', 3: 'collaborator'}[user.role],
            'association': {'id': str(association.pk), 'name': association.denomination} if association else None}


@api_view(['GET'])
@permission_classes([CanManageImpersonation])
def impersonation_users(request):
    try:
        page = int(request.query_params.get('pagination[page]', request.query_params.get('page', 1)))
        per_page = int(request.query_params.get('pagination[perpage]', 25))
        if page < 1 or per_page < 1 or per_page > 100:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValidationError('Pagina non valida.')
    actor = getattr(request, 'authenticated_user', request.user)
    association = actor_association(actor)
    users = eligible_users(actor)
    query = request.query_params.get('query[generalSearch]', request.query_params.get('q', '')).strip()[:150]
    if query:
        users = users.filter(Q(username__icontains=query) | Q(email__icontains=query) | Q(first_name__icontains=query) |
                             Q(last_name__icontains=query) | Q(sportassociation__denomination__icontains=query) |
                             Q(connected_user__sportassociation__denomination__icontains=query))
    roles = {'association': User.ASSOCIATION, 'athlete': User.ATHLETE, 'collaborator': User.COLLABORATOR}
    role = request.query_params.get('query[role]', request.query_params.get('role'))
    if role:
        if role not in roles:
            raise ValidationError('Ruolo non valido.')
        users = users.filter(role=roles[role])
    ordering = {'username': 'username', 'email': 'email', 'role': 'role',
                'first_name': 'first_name', 'last_login': 'last_login',
                'association.name': 'sportassociation__denomination'}
    field = request.query_params.get('sort[field]', 'username')
    direction = request.query_params.get('sort[sort]', 'asc')
    if field not in ordering or direction not in ('asc', 'desc'):
        raise ValidationError('Ordinamento non valido.')
    count = users.count()
    order = ('-' if direction == 'desc' else '') + ordering[field]
    rows = list(users.order_by(order, 'pk')[(page - 1) * per_page:page * per_page])
    return Response({'users': [describe(user, association) for user in rows],
                     'next_page': page + 1 if page * per_page < count else None,
                     'meta': {'page': page, 'perpage': per_page, 'total': count,
                              'pages': max(1, (count + per_page - 1) // per_page)}})



@api_view(['GET', 'POST', 'DELETE'])
@permission_classes([CanManageImpersonation])
def impersonation_session(request):
    actor = getattr(request, 'authenticated_user', request.user)
    previous = request.headers.get('X-Impersonation-Id')
    if request.method == 'DELETE':
        if previous:
            end(actor, previous)
        return Response(status=204)
    association = actor_association(actor)
    if request.method == 'GET':
        return Response({'target': describe(resolve_target(actor, previous), association) if previous else None})
    if not isinstance(request.data, dict) or set(request.data) != {'target_user_id'}:
        raise ValidationError('Seleziona un utente da impersonificare.')
    identifier, target = begin(actor, request.data['target_user_id'])
    if previous:
        try:
            end(actor, previous)
        except Exception:
            end(actor, identifier)
            raise
    return Response({'session_id': identifier, 'target': describe(target, association), 'expires_in': SESSION_TTL,
                     'actor_role': 'association' if association else 'administrator',
                     'association': {'id': str(association.pk), 'name': association.denomination} if association else None}, status=201)
