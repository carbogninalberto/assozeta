import uuid

from application.tests.base import BaseAPITestCase
from communications.models import AutomationWorkflow


class WorkflowPrimaryKeyTests(BaseAPITestCase):
    """A workflow key in the body is never written: update saves the path's row, add generates the key."""

    def test_update_saves_the_path_row(self):
        workflow = AutomationWorkflow.objects.create(sport_association=self.sport_association, name='Benvenuto')
        other = AutomationWorkflow.objects.create(sport_association=self.sport_association, name='Promemoria')
        for key, name in ((uuid.uuid4(), 'Benvenuto 2'), (other.pk, 'Benvenuto 3')):
            response = self.client.patch(f'/communications/workflows/{workflow.pk}/update',
                                         {'automation_workflow_id': str(key), 'name': name}, format='json')
            self.assertEqual(response.status_code, 200, response.content)
            workflow.refresh_from_db()
            self.assertEqual(workflow.name, name)
        self.assertEqual(AutomationWorkflow.objects.count(), 2)
        other.refresh_from_db()
        self.assertEqual(other.name, 'Promemoria')

    def test_add_generates_the_key(self):
        key = uuid.uuid4()
        response = self.client.post('/communications/workflows/add',
                                    {'automation_workflow_id': str(key), 'name': 'Nuovo', 'automation_tree': []},
                                    format='json')
        self.assertIn(response.status_code, (200, 201), response.content)
        self.assertFalse(AutomationWorkflow.objects.filter(pk=key).exists())
        self.assertEqual(AutomationWorkflow.objects.filter(name='Nuovo').count(), 1)
