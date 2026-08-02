import base64
import os
from decimal import Decimal
from io import BytesIO

from django.core.files.storage import default_storage
from rest_framework import serializers

from application.models import Subscription, Payment
from application.models.courses_models import Course, CourseSubscription, CourseSubscriptionInstallment, CourseLocation
from application.serializers.auth_serializers import SportAssociationBasicInfo
from application.serializers.payment_serializers import PaymentSerializer
from application.serializers.subscriptions_serializers import SubscriptionSerializer, SubscriptionSerializerSimplify
from core.settings import STORAGE_DIR
from docmanager.models import Document


class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = '__all__'

class CourseLocationSerializer(serializers.ModelSerializer):
    documents = DocumentSerializer(many=True, read_only=True)
    documents_data = serializers.ListField(
        child=serializers.DictField(), write_only=True, required=False
    )

    class Meta:
        model = CourseLocation
        fields = '__all__'

    def create(self, validated_data):
        documents_data = validated_data.pop('documents_data', [])
        course_location = super().create(validated_data)
        self._handle_documents(course_location, documents_data)
        return course_location

    def update(self, instance, validated_data):
        documents_data = validated_data.pop('documents_data', [])
        instance = super().update(instance, validated_data)
        self._handle_documents(instance, documents_data)
        return instance

    def _handle_documents(self, course_location, documents_data):
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
                course_location.documents.add(document)
            else:
                raise serializers.ValidationError({'exception': 'document key not present'})


class CourseLocationSimpleSerializer(serializers.ModelSerializer):

    class Meta:
        model = CourseLocation
        fields = (
            'course_location_id',
            'title',
            'address',
        )

class AthleteSerializer(serializers.ModelSerializer):
    subscription = SubscriptionSerializer(allow_null=True)

    class Meta:
        model = CourseSubscription
        fields = (
            'paid',
            'status_flag',
            'creation_date',
            'subscription',
        )


class CourseSerializer(serializers.ModelSerializer):
    athletes = AthleteSerializer(allow_null=True, many=True)
    tags = serializers.SerializerMethodField()
    locations = CourseLocationSimpleSerializer(many=True, read_only=True)

    def get_tags(self, obj):
        return [{'tag_id': tag.tag_id, 'tag_name': tag.tag_name} for tag in obj.tags.all()]


    def create(self, validated_data):
        instance = Course.objects.create(**validated_data)
        return instance

    class Meta:
        model = Course
        fields = '__all__'


class CourseDetailSerializer(serializers.ModelSerializer):
    # installment = serializers.SerializerMethodField()

    def create(self, validated_data):
        instance = Course.objects.create(**validated_data)
        return instance

    class Meta:
        model = Course
        fields = '__all__'


class CourseSerializerInfo(serializers.ModelSerializer):
    sport_association = SportAssociationBasicInfo()

    def create(self, validated_data):
        instance = Course.objects.create(**validated_data)
        return instance

    class Meta:
        model = Course
        fields = (
            'title',
            'description',
            'fee',
            'sport_association'
        )


class CourseSubscriptionSerializer(serializers.ModelSerializer):
    course = CourseSerializer()

    def create(self, validated_data):
        instance = CourseSubscription.objects.create(**validated_data)
        return instance

    class Meta:
        model = CourseSubscription
        fields = '__all__'


class CourseSimpleSerializer(serializers.ModelSerializer):

    class Meta:
        model = Course
        fields = (
            'course_id',
            'title',
            'description',
        )

