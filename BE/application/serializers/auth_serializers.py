import secrets
from datetime import timedelta
from django.utils import timezone

from rest_framework import serializers

from application.models import BillingSubscription, Group
from application.models.user_models import SportAssociationMembershipCardConfiguration, User, SportAssociation, UserAccount, \
    UsersOnboarding
from instance.models import InstanceConfiguration


class SportAssociationSerializer(serializers.ModelSerializer):
    reviewed = serializers.SerializerMethodField()
    groups = serializers.SerializerMethodField()

    def get_groups(self, obj):
        groups = Group.objects.filter(sport_association=obj)
        return [{"label": group.name, "value": group.group_id} for group in groups]

    def get_reviewed(self, obj):
        # if user date joined is within 5 days of today, return true (use django timezone)
        if obj.user.date_joined > (timezone.now() - timedelta(days=5)):
            return True
        return obj.reviewed

    class Meta:
        model = SportAssociation
        fields = ('denomination',
                  'tax_code',
                  'address',
                  'address_cap',
                  'address_city',
                  'regulation',
                  'demand',
                  'logo',
                  'subscription_fee',
                  'multiple_subscription_fee',
                  'subscription_fee_plans',
                  'membership_fee',
                  'multiple_membership_fee',
                  'membership_fee_plans',
                  'custom_subscription_form_data',
                  'document_header',
                  'invoice_footer',
                  'enable_quotes_management',
                  'configuration',
                  'reviewed',
                  'affiliate_code',
                  'sport_association_id',
                  'additional_sections',
                  'president_signature',
                  'stamp',
                  'president_first_name',
                  'president_last_name',
                  'federation',
                  'enroll_number',
                  'sport',
                  'enabled_for',
                  'checkout_info',
                  'additional_fields',
                  'stripe_available_methods',
                  'iban',
                  'website',
                  'abbreviated',
                  'vat_number',
                  'whatsapp',
                  'invoice_template',
                  'subscription_template',
                  'extra_text_invoices',
                  'imported_from_associami',
                  'groups',
                  'show_regulation_to_members',
                  'show_regulation_to_both',
                  'show_regulation_to_athletes',
                  'show_demand_to_members',
                  'show_demand_to_both',
                  'show_demand_to_athletes',
                  )


class SportAssociationOnboardingStep0Serializer(serializers.ModelSerializer):
    class Meta:
        model = SportAssociation
        fields = (
            'denomination',
            'tax_code',
            'tax_code',
            'logo',
            'sport',
            'subscription_fee',
            'membership_fee'
        )



class SportAssociationToolSerializer(serializers.ModelSerializer):
    date_joined = serializers.DateTimeField(source='user.date_joined', read_only=True)
    last_login = serializers.DateTimeField(source='user.last_login', read_only=True)
    user_id = serializers.CharField(source='user.user_id', read_only=True)
    email = serializers.CharField(source='user.email', read_only=True)
    username = serializers.CharField(source='user.username', read_only=True)
    first_name = serializers.CharField(source='user.first_name', read_only=True)
    last_name = serializers.CharField(source='user.last_name', read_only=True)
    phone = serializers.CharField(source='user.phone', read_only=True)
    role = serializers.CharField(source='user.lead_sport_association_role', read_only=True)
    size = serializers.CharField(source='user.lead_sport_association_size', read_only=True)
    market = serializers.CharField(source='user.lead_sport_market_channel', read_only=True)
    notes = serializers.CharField(source='user.sport_association.notes', read_only=True)

    user_onboarding = serializers.SerializerMethodField()
    billing_subscription = serializers.SerializerMethodField()

    def get_user_onboarding(self, obj):
        try:
            onboarding = UsersOnboarding.objects.get(user=obj.user)
            return UsersOnboardingSerializer(onboarding).data
        except Exception as e:
            return None


    def get_billing_subscription(self, obj):
        billing_subscription = BillingSubscription.objects.filter(user_id=obj.user.user_id).first()
        if billing_subscription:
            return {
                'auto_renewal': billing_subscription.auto_renewal,
                'renewal_type': billing_subscription.renewal_type,
                'ends_on': billing_subscription.ends_on,
                'billing_plan': billing_subscription.billing_plan_id,
            }
        return None

    class Meta:
        model = SportAssociation
        fields = ('date_joined',
                  'user_id',
                  'email',
                  'username',
                  'first_name',
                  'last_name',
                  'phone',
                  'role',
                  'size',
                  'denomination',
                  'tax_code',
                  'address',
                  'address_cap',
                  'address_city',
                  'market',
                  'last_login',
                  'notes',
                  # 'logo',
                  'sport_association_id',
                  'billing_subscription',
                  'user_onboarding',
                  )


