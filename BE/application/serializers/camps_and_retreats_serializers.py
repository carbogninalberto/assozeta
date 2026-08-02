from rest_framework import serializers

from application.models import CampsAndRetreats, CampsAndRetreatsPeriod, CampsAndRetreatsPeriodsService, \
    CampsAndRetreatsSubscriptionPeriod, CampsAndRetreatsSubscription, Subscription, Payment
from application.serializers.payment_serializers import PaymentCategorySerializer, PaymentSerializer
from application.serializers.subscriptions_serializers import SubscriptionBasicSerializer


class CampsAndRetreatsSerializer(serializers.ModelSerializer):

    def create(self, validated_data):
        instance = CampsAndRetreats.objects.create(**validated_data)
        return instance

    def save(self, **kwargs):
        return super().save(**kwargs)

    class Meta:
        model = CampsAndRetreats
        fields = '__all__'


class CampsAndRetreatsPeriodSerializer(serializers.ModelSerializer):

    def create(self, validated_data):
        instance = CampsAndRetreatsPeriod.objects.create(**validated_data)
        return instance

    def save(self, **kwargs):
        return super().save(**kwargs)

    def validate_max_participants(self, value):
        if value == "" or value is None:
            return None
        if not isinstance(value, int) or value < 0:
            raise serializers.ValidationError("Max participants must be a non-negative integer or null.")
        return value

    def validate_fee(self, value):
        try:
            return float(value)
        except (TypeError, ValueError):
            raise serializers.ValidationError("Fee must be a valid number.")

    def to_internal_value(self, data):
        # Preprocess 'fee' field
        if 'fee' in data and data['fee'] == '':
            data['fee'] = None

        # Preprocess 'max_participants' field
        if 'max_participants' in data and data['max_participants'] == '':
            data['max_participants'] = None
        return super().to_internal_value(data)

    class Meta:
        model = CampsAndRetreatsPeriod
        fields = '__all__'


class CampsAndRetreatsPeriodsServiceAddSerializer(serializers.ModelSerializer):

        class Meta:
            model = CampsAndRetreatsPeriodsService
            fields = '__all__'


class CampsAndRetreatsPeriodsServiceSerializer(serializers.ModelSerializer):
    payment_category_info = serializers.SerializerMethodField()
    fee = serializers.SerializerMethodField()

    @staticmethod
    def get_fee(obj):
        return str(obj.fee).replace('.', ',')

    @staticmethod
    def get_payment_category_info(obj):
        payment_category = obj.payment_category
        return PaymentCategorySerializer(payment_category).data

    def create(self, validated_data):
        instance = CampsAndRetreatsPeriodsService.objects.create(**validated_data)
        return instance

    def save(self, **kwargs):
        return super().save(**kwargs)

    class Meta:
        model = CampsAndRetreatsPeriodsService
        fields = '__all__'


class CampsAndRetreatsSubscriptionPeriodSerializer(serializers.ModelSerializer):

    def create(self, validated_data):
        instance = CampsAndRetreatsSubscriptionPeriod.objects.create(**validated_data)
        return instance

    def save(self, **kwargs):
        return super().save(**kwargs)

    class Meta:
        model = CampsAndRetreatsSubscriptionPeriod
        fields = '__all__'


class CampsAndRetreatsSubscriptionSerializer(serializers.ModelSerializer):

    def create(self, validated_data):
        instance = CampsAndRetreatsSubscription.objects.create(**validated_data)
        return instance

    def save(self, **kwargs):
        return super().save(**kwargs)

    class Meta:
        model = CampsAndRetreatsSubscription
        fields = '__all__'


# INFO serializers


class CampsAndRetreatsPeriodsServiceInfoSerializer(serializers.ModelSerializer):
    services = serializers.SerializerMethodField()
    fee = serializers.SerializerMethodField()

    @staticmethod
    def get_fee(obj):
        return str(obj.fee).replace('.', ',')

    @staticmethod
    def get_services(obj):
        services = CampsAndRetreatsPeriodsService.objects.filter(camps_and_retreats_period=obj)
        return CampsAndRetreatsPeriodsServiceSerializer(services, many=True).data

    class Meta:
        model = CampsAndRetreatsPeriodsService
        fields = '__all__'


class CampsAndRetreatsInfoSerializer(serializers.ModelSerializer):
    periods = serializers.SerializerMethodField()

    @staticmethod
    def get_periods(obj):
        periods = CampsAndRetreatsPeriod.objects.filter(camps_and_retreats=obj).order_by('start_date')
        return CampsAndRetreatsPeriodInfoSerializer(periods, many=True).data

    class Meta:
        model = CampsAndRetreats
        fields = '__all__'


class CampsAndRetreatsPublicInfoSerializer(serializers.ModelSerializer):
    periods = serializers.SerializerMethodField()
    sport_association = serializers.SerializerMethodField()

    @staticmethod
    def get_periods(obj):
        periods = CampsAndRetreatsPeriod.objects.filter(camps_and_retreats=obj).order_by('start_date')
        return CampsAndRetreatsPeriodInfoSerializer(periods, many=True).data

    @staticmethod
    def get_sport_association(obj):
        sport_association = obj.sport_association

        return {
            'sport_association_id': str(sport_association.sport_association_id),
            'denomination': sport_association.denomination,
            'username': sport_association.user.username,
        }

    class Meta:
        model = CampsAndRetreats
        fields = '__all__'


