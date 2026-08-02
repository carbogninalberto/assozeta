import base64
import binascii
import os

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone
from rest_framework import serializers

from application.models import Subscription
from application.models.user_models import AssociateTutorRelation, Associate
from core.settings import STORAGE_DIR, AWS_STORAGE_BUCKET_NAME, AWS_LOCATION


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
        Run our picture validation before the default serializer validation
        """
        # Make a mutable copy of the data
        mutable_data = data.copy()

        # Handle picture path validation
        if 'picture_path' in mutable_data and \
                mutable_data['picture_path'] is not None and \
                mutable_data['picture_path'] != '' and \
                'base64' in mutable_data['picture_path']:
            picture_path = mutable_data.get('picture_path')
            if picture_path:
                try:
                    associate_id = mutable_data.get('associate_id')
                    # cache_refresh_token is the current timestamp
                    cache_refresh_token = timezone.now().timestamp()
                    storage_path = os.path.join(STORAGE_DIR, f'associates/{associate_id}/picture_{cache_refresh_token}.jpg')
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

                    saved_path = default_storage.save(
                        storage_path,
                        ContentFile(decoded_image)
                    )
                    # Set ACL after saving
                    s3_client = default_storage.connection.meta.client

                    s3_client.put_object_acl(
                        Bucket=AWS_STORAGE_BUCKET_NAME,
                        Key=f"{AWS_LOCATION}/{saved_path}",
                        ACL='public-read'
                    )
                    # cdn_url
                    cdn_url = os.path.join('https://bakney-object-spaces.fra1.cdn.digitaloceanspaces.com', AWS_LOCATION, storage_path)
                    # get the cdn url
                    mutable_data['picture_path'] = cdn_url
                except Exception as e:
                    raise serializers.ValidationError({
                        'picture_path': f'Error processing image: {str(e)}'
                    })

        # Now call the parent's to_internal_value with our modified data
        return super().to_internal_value(mutable_data)

    def create(self, validated_data):
        tutors_data = self.context.get('tutors_data', [])
        associate = super().create(validated_data)
        self._handle_tutors(associate, tutors_data)
        return associate

    def update(self, instance, validated_data):
        # TODO: check if there is the need to sanitize notes
        tutors_data = self.context.get('tutors_data', [])
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
                tutor = Associate.objects.get(associate_id=tutor_id)
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


class AssociateSubscriptionSerializer(serializers.ModelSerializer):
    active = serializers.BooleanField(read_only=True)
    is_current = serializers.BooleanField(read_only=True)
    is_next_year = serializers.BooleanField(read_only=True)
    current_year = serializers.CharField(read_only=True)
    medical = serializers.SerializerMethodField(read_only=True)
    medical_expiration_date = serializers.SerializerMethodField(read_only=True)
    age = serializers.SerializerMethodField(read_only=True)
    renewal_available = serializers.SerializerMethodField(read_only=True)
    associate = BasicAssociateSerializer(read_only=True)

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
        # renewable available only if not current sub and there is not a sub in the current year
        renewal_available = not obj.is_current

        # check if there is a subscription created later than current
        if renewal_available is True:
            current_year_sub = Subscription.objects.filter(
                sport_association=obj.sport_association,
                start_date__gte=obj.end_date,
                archived=False,
                associate__tax_code__iexact=obj.associate.tax_code
            ).count()
            renewal_available = current_year_sub == 0
        return renewal_available

    class Meta:
        model = Subscription
        fields = '__all__'