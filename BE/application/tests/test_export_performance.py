import io
import os
import tempfile
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from application.models.courses_models import Course, CourseSubscription
from application.models.subscriptions_models import Subscription, SubscriptionFile
from application.models.user_models import Associate, User
from application.services.export_service import AssociationExportService
from application.tests.fixtures.factories import (
    create_test_course,
    create_test_document,
    create_test_invoice,
    create_test_medical_certificate,
    create_test_sport_association,
    create_test_subscription,
    create_test_user,
)


def logical_query_count(queries):
    """Exclude EXPLAIN statements installed by the API query profiler."""
    return sum(
        not query['sql'].lstrip().upper().startswith('EXPLAIN ')
        for query in queries.captured_queries
    )


class AssociationExportQueryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = create_test_user(role=User.ASSOCIATION)
        cls.association = create_test_sport_association(user=cls.owner)

        cls.associates = Associate.objects.bulk_create([
            Associate(
                user=cls.owner,
                sport_association=cls.association,
                first_name=f'Associate {index}',
                last_name='Export',
                tax_code=f'EXPORT{index:010d}',
            )
            for index in range(50)
        ])
        cls.subscriptions = Subscription.objects.bulk_create([
            Subscription(
                sport_association=cls.association,
                associate=associate,
                user=cls.owner,
                custom_data={},
                additional_fields={},
            )
            for associate in cls.associates
        ])
        cls.course = create_test_course(sport_association=cls.association)
        cls.course_subscriptions = CourseSubscription.objects.bulk_create([
            CourseSubscription(
                course=cls.course,
                subscription=subscription,
            )
            for subscription in cls.subscriptions
        ])

    def setUp(self):
        self.service = AssociationExportService(self.association.sport_association_id)

    @staticmethod
    def _legacy_query_count(queryset, m2m_fields=()):
        """Measure the pre-optimization relation access for comparison."""
        with CaptureQueriesContext(connection) as queries:
            for obj in queryset.iterator(chunk_size=1000):
                for field in obj._meta.get_fields():
                    if field.one_to_many or field.many_to_many:
                        continue
                    getattr(obj, field.name, None)
                for field_name in m2m_fields:
                    list(getattr(obj, field_name).values_list('pk', flat=True))
        return logical_query_count(queries)

    def _export_query_count(self, model_class, filename_prefix):
        with tempfile.TemporaryDirectory() as output_dir:
            with CaptureQueriesContext(connection) as queries:
                self.service.export_model_to_json(
                    model_class,
                    output_dir,
                    filename_prefix,
                )
        return logical_query_count(queries)

    def test_populated_foreign_keys_serialize_without_queries(self):
        associate = Associate.objects.get(pk=self.associates[0].pk)

        with self.assertNumQueries(0):
            record = self.service.serialize_record(associate)

        self.assertEqual(record['user_id'], str(self.owner.pk))
        self.assertEqual(
            record['sport_association_id'],
            str(self.association.pk),
        )

    def test_fifty_record_query_counts_are_constant_and_show_improvement(self):
        cases = [
            (Associate, '07_associates', (), 101, 1),
            (Subscription, '25_subscriptions', ('tags',), 201, 2),
            (
                CourseSubscription,
                '33_course_subscriptions',
                ('membership_payments',),
                151,
                2,
            ),
        ]

        for model_class, prefix, m2m_fields, expected_before, expected_after in cases:
            with self.subTest(model=model_class.__name__):
                queryset = self.service.get_queryset_for_model(model_class)
                before_queries = self._legacy_query_count(queryset, m2m_fields)
                after_queries = self._export_query_count(model_class, prefix)

                self.assertEqual(before_queries, expected_before)
                self.assertEqual(after_queries, expected_after)

    def test_m2m_document_ids_are_prefetched_serialized_and_reused(self):
        document = create_test_document()
        location = self.course.locations.model.objects.create(
            title='Prefetched location',
            sport_association=self.association,
        )
        location.documents.add(document)

        with tempfile.TemporaryDirectory() as output_dir:
            with CaptureQueriesContext(connection) as queries:
                self.service.export_model_to_json(
                    type(location),
                    output_dir,
                    '31_course_locations',
                )

        self.assertEqual(logical_query_count(queries), 2)
        self.assertIn(str(document.pk), self.service.document_ids)


