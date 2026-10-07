"""A changed guide must not hide unrelated verified chapters or bypass releases."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('runtime_scope_index', ROOT / 'BE/application/manuale/index.py')
index = importlib.util.module_from_spec(spec)
spec.loader.exec_module(index)


class RuntimeScopeTests(unittest.TestCase):
    def make(self, root):
        code, manual = root / 'code', root / 'manual'
        (code / 'BE').mkdir(parents=True)
        (manual / 'docs').mkdir(parents=True)
        tool = code / 'BE/capture.py'
        tool.write_text('capture_real_backend()\n')
        manifest = {'metadata': {'application_revision': 'revision', 'manual_revision': 'manual',
            'release': 'v1', 'manual_url': 'https://manual.invalid',
            'tooling_hashes': {'BE/capture.py': index.file_digest(tool)}}, 'pages': []}
        for name in ('course', 'member'):
            source = code / ('BE/' + name + '.py')
            source.write_text('def ' + name + '(): return True\n')
            body = '# ' + name.title() + '\nInformazioni sul ' + name + '.\n'
            (manual / ('docs/' + name + '.mdx')).write_text(body)
            manifest['pages'].append({'path': 'docs/' + name + '.mdx', 'sections': [{
                'id': name, 'kind': 'code-reference', 'status': 'verified',
                'content_sha256': index.digest(body.strip()), 'evidence': [{
                    'path': 'BE/' + name + '.py', 'symbol': 'def ' + name,
                    'start': 1, 'end': 1, 'sha256': index.file_digest(source)}]}]})
        return code, index.ManualIndex(index.build_index(manual, code, manifest))

    def test_source_and_tooling_changes_are_scoped_but_strict_install_still_rejects_them(self):
        with tempfile.TemporaryDirectory() as temporary:
            code, corpus = self.make(Path(temporary))
            context = {'revision': 'revision', 'release': 'v1', 'code_root': code}
            self.assertEqual(len(corpus.applicable(**context)), 2)
            (code / 'BE/capture.py').write_text('updated_capture_helper()\n')
            self.assertEqual(len(corpus.applicable(**context)), 2)
            with self.assertRaises(index.EvidenceError):
                corpus.verify_unchanged_dependencies(code)
            (code / 'BE/member.py').write_text('def member(): return False\n')
            self.assertEqual([chunk['id'] for chunk in corpus.applicable(**context)], ['docs/course#course'])
            self.assertEqual(corpus.stale_sections(code), {'docs/member#member': ['BE/member.py']})
            (code / 'BE/course.py').unlink()
            self.assertEqual(corpus.applicable(**context), [])
            with self.assertRaises(index.EvidenceError):
                corpus.applicable(**{**context, 'release': 'v2'})

    def test_shared_access_change_invalidates_all_guides_without_weakening_release_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            code, corpus = self.make(Path(temporary))
            auth = code / 'BE/core/authentication.py'
            auth.parent.mkdir()
            auth.write_text('class Authentication: pass\n')
            value = corpus.value.copy()
            value['dependencies'] = {**value['dependencies'], 'BE/core/authentication.py': index.file_digest(auth)}
            corpus = index.ManualIndex(index.seal_index(value))
            context = {'revision': 'revision', 'release': 'v1', 'code_root': code}
            self.assertEqual(len(corpus.applicable(**context)), 2)
            auth.write_text('class Authentication: changed = True\n')
            self.assertEqual(corpus.applicable(**context), [])
            with self.assertRaises(index.EvidenceError):
                corpus.applicable(**{**context, 'revision': 'other'})
