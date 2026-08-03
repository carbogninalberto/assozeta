import secrets

from django.utils import timezone

from django.template.loader import render_to_string
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from core.middleware import IsAuthenticated
from datetime import timedelta
from core.tasks import send_mail_async

from application.models import User
from application.models.user_models import CollaborationInvites
from application.permissions import IsProPlanAssociation, IsTeamsPlanAssociation
from application.serializers.auth_serializers import UserSerializer
from application.serializers.collaborators_serializers import CollaboratorSerializer, CollaborationInviteSerializer
from application.utils.api_utils import is_valid_uuid, check_email
import logging

from core import settings

logger = logging.getLogger(__name__)



@api_view(['GET'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def collaborators_list(request):
    if not request.user.is_sport_association(raise_exception=False):
        raise PermissionDenied(detail='User is not a sport association')
    user_id = request.GET.get('user_id', None)

    if user_id is None:
        collaborators = User.objects.filter(connected_user=request.user)
        serializer = CollaboratorSerializer(collaborators, many=True)

        collaborators_invites = CollaborationInvites.objects.filter(user=request.user).iterator(chunk_size=100)
        serializer_invites = CollaborationInviteSerializer(collaborators_invites, many=True)

        data = []
        for collaborator in serializer.data:
            collaborator['is_invite'] = False
            data.append(collaborator)
        for collaborator_invite in serializer_invites.data:
            collaborator_invite['is_invite'] = True
            data.append(collaborator_invite)

        return Response(data, status.HTTP_200_OK)
    else:
        is_valid_uuid(user_id)
        collaborator = User.objects.filter(
            connected_user=request.user,
            user_id=user_id
        ).first()
        serializer = CollaboratorSerializer(collaborator)
        return Response(serializer.data, status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def collaborators_add(request):
    if not request.user.is_sport_association(raise_exception=False):
        raise PermissionDenied(detail='User is not a sport association')

    data = request.data

    # validate email
    if 'email' not in data:
        raise ValidationError(detail='Email is required')
    email = check_email(data['email'])

    collaborator_role = int(data.get('collaborator_role', User.COLLABORATOR))
    logger.debug("Checking collaborator role", extra={'user_id': str(request.user.user_id), 'role': collaborator_role})
    if collaborator_role not in [User.FULL, User.ONLY_ACCOUNTING, User.CUSTOM_COLLABORATOR_ROLE]:
        # set to default collaborator role
        collaborator_role = User.FULL

    # get permissions
    collaborator_permissions = data.get('collaborator_permissions', None)
    if collaborator_permissions is not None:
        if collaborator_role == User.CUSTOM_COLLABORATOR_ROLE:
            collaborator_permissions = collaborator_permissions

    logger.info("Creating collaborator invitation", extra={'user_id': str(request.user.user_id), 'email': email, 'role': collaborator_role})

    token = secrets.token_hex(16)
    collaboration_invite = CollaborationInvites.objects.create(
        user=request.user,
        email=email,
        expiration_date=timezone.now() + timedelta(days=90),
        token=token,
        collaborator_role=collaborator_role,
        collaborator_permissions=collaborator_permissions
    )

    collaboration_invite.save()

    logger.info("Sending collaborator invitation email", extra={'user_id': str(request.user.user_id), 'email': email, 'collaboration_invite_id': str(collaboration_invite.collaboration_invite_id)})
    email_data = {
        'email': email,
        'token': token,
        'sport_association': request.user.sport_association,
        'app_host': settings.APP_URL,
        'settings': {
            'WHITELABEL_NAME': settings.WHITELABEL_NAME,
            'IS_WHITELABEL': settings.IS_WHITELABEL
        }
    }
    recipient_list = [email]
    message = render_to_string('email/account/email_welcome_collaborator_message.html', email_data)
    subject = f"{settings.WHITELABEL_NAME} | Invito a collaborare"

    send_mail_async.apply_async(
        kwargs={
            "subject": subject,
            "message": message,
            "from_email": settings.DEFAULT_FROM_EMAIL,
            "recipient_list": recipient_list,
            "html_message": message,
            "fail_silently": False,
            "sport_association_id": request.user.sport_association.sport_association_id
        }
    )

    logger.info("Collaborator invited successfully", extra={'user_id': str(request.user.user_id), 'email': email})
    return Response({'msg': 'User invited'}, status=status.HTTP_200_OK)


@api_view(['POST'])
def collaborators_accept(request, token):
    logger.info("Collaborator accepting invitation", extra={'token': token[:8]})

    collaboration_invite = CollaborationInvites.objects.filter(token=token).first()
    if collaboration_invite is None:
        logger.warning("Collaboration invite not found", extra={'token': token[:8]})
        raise ValidationError(detail='Collaboration invite not found')

    logger.debug("Checking invitation expiration", extra={'token': token[:8], 'expiration_date': str(collaboration_invite.expiration_date)})
    if collaboration_invite.expiration_date < timezone.now():
        logger.warning("Collaboration invite expired", extra={'token': token[:8], 'expiration_date': str(collaboration_invite.expiration_date)})
        collaboration_invite.delete()
        raise ValidationError(detail='Collaboration invite expired')

    user_data = UserSerializer(data=request.data)
    user_data.initial_data['role'] = User.COLLABORATOR
    if user_data.is_valid(raise_exception=True):
        user = user_data.save()
        user.connected_user = collaboration_invite.user
        user.collaborator_role = collaboration_invite.collaborator_role
        user.collaborator_permissions = collaboration_invite.collaborator_permissions
        user.save()

        collaboration_invite.accepted = True
        collaboration_invite.save()

        logger.info("Collaborator accepted successfully", extra={'user_id': str(user.user_id), 'collaboration_invite_id': str(collaboration_invite.collaboration_invite_id)})
        return Response({'msg': 'User added'}, status=status.HTTP_200_OK)

    return Response(user_data.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def collaborators_update(request, uid):
    if not request.user.is_sport_association(raise_exception=False):
        raise PermissionDenied(detail='User is not a sport association')

    data = request.data

    is_valid_uuid(uid)
    collaborator = User.objects.filter(
        connected_user=request.user,
        user_id=uid
    ).first()

    if collaborator is None:
        logger.warning("Collaborator not found for update", extra={'user_id': str(request.user.user_id), 'collaborator_id': uid})
        raise ValidationError(detail='Collaborator not found')

    collaborator_role = int(data.get('collaborator_role', User.COLLABORATOR))
    logger.debug("Updating collaborator role", extra={'collaborator_id': uid, 'old_role': collaborator.collaborator_role, 'new_role': collaborator_role})
    if collaborator_role not in [User.FULL, User.ONLY_ACCOUNTING, User.CUSTOM_COLLABORATOR_ROLE]:
        # set to default collaborator role
        collaborator_role = User.FULL

    # get permissions
    collaborator_permissions = data.get('collaborator_permissions', None)
    if collaborator_permissions is not None:
        if collaborator_role == User.CUSTOM_COLLABORATOR_ROLE:
            collaborator_permissions = collaborator_permissions
        else:
            collaborator_permissions = None

    logger.info("Updating collaborator", extra={'user_id': str(request.user.user_id), 'collaborator_id': uid, 'role': collaborator_role})

    # update collaborator
    collaborator.collaborator_role = collaborator_role
    collaborator.collaborator_permissions = collaborator_permissions
    collaborator.save()

    # Note: With JWT tokens (4-hour expiry), we don't need to manually invalidate tokens
    # The collaborator's permissions will be updated on their next login/token refresh
    logger.info("Collaborator updated successfully", extra={'collaborator_id': uid})
    return Response({'msg': 'collaborator updated.'}, status.HTTP_200_OK)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def collaborators_delete(request, uid):
    logger.info("Deleting collaborator", extra={'user_id': str(request.user.user_id), 'collaborator_id': uid})
    if not request.user.is_sport_association(raise_exception=False):
        raise PermissionDenied(detail='User is not a sport association')

    is_valid_uuid(uid)

    collaborator = User.objects.filter(
        connected_user=request.user,
        user_id=uid
    ).first()

    logger.debug("Checking collaborator type", extra={'collaborator_id': uid, 'is_user': collaborator is not None})
    if collaborator is None:
        collaborator = CollaborationInvites.objects.filter(
            user=request.user,
            collaboration_invite_id=uid
        ).first()
        if collaborator is None:
            logger.warning("Collaborator not found for deletion", extra={'user_id': str(request.user.user_id), 'collaborator_id': uid})
            raise ValidationError(detail='Collaborator not found')
        if collaborator.accepted:
            logger.debug("Deleting accepted invite user", extra={'collaborator_id': uid, 'email': collaborator.email})
            user_to_delete = User.objects.filter(email=collaborator.email).first()
            if user_to_delete is not None:
                user_to_delete.delete()

    logger.info("Collaborator deleted successfully", extra={'collaborator_id': uid})
    collaborator.delete()
    return Response({"msg": "collaborator deleted."}, status=status.HTTP_200_OK)
