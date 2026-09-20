from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('instance', '0002_setup_provenance_onboarding_completion')]
    operations = [
        migrations.AddField('instanceconfiguration', 'email_settings', models.JSONField(default=dict, blank=True)),
        migrations.AddField('instanceconfiguration', 'email_password_encrypted', models.TextField(default='', blank=True)),
        migrations.AddField('instanceconfiguration', 'email_revision', models.PositiveIntegerField(default=0)),
        migrations.AddField('instanceconfiguration', 'diagnostic_results', models.JSONField(default=dict, blank=True)),
    ]
