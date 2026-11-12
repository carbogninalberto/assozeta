import base64
import binascii
import posixpath
import uuid

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone
from rest_framework import serializers

from application.models import Subscription
from application.models.user_models import AssociateTutorRelation, Associate
from application.serializers.primary_key import ReadOnlyPrimaryKeyMixin


class BasicTutorsSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = Associate
        fields = '__all__'


class BasicAssociateSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    tutors = BasicTutorsSerializer(many=True, read_only=True)

    class Meta:
        model = Associate
        fields = '__all__'


class AssociateTutorRelationSerializer(serializers.ModelSerializer):
    tutor = BasicAssociateSerializer(read_only=True)
    class Meta:
        model = AssociateTutorRelation
        fields = ['tutor', 'is_primary']


class AssociateSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    is_minor_now = serializers.BooleanField(read_only=True)
    is_tutor = serializers.BooleanField(read_only=True)
    age = serializers.IntegerField(read_only=True)
    family_members = serializers.PrimaryKeyRelatedField(many=True, read_only=True)
    tutors = AssociateTutorRelationSerializer(source='tutor_relations', many=True, read_only=True)
    tutors_data = serializers.ListField(write_only=True, required=False)
    incomplete = serializers.BooleanField(read_only=True, required=False)

    class Meta:
        model = Associate
        fields = '__all__'
        read_only_fields = ['associate_id', 'creation_date']

    # implement the validation to allow blank values if incomplete is set to True
    def validate(self, data):
        if data.get('incomplete') is True:
            return data
        return super().validate(data)

    def to_internal_value(self, data):
        """
        Decode an uploaded picture before the default serializer validation.
        The picture is stored by create() / update(), once the data is valid.
        """
        # Make a mutable copy of the data
        mutable_data = data.copy()
        self._picture_upload = None

        # Handle picture path validation
        if 'picture_path' in mutable_data and \
                mutable_data['picture_path'] is not None and \
                mutable_data['picture_path'] != '' and \
                'base64' in mutable_data['picture_path']:
            picture_path = mutable_data.get('picture_path')
            if picture_path:
                try:
                    # Split base64 metadata from actual content
                    if ',' in mutable_data['picture_path']:
                        header, picture_data = mutable_data['picture_path'].split(',', 1)
                    else:
                        picture_data = mutable_data['picture_path']
                    try:
                        decoded_image = base64.b64decode(picture_data)
                    except binascii.Error:
                        raise serializers.ValidationError({
                            'picture_path': 'Invalid base64 string'
                        })
                except Exception as e:
                    raise serializers.ValidationError({
                        'picture_path': f'Error processing image: {str(e)}'
                    })
                # The stored value is the URL of the saved picture.
                mutable_data.pop('picture_path')
                validated_data = super().to_internal_value(mutable_data)
                self._picture_upload = decoded_image
                return validated_data

        # Now call the parent's to_internal_value with our modified data
        return super().to_internal_value(mutable_data)

    def _store_picture(self, associate_id, decoded_image):
        """
        Save the picture of this associate in the default storage and return its URL.
        The object lives under the associate's own id.
        """
        try:
            # cache_refresh_token is the current timestamp
            cache_refresh_token = timezone.now().timestamp()
            storage_path = f'associates/{associate_id}/picture_{cache_refresh_token}.jpg'
            saved_path = default_storage.save(storage_path, ContentFile(decoded_image))

            location = (getattr(default_storage, 'location', None) or settings.AWS_LOCATION or '').strip('/')
            object_key = posixpath.join(location, saved_path.lstrip('/')) if location else saved_path.lstrip('/')
            bucket_name = getattr(default_storage, 'bucket_name', None) or settings.AWS_STORAGE_BUCKET_NAME
            if settings.AWS_S3_USE_OBJECT_ACL:
                # Set ACL after saving
                s3_client = default_storage.connection.meta.client
                s3_client.put_object_acl(
                    Bucket=bucket_name,
                    Key=object_key,
                    ACL='public-read'
                )

            # Public address of the storage: its public base URL, its custom domain or its endpoint.
            base_url = (getattr(settings, 'AWS_S3_PUBLIC_BASE_URL', '') or '').strip().rstrip('/')
            custom_domain = getattr(default_storage, 'custom_domain', None)
            if not base_url and custom_domain:
                base_url = f"{getattr(default_storage, 'url_protocol', 'https:')}//{custom_domain}"
            if not base_url:
                endpoint_url = getattr(default_storage, 'endpoint_url', None) or settings.AWS_S3_ENDPOINT_URL
                base_url = f"{endpoint_url.rstrip('/')}/{bucket_name}"
            return f'{base_url}/{object_key}'
        except Exception as e:
            raise serializers.ValidationError({
                'picture_path': f'Error processing image: {str(e)}'
            })

    def create(self, validated_data):
        tutors_data = self.context.get('tutors_data', [])
        decoded_image = getattr(self, '_picture_upload', None)
        if decoded_image is not None:
            self._picture_upload = None
            validated_data['associate_id'] = uuid.uuid4()
            validated_data['picture_path'] = self._store_picture(validated_data['associate_id'], decoded_image)
        associate = super().create(validated_data)
        self._handle_tutors(associate, tutors_data)
        return associate

    def update(self, instance, validated_data):
        # TODO: check if there is the need to sanitize notes
        tutors_data = self.context.get('tutors_data', [])
        decoded_image = getattr(self, '_picture_upload', None)
        if decoded_image is not None:
            self._picture_upload = None
            validated_data['picture_path'] = self._store_picture(instance.pk, decoded_image)
        instance = super().update(instance, validated_data)
        self._handle_tutors(instance, tutors_data)
        return instance

    def _handle_tutors(self, associate, tutors_data):
        # Get the tutor IDs from the incoming data
        new_tutor_ids = {data['tutor']['associate_id'] for data in tutors_data if data.get('tutor')}

        # Delete relations that are not in the new data
        AssociateTutorRelation.objects.filter(
            associate=associate
        ).exclude(
            tutor_id__in=new_tutor_ids
        ).delete()

        # Handle existing and new tutors
        for tutor_data in tutors_data:
            tutor_info = tutor_data.get('tutor')
            if not tutor_info:
                continue

            tutor_id = tutor_info.get('associate_id')

            # Try to get existing tutor or create a new one
            try:
                tutor = Associate.objects.get(associate_id=tutor_id,
                                              sport_association_id=associate.sport_association_id)
                # Update tutor with new info using the BasicAssociateSerializer
                tutor_serializer = BasicAssociateSerializer(instance=tutor, data=tutor_info, partial=True)
                tutor_serializer.is_valid(raise_exception=True)
                tutor_serializer.save()
            except Associate.DoesNotExist:
                # Remove fields that might cause issues during creation
                tutor_info.pop('associate_id', None)
                tutor_info.pop('creation_date', None)
                tutor_info.pop('tutors', None)
                tutor_serializer = BasicAssociateSerializer(data=tutor_info)
                tutor_serializer.is_valid(raise_exception=True)
                tutor = tutor_serializer.save()

            # Create or update the relation
            AssociateTutorRelation.objects.update_or_create(
                associate=associate,
                tutor=tutor,
                defaults={'is_primary': tutor_data.get('is_primary', False)}
            )
        # check if there is no primary tutor set, and set the first one as primary
        if not AssociateTutorRelation.objects.filter(associate=associate, is_primary=True).exists():
            first_relation = AssociateTutorRelation.objects.filter(associate=associate).first()
            if first_relation:
                first_relation.is_primary = True
                first_relation.save()


