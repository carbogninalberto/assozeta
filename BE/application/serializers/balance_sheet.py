from django.db import models
from rest_framework import serializers

from application.models.balance_sheet_models import CustomAccounts, CustomAccountsTransfer
from application.models.payment_models import Payment


class CustomAccountSerializer(serializers.ModelSerializer):
    deletable = serializers.SerializerMethodField()
    current_balance = serializers.SerializerMethodField()

    def get_current_balance(self, obj):
        # select all custom accounts transfers from this account
        transfers_from = CustomAccountsTransfer.objects.filter(custom_account_from=obj)
        # select all custom accounts transfers to this account
        transfers_to = CustomAccountsTransfer.objects.filter(custom_account_to=obj)
        # sum all transfers from this account
        sum_from = transfers_from.aggregate(models.Sum('amount'))['amount__sum']
        # sum all transfers to this account
        sum_to = transfers_to.aggregate(models.Sum('amount'))['amount__sum']
        # if there are no transfers from this account, set sum_from to 0
        if sum_from is None:
            sum_from = 0
        # if there are no transfers to this account, set sum_to to 0
        if sum_to is None:
            sum_to = 0
        # calculate current balance
        current_balance = obj.initial_balance + sum_to - sum_from
        # get payments with this account
        payments = Payment.objects.filter(
            custom_accounts_id=obj.custom_account_id,
            paid=True
        )
        # sum all payments with this account
        # sum_payments = payments.aggregate(models.Sum('amount'))['amount__sum']
        sum_payments = 0
        for payment in payments:
            if payment.expense:
                sum_payments -= payment.amount
            else:
                sum_payments += payment.amount
        # if there are no payments with this account, set sum_payments to 0
        # if sum_payments is None:
        #     sum_payments = 0
        # calculate current balance
        current_balance = current_balance + sum_payments
        return current_balance

    def get_current_balance_from(self, date_from, date_to):
        obj = self.instance
        # select all custom accounts transfers from this account
        transfers_from = CustomAccountsTransfer.objects.filter(
            custom_account_from=obj,
            date__range=[date_from, date_to]
        )
        # select all custom accounts transfers to this account
        transfers_to = CustomAccountsTransfer.objects.filter(
            custom_account_to=obj,
            date__range=[date_from, date_to]
        )
        # sum all transfers from this account
        sum_from = transfers_from.aggregate(models.Sum('amount'))['amount__sum']
        # sum all transfers to this account
        sum_to = transfers_to.aggregate(models.Sum('amount'))['amount__sum']
        # if there are no transfers from this account, set sum_from to 0
        if sum_from is None:
            sum_from = 0
        # if there are no transfers to this account, set sum_to to 0
        if sum_to is None:
            sum_to = 0
        # calculate current balance
        current_balance = obj.initial_balance + sum_to - sum_from
        # get payments with this account
        payments = Payment.objects.filter(
            custom_accounts_id=obj.custom_account_id,
            paid=True,
            payment_date__range=[date_from, date_to]
        )
        # sum all payments with this account
        # sum_payments = payments.aggregate(models.Sum('amount'))['amount__sum']
        sum_payments = 0
        for payment in payments:
            if payment.expense:
                sum_payments -= payment.amount
            else:
                sum_payments += payment.amount
        # if there are no payments with this account, set sum_payments to 0
        # if sum_payments is None:
        #     sum_payments = 0
        # calculate current balance
        current_balance = current_balance + sum_payments
        return current_balance

    def get_deletable(self, obj):
        # check if is editable
        if not obj.editable:
            return False

        # account transfer from or to this account
        if CustomAccountsTransfer.objects.filter(custom_account_from=obj).exists() \
                or CustomAccountsTransfer.objects.filter(custom_account_to=obj).exists():
            return False
        # check if there are payments with this account
        payments = Payment.objects.filter(custom_accounts_id=obj.custom_account_id).count()
        if payments > 0:
            return False
        return True

    def create(self, validated_data):
        instance = CustomAccounts.objects.create(**validated_data)
        return instance

    def save(self, **kwargs):
        return super().save(**kwargs)

    class Meta:
        model = CustomAccounts
        fields = (
            'custom_account_id',
            'enabled',
            'name',
            'current_balance',
            'initial_balance',
            'account_type',
            'account_code',
            'deletable',
            'editable',
        )


class CustomAccountSerializerSimplify(serializers.ModelSerializer):

    class Meta:
        model = CustomAccounts
        fields = (
            'custom_account_id',
            'name',
            'account_type',
            'account_code',
        )


class CustomAccountTransferSerializer(serializers.ModelSerializer):

    def create(self, validated_data):
        instance = CustomAccountsTransfer.objects.create(**validated_data)
        return instance

    def save(self, **kwargs):
        return super().save(**kwargs)

    class Meta:
        model = CustomAccountsTransfer
        fields = (
            'custom_account_transfer_id',
            'custom_account_from',
            'custom_account_to',
            'amount',
            'date',
            'sport_association'
        )


class CustomAccountTransferInfoSerializer(serializers.ModelSerializer):
    custom_account_from = CustomAccountSerializerSimplify()
    custom_account_to = CustomAccountSerializerSimplify()

    class Meta:
        model = CustomAccountsTransfer
        fields = (
            'custom_account_transfer_id',
            'custom_account_from',
            'custom_account_to',
            'amount',
            'date',
            'sport_association'
        )