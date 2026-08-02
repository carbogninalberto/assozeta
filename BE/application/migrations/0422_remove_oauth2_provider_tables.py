"""
Migration to remove django-oauth-toolkit tables after JWT migration.

This migration drops the OAuth2 provider tables that are no longer needed
after migrating from OAuth2 tokens to JWT tokens.

Tables removed:
- oauth2_provider_accesstoken (had 53 rows)
- oauth2_provider_refreshtoken (had 76 rows)
- oauth2_provider_application (had 1 row)
- oauth2_provider_grant (was empty)
- oauth2_provider_idtoken (was empty)

Note: social_auth_* tables are KEPT as they're still used for Google/Apple login.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('application', '0421_create_audit_log_index'),
    ]

    operations = [
        # Drop tables in correct order (respecting foreign key constraints)
        # accesstoken and refreshtoken reference application
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS oauth2_provider_accesstoken CASCADE;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS oauth2_provider_refreshtoken CASCADE;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS oauth2_provider_grant CASCADE;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS oauth2_provider_idtoken CASCADE;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS oauth2_provider_application CASCADE;",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
