import base64
import os
from io import BytesIO

from django.core.files.storage import default_storage
from rest_framework import serializers

from application.models import CampsAndRetreatsSubscriptionPeriod
from application.models.carnet_models import CarnetSubscription
from application.models.courses_models import CourseSubscriptionInstallment, CourseSubscription
from application.models.invoices_models import Invoice
from application.models.payment_models import Payment, PaymentCategory, SupplierAndCustomers, VatManagement
from application.models.subscriptions_models import Subscription
from application.models.user_models import Associate
from application.serializers.auth_serializers import UserSerializer
from application.serializers.personas_serializers import AssociateTutorRelationSerializer
from application.serializers.user_serializers import AssociateSerializer, AssociateSerializerInfo, InstructorSerializer
from core.settings import STORAGE_DIR
from docmanager.models import Document


class SupplierSerializer(serializers.ModelSerializer):

    def create(self, validated_data):
        instance = SupplierAndCustomers.objects.create(**validated_data)
        return instance

    def save(self, **kwargs):
        return super().save(**kwargs)

    class Meta:
        model = SupplierAndCustomers
        fields = '__all__'

class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = '__all__'

class PaymentEntrySerializer(serializers.ModelSerializer):
    # make payment date optional
    payment_date = serializers.DateTimeField(required=False, allow_null=True)
    creation_date = serializers.DateTimeField(required=True, allow_null=False)
    user = UserSerializer(required=False)
    attachments = DocumentSerializer(many=True, read_only=True)
    attachments_data = serializers.ListField(
        child=serializers.DictField(), write_only=True, required=False
    )

    def create(self, validated_data):
        attachments_data = validated_data.pop('attachments_data', [])
        instance = Payment.objects.create(**validated_data)
        self._handle_documents(instance, attachments_data)
        return instance

    def update(self, instance, validated_data):
        attachments_data = validated_data.pop('attachments_data', [])
        instance = super().update(instance, validated_data)
        self._handle_documents(instance, attachments_data)
        return instance

    def save(self, **kwargs):
        # update amount based on meta_payment_categories
        if self.initial_data.get('meta_payment_categories') is not None:
            self.validated_data['amount'] = float(self.initial_data.get('amount'))
            meta_payment_categories = self.validated_data.get('meta_payment_categories')
            amount = 0
            for meta_payment_category in meta_payment_categories:
                amount += float(meta_payment_category.get('amount'))
            self.validated_data['amount'] += amount
        return super().save(**kwargs)

    def _handle_documents(self, obj, attachments_data):
        for attachment in attachments_data:
            if 'document' in attachment.keys() and 'filename' in attachment.keys():
                file_data = base64.b64decode(attachment['document'])
                document = Document.objects.create(
                    filename=attachment['filename']
                )
                document.save()

                storing_path = os.path.join(STORAGE_DIR, str(document.creation_date.timestamp()), str(document.document_id))
                file = os.path.join(storing_path, document.filename)
                file_like = BytesIO(file_data)

                default_storage.save(file, file_like)
                obj.attachments.add(document)
            else:
                raise serializers.ValidationError({'exception': 'document key not present'})

    class Meta:
        model = Payment
        fields = (
            'payment_id',
            'type',
            'subject',
            'amount',
            'user',
            'description',
            'expense',
            'paid',
            'payment_date',
            'payment_category',
            'sport_association',
            'custom_accounts',
            'notes',
            'course',
            'meta_payment_categories',
            'creation_date',
            'attachments',
            'attachments_data'
        )


