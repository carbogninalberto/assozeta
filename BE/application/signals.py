from django.db import transaction
from django.db.models.signals import pre_delete, post_save
from django.dispatch import receiver, Signal
from django.db.models import Q

from .models import CourseSubscription, CourseSubscriptionInstallment, Payment
from .tasks import check_workflows_trigger
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# AUDIT LOG INDEX SIGNAL HANDLER
# =============================================================================

def resolve_sport_association_for_log_entry(log_entry):
    """
    Resolve the SportAssociation for a LogEntry.

    Resolution order:
    1. Try registered resolver for the content type
    2. Try to get sport_association_id from changes JSON
    3. Fallback to actor's sport_association

    Returns: (sport_association, resolution_path) or (None, None)
    """
    from application.audit_resolvers import get_resolver
    from application.models.user_models import SportAssociation

    content_type = log_entry.content_type
    if not content_type:
        return None, None

    # Try registered resolver
    resolver = get_resolver(content_type)
    if resolver:
        try:
            model_class = content_type.model_class()
            if model_class:
                pk_field = model_class._meta.pk.name
                try:
                    # Try to get the object (might be deleted for DELETE actions)
                    obj = model_class.objects.get(**{pk_field: log_entry.object_pk})
                    result = resolver(obj)
                    if result:
                        return result
                except model_class.DoesNotExist:
                    # Object deleted, will try fallbacks
                    pass
        except Exception as e:
            logger.warning(f"Resolver error for {content_type}: {e}")

    # Fallback: try to get sport_association_id from changes JSON
    if log_entry.changes:
        changes = log_entry.changes if isinstance(log_entry.changes, dict) else {}
        if 'sport_association' in changes:
            try:
                # changes format: {'field': [old_value, new_value]}
                # For DELETE actions, new_value is 'None', so we try old_value first
                sa_data = changes['sport_association']
                sa_ids_to_try = []
                if isinstance(sa_data, list):
                    # Try old value first (index 0), then new value (index 1)
                    if len(sa_data) > 0:
                        sa_ids_to_try.append(sa_data[0])
                    if len(sa_data) > 1:
                        sa_ids_to_try.append(sa_data[1])
                else:
                    sa_ids_to_try.append(sa_data)

                for sa_id in sa_ids_to_try:
                    # Skip if sa_id is None or the string "None"
                    if sa_id and sa_id != 'None' and str(sa_id).lower() != 'none':
                        sa = SportAssociation.objects.filter(sport_association_id=sa_id).first()
                        if sa:
                            return sa, 'changes_json.sport_association'
            except (TypeError, IndexError, KeyError, Exception):
                pass

    # Fallback: use actor's sport_association
    if log_entry.actor:
        try:
            sa = SportAssociation.objects.filter(user=log_entry.actor).first()
            if sa:
                return sa, 'actor.sport_association'
        except Exception:
            pass

    return None, None

new_subscription = Signal()
subscription_signed = Signal()
subscription_approved = Signal()
new_course_subscription = Signal()
medical_certificate_expired = Signal()
medical_certificate_expiring = Signal()
payment_paid = Signal()
membership_created = Signal()
update_metadata_courses = Signal()

@receiver(update_metadata_courses)
def update_metadata_courses_callback(sender, instance, title, **kwargs):
    logger.info('update_metadata_courses_callback: received instance: %s, title: %s' % (instance, title))
    if not instance:
        logger.error('update_metadata_courses_callback: no instance received')
        return
    if not title:
        logger.error('update_metadata_courses_callback: no title received')
        return

    # get all the payments related to this course
    payments = Payment.objects.filter(
        Q(meta__icontains=title) |
        Q(description__icontains=title)
    )

    payments_to_update = []
    for payment in payments:
        needs_update = False
        logger.debug(f'Processing payment {payment.payment_id}')

        # Handle meta field if it exists
        if payment.meta:
            new_meta = {
                k: v.replace(title, instance.title) if isinstance(v, str) else v
                for k, v in payment.meta.items()
            }
            if new_meta != payment.meta:
                payment.meta = new_meta
                needs_update = True
                logger.debug(f'Updated meta for payment {payment.payment_id}: {new_meta}')

        # Handle description field if it exists
        if payment.description and title in payment.description:
            payment.description = payment.description.replace(title, instance.title)
            needs_update = True
            logger.debug(f'Updated description for payment {payment.payment_id}')

        if needs_update:
            payments_to_update.append(payment)

    if payments_to_update:
        Payment.objects.bulk_update(payments_to_update, ['meta', 'description'])
        logger.info('update_metadata_courses_callback: updated %d payments', len(payments_to_update))
    else:
        logger.info('No payments needed updating')


