"""Discovery is a conservative work queue, never evidence of behavioral parity."""
import tempfile
import unittest
from pathlib import Path

from TESTS.inventory.catalog import discover, reconcile, validate


class CatalogTests(unittest.TestCase):
    def scan(self, files):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            return discover(root)

    def test_routes_include_mounts_conditionals_regex_and_websockets(self):
        items = self.scan({'BE/core/urls.py': '''
urlpatterns = [path('healthz', health), path('', include('application.urls'))]
if DEBUG:
    urlpatterns += [re_path(r'^debug/(?P<id>.+)$', debug)]
''', 'BE/application/routing.py': "websocket_urlpatterns = [path('ws/updates/', Update.as_asgi())]"})
        self.assertEqual([i['kind'] for i in items].count('route'), 2)
        self.assertEqual([i['kind'] for i in items].count('mount'), 1)
        socket = next(i for i in items if i['kind'] == 'websocket')
        self.assertEqual(socket['declaration']['path'], 'ws/updates/')
        self.assertEqual(socket['status'], 'uncharacterized')
        self.assertIsNone(socket['contract']['methods'])

    def test_tasks_schedules_hooks_commands_and_mcp_are_discovered(self):
        items = self.scan({'BE/application/tasks.py': '''
@shared_task(bind=True, name='export')
def export(self, association_id): pass
@app.task
def cleanup(): pass
app.conf.beat_schedule = {'daily': {'task': 'export', 'schedule': crontab(hour=1)}}
PeriodicTask.objects.update_or_create(name='workflow', defaults={})
@receiver(post_save, sender=Payment)
def payment_saved(sender, instance, **kwargs): pass
@mcp.tool()
def search_manual(query): pass
''', 'BE/application/models/payment.py': '''
class Payment(models.Model):
    def save(self, *args, **kwargs): pass
    def delete(self, *args, **kwargs): pass
''', 'BE/application/management/commands/restore.py': '''
class Command(BaseCommand):
    def handle(self, *args, **options): pass
'''})
        kinds = [i['kind'] for i in items]
        for kind, count in {'task': 2, 'schedule': 1, 'schedule_mutation': 1,
                            'signal': 1, 'model_hook': 2, 'command': 1, 'mcp_tool': 1}.items():
            self.assertEqual(kinds.count(kind), count, kind)
        task = next(i for i in items if i['kind'] == 'task' and i['declaration'].get('name') == 'export')
        self.assertEqual(task['declaration']['arguments'], 'self, association_id')

    def test_reconciliation_preserves_review_and_invalidates_changed_evidence(self):
        old = self.scan({'BE/core/urls.py': "urlpatterns = [path('readyz', readiness)]"})
        old[0]['notes'] = ['Reviewed database and Redis requirements']
        old[0]['status'] = 'characterized'
        old[0]['tests'] = ['TESTS/contracts/test_health.py']
        new = self.scan({'BE/core/urls.py': "# line move\nurlpatterns = [path('readyz', readiness)]"})
        merged = reconcile(old, new)
        self.assertEqual(merged[0]['notes'], old[0]['notes'])
        self.assertEqual(merged[0]['status'], 'needs_review')
        self.assertEqual(merged[0]['tests'], old[0]['tests'])

    def test_removed_declarations_cannot_disappear_silently(self):
        old = self.scan({'BE/core/urls.py': "urlpatterns = [path('old', old)]"})
        removed = reconcile(old, [])
        self.assertEqual(removed[0]['status'], 'source_removed')
        self.assertFalse(removed[0]['source_present'])

    def test_verified_requires_contract_implementation_and_executed_evidence(self):
        items = self.scan({'BE/core/urls.py': "urlpatterns = [path('healthz', health)]"})
        items[0]['status'] = 'verified'
        with self.assertRaisesRegex(ValueError, 'verified'):
            validate(items)

    def test_literal_mcp_definitions_and_manual_implementation_are_in_scope(self):
        items = self.scan({'BE/application/manuale/tools.py': "MANUAL_TOOL_DEFINITIONS = [{'name': 'search_manual', 'parameters': {'type': 'object'}}]"})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['kind'], 'mcp_definition')
        self.assertEqual(items[0]['declaration']['name'], 'search_manual')

    def test_duplicate_ids_rejected_and_tests_not_counted_as_capabilities(self):
        items = self.scan({'BE/application/tests/test_demo.py': "x = path('fake', fake)",
                           'BE/core/urls.py': "urlpatterns = [path('healthz', health)]"})
        self.assertEqual(len(items), 1)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            validate(items + items)


if __name__ == '__main__':
    unittest.main()
