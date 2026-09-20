from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('instance', '0003_instance_operations')]
    operations = [migrations.AddField(
        model_name='instanceconfiguration', name='integration_settings',
        field=models.JSONField(default=dict, blank=True),
    )]