class PaymentCompressedSerializer(serializers.ModelSerializer):
    associate = serializers.SerializerMethodField()
    meta = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = (
            'payment_id',
            'type',
            'subject',
            'amount',
            'associate',
            'description',
            'paid',
            'creation_date',
            'payment_date',
            'payment_category',
            'sport_association',
            'notes',
            'course',
            'meta'
        )

    def get_associate(self, obj):
        # return object associate_id, first_name, last_name
        if obj.associate is not None:
            return {
                'associate_id': obj.associate.associate_id,
                'first_name': obj.associate.first_name,
                'last_name': obj.associate.last_name,
            }
        else:
            return None

    def get_meta(self, obj):
        data = {
            "type": '',
            "is_installment": False,
            "installment": {
                "id": None,
                "payment_date": None,
            },
            "conjunction": "",
            "description": obj.description,
            "notes": obj.notes,
            "name": None,
            "subscription": {
                "subscription_id": None,
                "start_date": None,
                "end_date": None,
            }
        }
        # check installments, subscriptions, carnet, course for details
        installment = CourseSubscriptionInstallment.objects.filter(payment=obj).select_related(
            'course_subscription',
            'course_subscription__subscription',
        ).first()
        if installment is not None:
            data['type'] = 'rata'
            data['conjunction'] = " del corso "
            data['is_installment'] = True
            data['installment']['id'] = installment.id
            data['installment']['payment_date'] = installment.payment_date
            data['name'] = installment.course_subscription.course.title
            data['subscription'] = {
                'subscription_id': installment.course_subscription.subscription.subscription_id,
                'start_date': installment.course_subscription.subscription.start_date,
                'end_date': installment.course_subscription.subscription.end_date,
            }
            return data
        subscription = Subscription.objects.filter(payment=obj).first()
        if subscription is not None:
            data['type'] = 'Iscrizione'
            data['conjunction'] = " all'associazione "
            data['name'] = f"{subscription.sport_association.denomination}"
            data['subscription'] = {
                'subscription_id': subscription.subscription_id,
                'start_date': subscription.start_date,
                'end_date': subscription.end_date,
            }
            return data
        carnet = CarnetSubscription.objects.filter(payment=obj).select_related('subscription').first()
        if carnet is not None:
            data['type'] = 'Assegnazione carnet'
            data['conjunction'] = " con nome "
            data['name'] = carnet.carnet_id.title
            data['subscription'] = {
                'subscription_id': carnet.subscription.subscription_id,
                'start_date': carnet.subscription.start_date,
                'end_date': carnet.subscription.end_date,
            }
            return data
        course = CourseSubscription.objects.filter(payment=obj).select_related('subscription').first()
        if course is not None:
            data['type'] = 'Iscrizione '
            data['conjunction'] = " al corso "
            data['name'] = course.course.title
            data['subscription'] = {
                'subscription_id': course.subscription.subscription_id,
                'start_date': course.subscription.start_date,
                'end_date': course.subscription.end_date,
            }
            return data
        camp_and_retreats = CampsAndRetreatsSubscriptionPeriod.objects.filter(payment=obj).select_related(
            'camps_and_retreats_subscription__subscription',
        ).first()
        if camp_and_retreats is not None:
            data['type'] = 'Iscrizione '
            data['conjunction'] = " al campo "
            data['name'] = camp_and_retreats.camps_and_retreats_period.title
            data['subscription'] = {
                'subscription_id': camp_and_retreats.camps_and_retreats_subscription.subscription.subscription_id,
                'start_date': camp_and_retreats.camps_and_retreats_subscription.subscription.start_date,
                'end_date': camp_and_retreats.camps_and_retreats_subscription.subscription.end_date,
            }
            return data
        return data


class InvoiceSerializer(serializers.ModelSerializer):
    document_token = serializers.SerializerMethodField()
    def create(self, validated_data):
        instance = Invoice.objects.create(**validated_data)
        return instance

    class Meta:
        model = Invoice
        fields = '__all__'

    def get_document_token(self, obj):
        # check document exists
        if obj.document_pdf is None:
            return None
        return obj.document_pdf.token


class PaymentSerializer(serializers.ModelSerializer):
    user = UserSerializer()
    associate = AssociateSerializer()
    subscription_id = serializers.SerializerMethodField()
    instructor_id = serializers.SerializerMethodField()
    instructor = InstructorSerializer()
    supplier = SupplierSerializer()
    supplier_id = serializers.SerializerMethodField()
    custom_account_type = serializers.SerializerMethodField()
    custom_account_name = serializers.SerializerMethodField()
    invoice = InvoiceSerializer(required=False, allow_null=True)
    attachments = DocumentSerializer(many=True, read_only=True)
    payment_category_name = serializers.SerializerMethodField()

    def create(self, validated_data):
        instance = Payment.objects.create(**validated_data)
        return instance

    class Meta:
        model = Payment
        fields = '__all__'

    def get_instructor_id(self, obj):
        if obj.instructor:
            return obj.instructor.instructor_id
        else:
            return None

    def get_supplier_id(self, obj):
        if obj.supplier:
            return obj.supplier.supplier_id
        else:
            return None

    def get_subscription_id(self, obj):
        if obj.subject == Payment.SUBSCRIPTION:
            # get the subscription id
            try:
                sub = Subscription.objects.get(payment=obj)
                return sub.subscription_id
            except Subscription.DoesNotExist:
                return None
        elif obj.subject == Payment.COURSE:
            # get the course id
            try:
                sub = CourseSubscriptionInstallment.objects.get(payment=obj)
                return sub.course_subscription.subscription.subscription_id
            except CourseSubscriptionInstallment.DoesNotExist:
                try:
                    sub = CourseSubscription.objects.get(payment=obj)
                    return sub.subscription.subscription_id
                except CourseSubscription.DoesNotExist:
                    # check carnet
                    try:
                        sub = CarnetSubscription.objects.get(payment=obj)
                        return sub.subscription.subscription_id
                    except CarnetSubscription.DoesNotExist:
                        return None
        elif obj.subject == Payment.OTHER:
            try:
                sub = Subscription.objects.filter(
                    associate=obj.associate,
                    archived=False
                ).order_by('creation_date').first()
                if sub is None:
                    return None
                return sub.subscription_id
            except Subscription.DoesNotExist:
                return None
        else:
            return None

    def get_custom_account_type(self, obj):
        if obj.custom_accounts:
            return obj.custom_accounts.account_type
        else:
            return None
    def get_custom_account_name(self, obj):
        if obj.custom_accounts:
            return obj.custom_accounts.name
        else:
            return None

    def get_payment_category_name(self, obj):
        return obj.payment_category.name if obj.payment_category else None


