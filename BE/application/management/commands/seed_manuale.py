"""Fictional demo data, restricted to a run-owned disposable database."""
import json
import os
import re
import secrets
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from django.conf import settings
from django.core.management import BaseCommand, CommandError, call_command
from django.db import connection, transaction

from application.models import Associate, Course, Invoice, Payment, PaymentCategory, SportAssociation, Subscription, Tags, User
from application.models.balance_sheet_models import CustomAccounts, CustomAccountsTransfer, BalanceSheet
from application.models.user_models import Instructor, CollaborationInvites
from application.models.attendee_models import AttendanceRegistry, Reminders, GlobalCalendarEvents
from application.models.carnet_models import Carnet, CarnetSubscription
from application.models.courses_models import CourseSubscription, CampsAndRetreats
from application.models.subscriptions_models import Signature, MedicalCertificate, SubscriptionToken, SubscriptionTransfer, AssociateImportDraft, AssociateImportDraftStatus
from docmanager.models import Document
from instance.models import InstanceConfiguration

FIXTURE_VERSION = 8
NAMESPACE = uuid.UUID('7e0f2b21-92c5-4c78-8d8f-7fb4779a361d')


def fixture_id(name):
    return uuid.uuid5(NAMESPACE, name)


def assert_disposable():
    token = os.environ.get('ASSOZETA_MANUAL_RUN_ID', '')
    if not re.fullmatch(r'[a-z0-9]{8,32}', token):
        raise CommandError('Manual seed requires an explicit disposable run identifier.')
    if connection.settings_dict['NAME'] != 'manuale_' + token or settings.ASSOZETA_DEPLOYMENT_MODE != 'development':
        raise CommandError('Manual seed refuses a database not owned by this development run.')


def reset_member_transfer(association, owner):
    """Reset only the deterministic recipient and its owned association links."""
    recipient = User._base_manager.filter(pk=fixture_id('transfer-recipient')).first()
    if recipient is None:
        return
    if (recipient.role != User.ATHLETE or recipient.username != 'manuale.destinataria'
            or recipient.email != 'destinataria@aurora.example.test' or recipient.connected_user_id is not None):
        raise CommandError('Transfer fixture identity belongs to another user.')
    if (SportAssociation.objects.filter(user=recipient).exists()
            or User._base_manager.filter(connected_user=recipient).exists()
            or Subscription._base_manager.filter(user=recipient).exclude(sport_association=association).exists()
            or Associate._base_manager.filter(user=recipient).exclude(sport_association=association).exists()
            or Payment._base_manager.filter(user=recipient).exclude(sport_association=association).exists()
            or SubscriptionTransfer.objects.filter(recipient=recipient).exclude(
                subscription__sport_association=association, requester=owner).exists()):
        raise CommandError('Transfer fixture identity has unrelated ownership; reset refused.')
    SubscriptionTransfer.objects.filter(subscription__sport_association=association,
        requester=owner, recipient=recipient).delete()
    Subscription._base_manager.filter(sport_association=association, user=recipient).update(user=owner)
    Payment._base_manager.filter(sport_association=association, user=recipient).update(user=owner)
    Associate._base_manager.filter(sport_association=association, user=recipient).update(user=None)
    # Notifications use a user-specific Redis broadcast. Never flush a shared
    # channel/database or construct a manager that also creates global channels.
    import redis
    from notifications.utils import REDIS_POOL
    from notifications.services import NotificationService
    redis.Redis(connection_pool=REDIS_POOL).delete('broadcast_' + str(recipient.pk))
    NotificationService.invalidate_user_broadcasts_cache(recipient.pk)
    recipient.delete()


def reset_payment_categories(association, owner):
    """Remove temporary categories after the disposable seed clears its payments.

    A soft-deleted category remains in edit form options. Keeping it between
    scenarios creates duplicate labels; VAT records need their own cleanup.
    Global categories and VAT still referenced by any retained category survive.
    """
    from application.models.payment_models import VatManagement
    from application.models.courses_models import CampsAndRetreatsPeriodsService

    if (association.pk != fixture_id('association') or owner.pk != fixture_id('owner')
            or association.user_id != owner.pk):
        raise CommandError('Category reset requires the owned manual fixture.')
    categories = PaymentCategory._base_manager.filter(sport_association=association).exclude(
        pk=fixture_id('payment-category'))
    ids = list(categories.values_list('pk', flat=True))
    if not ids:
        return
    # SET_NULL must never silently alter another association or account.
    if (Payment._base_manager.filter(payment_category_id__in=ids).exclude(sport_association=association).exists()
            or User._base_manager.filter(default_payment_category_id__in=ids).exclude(pk=owner.pk).exists()
            or CampsAndRetreatsPeriodsService.objects.filter(payment_category_id__in=ids).exclude(
                camps_and_retreats_period__camps_and_retreats__sport_association=association).exists()):
        raise CommandError('Temporary category has unrelated references; reset refused.')
    vat_ids = list(categories.exclude(vat_management=None).values_list('vat_management_id', flat=True))
    categories.delete()
    retained_vat = PaymentCategory._base_manager.filter(vat_management_id__in=vat_ids).values_list(
        'vat_management_id', flat=True)
    VatManagement.objects.filter(pk__in=vat_ids).exclude(pk__in=retained_vat).delete()


