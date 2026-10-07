"""Actual corpus construction, invalidation, retrieval, and agent integration."""
import asyncio
import copy
import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase, override_settings

from application.manuale.index import (EvidenceError, ManualIndex, build_index, canonical,
                                      digest, file_digest, promote, sections)
from application.manuale.tools import grounded_answer, is_manual_question


class ManualIndexTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        manual_settings = override_settings(
            MANUAL_DEVELOPMENT_ROOT=str(self.root / 'development'),
            MANUAL_CORPUS_BASE_URL='',
        )
        manual_settings.enable()
        self.addCleanup(manual_settings.disable)
        self.manual = self.root / 'manual'
        self.code = self.root / 'code'
        (self.manual / 'docs').mkdir(parents=True)
        (self.code / 'BE').mkdir(parents=True)
        self.source = self.code / 'BE/tags.py'
        self.source.write_text('def create_tag(name):\n    return name.strip()\n')
        self.page = self.manual / 'docs/tag.mdx'
        self.page.write_text('---\ntitle: Tag\n---\n\n## Creare un tag\nApri Libro Soci, seleziona gli atleti e premi Assegna Tag. Inserisci il nome e premi Applica.\n\n## Eliminare un tag\nSeleziona il tag e premi Elimina.\n')
        self.manifest = {'metadata': {'application_revision': 'revision-1', 'manual_revision': 'manual-1',
                                      'release': 'v1', 'manual_url': 'https://manual.example'},
                         'pages': [{'path': 'docs/tag.mdx', 'sections': [{
                             'id': 'creare-un-tag', 'status': 'verified', 'kind': 'code-reference',
                             'content_sha256': digest(sections(self.page.read_text())['creare-un-tag']['mdx']),
                             'evidence': [{'path': 'BE/tags.py', 'start': 1, 'end': 2, 'symbol': 'create_tag', 'sha256': file_digest(self.source)}],
                         }]}]}
        self.context = {'revision': 'revision-1', 'release': 'v1'}

    def index(self):
        return ManualIndex(build_index(self.manual, self.code, self.manifest))

    def test_verified_retrieval_has_citation_and_pending_text_is_excluded(self):
        index = self.index()
        hits = index.search('Come posso creare un tag?', **self.context)
        self.assertEqual(hits[0]['id'], 'docs/tag#creare-un-tag')
        self.assertGreater(hits[0]['semantic'], 0)
        self.assertGreater(hits[0]['lexical'], 0)
        self.assertEqual(hits[0]['url'], 'https://manual.example/docs/tag#creare-un-tag')
        self.assertEqual(index.value['gaps'][0]['section'], 'eliminare-un-tag')
        self.assertEqual(len(index.value['chunks']), 1)
        self.assertNotIn('premi Elimina', canonical(index.value['chunks']))

    def test_paraphrased_search_prefers_the_procedure_over_its_short_overview(self):
        body = '# Cercare una persona\nApri Iscrizioni, cerca Giulia nel campo Cerca. La ricerca mostra Giulia Bianchi. Premi X per cancellare la ricerca.\n'
        (self.manual / 'docs/search.mdx').write_text(body)
        self.manifest['pages'].append({'path': 'docs/search.mdx', 'sections': [{
            'id': 'cercare-una-persona', 'status': 'verified', 'kind': 'code-reference',
            'content_sha256': digest(body.strip()), 'evidence': self.manifest['pages'][0]['sections'][0]['evidence']}]})
        overview = '# Introduzione\nApri Iscrizioni per trovare una persona. Leggi la guida della ricerca.\n'
        (self.manual / 'docs/overview.mdx').write_text(overview)
        self.manifest['pages'].append({'path': 'docs/overview.mdx', 'sections': [{
            'id': 'introduzione', 'status': 'verified', 'kind': 'code-reference',
            'content_sha256': digest(overview.strip()), 'evidence': self.manifest['pages'][0]['sections'][0]['evidence']}]})
        hits = self.index().search('Come posso trovare Giulia nelle Iscrizioni?', **self.context)
        self.assertEqual(hits[0]['id'], 'docs/search#cercare-una-persona')
        self.assertIn('Premi X', grounded_answer({'results': hits}))
        self.assertEqual(self.index().search('Come posso trovare fatture elettroniche?', **self.context), [])

    def test_revision_release_source_changes_and_absent_functionality_abstain(self):
        index = self.index()
        with self.assertRaises(EvidenceError):
            index.search('tag', revision='another-release', release='v1')
        with self.assertRaises(EvidenceError):
            index.applicable(revision='revision-1', release='v2')
        self.assertEqual(index.search('teletrasporto automatico satellitare', **self.context), [])
        self.assertEqual(index.search('Come posso creare una fattura?', **self.context), [])
        self.assertEqual(index.search('Come posso eliminare un tag?', **self.context), [])
        self.source.write_text('def create_tag(name):\n    return None\n')
        self.assertEqual(index.applicable(**self.context, code_root=self.code), [])
        with self.assertRaises(EvidenceError):
            self.index()

    def test_generic_documentation_word_is_not_evidence_for_an_unknown_subject(self):
        body = '# Archiviazione manuale\nSeleziona le iscrizioni e premi Archivia.\n'
        (self.manual / 'docs/archive.mdx').write_text(body)
        self.manifest['pages'].append({'path': 'docs/archive.mdx', 'sections': [{
            'id': 'archiviazione-manuale', 'status': 'verified', 'kind': 'code-reference',
            'content_sha256': digest(body.strip()), 'evidence': self.manifest['pages'][0]['sections'][0]['evidence']}]})
        index = self.index()
        self.assertEqual(index.search('Manuale teletrasporto satellitare', **self.context), [])
        self.assertEqual(index.search('Manuale creare un tag', **self.context)[0]['id'], 'docs/tag#creare-un-tag')

    def test_changed_text_and_path_escape_cannot_be_promoted(self):
        output = self.root / 'index.json'
        promote(self.index().value, output)
        old = output.read_bytes()
        self.page.write_text(self.page.read_text().replace('premi Applica', 'premi Esporta'))
        with self.assertRaises(EvidenceError):
            promote(self.index().value, output)
        self.assertEqual(output.read_bytes(), old)
        self.manifest['pages'][0]['path'] = '../outside.mdx'
        with self.assertRaises(EvidenceError):
            self.index()

    def test_repeatable_identity_deleted_chunks_and_unsupported_index(self):
        first = self.index()
        self.assertEqual(first.value['identity'], self.index().value['identity'])
        self.manifest['pages'][0]['sections'][0]['status'] = 'unsupported'
        second = self.index()
        self.assertEqual(second.search('creare tag', **self.context), [])
        self.assertNotEqual(first.value['identity'], second.value['identity'])
        tampered = copy.deepcopy(first.value)
        tampered['chunks'][0]['text'] = 'Invented operation'
        with self.assertRaises(EvidenceError):
            ManualIndex(tampered)

    def test_restricted_sections_and_feature_requirements_filter_before_ranking(self):
        section = self.manifest['pages'][0]['sections'][0]
        section['audience'] = 'maintainer'
        section['features'] = ['self_hosted']
        index = self.index()
        self.assertEqual(index.search('creare tag', **self.context, features=['self_hosted']), [])
        self.assertEqual(index.search('creare tag', **self.context, maintainer=True), [])
        self.assertEqual(len(index.search('creare tag', **self.context, features=['self_hosted'], maintainer=True)), 1)

    def test_workflow_requires_real_passed_report_and_current_sources(self):
        section = self.manifest['pages'][0]['sections'][0]
        section['kind'] = 'workflow'
        with self.assertRaises(EvidenceError):
            self.index()
        section['scenario_ids'] = ['tags-create']
        report = {'id': 'tags-create', 'status': 'passed', 'backend': 'real', 'application_revision': 'revision-1',
                  'source_hashes': {'BE/tags.py': file_digest(self.source)}, 'screenshots': []}
        (self.root / 'capture.json').write_text(canonical(report))
        self.manifest['scenarios'] = [{**report, 'report_path': 'capture.json', 'report_sha256': file_digest(self.root / 'capture.json')}]
        self.assertEqual(len(build_index(self.manual, self.code, self.manifest, artifact_root=self.root)['chunks']), 1)
        report['status'] = 'failed'
        (self.root / 'capture.json').write_text(canonical(report))
        self.manifest['scenarios'][0]['report_sha256'] = file_digest(self.root / 'capture.json')
        with self.assertRaises(EvidenceError):
            build_index(self.manual, self.code, self.manifest, artifact_root=self.root)

    def test_tooling_drift_and_preview_location_do_not_hide_content_changes(self):
        tool = self.code / 'BE/capture.py'
        tool.write_text('assert_backend_is_real()\n')
        self.manifest['metadata']['tooling_hashes'] = {'BE/capture.py': file_digest(tool)}
        first = self.index()
        self.manifest['metadata']['manual_url'] = 'http://127.0.0.1:9999'
        second = self.index()
        self.assertNotEqual(first.value['identity'], second.value['identity'])
        self.assertEqual(first.value['content_identity'], second.value['content_identity'])
        tool.write_text('replace_backend_with_mock()\n')
        with self.assertRaises(EvidenceError):
            self.index()
        with self.assertRaises(EvidenceError):
            first.verify_unchanged_dependencies(self.code)
        self.assertTrue(first.applicable(**self.context, code_root=self.code))

    def test_real_corpus_download_preserves_last_valid_index_on_corruption_or_wrong_release(self):
        from application.manuale.sync import synchronize
        self.manifest['metadata'].update({'application_revision': 'a' * 40, 'manual_revision': 'b' * 40,
            'publication_status': 'published', 'code_state': 'committed', 'manual_state': 'committed'})
        fixture = {'payload': canonical(self.index().value).encode()}
        requested = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requested.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(fixture['payload'])

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        output = self.root / 'downloaded-index.json'
        try:
            with override_settings(MANUAL_CORPUS_BASE_URL=f'http://127.0.0.1:{server.server_port}',
                    MANUAL_APPLICATION_REVISION='a' * 40, RUNNING_VERSION='v1', MANUAL_INDEX_PATH=str(output),
                    MANUAL_SOURCE_ROOT=str(self.code), MANUAL_RUN_ID='owned-fixture'):
                self.assertEqual(synchronize()['status'], 'updated')
                valid = output.read_bytes()
                self.assertEqual(requested[-1], '/' + 'a' * 40 + '/v1.json')
                from application.manuale.index import seal_index
                source_path = 'BE/tags.py'
                for declaration in ('evidence', 'runtime_dependencies', 'dependencies'):
                    with self.subTest(declaration=declaration):
                        candidate = copy.deepcopy(self.index().value)
                        if declaration == 'evidence':
                            candidate['chunks'][0]['evidence'][0]['sha256'] = 'c' * 64
                        elif declaration == 'runtime_dependencies':
                            candidate['chunks'][0]['runtime_dependencies'][source_path] = 'c' * 64
                        else:
                            candidate['dependencies'][source_path] = 'c' * 64
                        fixture['payload'] = canonical(seal_index(candidate)).encode()
                        with self.assertRaisesMessage(EvidenceError, 'Conflicting implementation evidence hashes'):
                            synchronize()
                        self.assertEqual(output.read_bytes(), valid)
                # Consistent declarations still fail when the actual source
                # differs. Hash agreement is necessary, not source validation.
                candidate = copy.deepcopy(self.index().value)
                candidate['chunks'][0]['evidence'][0]['sha256'] = 'c' * 64
                candidate['chunks'][0]['runtime_dependencies'][source_path] = 'c' * 64
                candidate['dependencies'][source_path] = 'c' * 64
                fixture['payload'] = canonical(seal_index(candidate)).encode()
                with self.assertRaisesMessage(EvidenceError, 'stale implementation evidence'):
                    synchronize()
                self.assertEqual(output.read_bytes(), valid)
                self.assertTrue(ManualIndex.load(output).applicable(
                    revision='a' * 40, release='v1', code_root=self.code))
                fixture['payload'] = b'{incomplete'
                with self.assertRaises(ValueError):
                    synchronize()
                self.assertEqual(output.read_bytes(), valid)
                self.manifest['metadata']['release'] = 'v2'
                fixture['payload'] = canonical(self.index().value).encode()
                with self.assertRaises(EvidenceError):
                    synchronize()
                self.assertEqual(output.read_bytes(), valid)
                with override_settings(MANUAL_RUN_ID=''):
                    with self.assertRaises(EvidenceError):
                        synchronize()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_tools_ignore_client_version_and_context_and_restrict_evidence(self):
        from application.manuale.tools import tool_search_manual, tool_get_manual_section, tool_get_manual_evidence
        output = self.root / 'index.json'
        promote(self.index().value, output)
        with override_settings(MANUAL_INDEX_PATH=str(output)), patch('application.manuale.tools._context', return_value=self.context):
            result = tool_search_manual('association', 'creare tag', revision='fake', maintainer=True, path='/tmp/fake')
            self.assertEqual(result['status'], 'verified')
            self.assertNotIn('evidence', result['results'][0])
            self.assertEqual(result['results'][0]['features'], [])
            self.assertEqual(result['results'][0]['audience'], 'public')
            section = tool_get_manual_section('association', 'docs/tag#creare-un-tag')
            self.assertEqual(section['status'], 'verified')
            self.assertEqual(section['section']['features'], [])
            self.assertEqual(section['section']['audience'], 'public')
        public_context = {**self.context, 'maintainer': False}
        with override_settings(MANUAL_INDEX_PATH=str(output)), patch('application.manuale.tools._context', return_value=public_context):
            self.assertEqual(tool_get_manual_evidence('association', 'docs/tag#creare-un-tag', maintainer=True)['status'], 'forbidden')

    def test_public_gap_diagnostics_do_not_read_or_disclose_unreviewed_content(self):
        from application.manuale.tools import tool_get_manual_gaps
        with patch('application.manuale.tools._context', return_value={'maintainer': False}), patch('application.manuale.tools._load') as load:
            result = tool_get_manual_gaps('association', maintainer=True, revision='forged')
        self.assertEqual(result['status'], 'forbidden')
        self.assertNotIn('gaps', result)
        load.assert_not_called()

    def test_owner_gap_diagnostics_filter_pages_and_detect_stale_review_targets(self):
        from application.manuale.index import seal_index
        from application.manuale.tools import tool_get_manual_gaps
        unrelated = self.code / 'BE/unreviewed.py'
        unrelated.write_text('def unreviewed(): return False\n')
        value = self.index().value
        value['catalog'] = {'summary': {'navigation_pages': 1, 'verified_sections': 1, 'sections': 2},
            'pages': [{'path': 'docs/tag.mdx', 'domains': ['tags'], 'sections': [{'id': 'eliminare-un-tag', 'status': 'pending'}]}],
            'domains': {'tags': {'status': 'review_target', 'constraints': [{'status': 'needs_external_verification'}]}},
            'screenshot_backlog': [{'page': 'docs/tag.mdx', 'status': 'review_target'}],
            'files': {'BE/unreviewed.py': {'sha256': file_digest(unrelated)}}}
        index = ManualIndex(seal_index(value))
        context = {**self.context, 'maintainer': True, 'code_root': str(self.code)}
        with patch('application.manuale.tools._context', return_value=context), patch('application.manuale.tools._load', return_value=(index, context)):
            result = tool_get_manual_gaps('association', page='docs/tag.mdx')
            self.assertEqual(result['coverage_status'], 'complete_inventory')
            self.assertEqual(result['snapshot_status'], 'checked')
            self.assertEqual(result['gaps'][0]['section'], 'eliminare-un-tag')
            self.assertEqual(result['pages'][0]['sections'][0]['status'], 'pending')
            unrelated.unlink()
            self.assertEqual(tool_get_manual_gaps('association')['stale_sources'], ['BE/unreviewed.py'])
            self.assertEqual(tool_get_manual_gaps('association')['snapshot_status'], 'stale')
            self.assertEqual(tool_get_manual_gaps('association', page='../secret')['status'], 'invalid_query')
            self.assertEqual(tool_get_manual_gaps('association', page='docs/removed.mdx')['status'], 'no_evidence')

    def test_agent_gap_dispatch_filters_client_identity_and_retains_trusted_owner(self):
        from application.agent.core import Agent, TOOL_FUNCTIONS
        recorded = []

        def gaps(**kwargs):
            recorded.append(kwargs)
            return {'status': 'diagnostic'}

        agent = Agent('association', 'Aurora', AsyncMock(), AsyncMock(), user_id='trusted-owner')
        with patch.dict(TOOL_FUNCTIONS, {'get_manual_gaps': gaps}), patch('instance.restore.locking.coordinated_sync', side_effect=lambda fn: fn):
            asyncio.run(agent._execute_tool('get_manual_gaps', {'page': 'docs/tag.mdx', 'user_id': 'forged', 'maintainer': True, 'revision': 'fake'}))
        self.assertEqual(recorded, [{'page': 'docs/tag.mdx', 'user_id': 'trusted-owner', 'sport_association_id': 'association'}])

    def test_stale_owner_diagnostics_remain_visible_while_normal_retrieval_abstains(self):
        from application.manuale.tools import _load, tool_get_manual_gaps, tool_search_manual
        output = self.root / 'index.json'
        promote(self.index().value, output)
        self.source.write_text('def create_tag(name):\n    return "changed"\n')
        context = {**self.context, 'maintainer': True, 'code_root': str(self.code)}
        with override_settings(MANUAL_INDEX_PATH=str(output), MANUAL_RUN_ID='owned-fixture', MANUAL_CORPUS_BASE_URL=''), patch('application.manuale.tools._context', return_value=context):
            gaps = tool_get_manual_gaps('association')
            self.assertEqual(gaps['status'], 'diagnostic')
            self.assertEqual(gaps['snapshot_status'], 'stale')
            self.assertEqual(gaps['stale_sources'], ['BE/tags.py'])
            self.assertEqual(tool_search_manual('association', 'creare tag')['status'], 'no_evidence')
        context['maintainer'] = False
        with patch('application.manuale.tools._context', return_value=context):
            with self.assertRaises(EvidenceError):
                _load('association', validate=False)

    def test_explicit_coverage_question_uses_owner_diagnostics_without_model_generation(self):
        from application.agent.core import Agent, TOOL_FUNCTIONS
        from application.manuale.tools import is_manual_diagnostic_question
        self.assertTrue(is_manual_diagnostic_question('Mostrami la copertura del manuale'))
        self.assertFalse(is_manual_diagnostic_question('Come posso creare un tag?'))
        self.assertFalse(is_manual_diagnostic_question('Quanti soci hanno pagato?'))
        callback, provider = AsyncMock(), AsyncMock()
        agent = Agent('association', 'Aurora', provider, callback, user_id='owner')
        fixture = {'status': 'diagnostic', 'coverage_status': 'complete_inventory', 'snapshot_status': 'checked',
                   'summary': {'navigation_pages': 43, 'verified_sections': 4, 'sections': 501}, 'gaps': [], 'pages': []}
        with patch.dict(TOOL_FUNCTIONS, {'get_manual_gaps': lambda **kwargs: fixture}), patch('instance.restore.locking.coordinated_sync', side_effect=lambda fn: fn):
            asyncio.run(agent.process_message('Mostrami la copertura del manuale'))
        self.assertIn('43 pagine inventariate', callback.on_message_chunk.call_args.args[0])
        self.assertIn('4 sezioni verificate su 501', callback.on_message_chunk.call_args.args[0])
        callback.on_tool_call.assert_called_once_with('get_manual_gaps', {})
        provider.chat_stream.assert_not_called()

    def test_local_preview_index_cannot_be_served_as_a_published_manual(self):
        from application.manuale.tools import tool_search_manual
        self.manifest['metadata']['publication_status'] = 'local-preview-only'
        output = self.root / 'index.json'
        promote(self.index().value, output)
        with override_settings(MANUAL_INDEX_PATH=str(output), MANUAL_RUN_ID=''), patch('application.manuale.tools._context', return_value=self.context):
            self.assertEqual(tool_search_manual('association', 'creare tag')['status'], 'no_evidence')
        with override_settings(MANUAL_INDEX_PATH=str(output), MANUAL_RUN_ID='owned-run'), patch('application.manuale.tools._context', return_value=self.context):
            self.assertEqual(tool_search_manual('association', 'creare tag')['status'], 'verified')

    def test_explicit_manual_question_uses_existing_agent_and_never_invents_missing_steps(self):
        from application.agent.core import Agent, TOOL_FUNCTIONS, TOOLS_FOR_LLM
        self.assertIn('search_manual', TOOL_FUNCTIONS)
        self.assertIn('search_manual', {item['function']['name'] for item in TOOLS_FOR_LLM})
        output = self.root / 'index.json'
        promote(self.index().value, output)
        callback = AsyncMock()
        provider = AsyncMock()
        agent = Agent('association', 'Aurora', provider, callback)
        with override_settings(MANUAL_INDEX_PATH=str(output)), patch('application.manuale.tools._context', return_value=self.context), patch('instance.restore.locking.coordinated_sync', side_effect=lambda fn: fn):
            asyncio.run(agent.process_message('Come posso creare un tag?'))
        answer = callback.on_message_chunk.call_args.args[0]
        self.assertIn('premi Applica', answer)
        self.assertIn('[Leggi nel manuale](https://manual.example/docs/tag#creare-un-tag)', answer)
        attachment = callback.on_manual_section.call_args.args[0]
        self.assertEqual(attachment['embedded_url'], '/#/manuale?section=docs%2Ftag%23creare-un-tag')
        self.assertIn('premi Applica', attachment['reader'][0]['markdown'])
        self.assertNotIn('evidence', attachment)
        events = [call[0] for call in callback.mock_calls]
        self.assertLess(events.index('on_message_chunk'), events.index('on_manual_section'))
        self.assertLess(events.index('on_manual_section'), events.index('on_message_end'))
        provider.chat_stream.assert_not_called()
        callback.reset_mock()
        with override_settings(MANUAL_INDEX_PATH=str(output)), patch('application.manuale.tools._context', return_value=self.context), patch('instance.restore.locking.coordinated_sync', side_effect=lambda fn: fn):
            asyncio.run(agent.process_message('Manuale teletrasporto satellitare'))
        self.assertIn('Non ho trovato', callback.on_message_chunk.call_args.args[0])
        self.assertNotIn('Applica', callback.on_message_chunk.call_args.args[0])
        callback.on_manual_section.assert_not_called()

    def test_unconfigured_agent_uses_only_verified_manual_search_for_bare_queries(self):
        from application.agent.core import Agent
        output = self.root / 'index.json'
        promote(self.index().value, output)
        callback = AsyncMock()
        agent = Agent('association', 'Aurora', None, callback)
        with override_settings(MANUAL_INDEX_PATH=str(output)), patch('application.manuale.tools._context', return_value=self.context), patch('instance.restore.locking.coordinated_sync', side_effect=lambda fn: fn):
            asyncio.run(agent.process_message('creare tag'))
            self.assertIn('premi Applica', callback.on_message_chunk.call_args.args[0])
            callback.on_tool_call.assert_awaited_with('search_manual', {'query': 'creare tag'})
            callback.reset_mock()
            asyncio.run(agent.process_message('teletrasporto satellitare'))
            self.assertIn('Non ho trovato', callback.on_message_chunk.call_args.args[0])
            callback.on_manual_section.assert_not_called()

    def test_actual_websocket_callback_emits_reviewed_markdown_before_ending_the_answer(self):
        from application.agent.core import Agent
        from application.chat.consumers import WebSocketAgentCallback
        from types import SimpleNamespace
        self.page.write_text('## Creare un tag\n<Steps><Step title="Applica il tag">'
                             'Apri **Iscrizioni** e premi `Applica`.</Step></Steps>')
        self.manifest['pages'][0]['sections'][0]['content_sha256'] = digest(sections(self.page.read_text())['creare-un-tag']['mdx'])
        output = self.root / 'index.json'
        promote(self.index().value, output)
        consumer = SimpleNamespace(send_json=AsyncMock())
        provider = AsyncMock()
        agent = Agent('association', 'Aurora', provider, WebSocketAgentCallback(consumer))
        with override_settings(MANUAL_INDEX_PATH=str(output)), patch('application.manuale.tools._context', return_value=self.context), patch('instance.restore.locking.coordinated_sync', side_effect=lambda fn: fn):
            asyncio.run(agent.process_message('Come posso creare un tag?'))
        events = [call.args[0] for call in consumer.send_json.call_args_list]
        types = [event['type'] for event in events]
        self.assertEqual(types[-4:], ['message_chunk', 'manual_section', 'message_end', 'done'])
        guide = events[-3]['section']
        self.assertEqual(guide['reader'][0]['markdown'], 'Apri **Iscrizioni** e premi `Applica`.')
        self.assertEqual(guide['page'], 'docs/tag')
        self.assertNotIn('evidence', guide)
        provider.chat_stream.assert_not_called()

    def test_router_does_not_capture_normal_data_requests_and_ambiguity_is_explicit(self):
        self.assertFalse(is_manual_question('Quanti soci hanno pagato questo mese?'))
        self.assertTrue(is_manual_question('Come si crea un tag?'))
        self.assertTrue(is_manual_question('Come cerco Giulia nelle iscrizioni?'))
        self.assertTrue(is_manual_question('Come posso archiviare una iscrizione?'))
        self.assertTrue(is_manual_question('Come posso ripristinare una iscrizione?'))
        self.assertTrue(is_manual_question('Come posso inviare una fattura elettronica al Sistema di Interscambio?'))
        self.assertTrue(is_manual_question('Come invio una comunicazione?'))
        self.assertFalse(is_manual_question('Quante comunicazioni ho inviato?'))
        hits = [{'page': 'docs/a', 'title': 'Corso', 'url': 'https://manual.example/a', 'text': 'A', 'score': .5},
                {'page': 'docs/b', 'title': 'Pagamento', 'url': 'https://manual.example/b', 'text': 'B', 'score': .49}]
        self.assertIn('Quale di queste operazioni', grounded_answer({'results': hits}))
        # A scenario alone may exercise two distinct operations; only an explicit
        # shared intent makes duplicate chapter evidence corroborate an action.
        for hit in hits:
            hit['scenario_ids'] = ['one-verified-workflow']
        hits[0]['intent'] = 'courses.create'
        hits[1]['intent'] = 'payments.create'
        self.assertIn('Quale di queste operazioni', grounded_answer({'results': hits}))
        hits[1]['intent'] = 'courses.create'
        hits[1]['title'] = 'Creare un corso'
        self.assertIn('A', grounded_answer({'results': hits}))
        self.assertNotIn('Quale di queste operazioni', grounded_answer({'results': hits}))
