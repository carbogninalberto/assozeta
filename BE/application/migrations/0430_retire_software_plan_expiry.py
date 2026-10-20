from django.db import migrations
from django.utils import timezone


def disable_expiry_schedule(apps, schema_editor):
    tasks = apps.get_model('django_celery_beat', 'PeriodicTask')
    changed = tasks.objects.using(schema_editor.connection.alias).filter(
        task__in=['auto_move_users_to_base_plan', 'application.tasks.auto_move_users_to_base_plan',
                  'nurturing_warm_leads', 'application.tasks.nurturing_warm_leads'],
        enabled=True,
    ).update(enabled=False)
    if changed:
        apps.get_model('django_celery_beat', 'PeriodicTasks').objects.using(
            schema_editor.connection.alias).update_or_create(ident=1, defaults={'last_update': timezone.now()})


class Migration(migrations.Migration):
    dependencies = [
        ('application', '0429_repair_group_sport_association_fk'),
        ('django_celery_beat', '0001_initial'),
    ]
    operations = [migrations.RunPython(disable_expiry_schedule, migrations.RunPython.noop)]
