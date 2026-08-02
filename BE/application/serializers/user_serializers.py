from django.core.exceptions import MultipleObjectsReturned
from django.db import transaction
from django.db.utils import IntegrityError
from rest_framework import serializers

from application.models.subscriptions_models import MedicalAppointments
from application.models.user_models import Associate, Instructor, EmailLog, InstructorHours, \
    Folder, SportAssociationDocumentsArchive, SportAssociationModuleTemplates
from docmanager.models import Document


class EmailLogSerializer(serializers.ModelSerializer):

    class Meta:
        model = EmailLog
        fields = (
            'email_log_id',
            'recipient',
            'subject',
            'sent_at',
            'result',
        )


class InstructorSerializer(serializers.ModelSerializer):
    # expand validation

    def create(self, validated_data):
        instance = Instructor.objects.create(**validated_data)
        return instance

    class Meta:
        model = Instructor
        fields = '__all__'


class InstructorHoursSerializer(serializers.ModelSerializer):
    document_token = serializers.SerializerMethodField(required=False, read_only=True)

    def get_document_token(self, obj):
        # get the token from the document if it exists
        if obj.document:
            return obj.document.token

    def create(self, validated_data):
        instance = InstructorHours.objects.create(**validated_data)
        return instance

    class Meta:
        model = InstructorHours
        fields = '__all__'


class AssociateBasicInfoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Associate
        fields = ('associate_id',
                  'first_name',
                  'last_name',
                  'tax_code',
                  'family',
                  'family_role',
                  )


class AssociateSerializer(serializers.ModelSerializer):
    associate_id = serializers.CharField(required=False)
    user = serializers.SerializerMethodField(read_only=True)

    def get_user(self, obj):
        if obj.user is None:
            return str(obj.sport_association.user.user_id) if obj.sport_association else None
        return str(obj.user.user_id)

    def create(self, validated_data):
        associate_id = validated_data.get('associate_id')
        if associate_id:
            # Remove associate_id from defaults to avoid passing it twice
            defaults = {k: v for k, v in validated_data.items() if k != 'associate_id'}
            new_sport_association = validated_data.get('sport_association')
            tax_code = validated_data.get('tax_code')

            # First, try to get the existing associate directly
            # Use all_objects() to bypass soft-delete filter, as the associate might be
            # marked as deleted but still exist in the database (PK constraint)
            instance = Associate.objects.all_objects().filter(associate_id=associate_id).first()
            if instance:
                # Check if we're trying to change sport_association
                # If so, we need to find/create the associate in the target sport_association instead
                if new_sport_association and instance.sport_association_id != new_sport_association:
                    # Look for existing associate with same tax_code in target sport_association
                    if tax_code:
                        existing_in_target = Associate.objects.all_objects().filter(
                            tax_code__iexact=tax_code,
                            sport_association=new_sport_association
                        ).first()
                        if existing_in_target:
                            # Use the existing associate in target sport_association
                            for attr, value in validated_data.items():
                                if attr != 'associate_id':  # Don't change the ID
                                    setattr(existing_in_target, attr, value)
                            existing_in_target.deleted = False
                            existing_in_target.save()
                            return existing_in_target

                # Associate exists in same sport_association, update it
                for attr, value in validated_data.items():
                    setattr(instance, attr, value)
                instance.deleted = False  # Restore if it was soft-deleted
                instance.save()
                return instance

            # Associate doesn't exist, create it with retry logic for race conditions
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    with transaction.atomic():
                        instance = Associate.objects.create(
                            associate_id=associate_id,
                            **defaults
                        )
                    return instance
                except IntegrityError:
                    # Race condition: another request created the record
                    # Try to fetch it (including soft-deleted)
                    instance = Associate.objects.all_objects().filter(associate_id=associate_id).first()
                    if instance:
                        # Update with new data
                        for attr, value in validated_data.items():
                            setattr(instance, attr, value)
                        instance.deleted = False  # Restore if it was soft-deleted
                        instance.save()
                        return instance
                    # Also check by tax_code + sport_association (unique constraint)
                    if tax_code and new_sport_association:
                        instance = Associate.objects.all_objects().filter(
                            tax_code__iexact=tax_code,
                            sport_association=new_sport_association
                        ).first()
                        if instance:
                            for attr, value in validated_data.items():
                                if attr != 'associate_id':
                                    setattr(instance, attr, value)
                            instance.deleted = False
                            instance.save()
                            return instance
                    # If still not found, retry
                    if attempt == max_retries - 1:
                        raise
                    continue
        else:
            # Use case-insensitive lookup for tax_code to prevent duplicates
            tax_code = validated_data.get('tax_code')

            # Normalize tax_code to uppercase for consistent storage
            if tax_code:
                validated_data['tax_code'] = tax_code.upper()

            # If tax_code is None, we cannot use get_or_create reliably
            # because tax_code__iexact=None matches ALL records with NULL tax_code
            # In this case, just create a new Associate directly
            if not tax_code:
                instance = Associate.objects.create(**validated_data)
                return instance

            # Use a retry loop to handle race conditions robustly
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    with transaction.atomic():
                        instance, created = Associate.objects.get_or_create(
                            tax_code__iexact=validated_data.get('tax_code'),
                            sport_association=validated_data.get('sport_association'),
                            deleted=False,
                            defaults=validated_data
                        )
                    break  # Success, exit the retry loop
                except MultipleObjectsReturned:
                    # Handle existing duplicates - get the most recent one and update it
                    instance = Associate.objects.filter(
                        tax_code__iexact=validated_data.get('tax_code'),
                        sport_association=validated_data.get('sport_association'),
                        deleted=False
                    ).order_by('-creation_date').first()
                    created = False
                    break
                except IntegrityError:
                    # Race condition: another request created/modified the record
                    instance = Associate.objects.filter(
                        tax_code__iexact=validated_data.get('tax_code'),
                        sport_association=validated_data.get('sport_association'),
                        deleted=False
                    ).order_by('-creation_date').first()
                    if instance:
                        created = False
                        break
                    # If instance is None, the conflicting transaction was rolled back
                    if attempt == max_retries - 1:
                        raise
                    continue

            if not created and instance:
                # Update existing instance with new data
                for attr, value in validated_data.items():
                    setattr(instance, attr, value)
                instance.save()

        return instance

    class Meta:
        model = Associate
        fields = ('associate_id',
                  'address',
                  'address_cap',
                  'address_city',
                  'born_city',
                  'born_date',
                  'email',
                  'sex',
                  'first_name',
                  'last_name',
                  'is_minor',
                  'sport_association',
                  'tax_code',
                  'user',
                  'phone',
                  'family',
                  'family_role',
                  )


