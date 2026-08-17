from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('application', '0427_delete_sport_association_invoices'),
    ]

    operations = [
        migrations.AlterField(
            model_name='notificationtemplates',
            name='type',
            field=models.PositiveSmallIntegerField(
                choices=[(1, 'email')],
                default=1,
            ),
        ),
        migrations.AlterField(
            model_name='reminder',
            name='reminder_type',
            field=models.PositiveSmallIntegerField(
                choices=[(1, 'email'), (3, 'push')],
                default=1,
            ),
        ),
    ]
