from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('communications', '0012_staffboardmessage')]
    operations = [migrations.AddField(
        model_name='staffboardmessage', name='document',
        field=models.JSONField(blank=True, null=True),
    )]
