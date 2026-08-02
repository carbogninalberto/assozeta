"""
Management command to migrate Subscription signatures from base64 in database to S3 storage.

Usage:
    python manage.py migrate_subscription_signatures_to_s3 [--dry-run] [--batch-size 100]
"""
import base64
import binascii
import os
import logging
from datetime import datetime

from django.core.management.base import BaseCommand
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction

from application.models.subscriptions_models import Subscription
from core.settings import STORAGE_DIR, AWS_STORAGE_BUCKET_NAME, AWS_LOCATION

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Migrate Subscription signatures from base64 to S3 storage'

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

        if dry_run:
            self.stdout.write(self.style.WARNING('Running in DRY-RUN mode - no changes will be made'))

        # Find all subscriptions with signatures that contain base64 data
        subscriptions = Subscription.objects.filter(
            signature__isnull=False
        ).exclude(
            signature=''
        )

        total_count = subscriptions.count()
        self.stdout.write(f'Found {total_count} subscriptions with signatures')

        if total_count == 0:
            self.stdout.write(self.style.SUCCESS('No signatures to migrate'))
            return

        migrated_count = 0
        skipped_count = 0
        error_count = 0

        # Process in batches
        for offset in range(0, total_count, batch_size):
            batch = subscriptions[offset:offset + batch_size]
            self.stdout.write(f'\nProcessing batch {offset // batch_size + 1} ({offset + 1}-{min(offset + batch_size, total_count)} of {total_count})')

            for subscription in batch:
                try:
                    result = self.migrate_signature(subscription, dry_run)
                    if result == 'migrated':
                        migrated_count += 1
                    elif result == 'skipped':
                        skipped_count += 1
                except Exception as e:
                    error_count += 1
                    self.stdout.write(
                        self.style.ERROR(
                            f'Error processing subscription {subscription.subscription_id}: {str(e)}'
                        )
                    )
                    logger.error(
                        f'Error migrating signature for subscription {subscription.subscription_id}',
                        exc_info=True
                    )

        # Summary
        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(self.style.SUCCESS(f'Migration complete!'))
        self.stdout.write(f'Total subscriptions: {total_count}')
        self.stdout.write(self.style.SUCCESS(f'Migrated: {migrated_count}'))
        self.stdout.write(self.style.WARNING(f'Skipped (already URLs): {skipped_count}'))
        if error_count > 0:
            self.stdout.write(self.style.ERROR(f'Errors: {error_count}'))
        self.stdout.write('=' * 60)

    def migrate_signature(self, subscription, dry_run=False):
        """
        Migrate a single subscription signature to S3.
        Returns: 'migrated', 'skipped', or raises exception
        """
        signature = subscription.signature

        # Skip if signature is None or empty
        if not signature:
            return 'skipped'

        # Check if it's already a URL (starts with http:// or https://)
        if signature.startswith('http://') or signature.startswith('https://'):
            self.stdout.write(
                self.style.WARNING(
                    f'Skipping {subscription.subscription_id} - already a URL'
                )
            )
            return 'skipped'

        # Check if it's a base64 string
        if not ('base64' in signature.lower() or signature.startswith('data:image')):
            # Try to detect if it's base64 by attempting to decode
            try:
                base64.b64decode(signature[:100])  # Test first 100 chars
            except (binascii.Error, ValueError):
                self.stdout.write(
                    self.style.WARNING(
                        f'Skipping {subscription.subscription_id} - not a valid base64 or URL'
                    )
                )
                return 'skipped'

        # Extract base64 data
        if ',' in signature:
            # Format: "data:image/png;base64,iVBORw0KG..."
            _, signature_data = signature.split(',', 1)
        else:
            signature_data = signature

        # Decode base64
        try:
            decoded_signature = base64.b64decode(signature_data)
        except (binascii.Error, ValueError) as e:
            raise ValueError(f"Failed to decode base64 signature: {e}")

        # Generate storage path
        timestamp = datetime.now().timestamp()
        storage_path = os.path.join(
            STORAGE_DIR,
            f'subscriptions/{subscription.subscription_id}/signature_{timestamp}.png'
        )

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f'[DRY-RUN] Would migrate {subscription.subscription_id} to {storage_path}'
                )
            )
            return 'migrated'

        # Upload to S3
        with transaction.atomic():
            saved_path = default_storage.save(
                storage_path,
                ContentFile(decoded_signature)
            )

            # Set ACL to public-read
            s3_client = default_storage.connection.meta.client
            s3_client.put_object_acl(
                Bucket=AWS_STORAGE_BUCKET_NAME,
                Key=f"{AWS_LOCATION}/{saved_path}",
                ACL='public-read'
            )

            # Generate CDN URL
            cdn_url = os.path.join(
                'https://bakney-object-spaces.fra1.cdn.digitaloceanspaces.com',
                AWS_LOCATION,
                storage_path
            )

            # Update subscription with URL in the NEW signature_url field
            # Keep the old signature field as backup
            subscription.signature_url = cdn_url
            subscription.save(update_fields=['signature_url'])

            self.stdout.write(
                self.style.SUCCESS(
                    f'Migrated {subscription.subscription_id} to {cdn_url}'
                )
            )

        return 'migrated'
