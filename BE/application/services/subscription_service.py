"""
@ copyright: Bakney SRL

Service layer for subscription business logic.
This module contains service classes that encapsulate business logic
previously embedded in views, following the service layer pattern.

All services are stateless and use static methods for better testability.
"""
from datetime import datetime
import logging
import os
import re
import dateutil
import editdistance

from django.core.files.storage import default_storage
from django.utils.timezone import make_aware
from rest_framework.exceptions import ValidationError, PermissionDenied

from application.models.courses_models import CourseSubscription, CourseSubscriptionInstallment
from application.models.payment_models import Payment, PaymentCategory
from application.models.user_models import SportAssociation
from application.models.subscriptions_models import (
    MedicalCertificate, AssociateImportDraft,
    Tags, AssociateImportDraftStatus,
    Subscription
)
from application.utils.api_utils import (
    SubscriptionMaps, get_data_from_italian_fiscal_code
)
from application.tasks import send_email_template
from core import settings
from core.settings import STORAGE_DIR
from docmanager.models import Document

logger = logging.getLogger()


class SubscriptionService:
    """
    Service class for core subscription operations.
    Handles subscription CRUD operations and related business logic.
    """

    @staticmethod
    def update_subscription(subscription, data):
        """
        Update subscription with validated data.

        Args:
            subscription: Subscription object to update
            data: Dict containing update data

        Returns:
            Subscription: Updated subscription object
        """
        # Update subscription fields if provided
        if 'type' in data:
            subscription.type = data['type']
        if 'role' in data:
            subscription.role = data['role']
        if 'start_date' in data:
            subscription.start_date = data['start_date']
        if 'end_date' in data:
            subscription.end_date = data['end_date']
        if 'acceptance_date' in data:
            subscription.acceptance_date = data['acceptance_date']
        if 'resignation_date' in data:
            subscription.resignation_date = data['resignation_date']
        if 'subscription_number' in data:
            subscription.subscription_number = data['subscription_number']
        if 'subscription_type' in data:
            subscription.subscription_type = data['subscription_type']
        if 'custom_data' in data:
            subscription.custom_data = data['custom_data']
        if 'additional_fields' in data:
            subscription.additional_fields = data['additional_fields']
        if 'status_flag' in data:
            subscription.status_flag = data['status_flag']
        if 'notes' in data:
            subscription.notes = data['notes']

        # Clear document PDF to force regeneration
        subscription.document_pdf = None
        subscription.save()

        return subscription

    @staticmethod
    def delete_subscription(subscription, sport_association):
        """
        Delete subscription and clean up associated data.
        Handles course subscriptions, payments, invoices, and installments.

        Args:
            subscription: Subscription object to delete
            sport_association: SportAssociation object for validation

        Raises:
            PermissionDenied: If sport association doesn't match
        """
        # Validate ownership
        if subscription.sport_association.sport_association_id != sport_association.sport_association_id:
            raise PermissionDenied("User not allowed.")

        # Handle course subscriptions
        course_subscriptions = CourseSubscription.objects.filter(subscription=subscription)
        for course_subscription in course_subscriptions:
            # Delete unpaid payment and invoice
            if course_subscription.payment is not None and not course_subscription.payment.paid:
                if course_subscription.payment.invoice is not None:
                    course_subscription.payment.invoice.delete()
                course_subscription.payment.delete()
                course_subscription.payment = None

            # Handle installments
            if course_subscription.multi_payments:
                installments = CourseSubscriptionInstallment.objects.filter(
                    course_subscription=course_subscription
                )
                for installment in installments:
                    if installment.payment is not None and not installment.payment.paid:
                        if installment.payment.invoice is not None:
                            installment.payment.invoice.delete()
                        if installment.payment is not None:
                            installment.payment.delete()
                        installment.payment = None
                        installment.delete()
                    else:
                        installment.archived = True
                        installment.save()

            course_subscription.delete()

        # Delete unpaid subscription payment and invoice
        if subscription.payment is not None and not subscription.payment.paid:
            if subscription.payment.invoice is not None:
                subscription.payment.invoice.delete()
            subscription.payment.delete()
            subscription.payment = None

        # Archive then delete subscription
        subscription.archived = True
        subscription.save()

        # Clean up remaining unpaid payments WITHIN THIS SUBSCRIPTION'S DATE RANGE
        # Only delete payments created during this subscription's period
        payments = Payment.objects.filter(
            sport_association=sport_association,
            associate=subscription.associate,
            creation_date__gte=subscription.start_date,
            creation_date__lte=subscription.end_date,
            archived=False,
            paid=False
        )
        payments.delete()

        subscription.delete()

    @staticmethod
    def resolve_subscription_fee_and_meta(sport_association, data):
        """
        Resolve subscription fee and payment metadata based on plan selection.

        Args:
            sport_association: SportAssociation object
            data: Dict containing plan_id

        Returns:
            tuple: (fee_amount, payment_meta)

        Raises:
            ValidationError: If plan not found
        """
        fee_amount = sport_association.subscription_fee
        payment_meta = None

        if not sport_association.multiple_subscription_fee:
            return fee_amount, payment_meta

        available = sport_association.subscription_fee_plans
        found_plan = False
        plan = available[0] if len(available) > 0 else None

        for plan in available:
            if 'plan_id' in data and plan['id'] == data['plan_id']:
                fee_amount = plan['subscription_fee']
                found_plan = True
                payment_meta = {
                    "subscription_data": {
                        "subscription_fee": plan['subscription_fee'],
                        "name": plan['name'],
                        "id": plan['id']
                    }
                }
                break

        if not found_plan and plan:
            try:
                fee_amount = available[0]['subscription_fee']
                payment_meta = {
                    "subscription_data": {
                        "subscription_fee": plan['subscription_fee'],
                        "name": plan['name'],
                        "id": plan['id']
                    }
                }
            except IndexError:
                raise ValidationError('Plan not found.')
            except Exception as e:
                raise ValidationError(str(e))

        return fee_amount, payment_meta

    @staticmethod
    def get_latest_medical_certificate(associate):
        """
        Find the latest medical certificate for an associate.

        Args:
            associate: Associate object

        Returns:
            MedicalCertificate or None: Latest medical certificate
        """
        subscriptions = Subscription.objects.filter(associate=associate)
        medical = None

        for subscription in subscriptions:
            if subscription.medical is not None:
                if medical is None:
                    medical = subscription.medical
                elif subscription.medical.expiration_date is not None:
                    if medical.expiration_date is None or subscription.medical.expiration_date > medical.expiration_date:
                        medical = subscription.medical

        return medical

    @staticmethod
    def send_athlete_subscription_email(subscription):
        """
        Send notification email when athlete creates subscription.

        Args:
            subscription: Subscription object
        """
        email_data = {
            'athlete_first_name': subscription.associate.first_name,
            'athlete_last_name': subscription.associate.last_name,
            'sport_association': {
                "denomination": subscription.sport_association.denomination,
            },
            'app_host': settings.APP_URL,
            'settings': {
                'WHITELABEL_NAME': settings.WHITELABEL_NAME,
                'IS_WHITELABEL': settings.IS_WHITELABEL
            }
        }

        send_email_template.delay(
            recipient_list=[subscription.sport_association.user.email],
            subject=f"[{subscription.sport_association.denomination}] Ricordati di approvare l'iscrizione di "
                    f"{subscription.associate.first_name} {subscription.associate.last_name}",
            template="email/account/email_new_subscription.html",
            data=email_data,
            sport_association_id=subscription.sport_association.sport_association_id
        )

    @staticmethod
    def create_quick_subscription(sport_association, associate, data, user):
        """
        Create a quick subscription for an existing associate.

        Args:
            sport_association: SportAssociation object
            associate: Associate object
            data: Dict containing subscription data
            user: User creating the subscription

        Returns:
            Subscription: Created subscription object
        """
        fee_amount, payment_meta = SubscriptionService.resolve_subscription_fee_and_meta(
            sport_association, data
        )

        # Get payment category
        if sport_association.user.default_payment_category_courses is not None:
            payment_category = PaymentCategory.objects.filter(
                payment_category_id=sport_association.user.default_payment_category_courses
            ).first()
        else:
            payment_category = PaymentCategory.objects.filter(
                name__iexact='entrate e proventi da attività tipiche'
            ).first()

        # Create payment
        payment = Payment.objects.create(
            user=user,
            associate=associate,
            amount=fee_amount,
            subject=Payment.SUBSCRIPTION,
            sport_association=sport_association,
            payment_category=payment_category,
            meta=payment_meta
        )

        # Get latest medical certificate
        medical = SubscriptionService.get_latest_medical_certificate(associate)

        # Create subscription
        subscription = Subscription.objects.create(
            sport_association=sport_association,
            status_flag=Subscription.NOT_SIGNED,
            associate=associate,
            user_id=associate.user.user_id,
            medical=medical,
            payment=payment,
            meta={'plan_id': data.get('plan_id')}
        )

        return subscription

    @staticmethod
    def get_asd_custom_data_for_grouped_subscriptions(user):
        """
        Get ASD custom data for grouped subscriptions.

        Args:
            user: User object

        Returns:
            dict: Custom data dictionary
        """
        asd_custom_data = {}
        try:
            sport_association = SportAssociation.objects.get(user=user)
            if sport_association.additional_fields:
                asd_custom_data = {
                    field['field_name']: '' for field in sport_association.additional_fields
                }
        except Exception:
            pass
        return asd_custom_data


