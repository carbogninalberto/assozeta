from django.db import migrations


def delete_platform_invoice_documents(apps, schema_editor):
    """
    Remove Document rows that were owned only by the retired platform-issued
    invoice model. This intentionally does not touch object storage; the
    Document model stores paths as plain metadata and file cleanup is not safe
    inside a schema migration.
    """
    SportAssociationInvoices = apps.get_model('application', 'SportAssociationInvoices')
    Document = apps.get_model('docmanager', 'Document')

    invoice_document_ids = set(
        SportAssociationInvoices._base_manager.exclude(document_id__isnull=True)
        .values_list('document_id', flat=True)
        .distinct()
    )

    if not invoice_document_ids:
        return

    document_label = Document._meta.label_lower
    invoice_label = SportAssociationInvoices._meta.label_lower
    referenced_elsewhere = set()

    for model in apps.get_models():
        if model._meta.label_lower == invoice_label:
            continue

        for field in model._meta.get_fields():
            if not getattr(field, 'is_relation', False) or getattr(field, 'auto_created', False):
                continue

            remote_model = getattr(getattr(field, 'remote_field', None), 'model', None)
            if getattr(getattr(remote_model, '_meta', None), 'label_lower', None) != document_label:
                continue

            if getattr(field, 'many_to_one', False) or getattr(field, 'one_to_one', False):
                references = model._base_manager.filter(**{
                    f'{field.attname}__in': invoice_document_ids,
                }).values_list(field.attname, flat=True)
                referenced_elsewhere.update(value for value in references if value is not None)

            elif getattr(field, 'many_to_many', False):
                through = field.remote_field.through
                document_field = through._meta.get_field(field.m2m_reverse_field_name())
                references = through._base_manager.filter(**{
                    f'{document_field.attname}__in': invoice_document_ids,
                }).values_list(document_field.attname, flat=True)
                referenced_elsewhere.update(value for value in references if value is not None)

    exclusive_document_ids = invoice_document_ids - referenced_elsewhere

    if exclusive_document_ids:
        Document._base_manager.filter(document_id__in=exclusive_document_ids).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('application', '0426_subscription_signature_backup_compatibility'),
    ]

    operations = [
        migrations.RunPython(delete_platform_invoice_documents, migrations.RunPython.noop),
        migrations.DeleteModel(
            name='SportAssociationInvoices',
        ),
    ]
