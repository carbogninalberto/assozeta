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


OAUTH_TABLES = (
    'oauth2_provider_accesstoken',
    'oauth2_provider_refreshtoken',
    'oauth2_provider_grant',
    'oauth2_provider_idtoken',
    'oauth2_provider_application',
)


def drop_oauth_tables(apps, schema_editor):
    cascade = ' CASCADE' if schema_editor.connection.vendor == 'postgresql' else ''
    quote_name = schema_editor.quote_name
    for table_name in OAUTH_TABLES:
        schema_editor.execute(
            f'DROP TABLE IF EXISTS {quote_name(table_name)}{cascade};'
        )


class Migration(migrations.Migration):

    dependencies = [
        ('application', '0421_create_audit_log_index'),
    ]

    operations = [
        # Drop tables in correct order (respecting foreign key constraints)
        # accesstoken and refreshtoken reference application
        migrations.RunPython(
            code=drop_oauth_tables,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