class TagService:
    """
    Service class for subscription tag operations.
    Handles tag CRUD and assignment operations.
    """

    @staticmethod
    def create_tag(tag_name, sport_association):
        """
        Create a new tag for subscriptions.

        Args:
            tag_name: Name of the tag
            sport_association: SportAssociation object

        Returns:
            Tags: Created tag object

        Raises:
            ValidationError: If tag_name is missing
        """
        if tag_name is None or (isinstance(tag_name, str) and tag_name.strip() == ''):
            raise ValidationError('tag_name is required')

        tag = Tags.objects.create(
            tag_name=tag_name,
            sport_association=sport_association
        )

        return tag

    @staticmethod
    def update_tag(tag_id, tag_name, sport_association):
        """
        Update an existing tag.

        Args:
            tag_id: UUID of the tag
            tag_name: New name for the tag
            sport_association: SportAssociation object

        Returns:
            Tags: Updated tag object

        Raises:
            ValidationError: If tag not found or tag_name missing
        """
        if tag_name is None or (isinstance(tag_name, str) and tag_name.strip() == ''):
            raise ValidationError('tag_name is required')

        tag = Tags.objects.filter(
            tag_id=tag_id,
            sport_association=sport_association
        ).first()

        if tag is None:
            raise ValidationError('tag not found')

        tag.tag_name = tag_name
        tag.save()

        return tag

    @staticmethod
    def delete_tag(tag_id, sport_association):
        """
        Delete a tag.

        Args:
            tag_id: UUID of the tag
            sport_association: SportAssociation object

        Raises:
            ValidationError: If tag not found
        """
        tag = Tags.objects.filter(
            tag_id=tag_id,
            sport_association=sport_association
        ).first()

        if tag is None:
            raise ValidationError('tag not found')

        tag.delete()

    @staticmethod
    def assign_tag_to_subscription(tag_id, subscription_id, sport_association):
        """
        Assign a tag to a subscription.

        Args:
            tag_id: UUID of the tag
            subscription_id: UUID of the subscription
            sport_association: SportAssociation object

        Returns:
            Subscription: Updated subscription object

        Raises:
            ValidationError: If tag or subscription not found
        """
        tag = Tags.objects.filter(
            tag_id=tag_id,
            sport_association=sport_association
        ).first()

        if tag is None:
            raise ValidationError('tag not found')

        subscription = Subscription.objects.filter(
            subscription_id=subscription_id,
            sport_association=sport_association
        ).first()

        if subscription is None:
            raise ValidationError('subscription not found')

        # Check if tag already assigned
        if tag not in subscription.tags.all():
            subscription.tags.add(tag)
            subscription.save()

        return subscription

    @staticmethod
    def unassign_tag_from_subscription(tag_id, subscription_id, sport_association):
        """
        Unassign a tag from a subscription.

        Args:
            tag_id: UUID of the tag
            subscription_id: UUID of the subscription
            sport_association: SportAssociation object

        Returns:
            Subscription: Updated subscription object

        Raises:
            ValidationError: If tag or subscription not found
        """
        tag = Tags.objects.filter(
            tag_id=tag_id,
            sport_association=sport_association
        ).first()

        if tag is None:
            raise ValidationError('tag not found')

        subscription = Subscription.objects.filter(
            subscription_id=subscription_id,
            sport_association=sport_association
        ).first()

        if subscription is None:
            raise ValidationError('subscription not found')

        # Remove tag if assigned
        if tag in subscription.tags.all():
            subscription.tags.remove(tag)
            subscription.save()

        return subscription


