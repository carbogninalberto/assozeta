from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('application', '0431_associate_tax_code_upper_idx'),
    ]

    operations = [
        migrations.AddField(
            model_name='moduleresponses',
            name='attachment_token_hash',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
    ]