class UserSerializerSearch(serializers.ModelSerializer):
    preview_and_custom_features = serializers.SerializerMethodField()

    def get_preview_and_custom_features(self, obj):
        return [preview.name for preview in obj.preview_and_custom_features.all()]

    class Meta:
        model = User
        fields = (
            'first_name',
            'last_name',
            'username',
            'avatar_image',
            'preview_and_custom_features',
            'disable_account_creation',
            'force_account_creation',
            'online_payments',
        )


class UserSettingsSerializer(serializers.ModelSerializer):

    class Meta:
        model = User
        fields = ('enumerate_invoices',
                  'online_payments',
                  'balance_sheet_year',
                  'balance_sheet_start_day',
                  'balance_sheet_start_month',
                  'temporary_invoice_deletion',
                  'auto_archive',
                  'auto_mark_attendance',
                  'payment_date_equal_invoice_date',
                  'starting_number_invoices',
                  'auto_paid_payment',
                  'full_installments_plan',
                  'show_zero_payments',
                  'dark_mode',
                  'medical_certificate_notifications',
                  'hide_category_name',
                  'subscription_duration_equal_sport_year',
                  'subscription_duration',
                  'membership_duration',
                  'membership_starting_number',
                  'default_membership_type',
                  'subscription_start_day',
                  'subscription_start_month',
                  'custom_end_date',
                  'subscription_end_day',
                  'subscription_end_month',
                  'disable_account_creation',
                  'force_account_creation',
                  'default_payment_category',
                  'default_payment_category_courses',
                  )

class UserTablesSettingsSerializer(serializers.ModelSerializer):

    class Meta:
        model = User
        fields = ('tables_settings',)


class SportAssociationSearchSerializer(serializers.ModelSerializer):
    
    membership_card_configuration = serializers.SerializerMethodField()

    def get_membership_card_configuration(self, obj):
        membership_card_configuration: SportAssociationMembershipCardConfiguration = SportAssociationMembershipCardConfiguration.objects.filter(sport_association=obj).first()
        if membership_card_configuration is None:
            return {
                'emit_only_on_approval': False,
                'customized_template': {}
            }
        return {
            'emit_only_on_approval': membership_card_configuration.emit_only_on_approval,
            'customized_template': membership_card_configuration.customized_template
        }

    user = UserSerializerSearch()

    class Meta:
        model = SportAssociation
        fields = ('denomination',
                  'tax_code',
                  'address',
                  'address_cap',
                  'address_city',
                  'user',
                  'sport_association_id',
                  'membership_card_configuration',
                  )


class SportAssociationSearchProfileSerializer(serializers.ModelSerializer):

    class Meta:
        model = SportAssociation
        fields = ('denomination',
                  'tax_code',
                  'address',
                  'address_cap',
                  'address_city',
                  'subscription_fee',
                  'multiple_subscription_fee',
                  'subscription_fee_plans',
                  'membership_fee',
                  'multiple_membership_fee',
                  'membership_fee_plans',
                  'custom_subscription_form_data',
                  'configuration',
                  'enabled_for',
                  'enable_quotes_management',
                  'checkout_info',
                  'additional_fields',
                  'stripe_available_methods',
                  'invoice_template',
                  'subscription_template',
                  'extra_text_invoices',
                  )


class SportAssociationBasicInfo(serializers.ModelSerializer):

    class Meta:
        model = SportAssociation
        fields = (
            'denomination',
            'tax_code',
            'checkout_info',
        )


class UserSerializerSignup(serializers.ModelSerializer):
    password = serializers.RegexField(regex=r'^(?=.*[A-Z])(?=.*[!@#$&\.\-\_*])(?=.*[0-9]).{10,}$')

    def create(self, validated_data):
        instance = User.objects.create_user(**validated_data, username=str(secrets.token_hex(10)))
        return instance

    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'email', 'phone', 'username', 'password')


class UserSerializer(serializers.ModelSerializer):
    username = serializers.CharField(required=False)
    def create(self, validated_data):
        instance = User.objects.create_user(**validated_data, username=str(secrets.token_hex(10)))
        return instance

    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'email', 'phone', 'username', 'role')