class MedicalCertificateService:
    """
    Service class for medical certificate operations.
    Handles medical certificate upload, management, and validation.
    """

    @staticmethod
    def upload_medical_certificate(medical_certificate_file, user):
        """
        Upload and store a medical certificate file.

        Args:
            medical_certificate_file: Uploaded file object
            user: User uploading the certificate

        Returns:
            tuple: (MedicalCertificate, Document, output_dict)

        Raises:
            TypeError: If medical_certificate key not present
        """
        if medical_certificate_file is None:
            raise TypeError("medical_certificate key not present!")

        # Create document record
        document = Document.objects.create(filename=medical_certificate_file.name)
        document.save()

        # Store file
        storing_path = os.path.join(
            STORAGE_DIR,
            str(document.creation_date.timestamp()),
            str(document.document_id)
        )
        file_path = os.path.join(storing_path, document.filename)
        default_storage.save(file_path, medical_certificate_file.file)

        # Create medical certificate record
        output_dict = {'expiring_date': None}
        medical_certificate_doc = MedicalCertificate.objects.create(
            document=document,
            user=user
        )

        # Handle expiration date if provided
        if output_dict['expiring_date'] is not None:
            try:
                medical_certificate_doc.expiration_date = make_aware(
                    datetime.strptime(output_dict['expiring_date'], '%Y-%m-%d')
                )
            except Exception as e:
                logger.exception(e)

        medical_certificate_doc.save()

        return medical_certificate_doc, document, output_dict

    @staticmethod
    def attach_certificate_to_subscription(medical_certificate_doc, subscription):
        """
        Attach a medical certificate to a subscription.

        Args:
            medical_certificate_doc: MedicalCertificate object
            subscription: Subscription object

        Returns:
            tuple: (Subscription, expiring_date_str)
        """
        subscription.medical_id = medical_certificate_doc.medical_id

        expiring_date_str = None
        try:
            if medical_certificate_doc.expiration_date is not None:
                # Convert to datetime if string
                if isinstance(medical_certificate_doc.expiration_date, str):
                    try:
                        medical_certificate_doc.expiration_date = datetime.strptime(
                            medical_certificate_doc.expiration_date, '%Y-%m-%d'
                        )
                    except ValueError as e:
                        logger.exception(e)

                # Format expiration date
                expiring_date_str = medical_certificate_doc.expiration_date.strftime('%Y-%m-%d')
        except Exception as e:
            logger.exception(e)

        subscription.save()

        return subscription, expiring_date_str

    @staticmethod
    def attach_certificate_to_draft(medical_certificate_doc, document, draft):
        """
        Attach a medical certificate to a draft subscription.

        Args:
            medical_certificate_doc: MedicalCertificate object
            document: Document object
            draft: AssociateImportDraft object

        Returns:
            AssociateImportDraft: Updated draft object
        """
        if 'medical_certificate' not in draft.data.keys():
            draft.data['medical_certificate'] = {
                'medical_id': None,
                'filename': None,
                'certificate_expring_date': None
            }

        draft.data['medical_certificate']['medical_id'] = str(medical_certificate_doc.medical_id)
        draft.data['medical_certificate']['filename'] = str(document.filename)
        draft.save()

        return draft


