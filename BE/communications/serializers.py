from rest_framework import serializers

from django.utils.html import escape
from application.utils.api_utils import check_email
from .models import Message, CommunicationConfiguration, SmsCreditPayment, MessageTransaction, AutomationWorkflow


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
            'daily_email_limit',
            'daily_email_balance',
            'sms_balance',
        )


class SmsCreditPaymentSerializer(serializers.ModelSerializer):

    class Meta:
        model = SmsCreditPayment
        fields = (
            'sms_credit_payment_id',
            'amount',
            'payment_date',
            'paid'
        )


# create sms serializer for sending sms
class SmsSerializer(serializers.Serializer):
    phone_number = serializers.CharField()
    message = serializers.CharField(max_length=160)

    def validate_phone_number(self, value):
        phones = value.split(',')
        fixed_phones = []
        for phone in phones:
            # check if is a valid phone number, if it does not have italian prefix, add it
            if not phone.startswith('+39') and not phone.startswith('0039') and len(phone) == 10:
                phone = '+39' + phone
            if not phone.startswith('+'):
                raise serializers.ValidationError('Phone number must start with +')
            fixed_phones.append(phone)
        return ','.join(fixed_phones)

    def validate_message(self, value):
        if len(value) > 160:
            raise serializers.ValidationError('Message must be less than 160 characters')
        return value

    def is_valid(self, *, raise_exception=False):
        if not self.initial_data.get('phone_number'):
            raise serializers.ValidationError('Phone number is required')
        if not self.initial_data.get('message'):
            raise serializers.ValidationError('Message is required')
        return super().is_valid(raise_exception=raise_exception)


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

    class Meta:
        model = AutomationWorkflow
        fields = '__all__'