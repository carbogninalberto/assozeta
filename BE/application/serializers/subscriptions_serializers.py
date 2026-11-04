import base64
import os
from io import BytesIO

from django.core.files.storage import default_storage
from rest_framework import serializers

from application.models import Payment, CourseSubscription
from application.models.carnet_models import CarnetSubscription
from application.models.subscriptions_models import Subscription, SignatureRequest, SubscriptionTransfer, SubscriptionMembership
from application.serializers.auth_serializers import UserSerializer, SportAssociationSearchSerializer
from application.serializers.user_serializers import AssociateSerializer, AssociateInfoSerializer, AssociateBasicInfoSerializer
from application.serializers.personas_serializers import AssociateSerializer as AssociatePersonaSerializer
from core.settings import STORAGE_DIR
from docmanager.models import Document

SUBSCRIPTION_INTERNAL_FIELDS = ('signature_storage_key',)


def _active_payments(associate):
    """Non-archived payments of the associate, from the queryset's prefetch when present."""
    prefetched = getattr(associate, '_prefetched_objects_cache', {}).get('payment_set')
    if prefetched is not None:
        return list(prefetched)
    return list(Payment.objects.filter(associate=associate, archived=False))


class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = '__all__'

class SubscriptionMembershipSerializer(serializers.ModelSerializer):
    attached_membership_documents = DocumentSerializer(many=True, read_only=True)
    associate_id = serializers.UUIDField(read_only=True)
    documents_data = serializers.ListField(
        child=serializers.DictField(), write_only=True, required=False
    )

    class Meta:
        model = SubscriptionMembership
        fields = [
            'subscription_membership_id',
            'sport_association',
            'associate_id',
            'associate',
            'creation_date',
            'start_date',
            'end_date',
            'membership_type',
            'membership_number',
            'description',
            'price',
            'paid',
            'payment',
            'attached_membership_documents',
            'documents_data'
        ]

    def create(self, validated_data):
        documents_data = validated_data.pop('documents_data', [])
        subscription = super().create(validated_data)
        self._handle_documents(subscription, documents_data)
        return subscription

    def update(self, instance, validated_data):
        documents_data = validated_data.pop('documents_data', [])
        instance = super().update(instance, validated_data)
        self._handle_documents(instance, documents_data)
        return instance

    def _handle_documents(self, subscription, documents_data):
        for document_data in documents_data:
            if 'document' in document_data.keys() and 'filename' in document_data.keys():
                file_data = base64.b64decode(document_data['document'])
                document = Document.objects.create(
                    filename=document_data['filename']
                )
                document.save()

                storing_path = os.path.join(STORAGE_DIR, str(document.creation_date.timestamp()), str(document.document_id))
                file = os.path.join(storing_path, document.filename)
                file_like = BytesIO(file_data)

                default_storage.save(file, file_like)
                subscription.attached_membership_documents.add(document)
            else:
                raise serializers.ValidationError({'exception': 'document key not present'})


class SubscriptionOptimizedSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    associate = AssociateInfoSerializer(read_only=True)
    tags = serializers.SerializerMethodField()
    all_payments_paid = serializers.SerializerMethodField()
    payments_info = serializers.SerializerMethodField()
    age = serializers.SerializerMethodField()
    current_year = serializers.SerializerMethodField(read_only=True)
    next_years = serializers.SerializerMethodField(read_only=True)
    renewal_available = serializers.SerializerMethodField(read_only=True)
    medical = serializers.SerializerMethodField(read_only=True)
    medical_document = serializers.SerializerMethodField(read_only=True)
    medical_token = serializers.SerializerMethodField(read_only=True)
    medical_expiration_date = serializers.SerializerMethodField(read_only=True)
    document = serializers.SerializerMethodField(read_only=True)
    document_token = serializers.SerializerMethodField(read_only=True)
    plain_medical_label = serializers.SerializerMethodField(read_only=True)
    signature_present = serializers.SerializerMethodField(read_only=True)

    def get_current_year(self, obj):
        return obj.is_current

    def get_next_years(self, obj):
        return obj.is_next_year

    def get_renewal_available(self, obj):
        """
        Check if subscription renewal is available.

        OPTIMIZED: Uses pre-computed annotation (_has_future_renewal) from queryset
        to avoid N+1 queries. Falls back to direct query if annotation not available.
        """
        return obj.is_renewal_available()

    def get_medical(self, obj):
        if obj.medical is not None:
            return obj.medical.medical_id
        return None

    def get_medical_document(self, obj):
        if obj.medical is not None:
            if obj.medical.document is not None:
                return obj.medical.document.document_id
        return None

    def get_medical_token(self, obj):
        if obj.medical is not None and obj.medical.document is not None:
            return obj.medical.document.token
        return None

    def get_medical_expiration_date(self, obj):
        if obj.medical is not None:
            return obj.medical.expiration_date
        return None

    def get_document(self, obj):
        if obj.document_pdf is not None:
            return obj.document_pdf.document_id
        return None

    def get_document_token(self, obj):
        if obj.document_pdf is not None:
            return obj.document_pdf.token
        return None

    def get_tags(self, obj):
        # Use prefetched tags instead of obj.tags.all()
        return [{'tag_id': tag.tag_id, 'tag_name': tag.tag_name} for tag in obj.tags.all()]

    def get_all_payments_paid(self, obj):
        # Use prefetched payments (much faster than separate query)
        payments = obj.associate.payment_set.all()
        unpaid_count = sum(1 for payment in payments if not payment.paid and not payment.archived)
        return unpaid_count == 0

    def get_payments_info(self, obj):
        """
        Get total payments and unpaid payments for an associate using prefetched data.
        """
        # Use prefetched payment_set (much faster than database aggregation)
        payments = obj.associate.payment_set.all()
        # Filter archived=False in Python since we're using prefetched data
        active_payments = [p for p in payments if not p.archived]

        total = sum(payment.amount for payment in active_payments)
        to_be_paid = sum(payment.amount for payment in active_payments if not payment.paid)

        return {
            'total': total,
            'to_be_paid': to_be_paid
        }

    def get_age(self, obj):
        return obj.get_age()

    def get_plain_medical_label(self, obj):
        return obj.get_plain_medical_label()

    def get_signature_present(self, obj):
        return obj.has_signature

    def create(self, validated_data):
        # Remove deprecated 'signature' field if present
        validated_data.pop('signature', None)
        instance = Subscription.objects.create(**validated_data)
        return instance

    class Meta:
        model = Subscription
        exclude = SUBSCRIPTION_INTERNAL_FIELDS
        read_only_fields = tuple(
            field.name for field in model._meta.fields
            if field.name not in SUBSCRIPTION_INTERNAL_FIELDS
        )