@receiver(membership_created)
def membership_created_callback(sender, instance, **kwargs):
    """
    Signal handler to ensure payment is deleted when instance is cascade deleted
    """
    logger.info('membership_created_callback: instance: %s' % instance)
    if instance.membership_fee > 0 and instance.type == CourseSubscription.MEMBERSHIP_TYPE:
        payment = Payment.objects.create(
            user=instance.course.sport_association.user,
            description=f"Abbonamento: {instance.course.title} dal {instance.billed_from.strftime('%d/%m/%Y')} al {instance.billed_until.strftime('%d/%m/%Y')}",
            associate=instance.subscription.associate,
            creation_date=instance.billed_from,
            payment_date=instance.billed_from,
            amount=instance.membership_fee,
            subject=Payment.COURSE,
            sport_association=instance.course.sport_association,
            meta={
                'description': f"Abbonamento: {instance.course.title} dal {instance.billed_from.strftime('%d/%m/%Y')} al {instance.billed_until.strftime('%d/%m/%Y')}",
                'course_id': str(instance.course.course_id),
                'course_title': instance.course.title,
                'course_subscription_id': str(instance.course_subscription_id),
                'billed_from': instance.billed_from.strftime('%Y-%m-%d'),
                'billed_until': instance.billed_until.strftime('%Y-%m-%d'),
                'amount': str(instance.membership_fee)
            }
        )
        instance.membership_payments.add(payment)
        instance.save()

@receiver(pre_delete, sender=Payment)
def delete_installment_of_payment(sender, instance, **kwargs):
    """
    Signal handler to ensure payment is deleted when instance is cascade deleted.
    Uses select_for_update to prevent recursive calls and maintain consistency.
    """
    logger.info('delete_installment_of_payment: instance: %s' % instance)

    with transaction.atomic():
        # Lock the related records to prevent concurrent modifications
        installments = (CourseSubscriptionInstallment.objects
                        .select_for_update()
                        .filter(payment=instance))

        # Disconnect the signal temporarily to prevent recursion
        pre_delete.disconnect(delete_installment_of_payment, sender=Payment)
        try:
            installments.delete()
        finally:
            # Reconnect the signal
            pre_delete.connect(delete_installment_of_payment, sender=Payment)

@receiver(pre_delete, sender=CourseSubscription)
def delete_related_course_payment(sender, instance, **kwargs):
    """
    Signal handler to ensure payment is deleted when instance is cascade deleted
    """
    try:
        logger.info('delete_related_course_payment: instance: %s' % instance)
        if instance.payment_id:
            try:
                payment = Payment.objects.get(id=instance.payment_id)
                payment.delete()
                logger.info('Main payment deleted: %s', instance.payment_id)
            except Payment.DoesNotExist:
                logger.warning('Main payment not found for ID: %s', instance.payment_id)

            # Handle membership payments
        if instance.type == CourseSubscription.MEMBERSHIP_TYPE:
            unpaid_payments = instance.membership_payments.filter(paid=False)
            deleted_count = unpaid_payments.count()
            unpaid_payments.delete()
            logger.info('Deleted %d unpaid membership payments', deleted_count)

            # Delete installments
        installments_count = CourseSubscriptionInstallment.objects.filter(
            course_subscription=instance
        ).delete()[0]
        logger.info('Deleted %d installments', installments_count)
    except Exception as e:
        logger.error('delete_related_course_payment: %s' % e)

@receiver(pre_delete, sender=CourseSubscriptionInstallment)
def delete_related_payment(sender, instance, **kwargs):
    """
    Signal handler to ensure payment is deleted when instance is cascade deleted
    """
    logger.info('delete_related_payment: instance: %s' % instance)
    try:
        if not instance.payment_id:
            logger.info('No payment associated with installment: %s', instance.id)
            return

        try:
            payment = instance.payment
            if not payment.paid:
                payment_id = payment.payment_id
                payment.delete()
                logger.info('Successfully deleted unpaid payment: %s for installment: %s',
                            payment_id, instance.id)
            else:
                logger.info('Skipping deletion of paid payment: %s for installment: %s',
                            payment.payment_id, instance.id)

        except Payment.DoesNotExist:
            logger.warning('Payment not found for installment: %s', instance.id)

    except Exception as e:
        logger.error('Error in delete_related_payment for installment %s: %s',
                     getattr(instance, 'id', 'unknown'), str(e))

@receiver(new_subscription)
def new_subscription_callback(sender, **kwargs):
    if kwargs.get('subscription'):
        subscription = kwargs.get('subscription')
        logger.info('new_subscription_callback: received subscription instance: %s' % subscription)
        check_workflows_trigger.delay(
            trigger_type='new_subscription',
            sport_association_id=subscription.sport_association.sport_association_id,
            subscription_id=subscription.subscription_id)
    else:
        logger.error('new_subscription_callback: no subscription instance received')


