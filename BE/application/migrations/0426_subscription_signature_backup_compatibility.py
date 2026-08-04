from django.db import migrations

from application.migration_helpers import (
    migrate_subscription_signature_compatibility,
    restore_legacy_signature_column,
)


class Migration(migrations.Migration):

    dependencies = [
        ('application', '0425_subscription_signature_storage_key'),
    ]

    operations = [
        migrations.RunPython(
            migrate_subscription_signature_compatibility,
            restore_legacy_signature_column,
        ),
    ]