class AssociationExportStorageTests(TestCase):
    def setUp(self):
        self.owner = create_test_user(role=User.ASSOCIATION)
        self.association = create_test_sport_association(user=self.owner)
        self.service = AssociationExportService(self.association.sport_association_id)

    def test_document_categories_are_bulk_annotated_and_files_open_once(self):
        subscription = create_test_subscription(sport_association=self.association)
        documents = {
            'medical_certificates': create_test_document(filepath='documents/medical.pdf'),
            'invoices': create_test_document(filepath='documents/invoice.pdf'),
            'subscription_documents': create_test_document(filepath='documents/subscription.pdf'),
            'general_documents': create_test_document(filepath='documents/general.pdf'),
        }
        create_test_medical_certificate(
            user=self.owner,
            document=documents['medical_certificates'],
        )
        create_test_invoice(
            sport_association=self.association,
            document_pdf=documents['invoices'],
        )
        SubscriptionFile.objects.create(
            subscription=subscription,
            document=documents['subscription_documents'],
        )
        self.service.document_ids.update(document.pk for document in documents.values())

        with tempfile.TemporaryDirectory() as output_dir, patch(
            'application.services.export_service.default_storage.open',
            side_effect=lambda *args: io.BytesIO(b'document-content'),
        ) as storage_open, patch(
            'application.services.export_service.default_storage.exists',
        ) as storage_exists:
            with CaptureQueriesContext(connection) as queries:
                exported = self.service.export_files(output_dir)

            for category, document in documents.items():
                exported_path = os.path.join(
                    output_dir,
                    'files',
                    category,
                    str(document.pk),
                    document.filename,
                )
                self.assertTrue(os.path.exists(exported_path))

        self.assertEqual(exported, 4)
        # One annotated document query plus one signature query, regardless of
        # the number of documents.
        self.assertEqual(logical_query_count(queries), 2)
        self.assertEqual(storage_open.call_count, 4)
        for document in documents.values():
            storage_open.assert_any_call(document.filepath, 'rb')
        storage_exists.assert_not_called()

    def test_missing_document_and_signature_keep_failure_statistics(self):
        document = create_test_document(filepath='documents/missing.pdf')
        self.service.document_ids.add(document.pk)
        subscription = create_test_subscription(
            sport_association=self.association,
            signature_storage_key='signatures/missing.png',
        )

        with tempfile.TemporaryDirectory() as output_dir, patch(
            'application.services.export_service.default_storage.open',
            side_effect=FileNotFoundError('missing'),
        ) as storage_open, patch(
            'application.services.export_service.default_storage.exists',
        ) as storage_exists:
            self.service.export_files(output_dir)

        self.assertEqual(self.service.stats['files_exported'], 0)
        self.assertEqual(self.service.stats['files_failed'], 2)
        self.assertEqual(self.service.stats['files_skipped'], 0)
        self.assertEqual(self.service.stats['signature_files_failed'], 1)
        self.assertEqual(self.service.errors, [])
        self.assertEqual(storage_open.call_count, 2)
        storage_open.assert_any_call(document.filepath, 'rb')
        storage_open.assert_any_call(subscription.signature_storage_key, 'rb')
        storage_exists.assert_not_called()

    def test_missing_reconstructed_document_path_remains_skipped(self):
        document = create_test_document(filepath=None, filename='missing.pdf')
        self.service.document_ids.add(document.pk)

        with tempfile.TemporaryDirectory() as output_dir, patch(
            'application.services.export_service.default_storage.open',
            side_effect=FileNotFoundError('missing'),
        ) as storage_open, patch(
            'application.services.export_service.default_storage.exists',
        ) as storage_exists:
            self.service.export_files(output_dir)

        self.assertEqual(self.service.stats['files_failed'], 0)
        self.assertEqual(self.service.stats['files_skipped'], 1)
        self.assertEqual(storage_open.call_count, 1)
        storage_exists.assert_not_called()