@receiver(subscription_signed)
def subscription_signed_callback(sender, **kwargs):
    if kwargs.get('subscription'):
        subscription = kwargs.get('subscription')
        logger.info('subscription_signed_callback: received subscription instance: %s' % subscription)
        check_workflows_trigger.delay(
            trigger_type='subscription_signed',
            sport_association_id=subscription.sport_association.sport_association_id,
            subscription_id=subscription.subscription_id)
    else:
        logger.error('subscription_signed_callback: no subscription instance received')


@receiver(subscription_approved)
def subscription_approved_callback(sender, **kwargs):
    if kwargs.get('subscription'):
        subscription = kwargs.get('subscription')
        logger.info('subscription_approved_callback: received subscription instance: %s' % subscription)
        check_workflows_trigger.delay(
            trigger_type='subscription_approved',
            sport_association_id=subscription.sport_association.sport_association_id,
            subscription_id=subscription.subscription_id)
    else:
        logger.error('subscription_approved_callback: no subscription instance received')


@receiver(new_course_subscription)
def new_course_subscription_callback(sender, **kwargs):
    if kwargs.get('course_subscription'):
        course_subscription = kwargs.get('course_subscription')
        logger.info('new_course_subscription_callback: received subscription instance: %s' % course_subscription)
        check_workflows_trigger.delay(
            trigger_type='new_course_subscription',
            sport_association_id=course_subscription.subscription.sport_association.sport_association_id,
            subscription_id=course_subscription.subscription.subscription_id,
            data={'course_subscription_id': course_subscription.course_subscription_id if course_subscription is not None else None}
        )
    else:
        logger.error('new_course_subscription_callback: no subscription instance received')


@receiver(medical_certificate_expired)
def medical_certificate_expired_callback(sender, **kwargs):
    if kwargs.get('medical_certificate'):
        medical_certificate = kwargs.get('medical_certificate')
        logger.info('medical_certificate_expired_callback: received medical_certificate instance: %s' % medical_certificate)
        check_workflows_trigger.delay(
            trigger_type='medical_certificate_expired',
            medical_certificate_id=medical_certificate.medical_id)
    else:
        logger.error('medical_certificate_expired_callback: no medical_certificate instance received')


@receiver(medical_certificate_expiring)
def medical_certificate_expiring_callback(sender, **kwargs):
    if kwargs.get('medical_certificate'):
        medical_certificate = kwargs.get('medical_certificate')
        logger.info('medical_certificate_expiring_callback: received medical_certificate instance: %s' % medical_certificate)
        check_workflows_trigger.delay(
            trigger_type='medical_certificate_expiring',
            medical_certificate_id=medical_certificate.medical_id)
    else:
        logger.error('medical_certificate_expiring_callback: no medical_certificate instance received')


@receiver(payment_paid)
def payment_paid_callback(sender, **kwargs):
    if kwargs.get('payment'):
        payment = kwargs.get('payment')
        logger.info('payment_paid_callback: received payment instance: %s' % payment)
        check_workflows_trigger.delay(
            trigger_type='payment_paid',
            payment_id=payment.payment_id)
    else:
        logger.error('payment_paid_callback: no payment instance received')


# =============================================================================
# AUDIT LOG INDEX CREATION
# =============================================================================

def create_audit_log_index(sender, instance, created, **kwargs):
    """
    Signal handler to create AuditLogIndex when LogEntry is created.
    Only processes newly created log entries.
    """
    if not created:
        return  # Only process new log entries

    from application.models.audit_models import AuditLogIndex

    # Skip if index already exists (safety check)
    if AuditLogIndex.objects.filter(log_entry=instance).exists():
        return

    sport_association, resolution_path = resolve_sport_association_for_log_entry(instance)

    if sport_association:
        try:
            AuditLogIndex.objects.create(
                log_entry=instance,
                sport_association=sport_association,
                resolution_path=resolution_path
            )
            logger.debug(
                f"Created AuditLogIndex for LogEntry {instance.pk} "
                f"-> SportAssociation {sport_association.sport_association_id} "
                f"(via {resolution_path})"
            )
        except Exception as e:
            logger.error(f"Failed to create AuditLogIndex for LogEntry {instance.pk}: {e}")
    else:
        logger.debug(
            f"Could not resolve sport_association for LogEntry {instance.pk} "
            f"(content_type={instance.content_type}, object_pk={instance.object_pk})"
        )


def connect_audit_log_signal():
    """
    Connect the audit log signal handler.
    This should be called from apps.py ready() method after resolvers are set up.
    """
    from auditlog.models import LogEntry
    post_save.connect(create_audit_log_index, sender=LogEntry)