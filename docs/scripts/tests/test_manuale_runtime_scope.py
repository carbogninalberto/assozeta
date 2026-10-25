"""A changed guide must not hide unrelated verified chapters or bypass releases."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def asset_corpus(self, root):
        code, corpus = self.make(root)
        for chunk in corpus.value['chunks']:
            chunk['screenshots'] = [{'path': 'images/' + chunk['section'] + '.png'}]
        auth = code / 'BE/core/authentication.py'
        auth.parent.mkdir()
        auth.write_text('authenticate()\n')
        corpus.value['dependencies']['BE/core/authentication.py'] = index.file_digest(auth)
        return code, index.ManualIndex(index.seal_index(corpus.value))

    def test_asset_checks_only_referring_sources_and_rechecks_changes_on_every_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            code, corpus = self.asset_corpus(Path(temporary))
            context = {'revision': 'revision', 'release': 'v1', 'code_root': code, 'asset_path': 'images/course.png'}
            with patch.object(index, 'file_digest', wraps=index.file_digest) as read:
                self.assertEqual([c['id'] for c in corpus.applicable(**context)], ['docs/course#course'])
                self.assertEqual({Path(call.args[0]).relative_to(code.resolve()).as_posix() for call in read.call_args_list},
                                 {'BE/course.py', 'BE/core/authentication.py'})
            (code / 'BE/member.py').unlink()
            self.assertEqual(len(corpus.applicable(**context)), 1)
            (code / 'BE/course.py').write_text('changed()\n')
            self.assertEqual(corpus.applicable(**context), [])
            (code / 'BE/course.py').unlink()
            self.assertEqual(corpus.applicable(**context), [])

    def test_asset_selection_keeps_shared_source_version_and_access_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            code, corpus = self.asset_corpus(Path(temporary))
            context = {'revision': 'revision', 'release': 'v1', 'code_root': code, 'asset_path': 'images/course.png'}
            course = next(c for c in corpus.value['chunks'] if c['section'] == 'course')
            for field, value in [('audience', 'maintainer'), ('features', ['private_feature']), ('status', 'pending')]:
                original = course[field]
                course[field] = value
                self.assertEqual(corpus.applicable(**context), [])
                course[field] = original
            self.assertEqual(corpus.applicable(**{**context, 'asset_path': 'images/missing.png'}), [])
            for mismatch in [{'release': 'v2'}, {'revision': 'other'}]:
                with self.assertRaises(index.EvidenceError):
                    corpus.applicable(**{**context, **mismatch})
            runtime = code / 'BE/course_runtime.py'
            runtime.write_text('current_runtime()\n')
            course['runtime_dependencies'] = {'BE/course_runtime.py': index.file_digest(runtime)}
            self.assertEqual(len(corpus.applicable(**context)), 1)
            runtime.write_text('changed_runtime()\n')
            self.assertEqual(corpus.applicable(**context), [])
            runtime.write_text('current_runtime()\n')
            (code / 'BE/core/authentication.py').write_text('changed_authentication()\n')
            self.assertEqual(corpus.applicable(**context), [])

    def test_shared_image_remains_available_only_through_a_current_accessible_section(self):
        with tempfile.TemporaryDirectory() as temporary:
            code, corpus = self.asset_corpus(Path(temporary))
            for chunk in corpus.value['chunks']:
                chunk['screenshots'] = [{'path': 'images/shared.png'}]
            context = {'revision': 'revision', 'release': 'v1', 'code_root': code, 'asset_path': 'images/shared.png'}
            self.assertEqual(len(corpus.applicable(**context)), 2)
            (code / 'BE/course.py').write_text('changed()\n')
            self.assertEqual([c['id'] for c in corpus.applicable(**context)], ['docs/member#member'])
            member = next(c for c in corpus.value['chunks'] if c['section'] == 'member')
            member['audience'] = 'maintainer'
            self.assertEqual(corpus.applicable(**context), [])
            self.assertEqual(len(corpus.applicable(**context, maintainer=True)), 1)

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
