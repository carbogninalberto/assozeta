from rest_framework import serializers

from django.utils.html import escape
from application.utils.api_utils import check_email
from .models import Message, CommunicationConfiguration, MessageTransaction, AutomationWorkflow, StaffBoardMessage


class CommunicationConfigurationSerializer(serializers.ModelSerializer):
    class Meta:
        model = CommunicationConfiguration
        fields = (
            'email_smtp_host',
            'email_smtp_port',
            'email_smtp_user',
            'email_sender_name',
            'email_encryption',
            'daily_email_limit',
            'daily_email_balance',
        )


# create the EmailSerializer for sending emails
class EmailSerializer(serializers.Serializer):
    # email can be a list of emails in the format of email,email,email etc
    email = serializers.CharField()
    subject = serializers.CharField(max_length=100)
    message = serializers.CharField(max_length=10000)

    def is_valid(self, *, raise_exception=False):
        if not self.initial_data.get('email'):
            raise serializers.ValidationError('Email is required')
        if not self.initial_data.get('subject'):
            raise serializers.ValidationError('Subject is required')
        if not self.initial_data.get('message'):
            raise serializers.ValidationError('Message is required')

        # check if email is a list of emails and validate each email
        emails = self.initial_data.get('email').split(',')
        for email in emails:
            if not check_email(email):
                raise serializers.ValidationError('Email is invalid')
        return super().is_valid(raise_exception=raise_exception)


# create the PostSerializer for posting communications
class PostSerializer(serializers.Serializer):

    message = serializers.CharField(max_length=10000)

    def is_valid(self, *, raise_exception=False):
        if not self.initial_data.get('message'):
            raise serializers.ValidationError('Message is required')
        return super().is_valid(raise_exception=raise_exception)


# create the CommunicationConfigurationSerializer for patching the SMTP settings
class CommunicationConfigurationPatchSerializer(serializers.ModelSerializer):
    class Meta:
        model = CommunicationConfiguration
        fields = (
            'email_smtp_host',
            'email_smtp_port',
            'email_smtp_user',
            'email_smtp_password',
            'email_sender_name',
            'email_encryption',
        )


class MessageSerializer(serializers.ModelSerializer):
    message = serializers.SerializerMethodField(read_only=True)
    subject = serializers.SerializerMethodField(read_only=True)

    def get_message(self, obj):
        return escape(obj.message)

    def get_subject(self, obj):
        return escape(obj.subject)
    
    
    class Meta:
        model = Message
        fields = '__all__'


class MessageTransactionSerializer(serializers.ModelSerializer):

    class Meta:
        model = MessageTransaction
        fields = '__all__'


class AutomationWorkflowSerializer(serializers.ModelSerializer):

    def validate_automation_tree(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError('Il flusso dell’automazione non è valido.')

        unsupported_message_nodes = [
            node for node in value
            if isinstance(node, dict)
            and node.get('id') == 'message'
            and node.get('value') != 'email'
        ]
        if unsupported_message_nodes:
            raise serializers.ValidationError('Le automazioni possono inviare solo email.')

        return value

    class Meta:
        model = AutomationWorkflow
        fields = '__all__'


class StaffBoardMessageSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField(read_only=True)

    def get_author_name(self, obj):
        if obj.author is None:
            return 'Utente eliminato'
        full_name = f"{obj.author.first_name} {obj.author.last_name}".strip()
        if full_name:
            return full_name
        # association admin accounts often have generated usernames (hash-like);
        # fall back to the association denomination for a readable name
        if obj.sport_association.user_id == obj.author_id:
            return obj.sport_association.denomination
        return obj.author.username

    class Meta:
        model = StaffBoardMessage
        fields = (
            'staff_board_message_id',
            'sport_association',
            'author',
            'author_name',
            'content',
            'pinned',
            'created_at',
            'updated_at',
        )


class StaffBoardMessageInputSerializer(serializers.Serializer):
    content = serializers.CharField(max_length=10000, allow_blank=False)
    pinned = serializers.BooleanField(required=False)

    def validate_content(self, value):
        if not isinstance(self.initial_data.get('content'), str):
            raise serializers.ValidationError('Il messaggio deve essere un testo.')
        return value