# Minimal Tutor Serializer
class TutorSearchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Associate
        fields = ['first_name', 'last_name', 'associate_id']
        read_only_fields = fields


# Minimal Tutor Relation Serializer
class TutorRelationSearchSerializer(serializers.ModelSerializer):
    tutor = TutorSearchSerializer(read_only=True)

    class Meta:
        model = AssociateTutorRelation
        fields = ['tutor']
        read_only_fields = fields


# Optimized Search Serializer
class AssociateSearchSerializer(serializers.ModelSerializer):
    tutors = TutorRelationSearchSerializer(source='tutor_relations', many=True, read_only=True)
    age = serializers.SerializerMethodField()
    is_tutor = serializers.BooleanField(source='is_tutor_annotated', read_only=True)

    class Meta:
        model = Associate
        fields = [
            'associate_id',
            'first_name',
            'last_name',
            'born_date',
            'is_tutor',
            'age',
            'notes',
            'tutors',
            'picture_path'
        ]
        read_only_fields = fields

    def get_age(self, obj):
        return obj.calculate_age()


class AssociateSubscriptionSerializer(ReadOnlyPrimaryKeyMixin, serializers.ModelSerializer):
    active = serializers.BooleanField(read_only=True)
    is_current = serializers.BooleanField(read_only=True)
    is_next_year = serializers.BooleanField(read_only=True)
    current_year = serializers.CharField(read_only=True)
    medical = serializers.SerializerMethodField(read_only=True)
    medical_expiration_date = serializers.SerializerMethodField(read_only=True)
    age = serializers.SerializerMethodField(read_only=True)
    renewal_available = serializers.SerializerMethodField(read_only=True)
    signature_present = serializers.SerializerMethodField(read_only=True)
    associate = BasicAssociateSerializer(read_only=True)

    def get_signature_present(self, obj):
        return obj.has_signature

    def get_medical(self, obj):
        medical = obj.medical
        if medical:
            return medical.medical_id
        return None

    def get_medical_expiration_date(self, obj):
        medical = obj.medical
        if medical and medical.expiration_date is not None:
            return medical.expiration_date
        return None

    def get_age(self, obj):
        return obj.associate.age

    def get_renewal_available(self, obj):
        return obj.is_renewal_available()

    class Meta:
        model = Subscription
        exclude = ('signature_storage_key',)
