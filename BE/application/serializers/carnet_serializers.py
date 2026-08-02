
from rest_framework import serializers

from application.models import Carnet
from application.models.carnet_models import CarnetSubscription
from application.models.user_models import SportAssociation
from application.serializers.subscriptions_serializers import SubscriptionSerializerSimplify


class SportAssociationSerializer(serializers.ModelSerializer):
    class Meta:
        model = SportAssociation


class CarnetAddSerializer(serializers.ModelSerializer):
    fee = serializers.DecimalField(max_digits=9, decimal_places=2, min_value=0.00, required=True)
    title = serializers.CharField(max_length=255, required=True)
    description = serializers.CharField(max_length=1024, required=True)
    lessons_number = serializers.IntegerField(min_value=1, required=True)
    meta = serializers.JSONField(default=None, required=False)

    def create(self, validated_data):
        instance = Carnet.objects.create(**validated_data)
        return instance

    def save(self, **kwargs):
        return super().save(**kwargs)

    class Meta:
        model = Carnet
        fields = (
            'title',
            'description',
            'lessons_number',
            'fee',
            'meta'
        )

    def is_valid(self, raise_exception=False):
        valid = super().is_valid(raise_exception=raise_exception)
        if valid:
            if 'meta' in self.validated_data:
                if self.validated_data['meta'] and not isinstance(self.validated_data['meta'], dict):
                    self._errors['meta'] = ['This field must be a dictionary.']
                    return False
        return valid


class CarnetUpdateSerializer(serializers.ModelSerializer):
    fee = serializers.DecimalField(max_digits=9, decimal_places=2, min_value=0.00, required=True)
    title = serializers.CharField(max_length=255, required=True)
    description = serializers.CharField(max_length=1024, required=True)
    lessons_number = serializers.IntegerField(min_value=1, required=True)
    public = serializers.BooleanField(required=True)

    class Meta:
        model = Carnet
        fields = (
            'title',
            'description',
            'lessons_number',
            'fee',
            'public'
        )


class CarnetListSerializer(serializers.ModelSerializer):
    carnet_id = serializers.UUIDField()
    fee = serializers.DecimalField(max_digits=9, decimal_places=2, default=0.00, min_value=0.00)
    title = serializers.CharField(max_length=255, required=True)
    description = serializers.CharField(max_length=1024, required=True)
    lessons_number = serializers.IntegerField(min_value=1, required=True)
    creation_date = serializers.DateTimeField(required=True)
    public = serializers.BooleanField(required=True)
    meta = serializers.JSONField(default=None, required=False)

    class Meta:
        model = Carnet
        fields = (
            'carnet_id',
            'title',
            'description',
            'lessons_number',
            'creation_date',
            'fee',
            'public',
            'meta'
        )


class CarnetListInfoSerializer(serializers.ModelSerializer):
    carnet_id = serializers.UUIDField()
    fee = serializers.DecimalField(max_digits=9, decimal_places=2, default=0.00, min_value=0.00)
    title = serializers.CharField(max_length=255, required=True)
    description = serializers.CharField(max_length=1024, required=True)
    lessons_number = serializers.IntegerField(min_value=1, required=True)
    creation_date = serializers.DateTimeField(required=True)
    public = serializers.BooleanField(required=True)

    class Meta:
        model = Carnet
        fields = (
            'carnet_id',
            'title',
            'description',
            'lessons_number',
            'creation_date',
            'fee',
            'public',
        )


class CarnetSubscriptionSerializer(serializers.ModelSerializer):

    carnet_subscription_id = serializers.UUIDField()
    carnet_id = serializers.UUIDField()
    subscription = SubscriptionSerializerSimplify()
    course = serializers.SerializerMethodField()
    creation_date = serializers.DateTimeField(required=True)
    payment = serializers.UUIDField(required=False)
    meta = serializers.JSONField(default=None, required=False)
    carnet = serializers.SerializerMethodField()

    def get_carnet(self, obj):
        return {
            "carnet_id": obj.carnet_id.carnet_id,
            "title": obj.carnet_id.title,
        }

    class Meta:
        model = CarnetSubscription
        fields = (
            'carnet_subscription_id',
            'user_id',
            'carnet',
            'subscription',
            'course_subscription',
            'course',
            'carnet_id',
            'creation_date',
            'payment',
            'meta'
        )

    def get_course(self, obj):
        if obj.course_subscription:
            courses = []
            for course_sub in obj.course_subscription.all():
                courses.append({
                    "course_id": course_sub.course.course_id,
                    "course_subscription_id": course_sub.course_subscription_id,
                    "course_title": course_sub.course.title
                })
            return courses
        return None


class CarnetSubscriptionAddSerializer(serializers.ModelSerializer):

    carnet_subscription_id = serializers.UUIDField()
    user_id = serializers.UUIDField()
    carnet_id = serializers.UUIDField()
    subscription_id = serializers.UUIDField()

    class Meta:
        model = CarnetSubscription
        fields = (
            'carnet_subscription_id',
            'user_id',
            'carnet_id',
            'subscription_id'
        )

    def create(self, validated_data):
        instance = CarnetSubscription.objects.create(**validated_data)
        return instance

    def save(self, **kwargs):
        return super().save(**kwargs)

    def is_valid(self, raise_exception=False):
        valid = super().is_valid(raise_exception=raise_exception)
        return valid
