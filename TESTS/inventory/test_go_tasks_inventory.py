"""Failure controls for task discovery and switch coverage."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('go_tasks_inventory', ROOT / 'tools/go-migration/go_tasks_inventory.py')


class InventoryControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = importlib.util.module_from_spec(SPEC)
        SPEC.loader.exec_module(cls.tool)

    def test_task_names_and_sites(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'BE/example'
            package.mkdir(parents=True)
            (package / 'tasks.py').write_text(
                "from celery import shared_task as shared\n"
                "@shared\ndef implicit(): pass\n"
                "@app.task(name='alternate.name')\ndef explicit(): pass\n"
            )
            found = self.tool.django_tasks(root)
            self.assertEqual(found, {'example.tasks.implicit': ('BE/example/tasks.py:2', 'implicit'),
                                     'alternate.name': ('BE/example/tasks.py:4', 'explicit')})
            (package / 'other.py').write_text("@app.task(name='alternate.name')\ndef duplicate(): pass\n")
            with self.assertRaisesRegex(ValueError, 'alternate.name'):
                self.tool.django_tasks(root)

    def test_reject_route_without_consumer(self):
        with self.assertRaisesRegex(ValueError, 'new_task'):
            self.tool.build_rows({'new_task': ('BE/example.py:1', 'new_task')}, set(), set(),
                                 set(), {'new_task'}, {})

    def test_reject_unknown_go_or_scheduled_name(self):
        for groups in (({'removed'}, set(), set()), (set(), {'removed'}, set()), (set(), set(), {'removed'})):
            with self.subTest(groups=groups), self.assertRaisesRegex(ValueError, 'removed'):
                self.tool.build_rows({}, *groups, set(), {})