class SubscriptionFastOptimizedSerializer(serializers.Serializer):
    """
    Ultra high-performance serializer for subscription list data.

    Uses DRF's base Serializer instead of ModelSerializer to eliminate:
    - Model introspection overhead (~30-40% faster)
    - Field validation overhead
    - Automatic field generation

    Expected queryset optimizations:
    - select_related('associate', 'medical', 'medical__document', 'document_pdf', 'user', 'sport_association', 'payment')
    - prefetch_related('tags', 'associate__payment_set')
    - annotate(_has_future_renewal=Exists(...))

    Matches the exact field structure of SubscriptionOptimizedSerializer.
    """

    def to_representation(self, obj):
        """
        Manually build response dict - fastest possible serialization.
        All fields are accessed directly without DRF's field processing.
        """
        # Helper: safely serialize user (uses prefetched data)
        user_data = None
        if obj.user:
            user_data = {
                'user_id': str(obj.user.user_id),
                'username': obj.user.username,
                'email': obj.user.email,
                'first_name': obj.user.first_name,
                'last_name': obj.user.last_name,
                'role': obj.user.role,
            }

        # Helper: safely serialize associate (uses prefetched data)
        # Match AssociateInfoSerializer fields exactly
        associate_data = None
        if obj.associate:
            associate_data = {
                'associate_id': str(obj.associate.associate_id),
                'address': obj.associate.address,
                'address_cap': obj.associate.address_cap,
                'address_city': obj.associate.address_city,
                'born_city': obj.associate.born_city,
                'born_date': obj.associate.born_date,
                'email': obj.associate.email,
                'first_name': obj.associate.first_name,
                'last_name': obj.associate.last_name,
                'is_minor': obj.associate.is_minor,
                'sex': obj.associate.sex,
                'tax_code': obj.associate.tax_code,
                'phone': obj.associate.phone,
                'phone_2': obj.associate.phone_2,
                'phone_3': obj.associate.phone_3,
                'phone_4': obj.associate.phone_4,
                'phone_label': obj.associate.phone_label,
                'phone_2_label': obj.associate.phone_2_label,
                'phone_3_label': obj.associate.phone_3_label,
                'phone_4_label': obj.associate.phone_4_label,
                'picture_path': obj.associate.picture_path,
            }

        # Helper: tags (uses prefetched tags)
        tags = [
            {'tag_id': str(tag.tag_id), 'tag_name': tag.tag_name}
            for tag in obj.tags.all()
        ]

        # Helper: payments info (uses prefetched payment_set)
        payments = obj.associate.payment_set.all() if obj.associate else []
        active_payments = [p for p in payments if not p.archived]
        total_amount = sum(p.amount for p in active_payments)
        to_be_paid = sum(p.amount for p in active_payments if not p.paid)
        all_payments_paid = sum(1 for p in payments if not p.paid and not p.archived) == 0

        # Helper: medical data (uses prefetched medical and medical__document)
        medical_id = str(obj.medical.medical_id) if obj.medical else None
        medical_document_id = str(obj.medical.document.document_id) if obj.medical and obj.medical.document else None
        medical_token = obj.medical.document.token if obj.medical and obj.medical.document else None
        medical_expiration_date = obj.medical.expiration_date if obj.medical else None

        # Helper: document data (uses prefetched document_pdf)
        document_id = str(obj.document_pdf.document_id) if obj.document_pdf else None
        document_token = obj.document_pdf.token if obj.document_pdf else None

        # Helper: renewal_available (uses annotation)
        renewal_available = (
            not obj.is_current and not obj._has_future_renewal
            if hasattr(obj, '_has_future_renewal')
            else not obj.is_current
        )

        # Helper: payment data
        payment_data = None
        if obj.payment:
            payment_data = {
                'payment_id': str(obj.payment.payment_id),
                'amount': float(obj.payment.amount),
                'paid': obj.payment.paid,
                'payment_date': obj.payment.payment_date,
            }

        # Build final response
        return {
            # Model fields
            'subscription_id': str(obj.subscription_id),
            'sport_association': str(obj.sport_association_id),
            'creation_date': obj.creation_date,
            'start_date': obj.start_date,
            'end_date': obj.end_date,
            'status_flag': obj.status_flag,
            'type': obj.type,
            'role': obj.role,
            'acceptance_date': obj.acceptance_date,
            'resignation_date': obj.resignation_date,
            'signature_url': obj.signature_url,
            'deleted': obj.deleted,
            'archived': obj.archived,
            'draft': obj.draft,
            'trial': obj.trial,
            'meta': obj.meta,
            'custom_data': obj.custom_data,
            'additional_fields': obj.additional_fields,
            'subscription_number': obj.subscription_number,
            'subscription_type': obj.subscription_type,
            'competitive': obj.competitive,
            'notes': obj.notes,

            # Nested serializers (manually constructed)
            'user': user_data,
            'associate': associate_data,
            'payment': payment_data,

            # Computed fields
            'tags': tags,
            'all_payments_paid': all_payments_paid,
            'payments_info': {
                'total': float(total_amount),
                'to_be_paid': float(to_be_paid),
            },
            'age': obj.get_age() if obj.associate else None,
            'current_year': obj.is_current,
            'next_years': obj.is_next_year,
            'renewal_available': renewal_available,

            # Medical fields
            'medical': medical_id,
            'medical_document': medical_document_id,
            'medical_token': medical_token,
            'medical_expiration_date': medical_expiration_date,

            # Document fields
            'document': document_id,
            'document_token': document_token,

            # Helper fields
            'plain_medical_label': obj.get_plain_medical_label() if obj.associate else None,
            'signature_present': obj.has_signature,
        }


class SubscriptionSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    associate = AssociateInfoSerializer(read_only=True)
    tags = serializers.SerializerMethodField()
    all_payments_paid = serializers.SerializerMethodField()
    payments_info = serializers.SerializerMethodField()
    age = serializers.SerializerMethodField()
    current_year = serializers.SerializerMethodField(read_only=True)
    next_years = serializers.SerializerMethodField(read_only=True)
    renewal_available = serializers.BooleanField(read_only=True)
    medical = serializers.SerializerMethodField(read_only=True)
    medical_token = serializers.SerializerMethodField(read_only=True)
    medical_expiration_date = serializers.SerializerMethodField(read_only=True)
    document = serializers.SerializerMethodField(read_only=True)
    document_token = serializers.SerializerMethodField(read_only=True)
    signature_present = serializers.SerializerMethodField(read_only=True)

    def get_current_year(self, obj):
        return obj.is_current

    def get_next_years(self, obj):
        return obj.is_next_year

    def get_signature_present(self, obj):
        return obj.has_signature

    def get_renewal_available(self, obj):
        return obj.is_renewal_available()

    def get_medical(self, obj):
        if obj.medical is not None and obj.medical.document is not None:
            return obj.medical.document.document_id
        return None

    def get_medical_token(self, obj):
        if obj.medical is not None and obj.medical.document is not None:
            return obj.medical.document.token
        return None

    def get_medical_expiration_date(self, obj):
        if obj.medical is not None:
            return obj.medical.expiration_date
        return None

    def get_document(self, obj):
        if obj.document_pdf is not None:
            return obj.document_pdf.document_id
        return None

    def get_document_token(self, obj):
        if obj.document_pdf is not None:
            return obj.document_pdf.token
        return None

    def get_tags(self, obj):
        return [{'tag_id': tag.tag_id, 'tag_name': tag.tag_name} for tag in obj.tags.all()]

    def get_all_payments_paid(self, obj):
        return not any(not payment.paid for payment in _active_payments(obj.associate))

    def get_payments_info(self, obj):
        # sum all payments to be paid and all the payments
        payments = _active_payments(obj.associate)

        return {"total": sum([payment.amount for payment in payments]), "to_be_paid": sum([payment.amount for payment in payments if not payment.paid])}

    def get_age(self, obj):
        return obj.get_age()

    def create(self, validated_data):
        # Remove deprecated 'signature' field if present
        validated_data.pop('signature', None)
        instance = Subscription.objects.create(**validated_data)
        return instance

    class Meta:
        model = Subscription
        exclude = SUBSCRIPTION_INTERNAL_FIELDS


class SubscriptionInfoSerializer(serializers.ModelSerializer):
    membership_card_configuration = serializers.SerializerMethodField()

    def get_membership_card_configuration(self, obj):
        return SportAssociationSearchSerializer().get_membership_card_configuration(obj.sport_association)

    user = UserSerializer()
    associate = AssociatePersonaSerializer()
    medical_expiration_date = serializers.DateField(source='medical.expiration_date', read_only=True, allow_null=True)
    medical_document = serializers.UUIDField(source='medical.document_id', read_only=True, allow_null=True)
    medical_token = serializers.CharField(source='medical.document.token', read_only=True, allow_null=True)
    competitive_medical_certificate = serializers.BooleanField(source='medical.competitive_medical_certificate', read_only=True)
    subscription_files = serializers.SerializerMethodField()
    is_current = serializers.BooleanField(read_only=True)
    is_next_year = serializers.BooleanField(read_only=True)
    plain_medical_label = serializers.SerializerMethodField(read_only=True)
    signature_present = serializers.SerializerMethodField(read_only=True)

    def get_plain_medical_label(self, obj):
        return obj.get_plain_medical_label()

    def get_signature_present(self, obj):
        return obj.has_signature

    def create(self, validated_data):
        # Remove deprecated 'signature' field if present
        validated_data.pop('signature', None)
        instance = Subscription.objects.create(**validated_data)
        return instance

    class Meta:
        model = Subscription
        exclude = SUBSCRIPTION_INTERNAL_FIELDS

    def get_subscription_files(self, obj):
        # Use prefetched data if available, otherwise query with select_related
        if hasattr(obj, '_prefetched_objects_cache') and 'subscriptionfile_set' in obj._prefetched_objects_cache:
            subscription_files = obj.subscriptionfile_set.all()
        else:
            subscription_files = obj.subscriptionfile_set.select_related('document').all()

        return [{'subscription_file_id': subscription_file.subscription_file_id,
                 'document_id': subscription_file.document.document_id,
                 'filename': subscription_file.document.filename,
                 'document_token': subscription_file.document.token,
                 'creation_date': subscription_file.creation_date} for subscription_file in subscription_files]


