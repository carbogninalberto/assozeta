"""Unit tests for the Go test build-cache prewarm extraction."""
import tempfile
import textwrap
import unittest
from pathlib import Path

from TESTS.adapters.go import prewarm


def write(root: Path, relative: str, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding='utf-8')
    return path


class SyntheticTreeTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name).resolve()
        write(self.root, 'TESTS/__init__.py', '')
        write(self.root, 'TESTS/pkg/__init__.py', '')
        write(self.root, 'TESTS/entry.py', '''\
            import TESTS.pkg.nested


            def helper():
                from TESTS.pkg.function_level import run
                return run
        ''')
        write(self.root, 'TESTS/pkg/nested.py', '''\
            import subprocess

            import TESTS.deepmodule  # reached only through this import

            subprocess.run(('go', 'test', '-race', '-tags', 'tupletag', '-count=1',
                            './pkg/one', '-run', '^TestOne$'))
        ''')
        write(self.root, 'TESTS/deepmodule.py', '''\
            import subprocess

            subprocess.run(['go', 'test', '-json', '-v', '-timeout', '3m', '-race',
                            '-tags=deepintegration', './pkg/deep', '-run', '^TestDeep$',
                            '-bench', 'BenchmarkDeep'])
            subprocess.run(['go', 'validate', './pkg/deep'])
        ''')
        write(self.root, 'TESTS/pkg/function_level.py', '''\
            import subprocess

            subprocess.run(['go', 'test', '-race', '-tags=sharedtag', './internal/service',
                            '-count=1'])
            subprocess.run(['go', 'test', '-race', '-tags', 'sharedtag', './internal/service',
                            '-run', '^TestShared$'])
            subprocess.run(['go', 'test', '-race', '-tags=othertag', './internal/service',
                            '-count=1', '-gcflags=all=-l'], cwd=ROOT / 'BE_GO')
            subprocess.run(['go', 'test', '-race', '-tags=othertag', './internal/service',
                            '-count=1', '-gcflags=all=-l'], cwd=ROOT / 'BE_GO')
            subprocess.run(['go', 'test', '-race', '-tags=altdir', './pkg/alt'],
                           cwd=ROOT / 'OTHER_GO')
            extra = ['-count=1']
            subprocess.run(['go', 'test', *extra, '-race', '-tags=dynamic', './pkg/dyn'])
        ''')
        self.entry = self.root / 'TESTS/entry.py'

    def tearDown(self):
        self.directory.cleanup()

    def test_follows_nested_and_function_level_imports(self):
        extraction = prewarm.extract([self.entry], self.root)
        names = {path.relative_to(self.root).as_posix() for path in extraction.sources}
        self.assertEqual(names, {
            'TESTS/__init__.py',
            'TESTS/entry.py',
            'TESTS/pkg/__init__.py',
            'TESTS/pkg/nested.py',
            'TESTS/pkg/function_level.py',
            'TESTS/deepmodule.py',
        })

    def test_keeps_only_build_flags_and_packages(self):
        extraction = prewarm.extract([self.entry], self.root)
        keys = {build.key for build in extraction.builds}
        self.assertEqual(keys, {
            '-race -tags=tupletag ./pkg/one',
            '-race -tags=deepintegration ./pkg/deep',
            '-race -tags=sharedtag ./internal/service',
            '-gcflags=all=-l -race -tags=othertag ./internal/service',
            '-race -tags=altdir ./pkg/alt',
        })
        for key in keys:
            for dropped in ('-run', '-json', '-v ', '-count', '-timeout', '-bench'):
                self.assertNotIn(dropped, key)

    def test_deduplicates_equivalent_commands(self):
        extraction = prewarm.extract([self.entry], self.root)
        self.assertEqual(len(extraction.builds), 5)
        shared = [build for build in extraction.builds if 'sharedtag' in build.key]
        self.assertEqual(len(shared), 1)
        self.assertEqual(shared[0].flags, ('-race', '-tags=sharedtag'))

    def test_detects_explicit_and_default_cwd(self):
        extraction = prewarm.extract([self.entry], self.root)
        cwds = {build.cwd for build in extraction.builds}
        self.assertEqual(cwds, {'BE_GO', 'OTHER_GO'})
        alt = next(build for build in extraction.builds if 'altdir' in build.key)
        self.assertEqual(alt.cwd, 'OTHER_GO')
        default = next(build for build in extraction.builds if 'tupletag' in build.key)
        self.assertEqual(default.cwd, 'BE_GO')

    def test_reports_non_literal_command_as_skipped(self):
        extraction = prewarm.extract([self.entry], self.root)
        self.assertEqual(len(extraction.skipped), 1)
        skipped = extraction.skipped[0]
        self.assertEqual(skipped.path.relative_to(self.root).as_posix(),
                         'TESTS/pkg/function_level.py')
        self.assertIn('not fully literal', skipped.reason)


class RealAccountsExtractionTest(unittest.TestCase):
    def test_accounts_entry_needs_many_builds(self):
        root = Path(__file__).resolve().parents[1]
        entry = root / 'TESTS/adapters/django/accounts.py'
        extraction = prewarm.extract([entry], root)
        self.assertGreaterEqual(len(extraction.builds), 20)
        self.assertTrue(any('-tags=associationprofileintegration' in build.key
                            and './internal/service' in build.key
                            for build in extraction.builds))
        self.assertEqual({build.cwd for build in extraction.builds}, {'BE_GO'})


if __name__ == '__main__':
    unittest.main()
