"""Request-scoped validation of capabilities issued by the paired Bakney authority."""
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.debug import sensitive_variables

from application.models import SportAssociation, Subscription, User
from application.models.subscriptions_models import SubscriptionToken, CumulativeSubscriptionGymLinks
from .models import BakneyPairing
from .protocol import SSOError, canonical_uuid, upstream
from .service import binding, check_binding


@sensitive_variables()
def source_token(kind, token, resource_id=None):
    """Return an unsaved capability, never persist/cache an upstream grant.

    Existing locally issued/imported tokens retain their normal validation. This
    fallback runs only for unknown tokens and fails closed on source outages,
    revocation, expiry, configuration changes or cross-association resources.
    """
    if not canonical_uuid(token) or (kind == 'card' and not canonical_uuid(str(resource_id))):
        return None
    with transaction.atomic():
        pairing = BakneyPairing.objects.select_for_update().select_related('config').first()
        if (not pairing or not pairing.config.self_hosted or
                pairing.state not in ('pending', 'paired') or not pairing.remote_pairing_id):
            return None
        try:
            check_binding(pairing)
            association = SportAssociation.objects.filter(
                pk=pairing.association_id, deleted=False, user__is_active=True,
                user__deleted=False, user__role=User.ASSOCIATION).first()
            if not association:
                return None
            payload = {'kind': kind, 'token': token}
            if kind == 'card':
                if not Subscription.objects.filter(pk=resource_id, sport_association=association).exists():
                    return None
                payload['resource_id'] = str(resource_id)
            elif kind != 'cumulative':
                return None
            data = upstream(pairing, 'public-token', payload)
            if (any(data.get(key) != value for key, value in binding(pairing).items())
                    or data.get('kind') != kind or not canonical_uuid(data.get('generation'))):
                return None
            expires = parse_datetime(data.get('expires_at', ''))
            if not expires or timezone.is_naive(expires) or expires <= timezone.now():
                return None
            if kind == 'card':
                if data.get('resource_id') != str(resource_id):
                    return None
                return SubscriptionToken(subscription_id=resource_id, token=token, expiration_date=expires)
            gym_name = data.get('gym_name')
            if not isinstance(gym_name, str) or len(gym_name) > 255:
                return None
            return CumulativeSubscriptionGymLinks(
                sport_association=association, token=token, gym_name=gym_name, expires_at=expires)
        except (SSOError, ValueError, TypeError):
            return None