class SubscriptionBasicSerializer(serializers.ModelSerializer):
    associate = AssociateBasicInfoSerializer()
    current_year = serializers.SerializerMethodField(read_only=True)

    def get_current_year(self, obj):
        return obj.is_current

    class Meta:
        model = Subscription
        fields = (
            'subscription_id',
            'associate',
            'current_year'
        )


class SubscriptionFastBasicSerializer(serializers.Serializer):
    """
    High-performance serializer for basic subscription data.
    Uses DRF's base Serializer instead of ModelSerializer to avoid introspection overhead.

    Expected to be used with a queryset that has select_related('associate') applied.
    """

    def to_representation(self, obj):
        """Build the dict directly: per-field DRF processing dominates on lists of thousands of rows."""
        associate = obj.associate
        return {
            'subscription_id': str(obj.subscription_id),
            'associate': {
                'associate_id': str(associate.associate_id),
                'first_name': associate.first_name,
                'last_name': associate.last_name,
                'tax_code': associate.tax_code,
                'family': str(associate.family_id) if associate.family_id else None,
                'family_role': associate.family_role,
            } if associate else None,
            'current_year': obj.is_current,
        }


class SubscriptionSerializerSimplify(serializers.ModelSerializer):
    associate = AssociateSerializer()

    class Meta:
        model = Subscription
        fields = (
            'subscription_id',
            'associate',
            'current_year',
        )


class SignatureRequestSerializer(serializers.Serializer):
    there_is_signature = serializers.BooleanField(default=False)
    data = serializers.CharField(allow_blank=True, allow_null=True)

    def validate(self, attrs):
        if attrs.get('there_is_signature') and not attrs.get('data'):
            raise serializers.ValidationError({'data': 'La firma è obbligatoria.'})
        return attrs

    def update(self, instance, validated_data):
        pass

    def create(self, validated_data):
        instance = SignatureRequest(**validated_data)
        return instance


class SubscriptionSerializerAthleteList(serializers.ModelSerializer):
    user = UserSerializer()
    associate = AssociateSerializer()
    sport_association = SportAssociationSearchSerializer()
    signature_present = serializers.SerializerMethodField(read_only=True)

    def get_signature_present(self, obj):
        return obj.has_signature

    def create(self, validated_data):
        # Remove deprecated 'signature' field if present
        validated_data.pop('signature', None)
        instance = Subscription.objects.create(**validated_data)
        return instance

    class Meta:
        model = Subscription
        exclude = SUBSCRIPTION_INTERNAL_FIELDS


class DictListSerializer(serializers.ListSerializer):
    @property
    def data(self):
        # Get the regular serialized data
        ret = super().data
        # Convert to dictionary with index as key
        return {str(i): item for i, item in enumerate(ret)}


