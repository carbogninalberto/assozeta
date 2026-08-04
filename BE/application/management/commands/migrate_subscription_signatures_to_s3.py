"""
Migrate legacy Subscription.signature values to the current storage fields.

The legacy signature model field was removed, but some old installations may
still have a database column containing base64 data or public URLs. This command
detects that column safely and moves values to signature_storage_key/signature_url.
"""
import base64
import binascii
import logging

from django.core.management.base import BaseCommand
from django.core.files.storage import default_storage
from django.db import connection, transaction

from application.models.subscriptions_models import Subscription

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Migrate legacy Subscription.signature values to configured storage'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run without making actual changes',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=100,
            help='Number of subscriptions to process in each batch (default: 100)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        batch_size = options['batch_size']

        table_name = Subscription._meta.db_table
        legacy_column = 'signature'

        if not self._legacy_column_exists(table_name, legacy_column):
            self.stdout.write(self.style.SUCCESS('No legacy subscription.signature column found'))
            return

        if dry_run:
            self.stdout.write(self.style.WARNING('Running in DRY-RUN mode - no changes will be made'))

        total_count = self._legacy_signature_count(table_name, legacy_column)
        self.stdout.write(f'Found {total_count} legacy signatures')

        migrated_count = 0
        skipped_count = 0
        error_count = 0

        for offset in range(0, total_count, batch_size):
            rows = self._legacy_signature_rows(table_name, legacy_column, batch_size, offset)
            self.stdout.write(
                f'\nProcessing batch {offset // batch_size + 1} '
                f'({offset + 1}-{min(offset + batch_size, total_count)} of {total_count})'
            )

            for subscription_id, legacy_signature in rows:
                try:
                    subscription = Subscription.objects.get(subscription_id=subscription_id)
                    result = self.migrate_signature(subscription, legacy_signature, dry_run)
                    if result == 'migrated':
                        migrated_count += 1
                    else:
                        skipped_count += 1
                except Exception as e:
                    error_count += 1
                    self.stdout.write(
                        self.style.ERROR(
                            f'Error processing subscription {subscription_id}: {str(e)}'
                        )
                    )
                    logger.error(
                        'Error migrating legacy signature for subscription %s',
                        subscription_id,
                        exc_info=True
                    )

        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(self.style.SUCCESS('Migration complete!'))
        self.stdout.write(f'Total legacy signatures: {total_count}')
        self.stdout.write(self.style.SUCCESS(f'Migrated: {migrated_count}'))
        self.stdout.write(self.style.WARNING(f'Skipped: {skipped_count}'))
        if error_count > 0:
            self.stdout.write(self.style.ERROR(f'Errors: {error_count}'))
        self.stdout.write('=' * 60)

    def _legacy_column_exists(self, table_name, column_name):
        with connection.cursor() as cursor:
            columns = connection.introspection.get_table_description(cursor, table_name)
        return column_name in {column.name for column in columns}

    def _legacy_signature_count(self, table_name, legacy_column):
        quoted_table = connection.ops.quote_name(table_name)
        quoted_column = connection.ops.quote_name(legacy_column)
        with connection.cursor() as cursor:
            cursor.execute(
                f'SELECT COUNT(*) FROM {quoted_table} '
                f'WHERE {quoted_column} IS NOT NULL AND {quoted_column} <> %s',
                ['']
            )
            return cursor.fetchone()[0]

    def _legacy_signature_rows(self, table_name, legacy_column, limit, offset):
        quoted_table = connection.ops.quote_name(table_name)
        quoted_column = connection.ops.quote_name(legacy_column)
        quoted_pk = connection.ops.quote_name('subscription_id')
        with connection.cursor() as cursor:
            cursor.execute(
                f'SELECT {quoted_pk}, {quoted_column} FROM {quoted_table} '
                f'WHERE {quoted_column} IS NOT NULL AND {quoted_column} <> %s '
                f'ORDER BY {quoted_pk} LIMIT %s OFFSET %s',
                ['', limit, offset]
            )
            return cursor.fetchall()

    def migrate_signature(self, subscription, legacy_signature, dry_run=False):
        if not legacy_signature:
            return 'skipped'
        if subscription.signature_storage_key or subscription.signature_url:
            self.stdout.write(
                self.style.WARNING(
                    f'Skipping {subscription.subscription_id} - destination already populated'
                )
            )
            return 'skipped'

        if legacy_signature.startswith('http://') or legacy_signature.startswith('https://'):
            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'[DRY-RUN] Would keep legacy URL for {subscription.subscription_id}'
                    )
                )
                return 'migrated'
            subscription.signature_url = legacy_signature
            subscription.signature_storage_key = None
            subscription.save(update_fields=['signature_url', 'signature_storage_key'])
            self.stdout.write(self.style.SUCCESS(f'Kept legacy URL for {subscription.subscription_id}'))
            return 'migrated'

        signature_data = legacy_signature.split(',', 1)[1] if ',' in legacy_signature else legacy_signature
        try:
            base64.b64decode(signature_data[:100])
        except (binascii.Error, ValueError):
            self.stdout.write(
                self.style.WARNING(
                    f'Skipping {subscription.subscription_id} - not valid base64 or URL'
                )
            )
            return 'skipped'

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f'[DRY-RUN] Would migrate {subscription.subscription_id} to configured storage'
                )
            )
            return 'migrated'

        storage_key = None
        try:
            with transaction.atomic():
                storage_key = subscription.set_signature_from_base64(legacy_signature)
                subscription.save(update_fields=['signature_url', 'signature_storage_key'])
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Migrated {subscription.subscription_id} to storage key {storage_key}'
                    )
                )
        except Exception:
            if storage_key:
                default_storage.delete(storage_key)
            raise

        return 'migrated'
