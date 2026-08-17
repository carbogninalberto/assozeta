from rest_framework import serializers

from django.utils.html import escape
from application.utils.api_utils import check_email
from .models import Message, CommunicationConfiguration, MessageTransaction, AutomationWorkflow


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
