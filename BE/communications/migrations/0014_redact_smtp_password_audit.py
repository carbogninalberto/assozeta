import json

from django.db import migrations


def redact(apps, schema_editor):
    """Remove SMTP passwords from existing CommunicationConfiguration audit entries."""
    ContentType = apps.get_model('contenttypes', 'ContentType')
    LogEntry = apps.get_model('auditlog', 'LogEntry')
    content_type = ContentType.objects.filter(app_label='communications', model='communicationconfiguration').first()
    if content_type is None:
        return
    for entry in LogEntry.objects.filter(content_type=content_type).iterator():
        changes = entry.changes
        if isinstance(changes, str):
            try:
                changes = json.loads(changes)
            except ValueError:
                continue
        if isinstance(changes, dict) and 'email_smtp_password' in changes:
            del changes['email_smtp_password']
            entry.changes = changes
            entry.save(update_fields=['changes'])


class Migration(migrations.Migration):
    dependencies = [
        ('communications', '0013_staffboardmessage_document'),
        ('auditlog', '0017_add_actor_email'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]
    operations = [migrations.RunPython(redact, migrations.RunPython.noop)]