class AssociateOptimizedSerializer(serializers.ModelSerializer):
    available_tutors = AssociateTutorRelationSerializer(source='tutor_relations', many=True, read_only=True)


    def create(self, validated_data):
        # Normalize tax_code to uppercase for consistent storage
        tax_code = validated_data.get('tax_code')
        if tax_code:
            validated_data['tax_code'] = tax_code.upper()

        # Use get_or_create with case-insensitive lookup to prevent duplicates
        instance, created = Associate.objects.get_or_create(
            tax_code__iexact=validated_data.get('tax_code'),
            sport_association=validated_data.get('sport_association'),
            defaults=validated_data
        )

        # Update if already exists
        if not created:
            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()

        return instance

    class Meta:
        model = Associate
        fields = ('associate_id',
                  'first_name',
                  'last_name',
                  'is_minor',
                  'available_tutors')


class PaymentInvoiceSerializer(serializers.ModelSerializer):
    user = UserSerializer()
    associate = AssociateOptimizedSerializer()
    supplier = SupplierSerializer()
    subscription_id = serializers.SerializerMethodField()

    def to_representation(self, instance):
        representation = super().to_representation(instance)

        # Convert user OrderedDict to dict if it exists
        if representation.get('user'):
            representation['user'] = dict(representation['user'])

        # Convert associate OrderedDict to dict if it exists
        if representation.get('associate'):
            representation['associate'] = dict(representation['associate'])

        return representation

    def create(self, validated_data):
        instance = Payment.objects.create(**validated_data)
        return instance

    class Meta:
        model = Payment
        fields = (
            'user',
            'associate',
            'supplier',
            'subscription_id',
            'payment_date',
            'course'
        )

    def get_subscription_id(self, obj):
        if obj.subject == Payment.SUBSCRIPTION:
            # get the subscription id
            try:
                sub = Subscription.objects.get(payment=obj)
                return sub.subscription_id
            except Subscription.DoesNotExist:
                return None
        elif obj.subject == Payment.COURSE:
            # get the course id
            try:
                sub = CourseSubscriptionInstallment.objects.get(payment=obj)
                return sub.course_subscription.subscription.subscription_id
            except CourseSubscriptionInstallment.DoesNotExist:
                try:
                    sub = CourseSubscription.objects.get(payment=obj)
                    return sub.subscription.subscription_id
                except CourseSubscription.DoesNotExist:
                    # check carnet
                    try:
                        sub = CarnetSubscription.objects.get(payment=obj)
                        return sub.subscription.subscription_id
                    except CarnetSubscription.DoesNotExist:
                        return None
        elif obj.subject == Payment.OTHER:
            try:
                sub = Subscription.objects.filter(
                    associate=obj.associate,
                    archived=False
                ).order_by('creation_date').first()
                if sub is None:
                    return None
                return sub.subscription_id
            except Subscription.DoesNotExist:
                return None
        else:
            return None


