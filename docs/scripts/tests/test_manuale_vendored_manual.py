"""Baseline manual changes across history-free vendored manual commits.

Uses real temporary Git repositories: an application repository with a
vendored manuale/ folder and companion checkouts materialized like
`manuale-ci.py pin`.
"""
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load('manual_vendored_runner', ROOT / 'docs/scripts/manuale.py')
ci = load('manual_vendored_ci', ROOT / 'docs/scripts/manuale-ci.py')


def git(root, *arguments, environment=None):
    return subprocess.run(['git', '-C', str(root), '-c', 'commit.gpgsign=false', *arguments], check=True,
                          capture_output=True, text=True, env={**os.environ, **(environment or {})}).stdout.strip()


class VendoredManualChangesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.application = self.base / 'application'
        self.application.mkdir()
        git(self.application, 'init', '-q', '-b', 'main')
        git(self.application, 'config', 'user.name', 'Test')
        git(self.application, 'config', 'user.email', 'test@example.invalid')
        self.write('manuale/docs/a.mdx', 'Prima\n')
        self.write('manuale/docs/old.mdx', 'Vecchia\n')
        self.write('manuale/.manuale-evidence.json', '{}\n')
        self.producer = self.commit('baseline')
        self.write('manuale/docs/a.mdx', 'Seconda\n')
        (self.application / 'manuale/docs/old.mdx').rename(self.application / 'manuale/docs/new.mdx')
        self.head = self.commit('head')
        self.old_manual = self.materialize(self.producer, 'baseline-manual')
        self.new_repository = self.base / 'head-manual'
        self.new_manual = self.materialize(self.head, 'head-manual')

    def write(self, relative, text):
        target = self.application / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def commit(self, message):
        git(self.application, 'add', '-A')
        git(self.application, 'commit', '-q', '-m', message)
        return git(self.application, 'rev-parse', 'HEAD')

    def materialize(self, revision, name):
        destination = self.base / name
        destination.mkdir()
        archive = subprocess.run(['git', '-C', str(self.application), 'archive', '--format=tar', revision + ':manuale'],
                                 check=True, stdout=subprocess.PIPE).stdout
        subprocess.run(['tar', '-x', '-C', str(destination)], input=archive, check=True)
        git(destination, 'init', '-q', '-b', 'main')
        git(destination, 'add', '-A', '-f')
        git(destination, 'commit', '-q', '-m', 'Vendored manual content', environment=ci.MANUAL_COMMIT_ENVIRONMENT)
        return git(destination, 'rev-parse', 'HEAD')

    def changes(self, producer, old_manual):
        return runner.vendored_manual_changes({'application_input': {'revision': producer}}, old_manual,
                                              self.new_repository, self.new_manual, root=self.application)

    def test_baseline_manual_commit_is_unreachable_from_the_companion_checkout(self):
        with self.assertRaises(subprocess.CalledProcessError):
            git(self.new_repository, 'diff', '--name-only', self.old_manual, self.new_manual)

    def test_rebuilt_producer_tree_lists_exact_changes_including_both_rename_paths(self):
        self.assertEqual(self.changes(self.producer, self.old_manual), ['docs/a.mdx', 'docs/new.mdx', 'docs/old.mdx'])

    def test_producer_tree_that_does_not_rebuild_the_recorded_revision_is_unknown(self):
        self.assertIsNone(self.changes(self.head, self.old_manual))

    def test_missing_producer_revision_or_record_is_unknown(self):
        self.assertIsNone(self.changes('0' * 40, self.old_manual))
        self.assertIsNone(runner.vendored_manual_changes({}, self.old_manual, self.new_repository, self.new_manual,
                                                         root=self.application))


if __name__ == '__main__':
    unittest.main()