class SubscriptionImportService:
    """
    Service class for subscription import operations.
    Handles bulk import of subscriptions from files.
    """

    @staticmethod
    def upload_and_store_file(associates_file):
        """
        Upload file and store it, returning document and file path.

        Args:
            associates_file: Uploaded file object

        Returns:
            tuple: (Document, file_path)
        """
        document = Document.objects.create(filename=associates_file.name)
        document.save()

        storing_path = os.path.join(
            STORAGE_DIR,
            str(document.creation_date.timestamp()),
            str(document.document_id)
        )
        file_path = os.path.join(storing_path, document.filename)
        default_storage.save(file_path, associates_file.file)

        return document, file_path

    @staticmethod
    def auto_map_columns(columns, column_map):
        """
        Auto-map columns using edit distance similarity matching.
        Modifies column_map in place.

        Args:
            columns: List of column names from file
            column_map: Dict to store mappings
        """
        keys = list(column_map.keys())
        differential_factor = 0.25

        for col in columns:
            col = str(col)
            lowest_distance = len(col) * differential_factor
            key_to_map = None
            key_to_remove = None

            for key in keys:
                distance = editdistance.eval(col.lower(), key.lower())
                if distance < lowest_distance:
                    key_to_map = col
                    lowest_distance = distance
                    key_to_remove = key
                    if distance == 0:
                        break

            if key_to_remove is not None and key_to_map is not None:
                keys.remove(key_to_remove)
                column_map[key_to_remove] = key_to_map

    @staticmethod
    def retrieve_document_path(document_id):
        """
        Retrieve the file path for an existing document.

        Args:
            document_id: UUID of the document

        Returns:
            str: File path

        Raises:
            ValueError: If document not found
        """
        from application.utils.api_utils import is_valid_uuid
        is_valid_uuid(document_id)

        document = Document.objects.all().filter(document_id=document_id).first()
        if document is None:
            raise ValueError('No document found')

        storing_path = os.path.join(
            STORAGE_DIR,
            str(document.creation_date.timestamp()),
            str(document.document_id)
        )
        file_path = os.path.join(storing_path, document.filename)

        return file_path

    @staticmethod
    def process_row_value(value, inverse_mapped_key):
        """
        Process and transform a single row value based on field type.

        Args:
            value: Raw value from spreadsheet
            inverse_mapped_key: Field name to determine processing rules

        Returns:
            Processed value
        """
        if value is not None and isinstance(value, str):
            value = value.strip()
        if str(value) == 'nan':
            value = None

        # Handle membership dates
        if inverse_mapped_key in ('membership_start_date', 'membership_end_date'):
            if value:
                try:
                    value = dateutil.parser.parse(str(value)).date().strftime('%d/%m/%Y')
                except Exception:
                    value = None

        # Handle birth dates
        elif inverse_mapped_key in ('born_date', 'born_data_tutor'):
            if value:
                try:
                    value = dateutil.parser.parse(str(value)).date().strftime('%d/%m/%Y')
                except Exception:
                    value = None

        # Handle sex field default
        elif inverse_mapped_key == 'sex' and value is None:
            value = 'A'

        return value

    @staticmethod
    def create_medical_certificate_if_needed(inverse_mapped_key, value, user, row_data):
        """
        Create medical certificate if certificate_expiring_date is present.
        Modifies row_data in place.

        Args:
            inverse_mapped_key: Field name
            value: Field value
            user: User object
            row_data: Dict to store row data
        """
        if inverse_mapped_key == 'certificate_expiring_date' and value:
            medical_certificate = MedicalCertificate.objects.create(user=user)
            try:
                parsed_date = dateutil.parser.parse(str(value)).date().strftime('%d/%m/%Y')
                medical_certificate.expiration_date = make_aware(
                    datetime.strptime(parsed_date, '%d/%m/%Y')
                )
                medical_certificate.save()

                row_data['medical_certificate'] = {
                    'medical_id': str(medical_certificate.medical_id),
                    'filename': None,
                    'certificate_expring_date': parsed_date
                }
            except Exception as e:
                logger.info(f"Exception {e}")
                medical_certificate.delete()

    @staticmethod
    def enrich_with_tax_code_data(row_data):
        """
        Extract and enrich associate data from Italian tax codes.
        Modifies row_data in place.

        Args:
            row_data: Dict containing associate and tutor data
        """
        # Enrich associate data
        if ('tax_code' in row_data['associate'] and
            row_data['associate']['tax_code'] is not None and
            ('born_city' not in row_data['associate'] or row_data['associate']['born_city'] is None)):
            tax_code_data = get_data_from_italian_fiscal_code(row_data['associate']['tax_code'])
            if tax_code_data:
                row_data['associate']['born_city'] = tax_code_data['birthplace']['name']

        # Enrich tutor data
        if ('tax_code' in row_data['associate_tutor'] and
            row_data['associate_tutor']['tax_code'] is not None and
            ('born_city' not in row_data['associate_tutor'] or row_data['associate_tutor']['born_city'] is None)):
            tax_code_data = get_data_from_italian_fiscal_code(row_data['associate_tutor']['tax_code'])
            if tax_code_data:
                row_data['associate_tutor']['born_city'] = tax_code_data['birthplace']['name']

    @staticmethod
    def apply_unmapped_static_values(row_data, data):
        """
        Apply static unmapped values to associate and tutor data.
        Modifies row_data in place.

        Args:
            row_data: Dict containing associate and tutor data
            data: Dict containing unmapped static values
        """
        if 'unmapped_static' in data:
            for key, value in data['unmapped_static'].items():
                row_data['associate'][key] = value

        if 'unmapped_static_tutor' in data:
            if row_data['associate_tutor']:
                for key, value in data['unmapped_static_tutor'].items():
                    row_data['associate_tutor'][key] = value

    @staticmethod
    def validate_row_data(row_data, tax_code_regex):
        """
        Validate row data against mandatory fields and tax code format.

        Args:
            row_data: Dict containing associate data
            tax_code_regex: Compiled regex pattern for tax code

        Returns:
            bool: True if valid, False otherwise
        """
        valid = True

        for key in SubscriptionMaps.get_mandatory_fields():
            if key not in row_data['associate']:
                valid = False
                break
            if row_data['associate'][key] is None or row_data['associate'][key] == '':
                valid = False
                break

        if valid and re.search(tax_code_regex, row_data['associate']['tax_code']) is None:
            valid = False

        return valid

    @staticmethod
    def process_import_rows(rows_data_df, data, sport_association, user):
        """
        Process all rows from the dataframe and create draft objects.

        Args:
            rows_data_df: Pandas DataFrame with row data
            data: Dict containing mapping configuration
            sport_association: SportAssociation object
            user: User object

        Returns:
            list: List of AssociateImportDraft objects ready for bulk_create
        """
        tax_code_regex = re.compile(
            r'^[a-zA-Z]{6}[0-9]{2}[abcdehlmprstABCDEHLMPRST]{1}[0-9]{2}([a-zA-Z]{1}[0-9]{3})[a-zA-Z]{1}$',
            re.IGNORECASE
        )
        objects_to_create = []

        for index, row in rows_data_df.iterrows():
            row_data = {"associate": {}, "associate_tutor": {}}

            # Process each mapped key
            for key in data['map'].keys():
                inverse_mapped_key = SubscriptionMaps.KEY_TO_FIELD_ASSOCIATE[key.replace("tutore", "").strip()]

                # Extract and process value
                value = None
                if data['map'][key] is not None and data['map'][key] in row.keys():
                    value = row[data['map'][key]]

                value = SubscriptionImportService.process_row_value(value, inverse_mapped_key)

                # Handle medical certificate creation
                SubscriptionImportService.create_medical_certificate_if_needed(
                    inverse_mapped_key, value, user, row_data
                )

                # Assign value to appropriate target (tutor or associate)
                target = row_data['associate_tutor'] if "tutore" in key else row_data['associate']
                if value is not None:
                    target[inverse_mapped_key] = value
                elif 'default' in data and key in data['default']:
                    target[inverse_mapped_key] = data['default'][key]

            # Enrich with tax code data
            SubscriptionImportService.enrich_with_tax_code_data(row_data)

            # Apply unmapped static values
            SubscriptionImportService.apply_unmapped_static_values(row_data, data)

            # Validate row data
            valid = SubscriptionImportService.validate_row_data(row_data, tax_code_regex)

            objects_to_create.append(
                AssociateImportDraft(
                    sport_association=sport_association,
                    data=row_data,
                    valid=valid
                )
            )

        return objects_to_create

    @staticmethod
    def manage_import_status(sport_association, action='start'):
        """
        Manage the import status lifecycle.

        Args:
            sport_association: SportAssociation object
            action: 'start' or 'end'
        """
        if action == 'start':
            AssociateImportDraftStatus.objects.filter(
                sport_association=sport_association,
                status_type=AssociateImportDraftStatus.IMPORTING
            ).delete()
            AssociateImportDraftStatus.objects.create(
                sport_association=sport_association,
                status_type=AssociateImportDraftStatus.IMPORTING,
                doing=True
            )
        elif action == 'end':
            AssociateImportDraftStatus.objects.filter(
                sport_association=sport_association,
                status_type=AssociateImportDraftStatus.IMPORTING
            ).delete()
