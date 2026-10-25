"""Read-only current-schema deletion dependencies for the private HTTP oracle.

This records ORM policy and actual PostgreSQL foreign keys separately. It does
not execute a collector, infer historical schemas, or prove cascade behavior.
"""
from collections import deque


def describe(models, connection):
    from auditlog.registry import auditlog
    from django.db.models.signals import pre_delete, post_delete
    from application.models import User
    from application.models.user_models import CollaborationInvites

    assert connection.vendor == 'postgresql'
    assert connection.settings_dict['NAME'] == 'assozeta_contract_accounts'
    assert str(connection.settings_dict['HOST']).startswith('/tmp/az-accounts-')

    def receivers(signal, model):
        synchronous, asynchronous = signal._live_receivers(model)
        return sorted({receiver.__module__ + '.' + receiver.__qualname__
                       for receiver in (*synchronous, *asynchronous)})

    model_rows, relations = {}, []
    for model in models:
        meta = model._meta
        model_rows[meta.label] = {
            'table': meta.db_table, 'primary_key': meta.pk.column,
            'auto_created': bool(meta.auto_created),
            'audited': auditlog.contains(model),
            'audit_fields': auditlog.get_model_fields(model) if auditlog.contains(model) else None,
            'audit_serialization': auditlog.get_serialize_options(model) if auditlog.contains(model) else None,
            'pre_delete_receivers': receivers(pre_delete, model),
            'post_delete_receivers': receivers(post_delete, model),
            'private_cascade_fields': sorted(field.name for field in meta.private_fields
                if hasattr(field, 'bulk_related_objects')),
            'fields': [{'name': field.name, 'column': field.column,
                        'type': field.get_internal_type(), 'nullable': field.null}
                       for field in meta.local_fields],
        }
        for field in meta.local_fields:
            remote = getattr(field, 'remote_field', None)
            target = getattr(remote, 'model', None)
            if target is None or not hasattr(target, '_meta'):
                continue
            relations.append({
                'source': meta.label, 'source_table': meta.db_table,
                'column': field.column, 'target': target._meta.label,
                'target_table': target._meta.db_table,
                'target_column': field.target_field.column,
                'on_delete': remote.on_delete.__name__,
                'nullable': field.null, 'db_constraint': field.db_constraint,
            })
    relations.sort(key=lambda row: (row['target'], row['source'], row['column']))
    closure = {}
    for root in (User, CollaborationInvites):
        reached, pending = {root._meta.label}, deque([root._meta.label])
        while pending:
            target = pending.popleft()
            for relation in relations:
                source = relation['source']
                if relation['target'] == target and relation['on_delete'] == 'CASCADE' and source not in reached:
                    reached.add(source)
                    pending.append(source)
        closure[root._meta.label] = {
            'potentially_deleted_models': sorted(reached),
            'incoming_relations': [row for row in relations if row['target'] in reached],
        }
    with connection.cursor() as cursor:
        cursor.execute('''
            SELECT constraint_row.conname, child.relname, parent.relname,
                   constraint_row.confdeltype, constraint_row.condeferrable,
                   constraint_row.condeferred, pg_get_constraintdef(constraint_row.oid)
              FROM pg_constraint AS constraint_row
              JOIN pg_class AS child ON child.oid = constraint_row.conrelid
              JOIN pg_class AS parent ON parent.oid = constraint_row.confrelid
              JOIN pg_namespace AS namespace ON namespace.oid = child.relnamespace
             WHERE constraint_row.contype = 'f' AND namespace.nspname = current_schema()
             ORDER BY child.relname, constraint_row.conname
        ''')
        constraints = [{'name': name, 'source_table': child, 'target_table': parent,
                        'delete_action': action, 'deferrable': deferrable,
                        'initially_deferred': deferred, 'definition': definition}
                       for name, child, parent, action, deferrable, deferred, definition in cursor.fetchall()]
    assert any(row['source_table'] == 'bakney_user' and row['target_table'] == 'bakney_user'
               for row in constraints), 'Expected the real self-user foreign key'
    required = {('application.CollaborationInvites', 'application.User', 'CASCADE'),
                ('application.Associate', 'application.User', 'DO_NOTHING'),
                ('instance.DataRestore', 'application.User', 'PROTECT')}
    observed = {(row['source'], row['target'], row['on_delete']) for row in relations}
    assert required <= observed, ('Required deletion boundaries changed', required - observed)
    return {'scope': 'Current-model schema metadata only; no cascade or historical acceptance',
            'models': model_rows, 'relations': relations, 'roots': closure,
            'postgresql_foreign_keys': constraints, 'metadata_queries': 1,
            'business_operations_executed': False}
