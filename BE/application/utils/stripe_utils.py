from django.conf import settings

from instance.models import InstanceConfiguration


def online_payments_available(sport_association):
    owner = sport_association.user
    if not (
        owner.online_payments
        and owner.stripe_account_id
        and owner.stripe_on_boarding_completed
        and settings.STRIPE_KEY
    ):
        return False

    instance_config = InstanceConfiguration.get_config()
    if instance_config and instance_config.self_hosted:
        return bool(instance_config.stripe_public_key)

    return True