class Command(BaseCommand):
    help = 'Seed a run-owned manual demonstration database; never a developer/live database.'

    def add_arguments(self, parser):
        parser.add_argument('--reference-date', default='2026-09-30')
        parser.add_argument('--output', required=True, help='Private browser credentials JSON; never write to stdout.')
        parser.add_argument('--origin', required=True)
        parser.add_argument('--scenario', choices=['baseline', 'receipts-edit-delete', 'collaborator-removal', 'member-transfer'], default='baseline')

    def handle(self, *args, **options):
        assert_disposable()
        from freezegun import freeze_time
        reference = date.fromisoformat(options['reference_date'])
        with freeze_time(datetime.combine(reference, time(12), timezone.utc)), transaction.atomic():
            return self._seed(options)

    def _seed(self, options):
        reference = date.fromisoformat(options['reference_date'])
        when = datetime.combine(reference, time(12), timezone.utc)
        config = InstanceConfiguration.get_config()
        if config and config.primary_association_id != fixture_id('association'):
            raise CommandError('This database already contains an unrelated instance.')
        owner, _ = User.objects.update_or_create(pk=fixture_id('owner'), defaults={
            'username': 'manuale.aurora', 'email': 'segreteria@aurora.example.test',
            'first_name': 'Elena', 'last_name': 'Rossi', 'role': User.ASSOCIATION,
            'is_active': True, 'subscription_duration': User.LIKE_SEASON_YEAR,
            'subscription_start_day': 1, 'subscription_start_month': 9,
            'balance_sheet_start_day': 1, 'balance_sheet_start_month': 1, 'balance_sheet_year': 1,
            'membership_duration': User.LIKE_SEASON_YEAR, 'custom_end_date': False,
            'subscription_end_day': 31, 'subscription_end_month': 8,
            'dashboard_layout': None, 'auto_archive': False, 'auto_mark_attendance': False,
            'full_installments_plan': False, 'online_payments': False,
            'auto_paid_payment': False, 'starting_number_invoices': 0,
            'enumerate_invoices': True, 'payment_date_equal_invoice_date': False,
            'membership_starting_number': 0,
            'two_fa': False, 'two_fa_secret': '',
            'temporary_invoice_deletion': options['scenario'] != 'receipts-edit-delete',
        })
        # Only this disposable owner can exercise the real password/OTP login.
        # Rotate credentials on every reset; keep them in the private input file.
        login_password = secrets.token_urlsafe(24)
        owner.set_password(login_password)
        owner.save(update_fields=['password'])
        reader, _ = User.objects.update_or_create(pk=fixture_id('reader'), defaults={
            'username': 'manuale.segreteria', 'email': 'lettura@aurora.example.test',
            'first_name': 'Marco', 'last_name': 'Neri', 'role': User.COLLABORATOR,
            'connected_user': owner, 'is_active': True, 'deleted': False,
            'collaborator_role': User.CUSTOM_COLLABORATOR_ROLE, 'dashboard_layout': None,
            'collaborator_permissions': ['association.dashboard.read', 'association.members.read',
                'association.members.archive.read', 'association.courses.read', 'bookeeping.payments.read',
                'bookeeping.documents.invoices.read', 'association.instructor.read',
                'association.instructor.hours.read', 'association.carnet.read',
                'association.courses.attendance.read', 'other.settings.read',
                'other.users.collaborators.read', 'association.campsandretreats.read',
                'association.calendar.read', 'association.events.read'],
        })
        reader.set_unusable_password()
        reader.save(update_fields=['password'])
        # Deletion demonstrations use opt-in deterministic records. A normal
        # baseline reset removes only these IDs owned by this fictional owner.
        removable_id = fixture_id('removable-collaborator')
        invite_id = fixture_id('removable-invite')
        if options['scenario'] == 'collaborator-removal':
            if User._base_manager.filter(pk=removable_id).exclude(connected_user=owner).exists():
                raise CommandError('Removal fixture user belongs to another owner.')
            if CollaborationInvites.objects.filter(pk=invite_id).exclude(user=owner).exists():
                raise CommandError('Removal fixture invitation belongs to another owner.')
        User._base_manager.filter(pk=removable_id, connected_user=owner).delete()
        CollaborationInvites.objects.filter(pk=invite_id, user=owner).delete()
        removable = None
        if options['scenario'] == 'collaborator-removal':
            removable = User.objects.create(pk=removable_id,
                username='manuale.collaboratore.temporaneo', email='temporaneo@aurora.example.test',
                first_name='Paolo', last_name='Testa', role=User.COLLABORATOR,
                connected_user=owner, is_active=True, deleted=False,
                collaborator_role=User.CUSTOM_COLLABORATOR_ROLE,
                collaborator_permissions=['association.dashboard.read', 'other.users.collaborators.read'])
            removable.set_unusable_password()
            removable.save(update_fields=['password'])
            CollaborationInvites.objects.create(pk=invite_id, user=owner,
                email='invito.temporaneo@aurora.example.test', creation_date=when,
                accepted=False, expiration_date=when + timedelta(days=90),
                token=secrets.token_hex(16), collaborator_role=User.CUSTOM_COLLABORATOR_ROLE,
                collaborator_permissions=['association.dashboard.read'])
        association, _ = SportAssociation.objects.update_or_create(pk=fixture_id('association'), defaults={
            'user': owner, 'denomination': 'Associazione Sportiva Aurora', 'tax_code': '00000000000',
            'email': 'segreteria@aurora.example.test', 'address_city': 'Roma',
            'address': 'Via dello Sport 12', 'address_cap': '00100', 'subscription_fee': '25.00',
            'membership_fee': '0.00', 'multiple_subscription_fee': False, 'multiple_membership_fee': False,
            'president_first_name': 'Elena', 'president_last_name': 'Rossi',
            'regulation': 'Regolamento dimostrativo per le attività associative.', 'demand': 'Richiesta di iscrizione dimostrativa.',
            'additional_fields': [], 'additional_sections': [],
            'enabled_for': ['associate', 'associate-membership', 'membership'],
            'show_regulation_to_members': True, 'show_regulation_to_both': True,
            'show_regulation_to_athletes': True,
            'invoice_template': 'invoice.html', 'document_header': '', 'invoice_footer': '',
            'vat_number': '', 'iban': '', 'website': '', 'abbreviated': '', 'whatsapp': '',
            'federation': '', 'enroll_number': '', 'sport': '',
        })
        if not config:
            config = InstanceConfiguration.objects.create(domain=options['origin'], name='Assozeta',
                primary_association=association, self_hosted=True,
                setup_provenance=InstanceConfiguration.SETUP_PROVENANCE_FRESH,
                onboarding_completed_at=when)
        call_command('seed_selfhost', verbosity=0, stdout=self.stderr)
        reset_member_transfer(association, owner)
        AssociateImportDraft.objects.filter(sport_association=association).delete()
        AssociateImportDraftStatus.objects.filter(sport_association=association).delete()
        recipient = None
        if options['scenario'] == 'member-transfer':
            recipient = User.objects.create(pk=fixture_id('transfer-recipient'),
                username='manuale.destinataria', email='destinataria@aurora.example.test',
                first_name='Lucia', last_name='Testa', role=User.ATHLETE,
                connected_user=None, is_active=True, deleted=False)
            recipient.set_unusable_password()
            recipient.save(update_fields=['password'])
        BalanceSheet.objects.filter(sport_association=association).delete()
        CustomAccountsTransfer.objects.filter(sport_association=association).delete()
        CustomAccounts.objects.filter(sport_association=association).exclude(pk=fixture_id('cash-account')).delete()
        cash, _ = CustomAccounts.objects.update_or_create(pk=fixture_id('cash-account'), defaults={
            'sport_association': association, 'name': 'Cassa Aurora', 'initial_balance': '0.00',
            'account_type': CustomAccounts.CASH, 'account_code': 'CASSA', 'editable': False, 'enabled': True,
        })
        category, _ = PaymentCategory.objects.update_or_create(pk=fixture_id('payment-category'), defaults={
            'sport_association': association, 'name': 'Quote e attività associative',
            'creation_date': when, 'expense': False, 'tax_deductible': False, 'archived': False, 'deleted': False,
        })
        # Include soft-deleted rows: default managers hide them. Receipts belong
        # to this disposable association; detach them before restoring payments.
        # Remove association-owned attendance/carnets/enrollments before resetting
        # payments; use unfiltered enrollment manager to include hidden records.
        AttendanceRegistry.objects.filter(course__sport_association=association).delete()
        GlobalCalendarEvents.objects.filter(sport_association=association).delete()
        CampsAndRetreats.objects.filter(sport_association=association).delete()
        Reminders.objects.filter(sport_association=association).delete()
        CarnetSubscription.objects.filter(subscription__sport_association=association).delete()
        Carnet.objects.filter(sport_association=association).delete()
        CourseSubscription.objects.all_objects().filter(course__sport_association=association).delete()
        invoices = Invoice._base_manager.filter(sport_association=association)
        document_ids = list(invoices.exclude(document_pdf=None).values_list('document_pdf_id', flat=True))
        invoices.delete()
        Document.objects.filter(pk__in=document_ids).delete()
        Payment._base_manager.filter(sport_association=association).exclude(
            pk__in=[fixture_id(f'payment-{index}') for index in range(1, 4)]).delete()
        reset_payment_categories(association, owner)
        # Creating a new registration creates both a person and a subscription.
        # Include hidden/deleted records, and preserve the three stable fixtures.
        # All these deletes remain within the run-owned association and database.
        Subscription._base_manager.filter(sport_association=association).exclude(
            pk__in=[fixture_id(f'subscription-{index}') for index in range(1, 4)]).delete()
        Associate._base_manager.filter(sport_association=association).exclude(
            pk__in=[fixture_id(f'associate-{index}') for index in range(1, 4)]).delete()
        Signature.objects.filter(user=owner).delete()
        # Card links minted by a capture must not survive fixture reuse.
        SubscriptionToken.objects.filter(subscription__sport_association=association).delete()
        # Certificate uploads and subscription edits must not leak into the
        # next scenario. Protect medical records still used by another scope.
        foreign_medical_ids = Subscription._base_manager.exclude(sport_association=association).exclude(
            medical=None).values_list('medical_id', flat=True)
        certificates = MedicalCertificate.objects.filter(user=owner).exclude(pk__in=foreign_medical_ids)
        medical_document_ids = list(certificates.exclude(document=None).values_list('document_id', flat=True))
        Subscription._base_manager.filter(sport_association=association).update(medical=None)
        certificates.delete()
        # Documents can also be shared by invoices, subscription files and
        # other models. Preserve every surviving relation, including hidden rows.
        deletable_documents = Document.objects.filter(pk__in=medical_document_ids)
        for relation in Document._meta.related_objects:
            retained_ids = relation.related_model._base_manager.filter(
                **{relation.field.name + '__isnull': False}).values_list(relation.field.name, flat=True)
            deletable_documents = deletable_documents.exclude(pk__in=retained_ids)
        deletable_documents.delete()
        Subscription._base_manager.filter(sport_association=association).update(archived=False, deleted=False)
        # All mutations happen after the owned-database guard. Drop records
        # created by a previous demonstration while preserving stable fixtures.
        Course.objects.all_objects().filter(sport_association=association).exclude(pk=fixture_id('course')).delete()
        # Remove scenario-created tags and assignments so repeat capture starts clean.
        Tags.objects.filter(sport_association=association).delete()
        Instructor.objects.filter(user=owner).delete()
        subscriptions = []
        for index, (first, last, sex) in enumerate([('Giulia', 'Bianchi', 'F'), ('Luca', 'Verdi', 'M'), ('Sara', 'Conti', 'F')], 1):
            associate, _ = Associate.objects.update_or_create(pk=fixture_id(f'associate-{index}'), defaults={
                'sport_association': association, 'first_name': first, 'last_name': last,
                'sex': sex, 'born_date': date(1995 + index, 3, 10), 'born_city': 'Roma',
                'email': f'{first.lower()}@example.test', 'address_city': 'Roma',
                'address': f'Via delle Attività {index}', 'address_cap': '00100',
                'creation_date': when, 'draft': False, 'deleted': False,
            })
            subscription, _ = Subscription.objects.update_or_create(pk=fixture_id(f'subscription-{index}'), defaults={
                'sport_association': association, 'associate': associate, 'user': owner,
                'type': Subscription.ASSOCIATE_AND_MEMBER, 'role': Subscription.SOCIO_ORDINARIO,
                'status_flag': Subscription.ACCEPTED, 'draft': False, 'archived': False, 'deleted': False,
                'start_date': date(reference.year, 9, 1), 'end_date': date(reference.year + 1, 8, 31),
                'acceptance_date': reference, 'creation_date': when, 'subscription_number': str(index),
                'subscription_type': None, 'medical': None, 'notes': None, 'custom_data': None, 'additional_fields': None,
            })
            subscriptions.append(str(subscription.pk))
            payment, _ = Payment._base_manager.update_or_create(pk=fixture_id(f'payment-{index}'), defaults={
                'sport_association': association, 'associate': associate, 'user': owner,
                'amount': '25.00', 'type': Payment.CASH, 'subject': Payment.SUBSCRIPTION,
                'description': f'Quota associativa {first} {last}', 'paid': index != 3,
                'archived': False, 'deleted': False,
                'expense': False, 'payment_date': when if index != 3 else None, 'creation_date': when,
                'invoice': None, 'custom_accounts': cash, 'payment_category': category,
                'notes': None, 'meta_payment_categories': None, 'course': None,
            })
            payment.attachments.clear()
            subscription.payment = payment
            subscription.save(update_fields=['payment'])
        older_receipt = None
        if options['scenario'] == 'receipts-edit-delete':
            old_date = when - timedelta(days=29)
            payment = Payment.objects.get(pk=fixture_id('payment-1'))
            payment.creation_date = old_date
            payment.payment_date = old_date
            # Seed a real receipt record, leaving its PDF to the actual worker
            # invoked by receipt-list navigation. Never seed a fake PDF.
            older_receipt = Invoice.objects.create(pk=fixture_id('older-receipt'),
                sport_association=association, creation_date=old_date, number=7,
                membership_fee=payment.amount, activity_fee=0,
                description='Quota associativa Giulia Bianchi')
            payment.invoice = older_receipt
            payment.save(update_fields=['creation_date', 'payment_date', 'invoice'])
        Course.objects.update_or_create(pk=fixture_id('course'), defaults={
            'sport_association': association, 'title': 'Ginnastica per tutti', 'description': 'Corso dimostrativo di ginnastica.',
            'status_flag': Course.ACTIVE, 'fee': '120.00', 'start_date': when,
            'end_date': datetime(reference.year + 1, 6, 30, 12, tzinfo=timezone.utc),
            'creation_date': when, 'google_background_color': '#351DC2',
        })
        from freezegun import freeze_time
        with freeze_time(when):
            from application.services.jwt_token_service import JWTTokenService
            tokens = JWTTokenService.generate_tokens_for_user(owner)
            reader_tokens = JWTTokenService.generate_tokens_for_user(reader)
        data = {'fixture_version': FIXTURE_VERSION, 'reference_date': reference.isoformat(),
                'fixture_profile': options['scenario'],
                'integration_modes': {'email': {'backend': settings.EMAIL_BACKEND,
                    'external_delivery': settings.EMAIL_BACKEND != 'django.core.mail.backends.locmem.EmailBackend'}},
                'origin': options['origin'], 'association_id': str(association.pk), 'user_id': str(owner.pk),
                'login_username': owner.username, 'login_password': login_password,
                'token': tokens['access_token'], 'refresh_token': tokens['refresh_token'], 'subscription_ids': subscriptions,
                'course_id': str(fixture_id('course')),
                'payment_ids': [str(fixture_id(f'payment-{index}')) for index in range(1, 4)],
                'cash_account_id': str(cash.pk), 'payment_category_id': str(category.pk),
                'older_receipt_id': str(older_receipt.pk) if older_receipt else None,
                'identities': {'reader': {'user_id': str(reader.pk), 'token': reader_tokens['access_token'],
                    'refresh_token': reader_tokens['refresh_token']}}}
        if removable is not None:
            with freeze_time(when):
                removable_tokens = JWTTokenService.generate_tokens_for_user(removable)
            data['identities']['removable'] = {'user_id': str(removable.pk),
                'token': removable_tokens['access_token'],
                'refresh_token': removable_tokens['refresh_token']}
            data['removable_invite_id'] = str(invite_id)
        if recipient is not None:
            with freeze_time(when):
                recipient_tokens = JWTTokenService.generate_tokens_for_user(recipient)
            data['identities']['recipient'] = {'user_id': str(recipient.pk),
                'token': recipient_tokens['access_token'],
                'refresh_token': recipient_tokens['refresh_token'], 'email': recipient.email}
        output = Path(options['output'])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(data, indent=2))
        output.chmod(0o600)
        self.stdout.write(f'Manual fixtures ready: {len(subscriptions)} members; credentials saved privately.')
