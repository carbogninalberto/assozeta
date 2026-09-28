"""Explicit queryset boundaries for association-initiated impersonation.

These filters supplement existing person/permission checks; administrator sessions
and ordinary requests keep their existing behavior.
"""
from rest_framework.exceptions import PermissionDenied


RELATIONS = {
    'sportassociation': 'pk',
    'coursesubscription': 'subscription__sport_association_id',
    'coursesubscriptioninstallment': 'course_subscription__subscription__sport_association_id',
    'carnetsubscription': 'subscription__sport_association_id',
    'attendanceregistry': 'course__sport_association_id',
    'attendanceday': 'attendance_registry__course__sport_association_id',
    'subscriptionfile': 'subscription__sport_association_id',
}


def scoped_queryset(identity, manager):
    association = getattr(identity, 'impersonation_association', None)
    if association is None:
        return manager
    model = manager.model
    relation = RELATIONS.get(model._meta.model_name)
    if relation is None and any(f.name == 'sport_association' for f in model._meta.fields):
        relation = 'sport_association_id'
    if relation is None:
        raise PermissionDenied('Risorsa non disponibile durante l’impersonificazione.')
    queryset = manager.filter(**{relation: association.pk})
    if model._meta.model_name == 'coursesubscription':
        queryset = queryset.filter(course__sport_association=association)
    elif model._meta.model_name == 'carnetsubscription':
        queryset = queryset.filter(carnet_id__sport_association=association)
    from django.db.models import Q
    from application.models import User, Subscription, Associate, Payment
    person = getattr(identity, 'effective_user', identity)
    if person.role != User.ATHLETE:
        return queryset
    # Match the application's family subscription visibility inside this tenant.
    tax_codes = Subscription.objects.filter(user=person, sport_association=association).exclude(
        associate__tax_code__isnull=True).exclude(associate__tax_code='').values('associate__tax_code')
    members = Associate.objects.filter(sport_association=association).filter(
        Q(user=person) | Q(tax_code__in=tax_codes))
    subscriptions = Subscription.objects.filter(sport_association=association).filter(
        Q(user=person) | Q(associate__in=members))
    name = model._meta.model_name
    if name == 'subscription':
        return queryset.filter(pk__in=subscriptions.values('pk'))
    if name == 'associate':
        return queryset.filter(pk__in=members.values('pk'))
    if name in ('coursesubscription', 'carnetsubscription', 'subscriptionfile'):
        return queryset.filter(subscription__in=subscriptions)
    if name == 'coursesubscriptioninstallment':
        return queryset.filter(course_subscription__subscription__in=subscriptions)
    if name == 'payment':
        return queryset.filter(Q(user=person) | Q(associate__in=members))
    if name == 'invoice':
        payments = Payment.objects.filter(sport_association=association).filter(Q(user=person) | Q(associate__in=members))
        return queryset.filter(pk__in=payments.values('invoice_id'))
    return queryset


# Athlete-facing operations whose data sources carry the explicit boundary above.
# Account credentials, integrations and instance administration are account-wide,
# so an association-scoped session cannot operate them.
ATHLETE_VIEWS = {
    'profile_info', 'profile_associates_course', 'profile_associates_sport_association',
    'profile_settings_tables', 'statistic_athlete_dashboard', 'search_profile',
    'subscription_list', 'subscription_list_all', 'subscription_info',
    'subscription_payments', 'subscription_card', 'subscription_update',
    'subscription_add', 'subscription_sign', 'subscription_renew',
    'subscription_calculate_tax_code', 'subscription_attendance', 'subscription_calendar',
    'subscription_medical_certificate_upload', 'subscription_medical_certificate_set_certificate_expiration',
    'subscription_medical_certificate_edit', 'subscription_upload_document', 'subscription_delete_document',
    'course_list', 'course_overview_add', 'course_overview_update', 'course_overview_delete',
    'course_installment_make_payment', 'payment_list', 'carnet_list', 'carnet_subscription_list',
    'stripe_pay', 'stripe_multiple_pay', 'retrieve_document', 'medical_certificate_document',
    'attendance_day_mark_absent', 'carnet_subscription_topup',
    'document_subscription', 'document_subscription_view', 'document_invoice', 'document_invoice_view',
    'document_subscription_preview',
}


