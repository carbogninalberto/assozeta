"""Who the documents of the members are served to.

Documents are served to the association's staff (the owner and collaborators),
to the instance owner and administrators, and to the member they belong to. A
member's own records are those of their user, of the associates of their
subscriptions and of associates sharing a tax code with them.

Association-scoped sessions keep their own boundary (impersonation_scope) and
requests admitted without an account (a document token or the renderer's
header) are decided by the view's permission classes.
"""
from django.db.models import Q
from rest_framework.exceptions import PermissionDenied

NOT_AVAILABLE = 'Risorsa non disponibile per questo utente e associazione.'


def serves_everything(user):
    from application.models import User
    from instance.permissions import is_instance_owner
    return user.role != User.ATHLETE or user.is_superuser or is_instance_owner(user)


def member_associates(user):
    from application.models import Associate, Subscription
    own = Subscription._base_manager.filter(user=user, associate__isnull=False)
    tax_codes = own.exclude(associate__tax_code__isnull=True).exclude(associate__tax_code='').values('associate__tax_code')
    return Associate._base_manager.filter(Q(user=user) | Q(pk__in=own.values('associate')) | Q(tax_code__in=tax_codes))


def member_subscriptions(user):
    from application.models import Subscription
    return Subscription._base_manager.filter(Q(user=user) | Q(associate__in=member_associates(user)))


def member_invoices(user):
    from application.models import Invoice, Payment
    payments = Payment._base_manager.filter(Q(user=user) | Q(associate__in=member_associates(user)))
    return Invoice._base_manager.filter(pk__in=payments.filter(invoice__isnull=False).values('invoice'))


def member_documents(user):
    from application.models.subscriptions_models import MedicalCertificate, SubscriptionFile
    from docmanager.models import Document
    subscriptions = member_subscriptions(user)
    medicals = MedicalCertificate._base_manager.filter(Q(user=user) | Q(pk__in=subscriptions.values('medical')))
    return Document._base_manager.filter(
        Q(pk__in=subscriptions.values('document_pdf')) |
        Q(pk__in=medicals.values('document')) |
        Q(pk__in=SubscriptionFile._base_manager.filter(subscription__in=subscriptions).values('document')) |
        Q(pk__in=member_invoices(user).values('document_pdf')))


def can_access_document(user, record):
    """record is a Document, a Subscription or an Invoice."""
    from application.models import Invoice, Subscription
    if serves_everything(user):
        return True
    if isinstance(record, Subscription):
        records = member_subscriptions(user)
    elif isinstance(record, Invoice):
        records = member_invoices(user)
    else:
        records = member_documents(user)
    return records.filter(pk=record.pk).exists()


def require_document_access(request, record):
    user = getattr(request, 'user', None)
    if getattr(request, 'impersonation_association', None) or not (user and user.is_authenticated):
        return
    if not can_access_document(user, record):
        raise PermissionDenied(NOT_AVAILABLE)
