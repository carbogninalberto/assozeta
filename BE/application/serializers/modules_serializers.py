from rest_framework import serializers
from application.models import Module, ModuleResponses
from application.serializers.auth_serializers import SportAssociationSerializer
from docmanager.models import Document
from application.serializers.primary_key import ReadOnlyPrimaryKeyMixin

class ModulesAddSerializer(ReadOnlyPrimaryKeyMixin, serializers.ModelSerializer):

    enabled = serializers.SerializerMethodField()

    def get_enabled(self, obj):
        return obj.enabled

    def create(self, validated_data):
        instance = Module.objects.create(**validated_data)
        return instance

    class Meta:
        model = Module
        fields = '__all__'

class ModulesSerializer(ReadOnlyPrimaryKeyMixin, serializers.ModelSerializer):

    enabled = serializers.SerializerMethodField()
    sport_association = SportAssociationSerializer()

    def get_enabled(self, obj):
        return obj.enabled

    def create(self, validated_data):
        instance = Module.objects.create(**validated_data)
        return instance

    class Meta:
        model = Module
        fields = '__all__'


class ModuleResponseSerializer(serializers.ModelSerializer):

    attachments_docs = serializers.SerializerMethodField()

    class Meta:
        model = ModuleResponses
        exclude = ('attachment_token_hash',)

    @staticmethod
    def get_attachments_docs(obj):
        attachments = []

        if obj.attachments:
            for attachment in obj.attachments.all():
                document = Document.objects.get(document_id=attachment.document_id)
                attachments.append({
                    'document_id': document.document_id,
                    'filename': document.filename,
                    'token': document.token
                })

        return attachments



class PublicModuleSerializer(serializers.ModelSerializer):
    """The public module page: the fields the form needs."""

    sport_association = serializers.SerializerMethodField()

    @staticmethod
    def get_sport_association(obj):
        association = obj.sport_association
        if association is None:
            return None
        return {'denomination': association.denomination}

    class Meta:
        model = Module
        fields = ['module_id', 'title', 'response_message', 'allow_attachments', 'only_users', 'elements',
                  'sport_association']


class PublicModuleResponseSerializer(serializers.ModelSerializer):
    """The public module page: the attachment list of a response."""

    attachments_docs = serializers.SerializerMethodField()

    class Meta:
        model = ModuleResponses
        fields = ['attachments_docs']

    @staticmethod
    def get_attachments_docs(obj):
        return [{'document_id': document.document_id, 'filename': document.filename}
                for document in obj.attachments.all()]


class ModulesSerializerWithResponses(serializers.ModelSerializer):

    enabled = serializers.SerializerMethodField()
    responses = serializers.SerializerMethodField()

    def get_enabled(self, obj):
        return obj.enabled

    def get_responses(self, obj):
        responses = ModuleResponses.objects.filter(module=obj)
        return ModuleResponseSerializer(responses, many=True).data

    def create(self, validated_data):
        instance = Module.objects.create(**validated_data)
        return instance

    class Meta:
        model = Module
        fields = '__all__'
