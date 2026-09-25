import secrets
import uuid
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from application.models import User, Associate, Instructor
from instance.models import InstanceConfiguration
from .models import BakneyPairing, BakneyLogin, BakneyNonce, BakneyRevocation
from .protocol import CALLBACK, SSOError, authority, origin, encrypt, upstream


def locked_pairing():
    # Serialize first creation and rotation as well as updates to an existing row.
    config = InstanceConfiguration.objects.select_for_update().first()
    if not config or not config.self_hosted or not config.primary_association_id:
        raise SSOError('configuration_required', 409)
    pairing, _ = BakneyPairing.objects.get_or_create(config=config)
    return pairing


def binding(pairing):
    return {
        'instance_id': str(pairing.instance_id), 'pairing_id': str(pairing.pairing_id),
        'association_id': str(pairing.association_id), 'origin': pairing.origin,
        'callback_uri': pairing.origin + CALLBACK,
    }


def check_binding(pairing):
    if (not pairing.secret_encrypted or pairing.authority != authority() or pairing.origin != origin()
            or pairing.association_id != pairing.config.primary_association_id):
        raise SSOError('revalidation_required', 409)


def snapshot(pairing):
    valid = True
    try:
        check_binding(pairing)
    except SSOError:
        valid = False
    return {
        'protocol': 1, 'instance_id': str(pairing.instance_id),
        'pairing_id': str(pairing.pairing_id), 'origin': pairing.origin,
        'state': 'revalidation_required' if pairing.secret_encrypted and not valid else pairing.state,
        'association': {'id': str(pairing.association_id), 'name': pairing.association_name}
            if pairing.association_name else None,
        'forwarding_enabled': pairing.forwarding_enabled if valid and pairing.state == 'paired' else False,
        'checked_at': pairing.checked_at,
        'notification_pending': BakneyRevocation.objects.exists(),
    }


def queue_revocation(pairing):
    if pairing.secret_encrypted:
        BakneyRevocation.objects.get_or_create(pairing_id=pairing.pairing_id, defaults={
            'authority': pairing.authority, 'secret_encrypted': pairing.secret_encrypted,
        })
    BakneyLogin.objects.filter(pairing_id=pairing.pairing_id).delete()


def rotate(pairing):
    target, issuer = origin(), authority()
    queue_revocation(pairing)
    secret = secrets.token_urlsafe(32)
    pairing.pairing_id = uuid.uuid4()
    pairing.secret_encrypted = encrypt(secret)
    pairing.authority, pairing.origin = issuer, target
    pairing.association_id = pairing.config.primary_association_id
    pairing.association_name = ''
    pairing.state, pairing.forwarding_enabled = 'generated', False
    pairing.remote_revision, pairing.checked_at = 0, None
    pairing.save()
    return secret


def disconnect(pairing):
    queue_revocation(pairing)
    pairing.secret_encrypted = ''
    pairing.state, pairing.forwarding_enabled = 'disconnected', False
    pairing.save()


def synchronize(pairing, acknowledge=False):
    check_binding(pairing)
    if pairing.state not in ('pending', 'paired'):
        raise SSOError('invalid_pairing', 409)
    try:
        data = upstream(pairing, 'acknowledge' if acknowledge else 'status', binding(pairing))
    except SSOError as exc:
        if exc.code == 'pairing_rejected':
            disconnect(pairing)
        raise
    if any(data.get(key) != value for key, value in binding(pairing).items()):
        raise SSOError('invalid_response', 502)
    if (type(data.get('revision')) is not int or data['revision'] < pairing.remote_revision
            or type(data.get('forwarding_enabled')) is not bool
            or data.get('state') not in ('paired', 'disconnected')
            or not isinstance(data.get('association_name'), str) or not 1 <= len(data['association_name']) <= 255):
        raise SSOError('invalid_response', 502)
    if data['state'] == 'disconnected':
        disconnect(pairing)
    else:
        pairing.state = 'paired'
        pairing.forwarding_enabled = data['forwarding_enabled']
        pairing.association_name = data['association_name']
        pairing.remote_revision = data['revision']
        pairing.checked_at = timezone.now()
        pairing.save()
    return data


def require_forwarding(pairing):
    synchronize(pairing)
    if pairing.state != 'paired' or not pairing.forwarding_enabled:
        raise SSOError('forwarding_disabled', 403)


def local_user(pairing, user_id):
    try:
        user = User.objects.get(pk=uuid.UUID(str(user_id)), is_active=True, deleted=False)
    except (User.DoesNotExist, ValueError, TypeError):
        raise SSOError('account_unavailable', 403) from None
    if (user.role != User.ATHLETE or user.is_superuser or user.is_staff or user.connected_user_id
            or Instructor.objects.filter(associated_user_id=user.pk).exists()):
        raise SSOError('account_not_eligible', 403)
    # Source UUIDs are preserved by ImportService. Never merge identities by email.
    if not Associate._base_manager.filter(user_id=user.pk, sport_association_id=pairing.association_id, deleted=False).exists():
        raise SSOError('membership_required', 403)
    return user


def redeemed_user(pairing, data):
    if any(data.get(key) != value for key, value in binding(pairing).items()):
        raise SSOError('invalid_response', 502)
    if (data.get('role') != 'athlete' or data.get('is_active') is not True
            or data.get('is_superuser') is not False or data.get('is_instructor') is not False
            or data.get('authentication_complete') is not True):
        raise SSOError('account_not_eligible', 403)
    return local_user(pairing, data.get('user_id'))


def consume_nonce(value, expires_at):
    try:
        with transaction.atomic():
            BakneyNonce.objects.create(digest=value, expires_at=expires_at)
    except IntegrityError:
        raise SSOError('replayed_request', 409) from None


def deliver_revocations():
    # Network errors are intentionally not logged: transport objects can contain credentials.
    for notification in BakneyRevocation.objects.order_by('created_at')[:20]:
        try:
            result = upstream(notification, 'disconnect', {'pairing_id': str(notification.pairing_id)}, revocation=True)
            if result.get('state') != 'disconnected':
                continue
        except SSOError as exc:
            if exc.code != 'pairing_rejected':
                continue
        notification.delete()
    BakneyNonce.objects.filter(expires_at__lt=timezone.now() - timedelta(minutes=5)).delete()
    BakneyLogin.objects.filter(expires_at__lt=timezone.now()).delete()
