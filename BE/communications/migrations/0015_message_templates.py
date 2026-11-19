import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('communications', '0014_redact_smtp_password_audit'),
    ]

    operations = [
        migrations.CreateModel(
            name='MessageTemplate',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('channel', models.CharField(max_length=10)),
                ('locale', models.CharField(max_length=35)),
                ('name', models.CharField(max_length=100)),
                ('subject', models.CharField(blank=True, max_length=100)),
                ('body', models.TextField()),
                ('enabled', models.BooleanField(default=True)),
                ('is_default', models.BooleanField(default=False)),
                ('archived', models.BooleanField(default=False)),
                ('updated_by_id', models.UUIDField(null=True)),
                ('updated_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('sport_association', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='application.sportassociation')),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(
                        fields=('sport_association', 'channel', 'locale'),
                        condition=models.Q(is_default=True, enabled=True, archived=False),
                        name='message_template_active_default',
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name='OutgoingMessage',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('actor_id', models.UUIDField()),
                ('recipient_id', models.UUIDField()),
                ('template_id', models.UUIDField()),
                ('channel', models.CharField(max_length=10)),
                ('locale', models.CharField(max_length=35)),
                ('recipient', models.CharField(max_length=254)),
                ('rendered_subject', models.CharField(max_length=100)),
                ('rendered_body', models.TextField()),
                ('subject', models.CharField(max_length=100)),
                ('body', models.TextField()),
                ('action', models.CharField(blank=True, max_length=16)),
                ('status', models.CharField(default='preview', max_length=16)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('updated_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('sport_association', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='application.sportassociation')),
            ],
        ),
    ]
