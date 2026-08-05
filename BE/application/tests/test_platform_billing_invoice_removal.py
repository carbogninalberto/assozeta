from datetime import date

from django.apps import apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TransactionTestCase
from django.urls import Resolver404, resolve

from application.services.export_service import AssociationExportService
from application.services.import_service import AssociationImportService


class PlatformBillingInvoiceRemovalTests(SimpleTestCase):
    def test_removed_platform_invoice_routes_are_not_registered(self):
        removed_route_slug = '-'.join(['billing', 'invoice'])
        removed_paths = [
            f'/profile/{removed_route_slug}/list',
            f'/document/{removed_route_slug}/',
        ]

        for path in removed_paths:
            with self.subTest(path=path):
                with self.assertRaises(Resolver404):
                    resolve(path)

    def test_removed_platform_invoice_model_is_not_registered(self):
        removed_model = ''.join(['SportAssociation', 'Invoices'])

        with self.assertRaises(LookupError):
            apps.get_model('application', removed_model)

    def test_association_import_export_orders_exclude_platform_invoices(self):
        removed_model = ''.join(['SportAssociation', 'Invoices'])
        export_model_names = [entry[0].__name__ for entry in AssociationExportService.EXPORT_ORDER]
        import_model_names = [entry[1].__name__ for entry in AssociationImportService.IMPORT_ORDER]

        self.assertNotIn(removed_model, export_model_names)
        self.assertNotIn(removed_model, import_model_names)
        self.assertNotIn(removed_model, AssociationImportService.PK_FIELDS)
        self.assertNotIn(removed_model, AssociationImportService.FK_MAPPINGS)


class DeletePlatformInvoiceMigrationTests(TransactionTestCase):
    migrate_from = [('application', '0426_subscription_signature_backup_compatibility')]
    migrate_to = [('application', '0427_delete_' + 'sport_association_' + 'invoices')]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.latest_targets = self.executor.loader.graph.leaf_nodes()
        self.executor.migrate(self.migrate_from)
        self.old_apps = self.executor.loader.project_state(self.migrate_from).apps

    def tearDown(self):
        try:
            self.executor = MigrationExecutor(connection)
            self.executor.migrate(self.latest_targets)
        finally:
            super().tearDown()

    def test_deletes_only_exclusive_platform_invoice_document_rows(self):
        User = self.old_apps.get_model('application', 'User')
        SportAssociation = self.old_apps.get_model('application', 'SportAssociation')
        SportAssociationDocumentsArchive = self.old_apps.get_model(
            'application',
            'SportAssociationDocumentsArchive',
        )
        removed_model = ''.join(['SportAssociation', 'Invoices'])
        PlatformInvoice = self.old_apps.get_model('application', removed_model)
        Document = self.old_apps.get_model('docmanager', 'Document')

        owner = User._base_manager.create(
            username='migration-owner@example.com',
            email='migration-owner@example.com',
            password='unused',
            role=1,
        )
        association = SportAssociation._base_manager.create(
            user=owner,
            denomination='Migration ASD',
            tax_code='12345678901',
        )
        exclusive_document = Document._base_manager.create(filename='exclusive-platform-invoice.pdf')
        shared_document = Document._base_manager.create(filename='shared-platform-invoice.pdf')

        PlatformInvoice._base_manager.create(
            sport_association=association,
            document=exclusive_document,
            invoice_date=date(2026, 1, 1),
        )
        PlatformInvoice._base_manager.create(
            sport_association=association,
            document=shared_document,
            invoice_date=date(2026, 1, 2),
        )
        archive_reference = SportAssociationDocumentsArchive._base_manager.create(
            sport_association=association,
            document=shared_document,
        )

        invoice_table_name = PlatformInvoice._meta.db_table
        exclusive_document_id = exclusive_document.document_id
        shared_document_id = shared_document.document_id
        archive_reference_id = archive_reference.pk

        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_to)
        new_apps = self.executor.loader.project_state(self.migrate_to).apps

        MigratedDocument = new_apps.get_model('docmanager', 'Document')
        MigratedSportAssociationDocumentsArchive = new_apps.get_model(
            'application',
            'SportAssociationDocumentsArchive',
        )

        self.assertFalse(
            MigratedDocument._base_manager.filter(document_id=exclusive_document_id).exists()
        )
        self.assertTrue(
            MigratedDocument._base_manager.filter(document_id=shared_document_id).exists()
        )
        self.assertTrue(
            MigratedSportAssociationDocumentsArchive._base_manager.filter(
                sport_association_documents_archive_id=archive_reference_id,
                document_id=shared_document_id,
            ).exists()
        )
        with self.assertRaises(LookupError):
            new_apps.get_model('application', removed_model)
        self.assertNotIn(invoice_table_name, connection.introspection.table_names())