class CampsAndRetreatsPeriodInfoSerializer(serializers.ModelSerializer):
    start_date = serializers.DateTimeField(format="%d/%m/%Y")
    end_date = serializers.DateTimeField(format="%d/%m/%Y")
    fee = serializers.SerializerMethodField()
    services = serializers.SerializerMethodField()

    @staticmethod
    def get_fee(obj):
        return str(obj.fee).replace('.', ',')

    @staticmethod
    def get_services(obj):
        services = CampsAndRetreatsPeriodsService.objects.filter(camps_and_retreats_period=obj).order_by('-created_at')
        return CampsAndRetreatsPeriodsServiceSerializer(services, many=True).data

    class Meta:
        model = CampsAndRetreatsPeriod
        fields = '__all__'


class CampsAndRetreatsSubscriptionInfoSerializer(serializers.ModelSerializer):
    # camps_and_retreats = CampsAndRetreatsInfoSerializer(read_only=True)
    # subscription = serializers.SerializerMethodField()
    periods = serializers.SerializerMethodField()

    def create(self, validated_data):
        instance = CampsAndRetreatsSubscription.objects.create(**validated_data)
        return instance

    def save(self, **kwargs):
        # TODO: update the subscription with all the other fields and the payments for the periods
        return super().save(**kwargs)

    # @staticmethod
    # def get_subscription(obj):
    #     subscription = Subscription.objects.get(subscription_id=obj.subscription_id)
    #     return SubscriptionBasicSerializer(subscription).data

    @staticmethod
    def get_periods(obj):
        periods = CampsAndRetreatsSubscriptionPeriod.objects.filter(camps_and_retreats_subscription=obj)
        return CampsAndRetreatsSubscriptionPeriodSerializer(periods, many=True).data

    class Meta:
        model = CampsAndRetreatsSubscription
        fields = '__all__'


class CampsAndRetreatsSubscriptionPeriodInfoSerializer(serializers.ModelSerializer):
    payment = PaymentSerializer(read_only=True)
    camps_and_retreats_period = CampsAndRetreatsPeriodInfoSerializer(read_only=True)
    camps_and_retreats_period_services = serializers.SerializerMethodField()

    @staticmethod
    def get_camps_and_retreats_period_services(obj):
        services = CampsAndRetreatsPeriodsService.objects.filter(camps_and_retreats_period=obj.camps_and_retreats_period)
        return CampsAndRetreatsPeriodsServiceSerializer(services, many=True).data

    class Meta:
        model = CampsAndRetreatsSubscriptionPeriod
        fields = '__all__'


class CampsAndRetreatsSubscriptionListInfoSerializer(serializers.ModelSerializer):
    # camps_and_retreats = CampsAndRetreatsInfoSerializer(read_only=True)
    subscription = serializers.SerializerMethodField()
    periods = serializers.SerializerMethodField()
    selected_periods = serializers.SerializerMethodField()
    selected_services = serializers.SerializerMethodField()
    paid_periods = serializers.SerializerMethodField()

    @staticmethod
    def get_subscription(obj):
        subscription = Subscription.objects.get(subscription_id=obj.subscription_id)
        return SubscriptionBasicSerializer(subscription).data

    @staticmethod
    def get_periods(obj):
        periods = CampsAndRetreatsSubscriptionPeriod.objects.filter(camps_and_retreats_subscription=obj)
        return CampsAndRetreatsSubscriptionPeriodInfoSerializer(periods, many=True).data

    @staticmethod
    def get_selected_periods(obj):
        periods = CampsAndRetreatsSubscriptionPeriod.objects.filter(camps_and_retreats_subscription=obj)
        # return a dict with key = period_id and value a boolean
        return {str(period.camps_and_retreats_period_id): True for period in periods}

    @staticmethod
    def get_selected_services(obj):
        periods = CampsAndRetreatsSubscriptionPeriod.objects.filter(camps_and_retreats_subscription=obj)
        # return a dict with key = period_id and value a dict with key a service_id and value a boolean
        selected_periods = {str(period.camps_and_retreats_period_id): {} for period in periods}
        for period in periods:
            services = period.camps_and_retreats_period_services.all()
            selected_periods[str(period.camps_and_retreats_period_id)] = {str(service.camps_and_retreats_period_service_id): True for service in services}
        return selected_periods

    @staticmethod
    def get_paid_periods(obj):
        payments = Payment.objects.filter(associate=obj.subscription.associate_id, paid=True)
        periods = CampsAndRetreatsSubscriptionPeriod.objects.filter(payment__in=payments)
        return {str(period.camps_and_retreats_period_id): True for period in periods}

    class Meta:
        model = CampsAndRetreatsSubscription
        fields = '__all__'