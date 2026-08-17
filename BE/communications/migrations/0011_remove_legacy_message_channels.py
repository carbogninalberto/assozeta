from django.db import migrations, models


def disable_unsupported_automation_actions(apps, schema_editor):
    AutomationWorkflow = apps.get_model('communications', 'AutomationWorkflow')

    for workflow in AutomationWorkflow.objects.all().iterator():
        tree = workflow.automation_tree or []
        supported_tree = [
            node for node in tree
            if not (
                isinstance(node, dict)
                and node.get('id') == 'message'
                and node.get('value') != 'email'
            )
        ]

        if len(supported_tree) != len(tree):
            workflow.automation_tree = supported_tree
            workflow.enabled = False
            workflow.save(update_fields=['automation_tree', 'enabled'])


class Migration(migrations.Migration):

    dependencies = [
        ('communications', '0010_automationworkflow_communicati_automat_e17cf8_idx_and_more'),
    ]

    operations = [
        migrations.RunPython(
            disable_unsupported_automation_actions,
            migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name='communicationconfiguration',
            name='sms_balance',
        ),
        migrations.AlterField(
            model_name='message',
            name='type',
            field=models.CharField(
                choices=[
                    ('EMAIL', 'Email'),
                    ('INSIDE_APP', 'Inside App Message'),
                ],
                max_length=50,
            ),
        ),
        migrations.DeleteModel(
            name='SmsCreditPayment',
        ),
    ]
