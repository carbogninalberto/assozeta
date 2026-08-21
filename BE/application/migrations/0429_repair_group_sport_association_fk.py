from django.db import migrations


TABLE_NAME = 'application_group'
COLUMN_NAME = 'sport_association_id'
TARGET_TABLE = 'application_sportassociation'
TARGET_COLUMN = 'sport_association_id'
CONSTRAINT_NAME = 'application_group_sport_association_id_fk_sportassociation'


def repair_group_sport_association_fk(apps, schema_editor):
    """Repair fresh databases created by the original squashed migration.

    The squash initially created Group.sport_association as a foreign key to
    bakney_user and altered it later in the same migration.  PostgreSQL can be
    left with the original constraint even though Django's migration state is
    correct, so inspect the physical constraint and replace it when necessary.
    """
    connection = schema_editor.connection
    if connection.vendor != 'postgresql':
        return

    quote = schema_editor.quote_name
    expected_target = (TARGET_TABLE, TARGET_COLUMN)

    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, TABLE_NAME)

    matching_constraints = {
        name: details
        for name, details in constraints.items()
        if details.get('foreign_key') and details.get('columns') == [COLUMN_NAME]
    }
    has_expected_constraint = any(
        details.get('foreign_key') == expected_target
        for details in matching_constraints.values()
    )

    for name, details in matching_constraints.items():
        if details.get('foreign_key') == expected_target:
            continue
        schema_editor.execute(
            f'ALTER TABLE {quote(TABLE_NAME)} DROP CONSTRAINT {quote(name)}'
        )

    if has_expected_constraint:
        return

    schema_editor.execute(
        f'ALTER TABLE {quote(TABLE_NAME)} '
        f'ADD CONSTRAINT {quote(CONSTRAINT_NAME)} '
        f'FOREIGN KEY ({quote(COLUMN_NAME)}) '
        f'REFERENCES {quote(TARGET_TABLE)} ({quote(TARGET_COLUMN)}) '
        'DEFERRABLE INITIALLY DEFERRED'
    )


class Migration(migrations.Migration):

    dependencies = [
        ('application', '0428_remove_legacy_notification_channels'),
    ]

    operations = [
        migrations.RunPython(
            repair_group_sport_association_fk,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
