from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('application', '0424_saved_report'),
    ]

    operations = [
        migrations.AddField(
            model_name='subscription',
            name='signature_storage_key',
            field=models.CharField(
                blank=True,
                help_text='Internal storage key for private signature image',
                max_length=1024,
                null=True,
            ),
        ),
    ]
