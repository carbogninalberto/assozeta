import os
from pathlib import Path
import subprocess
import tempfile
import unittest


CLI = Path(__file__).resolve().parents[1] / 'bin/assozeta'


class SyncRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='assozeta-git-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.remote = self.root / 'remote.git'
        self.source = self.root / 'source'
        self.checkout = self.root / 'checkout'
        self.git(self.root, 'init', '--bare', str(self.remote))
        self.git(self.root, 'clone', str(self.remote), str(self.source))
        self.git(self.source, 'checkout', '-b', 'main')
        self.commit(self.source, 'initial')
        self.git(self.source, 'push', '-u', 'origin', 'main')
        self.git(self.root, 'clone', '--branch', 'main', str(self.remote), str(self.checkout))

    def git(self, directory, *args):
        return subprocess.check_output(['git', '-C', str(directory), '-c', 'user.name=Test',
            '-c', 'user.email=test@example.test', *args], text=True, stderr=subprocess.DEVNULL).strip()

    def commit(self, directory, value):
        (directory / 'file').write_text(value)
        self.git(directory, 'add', 'file')
        self.git(directory, 'commit', '-m', value)

    def sync(self, directory=None):
        return subprocess.run([str(CLI), 'sync-repo'], env={**os.environ,
            'ASSOZETA_INSTALL_ROOT': str(directory or self.checkout)}, text=True, capture_output=True)

    def test_fast_forward_and_repeated_sync(self):
        self.commit(self.source, 'upstream')
        self.git(self.source, 'push')
        for _ in range(2):
            result = self.sync()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.git(self.checkout, 'rev-parse', 'HEAD'), self.git(self.source, 'rev-parse', 'HEAD'))
            self.assertIn('containers were not changed', result.stdout)

    def test_dirty_tracked_and_untracked_files_are_preserved(self):
        for filename in ('file', 'untracked'):
            with self.subTest(filename=filename):
                target = self.checkout / filename
                target.write_text('local work')
                result = self.sync()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('dirty', result.stderr)
                self.assertEqual(target.read_text(), 'local work')
                if filename == 'file':
                    self.git(self.checkout, 'restore', 'file')

    def test_ignored_local_file_is_not_overwritten_by_upstream(self):
        original = self.git(self.checkout, 'rev-parse', 'HEAD')
        (self.checkout / '.git/info/exclude').write_text('local-config\n')
        local = self.checkout / 'local-config'
        local.write_text('local configuration')
        (self.source / 'local-config').write_text('upstream configuration')
        self.git(self.source, 'add', 'local-config')
        self.git(self.source, 'commit', '-m', 'Add configuration')
        self.git(self.source, 'push')
        result = self.sync()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(local.read_text(), 'local configuration')
        self.assertEqual(self.git(self.checkout, 'rev-parse', 'HEAD'), original)

    def test_detached_head_is_rejected(self):
        self.git(self.checkout, 'checkout', '--detach')
        self.assertIn('detached HEAD', self.sync().stderr)

    def test_local_and_divergent_commits_are_preserved(self):
        self.commit(self.checkout, 'local')
        local_head = self.git(self.checkout, 'rev-parse', 'HEAD')
        for divergent in (False, True):
            if divergent:
                self.commit(self.source, 'remote change')
                self.git(self.source, 'push')
            result = self.sync()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('local commits or divergent history', result.stderr)
            self.assertEqual(self.git(self.checkout, 'rev-parse', 'HEAD'), local_head)

    def test_missing_remote_and_upstream_are_explicit(self):
        self.git(self.checkout, 'config', 'branch.main.remote', 'missing')
        self.assertIn('does not exist', self.sync().stderr)
        self.git(self.checkout, 'config', '--unset', 'branch.main.remote')
        self.assertIn('no configured upstream', self.sync().stderr)

    def test_non_git_installation_is_explicit(self):
        installation = self.root / 'release'
        installation.mkdir()
        self.assertIn('not a Git checkout', self.sync(installation).stderr)

    def test_failed_fetch_preserves_checkout(self):
        original = self.git(self.checkout, 'rev-parse', 'HEAD')
        self.git(self.checkout, 'remote', 'set-url', 'origin', str(self.root / 'missing.git'))
        self.assertIn('fetch failed', self.sync().stderr)
        self.assertEqual(self.git(self.checkout, 'rev-parse', 'HEAD'), original)