class SubscriptionSerializerAthleteOptimizedList(serializers.ModelSerializer):
    # user = UserSerializer()
    associate = AssociateSerializer()
    sport_association = serializers.SerializerMethodField(read_only=True)
    is_current = serializers.BooleanField(read_only=True)
    carnets = serializers.SerializerMethodField(read_only=True)
    medical = serializers.SerializerMethodField(read_only=True)
    medical_token = serializers.SerializerMethodField(read_only=True)
    medical_expiration_date = serializers.SerializerMethodField(read_only=True)
    medical_document = serializers.SerializerMethodField(read_only=True)
    courses = serializers.SerializerMethodField(read_only=True)
    renewal_available = serializers.SerializerMethodField(read_only=True)
    signature_present = serializers.SerializerMethodField(read_only=True)
    # attendance_registry = serializers.SerializerMethodField(read_only=True)

    def get_sport_association(self, obj):
        # The rows share one or two associations, whose serializer queries several tables: serialize each once.
        cache = self.context.setdefault('_sport_associations', {})
        if obj.sport_association_id not in cache:
            cache[obj.sport_association_id] = SportAssociationSearchSerializer(obj.sport_association).data
        return cache[obj.sport_association_id]

    def get_carnets(self, obj):
        if hasattr(obj, '_has_carnets'):
            return obj._has_carnets
        return CarnetSubscription.objects.filter(subscription=obj).count() > 0

    def get_signature_present(self, obj):
        return obj.has_signature

    def get_medical(self, obj):
        if obj.medical is not None:
            return obj.medical.medical_id
        return None

    def get_medical_token(self, obj):
        if obj.medical is not None and obj.medical.document is not None:
            return obj.medical.document.token
        return None

    def get_medical_expiration_date(self, obj):
        if obj.medical is not None:
            return obj.medical.expiration_date
        return None

    def get_medical_document(self, obj):
        if obj.medical is not None:
            return obj.medical.document_id
        return None

    def get_renewal_available(self, obj):
        return obj.is_renewal_available()

    def get_courses(self, obj):
        prefetched = getattr(obj, '_prefetched_objects_cache', {}).get('coursesubscription_set')
        if prefetched is not None:
            return [{
                'course_subscription_id': cs.course_subscription_id,
                'paid': cs.paid,
                'creation_date': cs.creation_date,
                'course__title': cs.course.title if cs.course else None,
                'course__course_id': cs.course.course_id if cs.course else None,
                'course__one_fee_payment': cs.course.one_fee_payment if cs.course else None,
                'course__one_fee': cs.course.one_fee if cs.course else None,
                'one_fee_payment': cs.one_fee_payment,
            } for cs in prefetched]
        courses = CourseSubscription.objects.filter(subscription=obj) \
            .select_related().values(
            'course_subscription_id',
            'paid',
            'creation_date',
            'course__title',
            'course__course_id',
            'course__one_fee_payment',
            'course__one_fee',
            'one_fee_payment',
        )

        return courses


    def create(self, validated_data):
        # Remove deprecated 'signature' field if present
        validated_data.pop('signature', None)
        instance = Subscription.objects.create(**validated_data)
        return instance

    class Meta:
        list_serializer_class = DictListSerializer
        model = Subscription
        exclude = SUBSCRIPTION_INTERNAL_FIELDS


class SubscriptionTransferCreateSerializer(serializers.ModelSerializer):

    class Meta:
        model = SubscriptionTransfer
        fields = (
            'subscription',
            'expires_at',
            'requester',
            'recipient',
            'token',
            'status',
        )


class SubscriptionTransferSerializer(serializers.ModelSerializer):

    subscription = serializers.SerializerMethodField()
    recipient = UserSerializer()
    is_expired = serializers.SerializerMethodField()

    class Meta:
        model = SubscriptionTransfer
        fields = (
            'subscription_transfer_id',
            'subscription',
            'expires_at',
            'requester',
            'recipient',
            'token',
            'status',
            'is_expired'
        )

    @staticmethod
    def get_subscription(obj):
        try:
            return {
                'subscription_id': obj.subscription.subscription_id,
                'associate': {
                    'associate_id': obj.subscription.associate.associate_id,
                    'first_name': obj.subscription.associate.first_name,
                    'last_name': obj.subscription.associate.last_name,
                }
            }
        except Exception:
            return None

    @staticmethod
    def get_is_expired(obj):
        return obj.is_expired()