class UsersOnboardingSerializer(serializers.ModelSerializer):
    class Meta:
        model = UsersOnboarding
        fields = "__all__"

class UserAuthSerializer(serializers.ModelSerializer):
    '''
    def __init__(self, *args, **kwargs):
        kwargs['partial'] = True
        super(serializers.ModelSerializer, self).__init__(*args, **kwargs)
    '''
    requires_welcome = serializers.SerializerMethodField()
    onboarding = serializers.SerializerMethodField()

    def _get_legacy_requires_welcome(self, obj, sport_association):
        # check logo
        if sport_association.logo == '' or sport_association.logo is None:
            return True
        # check denomination
        if sport_association.denomination == '' or sport_association.denomination is None:
            return True
        # check tax code
        if sport_association.tax_code == '' or sport_association.tax_code is None:
            return True
        # check balance sheet start day
        if obj.balance_sheet_start_day == 0 or obj.balance_sheet_start_day is None:
            return True
        # check balance sheet start month
        if obj.balance_sheet_start_month == 0 or obj.balance_sheet_start_month is None:
            return True

        # check if the user joined less than 30 days ago
        if obj.date_joined > (timezone.now() - timedelta(days=30)):
            if obj.lead_sport_association_role == '' or obj.lead_sport_association_role is None:
                return True
            if obj.lead_sport_association_size == '' or obj.lead_sport_association_size is None:
                return True
            if obj.lead_sport_market_channel == '' or obj.lead_sport_market_channel is None:
                return True
        return False

    def get_requires_welcome(self, obj):
        '''
        We return a boolean value to determine if the user is a new user.
        The parameter we use are:
        - empty sport association logo
        - empty sport association denomination
        - empty sport association tax code
        - balance sheet start day
        - balance sheet start month
        ( less than 30 days since the user joined:
        - lead_sport_association_role
        - lead_sport_association_size
        - lead_sport_market_channel
        )
        '''
        if obj.role != User.ASSOCIATION:
            return False
        sport_association = obj.sport_association

        instance_config = InstanceConfiguration.objects.filter(
            self_hosted=True,
            primary_association=sport_association,
        ).first()
        if instance_config:
            if instance_config.setup_provenance == InstanceConfiguration.SETUP_PROVENANCE_FRESH:
                return instance_config.onboarding_completed_at is None
            if instance_config.setup_provenance == InstanceConfiguration.SETUP_PROVENANCE_IMPORT:
                return False

        return self._get_legacy_requires_welcome(obj, sport_association)

    def get_onboarding(self, obj):
        if obj.role != User.ASSOCIATION:
            return None
        # get the associated usersonboarding object
        try:
            onboarding = UsersOnboarding.objects.get(user=obj)
            return UsersOnboardingSerializer(onboarding).data
        except Exception as e:
            return None

    def create(self, validated_data):
        instance = User.objects.create(**validated_data)
        return instance

    class Meta:
        model = User
        fields = (
            'user_id',
            'first_name',
            'last_name',
            'username',
            'email',
            'avatar_image',
            'collaborator_role',
            'collaborator_permissions',
            'google_sync_enabled',
            'dashboard_layout',
            'full_installments_plan',
            'show_zero_payments',
            'dark_mode',
            'medical_certificate_notifications',
            'hide_category_name',
            'phone',
            'subscription_duration_equal_sport_year',
            'balance_sheet_start_day',
            'balance_sheet_start_month',
            'balance_sheet_year',
            'subscription_duration',
            'membership_duration',
            'membership_starting_number',
            'default_membership_type',
            'subscription_start_day',
            'subscription_start_month',
            'custom_end_date',
            'subscription_end_day',
            'subscription_end_month',
            'lead_sport_association_role',
            'lead_sport_association_size',
            'lead_sport_market_channel',
            'requires_welcome',
            'onboarding',
            'default_payment_category',
            'default_payment_category_courses',
        )


class UserAuthUpdateSerializer(serializers.ModelSerializer):
    sport_association = SportAssociationSerializer()

    def create(self, validated_data):
        instance = User.objects.create(**validated_data)
        return instance

    class Meta:
        model = User
        fields = (
            'first_name',
            'last_name',
            'username',
            'email',
            'avatar_image',
            'sport_association'
        )


class UserAccountSerializer(serializers.Serializer):
    new_member = serializers.BooleanField(default=False)

    def update(self, instance, validated_data):
        pass

    def create(self, validated_data):
        instance = UserAccount(**validated_data)
        return instance