class AssociateInfoSerializer(serializers.ModelSerializer):

    def create(self, validated_data):
        # Normalize tax_code to uppercase for consistent storage
        tax_code = validated_data.get('tax_code')
        if tax_code:
            validated_data['tax_code'] = tax_code.upper()

        # If tax_code is None, just create a new Associate directly
        if not tax_code:
            return Associate.objects.create(**validated_data)

        # Use a retry loop to handle race conditions robustly
        max_retries = 3
        for attempt in range(max_retries):
            try:
                with transaction.atomic():
                    instance, created = Associate.objects.get_or_create(
                        tax_code__iexact=validated_data.get('tax_code'),
                        sport_association=validated_data.get('sport_association'),
                        deleted=False,
                        defaults=validated_data
                    )
                break
            except MultipleObjectsReturned:
                instance = Associate.objects.filter(
                    tax_code__iexact=validated_data.get('tax_code'),
                    sport_association=validated_data.get('sport_association'),
                    deleted=False
                ).order_by('-creation_date').first()
                created = False
                break
            except IntegrityError:
                instance = Associate.objects.filter(
                    tax_code__iexact=validated_data.get('tax_code'),
                    sport_association=validated_data.get('sport_association'),
                    deleted=False
                ).order_by('-creation_date').first()
                if instance:
                    created = False
                    break
                if attempt == max_retries - 1:
                    raise
                continue

        # Update if already exists
        if not created:
            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()

        return instance

    class Meta:
        model = Associate
        fields = ('associate_id',
                  'address',
                  'address_cap',
                  'address_city',
                  'born_city',
                  'born_date',
                  'email',
                  'first_name',
                  'last_name',
                  'is_minor',
                  'sex',
                  'tax_code',
                  'phone',
                  'phone_2',
                  'phone_3',
                  'phone_4',
                  'phone_label',
                  'phone_2_label',
                  'phone_3_label',
                  'phone_4_label',
                  'picture_path',
                  )


class AssociateSerializerInfo(serializers.ModelSerializer):
    def create(self, validated_data):
        # Normalize tax_code to uppercase for consistent storage
        tax_code = validated_data.get('tax_code')
        if tax_code:
            validated_data['tax_code'] = tax_code.upper()

        # If tax_code is None, just create a new Associate directly
        if not tax_code:
            return Associate.objects.create(**validated_data)

        # Use a retry loop to handle race conditions robustly
        max_retries = 3
        for attempt in range(max_retries):
            try:
                with transaction.atomic():
                    instance, created = Associate.objects.get_or_create(
                        tax_code__iexact=validated_data.get('tax_code'),
                        sport_association=validated_data.get('sport_association'),
                        deleted=False,
                        defaults=validated_data
                    )
                break
            except MultipleObjectsReturned:
                instance = Associate.objects.filter(
                    tax_code__iexact=validated_data.get('tax_code'),
                    sport_association=validated_data.get('sport_association'),
                    deleted=False
                ).order_by('-creation_date').first()
                created = False
                break
            except IntegrityError:
                instance = Associate.objects.filter(
                    tax_code__iexact=validated_data.get('tax_code'),
                    sport_association=validated_data.get('sport_association'),
                    deleted=False
                ).order_by('-creation_date').first()
                if instance:
                    created = False
                    break
                if attempt == max_retries - 1:
                    raise
                continue

        # Update if already exists
        if not created:
            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()

        return instance

    class Meta:
        model = Associate
        fields = (
            'first_name',
            'last_name',
            'tax_code',
        )


class MedicalAppointmentsSerializer(serializers.ModelSerializer):
    document_token = serializers.SerializerMethodField()
    appointment_region = serializers.SerializerMethodField()

    def get_document_token(self, obj):
        # check document exists
        if obj.document is None:
            return None
        return obj.document.token

    def get_appointment_region(self, obj):
        return MedicalAppointments.REGIONS[obj.region]

    class Meta:
        model = MedicalAppointments
        fields = '__all__'

class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = '__all__'

class SportAssociationModuleTemplatesSerializer(serializers.ModelSerializer):
    class Meta:
        model = SportAssociationModuleTemplates
        fields = '__all__'

class FolderSerializer(serializers.ModelSerializer):
    level = serializers.IntegerField(read_only=True)
    children = serializers.SerializerMethodField()

    class Meta:
        model = Folder
        fields = ['id', 'name', 'parent', 'sport_association', 'created_at', 'level', 'children']

    def get_children(self, obj):
        return FolderSerializer(obj.get_children(), many=True).data

class DocumentArchiveSerializer(serializers.ModelSerializer):
    document = DocumentSerializer(many=True, read_only=True)

    class Meta:
        model = SportAssociationDocumentsArchive
        fields = ['sport_association_documents_archive_id', 'sport_association',
                 'document', 'date', 'folder']
