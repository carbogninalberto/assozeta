from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from application.models.billing_models import BillingPlan, BillingSubscription
from instance.models import InstanceConfiguration


class Command(BaseCommand):
    help = "Create the reference data required by a self-hosted instance."

    def handle(self, *args, **options):
        plan, created = BillingPlan.objects.update_or_create(
            name="Piano Pro",
            defaults={
                "description": "Piano completo per installazioni self-hosted",
                "monthly_fee": 0,
                "annually_fee": 0,
                "billing_type": BillingPlan.PRO_PLAN,
            },
        )
        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} self-hosted billing plan."))

        config = InstanceConfiguration.objects.select_related('primary_association').first()
        if not config or not config.primary_association_id:
            return

        owner = config.primary_association.user
        defaults = {
            "auto_renewal": True,
            "renewal_type": BillingSubscription.ANNUALLY,
            "ends_on": timezone.now() + timedelta(days=36500),
            "billing_plan": plan,
        }
        subscriptions = BillingSubscription.objects.filter(user=owner)
        if subscriptions.exists():
            subscriptions.update(**defaults)
        else:
            BillingSubscription.objects.create(user=owner, **defaults)
        self.stdout.write(self.style.SUCCESS("Renewed self-hosted owner entitlement."))
