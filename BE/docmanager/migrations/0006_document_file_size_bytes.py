from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('docmanager', '0005_document_filepath'),
    ]

    operations = [
        migrations.AddField(
            model_name='document',
            name='file_size_bytes',
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
    ]
