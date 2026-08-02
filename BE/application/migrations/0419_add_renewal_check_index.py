# Generated manually 2025-10-15
# Purpose: Add indexes to optimize renewal_available queries if re-enabled

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('application', '0418_remove_associate_application_sport_a_f36e4c_idx_and_more'),
    ]

    operations = [
        # Composite index for renewal_available queries
        # Speeds up: WHERE sport_association_id = X AND start_date >= Y AND archived = FALSE
        migrations.AddIndex(
            model_name='subscription',
            index=models.Index(
                fields=['sport_association', 'start_date', 'archived'],
                name='sub_renewal_idx'
            ),
        ),

        # Functional index for case-insensitive tax_code lookup
        # Speeds up: WHERE LOWER(tax_code) = 'xxx'
        migrations.RunSQL(
            sql='''
            CREATE INDEX IF NOT EXISTS associate_tax_code_lower_idx
            ON application_associate (LOWER(tax_code));
            ''',
            reverse_sql='DROP INDEX IF EXISTS associate_tax_code_lower_idx;'
        ),
    ]