class CourseSubscriptionOverviewSerializer(serializers.ModelSerializer):
    subscription = SubscriptionSerializerSimplify(read_only=True)
    installments = serializers.SerializerMethodField()
    course = serializers.PrimaryKeyRelatedField(queryset=Course.objects.all())
    subscription_id = serializers.UUIDField(write_only=True)
    multiple_quote = serializers.JSONField(required=False, allow_null=True)
    membership_payments = PaymentSerializer(many=True, read_only=True)
    events = serializers.JSONField(required=False, write_only=True, allow_null=True)
    medical_label = serializers.SerializerMethodField()
    carnets = serializers.SerializerMethodField()

    def get_medical_label(self, obj):
        if hasattr(obj, 'subscription') and obj.subscription:
            return obj.subscription.get_medical_label()
        return None

    # update the course subscription
    def update(self, instance, validated_data):
        # check for updates
        # loop through the events if they are present
        events = validated_data.pop('events', None)

        if events is not None:
            # get all the installments
            installments = CourseSubscriptionInstallment.objects.filter(course_subscription=instance)
            # delete the one that are not in the events and create the new ones that are not in the installments
            for installment in installments:
                # check if installment is in the events
                if not any(event['id'] == installment.id for event in events):
                    if installment.payment and installment.payment.paid:
                        installment.payment.description =  f"Rata n.{int(installment.id) + 1} del {installment.payment_date.strftime('%d/%m/%y')} ({instance.course.title})"
                        installment.payment.meta = {
                            'description': f"Rata n.{int(installment.id) + 1} del {installment.payment_date.strftime('%d/%m/%y')} ({instance.course.title})",
                            'course_id': str(instance.course.course_id),
                            'course_title': str(instance.course.title),
                            'course_subscription_id': str(instance.course_subscription_id),
                            'payment_date': str(installment.payment_date),
                        }
                        installment.payment.save()
                    installment.delete()
            for event in events:
                # if there is not course_subscription_installment_id create a new one
                if 'course_subscription_installment_id' not in event:
                    course_event = CourseEventsSerializer(data=event)
                    course_event.is_valid(raise_exception=True)
                    event = course_event.validated_data

                    payment = Payment.objects.create(
                        user=instance.course.sport_association.user,
                        associate=instance.subscription.associate,
                        description=f"Rata n.{int(event['id']) + 1} del {event['payment_date'].strftime('%d/%m/%y')} ({instance.course.title})",
                        amount=event['amount'],
                        subject=Payment.COURSE,
                        creation_date=event['payment_date'],
                        sport_association=instance.course.sport_association,
                        meta = {
                            'description': f"Rata n.{int(event['id']) + 1} del {event['payment_date'].strftime('%d/%m/%y')} ({instance.course.title})",
                            'course_id': str(instance.course.course_id),
                            'course_title': str(instance.course.title),
                            'course_subscription_id': str(instance.course_subscription_id),
                            'payment_date': str(event['payment_date']),
                        }
                    )

                    CourseSubscriptionInstallment.objects.create(
                        course_subscription=instance,
                        amount=event['amount'],
                        id=event['id'],
                        payment_date=event['payment_date'],
                        payment=payment,
                    )


        return super().update(instance, validated_data)

    def create(self, validated_data):
        # Get the subscription from the ID
        subscription_id = validated_data.pop('subscription_id')  # Remove it from validated_data
        subscription = Subscription.objects.get(subscription_id=subscription_id)

        # Set the type based on the associated course's type
        course = validated_data.get('course')
        if course:
            validated_data['type'] = course.course_type
        # Set the subscription
        validated_data['subscription'] = subscription

        # events
        events = validated_data.pop('events', None)

        instance = CourseSubscription.objects.create(**validated_data)

        # Create the selected installments
        if events:
            for e in events:
                course_event = CourseEventsSerializer(data=e)
                course_event.is_valid(raise_exception=True)
                event = course_event.validated_data

                payment = Payment.objects.create(
                    user=instance.course.sport_association.user,
                    associate=instance.subscription.associate,
                    amount=event['amount'],
                    subject=Payment.COURSE,
                    description=f"Rata n.{int(event['id']) + 1} del {event['payment_date'].strftime('%d/%m/%y')} ({instance.course.title})",
                    creation_date=event['payment_date'],
                    sport_association=instance.course.sport_association,
                    meta = {
                        'description': f"Rata n.{int(event['id']) + 1} del {event['payment_date'].strftime('%d/%m/%y')} ({instance.course.title})",
                        'course_id': str(instance.course.course_id),
                        'course_title': str(instance.course.title),
                        'course_subscription_id': str(instance.course_subscription_id),
                        'payment_date': str(event['payment_date']),
                    }
                )

                CourseSubscriptionInstallment.objects.create(
                    course_subscription=instance,
                    amount=event['amount'],
                    id=event['id'],
                    payment_date=event['payment_date'],
                    payment=payment,
                )
        elif instance.course.multi_payments:
            for e in course.events:
                course_event = CourseEventsSerializer(data=e)
                course_event.is_valid(raise_exception=True)
                event = course_event.validated_data

                if event['payment_date'] >= instance.creation_date.date() or \
                        instance.course.sport_association.user.full_installments_plan:
                    payment = Payment.objects.create(
                        user=instance.course.sport_association.user,
                        associate=instance.subscription.associate,
                        amount=event['amount'],
                        subject=Payment.COURSE,
                        creation_date=event['payment_date'],
                        description=f"Rata n.{int(event['id']) + 1} del {event['payment_date'].strftime('%d/%m/%y')} ({instance.course.title})",
                        sport_association=instance.course.sport_association,
                        meta = {
                            'description': f"Rata n.{int(event['id']) + 1} del {event['payment_date'].strftime('%d/%m/%y')} ({instance.course.title})",
                            'course_id': str(instance.course.course_id),
                            'course_title': str(instance.course.title),
                            'course_subscription_id': str(instance.course_subscription_id),
                            'payment_date': str(event['payment_date']),
                        }
                    )

                    CourseSubscriptionInstallment.objects.create(
                        course_subscription=instance,
                        amount=event['amount'],
                        id=event['id'],
                        payment_date=event['payment_date'],
                        payment=payment,
                    )
        return instance

    def get_installments(self, obj):
        installments = CourseSubscriptionInstallment.objects.filter(course_subscription=obj)
        # convert datetime to date
        for installment in installments:
            installment.payment_date = installment.payment_date.date()
        return CourseEventsSerializer(installments, many=True).data

    def get_carnets(self, obj):
        """
        Return associated carnets for this course subscription.
        Returns list of carnet info or empty list if none.
        """
        from application.models.carnet_models import CarnetSubscription

        carnet_subs = CarnetSubscription.objects.filter(
            course_subscription=obj,
            disabled=False
        ).select_related('carnet_id')

        if not carnet_subs.exists():
            return []

        result = []
        for cs in carnet_subs:
            lessons_total = cs.carnet_id.lessons_number if cs.carnet_id else 0
            lessons_left = cs.meta.get('lessons_left', lessons_total) if cs.meta else lessons_total
            lessons_used = lessons_total - lessons_left if lessons_total else 0

            result.append({
                'carnet_subscription_id': str(cs.carnet_subscription_id),
                'carnet_id': str(cs.carnet_id.carnet_id) if cs.carnet_id else None,
                'title': cs.carnet_id.title if cs.carnet_id else None,
                'lessons_total': lessons_total,
                'lessons_left': lessons_left,
                'lessons_used': lessons_used,
                'lessons_display': f"{lessons_left}/{lessons_total}" if lessons_total else None,
                'creation_date': cs.creation_date.strftime('%d/%m/%Y') if cs.creation_date else None,
            })

        return result

    class Meta:
        model = CourseSubscription
        fields = (
            'course_subscription_id',
            'subscription_id',
            'creation_date',
            'paid',
            'subscription',
            'multi_payments',
            'multiple_quote',
            'one_fee_payment',
            'installments',
            'course',
            'type',
            'billed_frequency',
            'billed_from',
            'billed_until',
            'billed_from_day_of_month',
            'membership_active',
            'membership_fee',
            'auto_renewal',
            'membership_payments',
            'events',
            'medical_label',
            'carnets',
        )
        read_only_fields = ('type',)

class CourseEventsSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(min_value=0, max_value=30, required=True)
    amount = serializers.DecimalField(max_digits=9, decimal_places=2, min_value=0.00, required=True)
    payment_date = serializers.DateField(required=True)
    paid = serializers.SerializerMethodField()

    def get_paid(self, obj):
        return obj.payment is not None and obj.payment.paid

    def create(self, validated_data):
        instance = CourseSubscriptionInstallment.objects.create(**validated_data)
        return instance

    class Meta:
        model = CourseSubscriptionInstallment
        fields = (
            'course_subscription_installment_id',
            'id',
            'amount',
            'payment_date',
            'paid',
        )


class CourseAddSerializer(serializers.ModelSerializer):
    fee = serializers.DecimalField(max_digits=9, decimal_places=2, default=0.00, min_value=0.00)
    multi_payments_split = serializers.BooleanField(default=False, source='multi_payments')
    events = serializers.JSONField(default=None, required=False)
    multiple_quotes = serializers.JSONField(default=None, required=False, allow_null=True)
    one_fee = serializers.DecimalField(max_digits=9, decimal_places=2, default=0.00, min_value=0.00, required=False)
    one_fee_payment = serializers.BooleanField(default=False, required=False)


    def create(self, validated_data):
        instance = Course.objects.create(**validated_data)
        return instance

    def save(self, **kwargs):
        return super().save(**kwargs)

    class Meta:
        model = Course
        fields = (
            'title',
            'description',
            'fee',
            'one_fee',
            'one_fee_payment',
            'multi_payments_split',
            'events',
            'multiple_quotes',
            'course_type',
            'billed_duration_is_sport_season',
            'billed_frequency',
            'billed_from_subscription_date',
            'billed_from_day_of_month',
            'auto_renewal',
        )

    def is_valid(self, raise_exception=False):
        valid = super().is_valid(raise_exception=raise_exception)
        if valid:
            if 'multi_payments' in self.validated_data:
                if self.validated_data['multi_payments'] and not self.validated_data['events']:
                    self._errors['events'] = ['This field is required.']
                    return False
                if not self.validated_data['multi_payments'] and self.validated_data['events']:
                    self._errors['events'] = ['This field is not allowed.']
                    return False
                if self.validated_data['multi_payments'] and self.validated_data['events']:
                    total_amount = 0
                    if len(self.validated_data['events']) > 30:
                        self._errors['events'] = ['The maximum number of events is 30.']
                        return False
                    if len(self.validated_data['events']) < 2:
                        self._errors['events'] = ['The minimum number of events is 2.']
                        return False
                    for event in self.validated_data['events']:
                        if CourseEventsSerializer(data=event).is_valid(raise_exception=True):
                            amount = Decimal(event['amount'])
                            if amount > self.validated_data['fee']:
                                self._errors['events'] = ['The amount of the event cannot be greater than the course '
                                                          'fee.']
                                return False
                            total_amount += amount
                    if total_amount != self.validated_data['fee']:
                        self._errors['events'] = ['The sum of the events amount must be equal to the course fee.']
                        return False
            if self.validated_data['fee'] < 0:
                self._errors['fee'] = ['The course fee cannot be negative.']
                return False
            if 'one_fee_payment' in self.validated_data and 'one_fee' not in self.validated_data:
                self._errors['one_fee'] = ['The course one_fee must be defined if one_fee_payment is true']
                return False
            if 'one_fee' in self.validated_data \
                    and self.validated_data['one_fee_payment'] \
                    and self.validated_data['one_fee'] < 0:
                self._errors['one_fee'] = ['The course one_fee cannot be negative']
                return False
        return valid
