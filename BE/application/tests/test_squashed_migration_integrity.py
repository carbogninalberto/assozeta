import importlib

from django.db import migrations


SQUASHED_MIGRATION = (
    "application.migrations."
    "0001_initial_squashed_0413_associate_associate_sport_assoc_id_idx"
)


def test_event_subscription_index_is_removed_before_indexed_fields():
    """SQLite must not rebuild EventSubscription with a stale field index."""
    operations = importlib.import_module(SQUASHED_MIGRATION).Migration.operations

    remove_index_position = next(
        index
        for index, operation in enumerate(operations)
        if isinstance(operation, migrations.RemoveIndex)
        and operation.model_name == "eventsubscription"
        and operation.name == "application_event_i_46528d_idx"
    )
    first_indexed_field_removal = next(
        index
        for index, operation in enumerate(operations)
        if isinstance(operation, migrations.RemoveField)
        and operation.model_name == "eventsubscription"
        and operation.name in {"event", "user"}
    )

    assert remove_index_position < first_indexed_field_removal


def test_oauth_table_removal_does_not_use_postgres_sql_on_all_databases():
    operations = importlib.import_module(
        "application.migrations.0422_remove_oauth2_provider_tables"
    ).Migration.operations

    assert not any(
        isinstance(operation, migrations.RunSQL)
        and "CASCADE" in str(operation.sql).upper()
        for operation in operations
    )