class PaymentOptimizedSerializer(serializers.ModelSerializer):
    associate = AssociateOptimizedSerializer()
    subscription_id = serializers.SerializerMethodField()
    instructor_id = serializers.SerializerMethodField()
    instructor = InstructorSerializer()
    supplier = SupplierSerializer()
    supplier_id = serializers.SerializerMethodField()
    custom_account_type = serializers.SerializerMethodField()
    custom_account_name = serializers.SerializerMethodField()
    invoice = InvoiceSerializer(required=False, allow_null=True)
    course = serializers.SerializerMethodField()
    attachments = DocumentSerializer(many=True, read_only=True)
    payment_category_name = serializers.SerializerMethodField()

    def create(self, validated_data):
        instance = Payment.objects.create(**validated_data)
        return instance

    class Meta:
        model = Payment
        fields = '__all__'

    def get_course(self, obj):
        if obj.subject == Payment.COURSE:
            try:
                sub = CourseSubscription.objects.get(payment=obj)
                return {
                    'title': sub.course.title,
                    'course_id': sub.course.course_id
                }
            except CourseSubscription.DoesNotExist:
                try:
                    sub = CourseSubscriptionInstallment.objects.get(payment=obj)
                    return {
                        'title': sub.course_subscription.course.title,
                        'course_id': sub.course_subscription.course.course_id
                    }
                except CourseSubscriptionInstallment.DoesNotExist:
                    if obj.course:
                        return obj.course
                    return None
        else:
            return None

    def get_instructor_id(self, obj):
        if obj.instructor:
            return obj.instructor.instructor_id
        else:
            return None

    def get_supplier_id(self, obj):
        if obj.supplier:
            return obj.supplier.supplier_id
        else:
            return None

    def get_subscription_id(self, obj):
        if obj.subject == Payment.SUBSCRIPTION:
            # get the subscription id
            try:
                sub = Subscription.objects.get(payment=obj, deleted=False)
                return sub.subscription_id
            except Subscription.DoesNotExist:
                try:
                    sub = Subscription.objects.filter(
                        associate=obj.associate,
                        deleted=False,
                        archived=False
                    ).order_by('creation_date').first()

                    if sub is None:
                        return None

                    return sub.subscription_id
                except Exception as e:
                    return None
                return None
        elif obj.subject == Payment.COURSE:
            # get the course id
            try:
                sub = CourseSubscriptionInstallment.objects.get(payment=obj)
                return sub.course_subscription.subscription.subscription_id
            except CourseSubscriptionInstallment.DoesNotExist:
                try:
                    sub = CourseSubscription.objects.get(payment=obj)
                    return sub.subscription.subscription_id
                except CourseSubscription.DoesNotExist:
                    # check carnet
                    try:
                        sub = CarnetSubscription.objects.get(payment=obj)
                        return sub.subscription.subscription_id
                    except CarnetSubscription.DoesNotExist:
                        return None
        elif obj.subject == Payment.OTHER:
            try:
                sub = Subscription.objects.filter(
                    associate=obj.associate,
                    archived=False
                ).order_by('creation_date').first()
                if sub is None:
                    return None
                return sub.subscription_id
            except Subscription.DoesNotExist:
                return None
        else:
            return None

    def get_custom_account_type(self, obj):
        if obj.custom_accounts:
            return obj.custom_accounts.account_type
        else:
            return None
    def get_custom_account_name(self, obj):
        if obj.custom_accounts:
            return obj.custom_accounts.name
        else:
            return None

    def get_payment_category_name(self, obj):
        return obj.payment_category.name if obj.payment_category else None


class PaymentSerializerInfo(serializers.ModelSerializer):
    user = UserSerializer()
    associate = AssociateSerializerInfo()

    def create(self, validated_data):
        instance = Payment.objects.create(**validated_data)
        return instance

    class Meta:
        model = Payment
        fields = (
            'user',
            'associate',
            'subject',
            'amount',
            'sport_association',
            'creation_date',
            'course'
        )


class PaymentSubscriptionSerializer(serializers.ModelSerializer):

    def create(self, validated_data):
        instance = Payment.objects.create(**validated_data)
        return instance

    class Meta:
        model = Payment
        fields = '__all__'


class VatManagementSerializer(serializers.ModelSerializer):
    class Meta:
        model = VatManagement
        fields = '__all__'


class PaymentCategorySerializer(serializers.ModelSerializer):
    vat_management = VatManagementSerializer()

    def create(self, validated_data):
        # Get and validate vat management data
        vat_management_data = validated_data.pop('vat_management')
        vat_management_serializer = VatManagementSerializer(data=vat_management_data)
        vat_management_serializer.is_valid(raise_exception=True)

        # Check if vat management exists or create it
        vat_management_instance = VatManagement.objects.filter(
            vat_management_id=vat_management_data.get('vat_management_id')
        ).first()
        if vat_management_instance is None:
            vat_management_instance = VatManagement.objects.create(**vat_management_data)

        # Create payment category
        validated_data['vat_management'] = vat_management_instance
        instance = PaymentCategory.objects.create(**validated_data)
        return instance

    def update(self, instance, validated_data):
        vat_management_data = validated_data.pop('vat_management', None)
        if vat_management_data:
            vat_management_serializer = VatManagementSerializer(data=vat_management_data)
            vat_management_serializer.is_valid(raise_exception=True)

            vat_management_instance = VatManagement.objects.filter(
                vat_management_id=vat_management_data.get('vat_management_id')
            ).first()
            if vat_management_instance is None:
                vat_management_instance = VatManagement.objects.create(**vat_management_data)
            instance.vat_management = vat_management_instance

        # Update other fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance

    class Meta:
        model = PaymentCategory
        fields = '__all__'