def enforce_scoped_request(request):
    """Validate referenced resources before services can create side effects."""
    from django.urls import resolve
    from django.core.exceptions import ValidationError
    from application import models
    from application.models.carnet_models import CarnetSubscription
    association = request.impersonation_association
    if association is None:
        return
    person = request.effective_user
    match = getattr(request, 'resolver_match', None) or resolve(request.path)
    name = getattr(getattr(match.func, 'cls', None), '__name__', match.func.__name__)
    if person.role == models.User.ATHLETE and name not in ATHLETE_VIEWS:
        raise PermissionDenied('Operazione non disponibile nel contesto dell’associazione.')
    if name in {'profile_update_password', 'oauth2_delete_account', 'profile_integrations'}:
        raise PermissionDenied('Torna al tuo account per gestire le credenziali.')

    resources = {
        'subscription': models.Subscription, 'course': models.Course,
        'associate': models.Associate, 'payment': models.Payment,
        'sport_association': models.SportAssociation, 'invoice': models.Invoice,
        'course_subscription': models.CourseSubscription,
        'course_subscription_installment': models.CourseSubscriptionInstallment,
        'carnet': models.Carnet, 'carnet_subscription': CarnetSubscription,
        'attendance_day': models.AttendanceDay, 'group': models.Group, 'tag': models.Tags,
    }

    def check(key, value):
        key = {'payments': 'payment', 'subscriptions': 'subscription', 'courses': 'course', 'tags': 'tag'}.get(key, key)
        key = key.removesuffix('_id')
        if key == 'medical' and value and not isinstance(value, dict):
            from django.core.cache import cache
            try:
                medicals = scoped_queryset(request, models.Subscription.objects).filter(medical_id=value)
            except (ValidationError, ValueError, TypeError):
                raise PermissionDenied('Certificato non disponibile.') from None
            uploaded = cache.get(medical_upload_key(request, value)) == str(association.pk)
            if not uploaded and not medicals.exists():
                raise PermissionDenied('Certificato non disponibile per questa associazione.')
            return
        model = resources.get(key)
        if not model or value in (None, '') or isinstance(value, bool):
            return
        if isinstance(value, dict):
            value = value.get(key + '_id', value.get('value', value.get('id')))
            if value is None:
                return
        if isinstance(value, list):
            for item in value:
                check(key, item)
            return
        queryset = scoped_queryset(request, model.objects)
        if key == 'sport_association' and str(value) == association.user.username:
            return
        try:
            available = queryset.filter(pk=value).exists()
        except (ValidationError, ValueError, TypeError):
            available = False
        if not available:
            raise PermissionDenied('Risorsa non disponibile per questo utente e associazione.')

    def walk(data):
        if isinstance(data, dict):
            for key, value in data.items():
                if key == 'new_member_info' and isinstance(value, dict) and value.get('role', models.User.ATHLETE) not in (models.User.ATHLETE, str(models.User.ATHLETE)):
                    raise PermissionDenied('Puoi creare solo account atleta.')
                check(key, value)
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(data, list):
            for value in data:
                walk(value)

    if name == 'search_profile' and match.kwargs.get('username', '').casefold() != association.user.username.casefold():
        raise PermissionDenied('Associazione non disponibile.')
    if name == 'subscription_add' and request.data.get('sport_association') not in (str(association.pk), association.user.username):
        raise PermissionDenied('Seleziona la tua associazione.')
    for key, value in match.kwargs.items():
        if key in ('uid_subscription', 'subscriptionId'):
            check('subscription', value)
        elif key == 'uid':
            if name.startswith(('subscription_', 'document_subscription')):
                check('subscription', value)
            elif name.startswith('document_invoice'):
                check('invoice', value)
            elif name == 'attendance_day_mark_absent':
                check('attendance_day', value)
            elif name == 'carnet_subscription_topup':
                check('carnet_subscription', value)
            elif name.startswith('course_installment_'):
                check('course_subscription_installment', value)
            elif name.startswith('course_') or name == 'profile_associates_course':
                check('course', value)
            elif name == 'profile_associates_sport_association':
                check('sport_association', value)
        else:
            check(key, value)
    walk(dict(getattr(request, 'query_params', {}).items()))
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        walk(getattr(request, 'data', {}))


def visible_documents(identity):
    from django.db.models import Q
    from application.models import Subscription, Invoice
    from application.models.subscriptions_models import SubscriptionFile
    from docmanager.models import Document
    subscriptions = scoped_queryset(identity, Subscription.objects)
    invoices = scoped_queryset(identity, Invoice.objects)
    files = SubscriptionFile.objects.filter(subscription__in=subscriptions)
    return Document.objects.filter(
        Q(pk__in=subscriptions.values('document_pdf_id')) |
        Q(pk__in=subscriptions.values('medical__document_id')) |
        Q(pk__in=invoices.values('document_pdf_id')) |
        Q(pk__in=files.values('document_id'))).distinct()


def isolate_medical_certificate(request, subscription):
    """Editing a shared certificate must not change another association's copy."""
    from application.models import Subscription, MedicalCertificate
    association = getattr(request, 'impersonation_association', None)
    medical = subscription.medical
    if association and medical and Subscription.objects.filter(medical=medical).exclude(sport_association=association).exists():
        medical = MedicalCertificate.objects.create(
            document=medical.document, user=medical.user, expiration_date=medical.expiration_date,
            competitive_medical_certificate=medical.competitive_medical_certificate, notes=medical.notes)
        subscription.medical = medical
        subscription.save(update_fields=['medical'])
    return medical


def medical_upload_key(request, medical_id):
    return f"impersonation-upload:{request.headers.get('X-Impersonation-Id')}:{medical_id}"


def remember_medical_upload(request, medical_id):
    from django.core.cache import cache
    from application.impersonation import SESSION_TTL
    association = getattr(request, 'impersonation_association', None)
    if association:
        cache.set(medical_upload_key(request, medical_id), str(association.pk), SESSION_TTL)
