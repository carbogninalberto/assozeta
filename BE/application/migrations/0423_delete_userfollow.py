"""
Migration to remove the UserFollow model.

This migration removes the follow/unfollow social feature that has been deprecated.
The UserFollow table tracked user-to-user follow relationships.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('application', '0422_remove_oauth2_provider_tables'),
    ]

    operations = [
        migrations.DeleteModel(
            name='UserFollow',
        ),
    ]
