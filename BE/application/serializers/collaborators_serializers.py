from application.models import User
from rest_framework import serializers

from application.models.user_models import CollaborationInvites


class CollaboratorSerializer(serializers.ModelSerializer):

    class Meta:
        model = User
        fields = (
            'user_id',
            'first_name',
            'last_name',
            'email',
            'phone',
            'role',
            'connected_user',
            'collaborator_role',
            'collaborator_permissions'
        )


class CollaborationInviteSerializer(serializers.ModelSerializer):

    class Meta:
        model = CollaborationInvites
        fields = (
            'collaboration_invite_id',
            'email',
            'expiration_date',
            'accepted',
            'token',
            'collaborator_role',
            'collaborator_permissions'
        )
