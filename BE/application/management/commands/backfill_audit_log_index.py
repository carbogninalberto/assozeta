"""
Copyright: Bakney S.r.l.
Management command to backfill AuditLogIndex for existing LogEntry records.

Usage:
    python manage.py backfill_audit_log_index [--batch-size 500] [--dry-run]
    python manage.py backfill_audit_log_index --content-type application.payment
"""
import logging
from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.contenttypes.models import ContentType
from auditlog.models import LogEntry

from application.models.audit_models import AuditLogIndex
from application.signals import resolve_sport_association_for_log_entry

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Backfill AuditLogIndex for existing LogEntry records'

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            type=int,
            default=500,
            help='Number of log entries to process in each batch (default: 500)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run without making actual changes',
        )
        parser.add_argument(
            '--content-type',
            type=str,
            help='Only process a specific content type (format: app_label.model)',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Limit total number of records to process',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        batch_size = options['batch_size']
        content_type_filter = options['content_type']
        limit = options['limit']

        if dry_run:
            self.stdout.write(self.style.WARNING('Running in DRY-RUN mode'))

        # Get all LogEntry records that don't have an index
        log_entries = LogEntry.objects.exclude(
            pk__in=AuditLogIndex.objects.values_list('log_entry_id', flat=True)
        ).select_related('content_type', 'actor').order_by('pk')

        if content_type_filter:
            try:
                app_label, model = content_type_filter.split('.')
                ct = ContentType.objects.get(app_label=app_label, model=model)
                log_entries = log_entries.filter(content_type=ct)
                self.stdout.write(f'Filtering by content type: {content_type_filter}')
            except (ValueError, ContentType.DoesNotExist) as e:
                self.stdout.write(self.style.ERROR(f'Invalid content type: {content_type_filter}'))
                return

        total_count = log_entries.count()
        if limit:
            total_count = min(total_count, limit)
            log_entries = log_entries[:limit]

        self.stdout.write(f'Found {total_count} LogEntry records to process')

        if total_count == 0:
            self.stdout.write(self.style.SUCCESS('No records to backfill'))
            return

        indexed_count = 0
        skipped_count = 0
        error_count = 0

        # Statistics by content type
        stats_by_type = {}

        # Process in batches
        processed = 0
        for offset in range(0, total_count, batch_size):
            batch = list(log_entries[offset:offset + batch_size])
            batch_num = offset // batch_size + 1
            batch_end = min(offset + batch_size, total_count)
            self.stdout.write(
                f'\nProcessing batch {batch_num} '
                f'({offset + 1}-{batch_end} of {total_count})'
            )

            indexes_to_create = []

            for entry in batch:
                try:
                    sport_association, resolution_path = resolve_sport_association_for_log_entry(entry)

                    ct_key = f"{entry.content_type.app_label}.{entry.content_type.model}" if entry.content_type else "unknown"
                    if ct_key not in stats_by_type:
                        stats_by_type[ct_key] = {'indexed': 0, 'skipped': 0, 'errors': 0}

                    if sport_association:
                        indexes_to_create.append(
                            AuditLogIndex(
                                log_entry=entry,
                                sport_association=sport_association,
                                resolution_path=resolution_path
                            )
                        )
                        indexed_count += 1
                        stats_by_type[ct_key]['indexed'] += 1
                    else:
                        skipped_count += 1
                        stats_by_type[ct_key]['skipped'] += 1

                except Exception as e:
                    error_count += 1
                    ct_key = f"{entry.content_type.app_label}.{entry.content_type.model}" if entry.content_type else "unknown"
                    if ct_key not in stats_by_type:
                        stats_by_type[ct_key] = {'indexed': 0, 'skipped': 0, 'errors': 0}
                    stats_by_type[ct_key]['errors'] += 1
                    self.stdout.write(
                        self.style.ERROR(f'  Error processing LogEntry {entry.pk}: {e}')
                    )

                processed += 1

            # Bulk create indexes
            if not dry_run and indexes_to_create:
                with transaction.atomic():
                    AuditLogIndex.objects.bulk_create(indexes_to_create, ignore_conflicts=True)
                self.stdout.write(f'  Created {len(indexes_to_create)} index records')
            elif dry_run:
                self.stdout.write(f'  [DRY-RUN] Would create {len(indexes_to_create)} index records')

        # Summary
        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(self.style.SUCCESS('Backfill complete!'))
        self.stdout.write(f'Total processed: {processed}')
        self.stdout.write(self.style.SUCCESS(f'Indexed: {indexed_count}'))
        self.stdout.write(self.style.WARNING(f'Skipped (no sport_association): {skipped_count}'))
        if error_count > 0:
            self.stdout.write(self.style.ERROR(f'Errors: {error_count}'))

        # Stats by content type (top 10)
        self.stdout.write('\nTop content types:')
        sorted_stats = sorted(stats_by_type.items(), key=lambda x: x[1]['indexed'], reverse=True)[:10]
        for ct_key, stats in sorted_stats:
            self.stdout.write(
                f'  {ct_key}: indexed={stats["indexed"]}, '
                f'skipped={stats["skipped"]}, errors={stats["errors"]}'
            )
        self.stdout.write('=' * 60)
