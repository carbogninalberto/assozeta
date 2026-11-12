"""Offline integration controls; no acceptance, sockets or services."""
import copy
import io
import json
import subprocess
import sys
import tempfile
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from TESTS.adapters.django import message_mutations_browser_runtime as runtime
from TESTS import test_message_mutations_browser as controls
from TESTS.adapters.django import message_reads_browser_runtime as shared
from TESTS.adapters.django import message_mutations_browser as consumer


class IntegrationControls(unittest.TestCase):
    def test_static_mutation_diagnostics_dependency_is_bound_for_read_runtime(self):
        sources = shared.source_hashes()
        self.assertTrue('TESTS/browser/message-mutations.mjs' in sources)
        self.assertTrue('TESTS/browser/message-mutations.test.mjs' in sources)


    def test_mutation_failure_wire_is_closed_bounded_and_separate_from_default_reads(self):
        program = r'''
import assert from 'node:assert/strict';
import {exercise} from './TESTS/browser/message-mutations.mjs';
import {childFailure} from './TESTS/browser/message-reads-runner.mjs';
const frames=[];
assert.throws(()=>childFailure('browser-exercise',Error('PRIVATE'),{},'arbitrary'));
const receipt=await exercise({browser:{},expect:{},input:{password:'PRIVATE'},checkpoint:async()=>{}});
assert.equal(receipt.success,false);
assert.deepEqual(receipt.workflow_failure,{actor:null,action:'initialization',step:'mutation-input-validation'});
const diagnostic={workflow_failure:receipt.workflow_failure,failures:receipt.failures,
 failure_count:receipt.failure_count,failures_truncated:receipt.failures_truncated};
frames.push(childFailure('browser-exercise',Error('PRIVATE URL?token=PRIVATE'),diagnostic,'mutations'));
const location={actor:'restricted_collaborator',action:'read',step:'mutation-restricted-delete-disabled'};
const row={...location,kind:'workflow-failed'};
const known={workflow_failure:location,failures:[row],failure_count:1,failures_truncated:false};
frames.push(childFailure('browser-exercise',Error('PRIVATE'),known,'mutations'));
frames.push(childFailure('browser-exercise',Error('PRIVATE'),{...known,failures:Array(16).fill(row),
 failure_count:1000000,failures_truncated:true},'mutations'));
for(const bad of [
 {...known,raw:'PRIVATE'}, {...known,workflow_failure:{...location,url:'PRIVATE'}},
 {...known,workflow_failure:{...location,step:'denied-navigation'}},
 {...known,workflow_failure:{...location,actor:'PRIVATE'}},
 {...known,failures:[{...row,message:'PRIVATE'}]}, {...known,failures:[{...row,kind:'PRIVATE'}]},
 {...known,failures:[{...row,action:'reload'}]}, {...known,failure_count:true},
 {...known,failures_truncated:true}, {...known,failures:Array(17).fill(row)},
 {...known,workflow_failure:{...location,actor:[],action:[]}},
 {...known,workflow_failure:{...location,step:'mutation-checkpoint'}},
]) {
 const frame=childFailure('browser-exercise',Error('PRIVATE'),bad,'mutations');
 assert(!JSON.stringify(frame).includes('PRIVATE'));
 assert.equal(frame.mutation_diagnostics.failures[0].kind,'invalid-mutation-diagnostic');
 frames.push(frame);
}
assert.deepEqual(childFailure('startup',Error('PRIVATE'),known,'mutations'),
 {type:'failure',stage:'startup',code:'startup-failed',error_class:'Error'});
const read=childFailure('browser-exercise',Error('PRIVATE'),{workflow_step:'denied-navigation',
 failures:[{kind:'workflow-assertion',actor:'restricted_collaborator',action:'denied'}]});
assert(!('mutation_diagnostics' in read));frames.push(read);
console.log(JSON.stringify(frames));
'''
        result = subprocess.run(['node', '--input-type=module', '-e', program], cwd=shared.ROOT,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        frames = json.loads(result.stdout)
        for frame in frames[:-1]:
            checked = shared.validate_child_failure(frame, consumer='mutations')
            self.assertEqual(checked['mutation_diagnostics'], frame['mutation_diagnostics'])
            self.assertNotIn('PRIVATE', json.dumps(checked))
            with self.assertRaises(shared.HarnessError): shared.validate_child_failure(frame)
        self.assertEqual(frames[1]['mutation_diagnostics']['workflow_failure']['actor'], 'restricted_collaborator')
        self.assertEqual(frames[2]['mutation_diagnostics']['failure_count'], 1000000)
        shared.validate_child_failure(frames[-1])
        with self.assertRaises(shared.HarnessError):
            shared.validate_child_failure(frames[-1], consumer='mutations')
        for mode in ('extra', 'step', 'row-extra', 'kind', 'actor', 'action', 'count', 'bool',
                     'truncated', 'short', 'oversize', 'outer', 'mixed', 'stage', 'nested-type', 'first-location'):
            frame = copy.deepcopy(frames[1])
            diagnostic = frame['mutation_diagnostics']
            if mode == 'extra': diagnostic['url'] = 'PRIVATE'
            if mode == 'step': diagnostic['workflow_failure']['step'] = 'denied-navigation'
            if mode == 'row-extra': diagnostic['failures'][0]['message'] = 'PRIVATE'
            if mode == 'kind': diagnostic['failures'][0]['kind'] = 'PRIVATE'
            if mode == 'actor': diagnostic['failures'][0]['actor'] = 'PRIVATE'
            if mode == 'action': diagnostic['failures'][0]['action'] = 'reload'
            if mode == 'count': diagnostic['failure_count'] = 1000001
            if mode == 'bool': diagnostic['failure_count'] = True
            if mode == 'truncated': diagnostic['failures_truncated'] = True
            if mode == 'short': diagnostic['failure_count'] = 2
            if mode == 'oversize': diagnostic['failures'] *= 17
            if mode == 'outer': frame['url'] = 'PRIVATE'
            if mode == 'mixed': frame['workflow_step'] = 'denied-navigation'
            if mode == 'stage': frame.update(stage='startup', code='startup-failed')
            if mode == 'nested-type': diagnostic['failures'][0]['kind'] = []
            if mode == 'first-location': diagnostic['workflow_failure']['step'] = 'mutation-checkpoint'
            with self.subTest(mode=mode), self.assertRaises(shared.HarnessError):
                shared.validate_child_failure(frame, consumer='mutations')

    def test_actual_mutation_failure_paths_cross_js_python_and_parent_rpc(self):
        result = subprocess.run(['node', '--test', '--test-reporter=tap',
            '--test-name-pattern=actual mutation failure frames survive the closed wire',
            'TESTS/browser/message-mutations.test.mjs'], cwd=shared.ROOT,
            env=dict(shared.os.environ, MUTATION_DIAGNOSTIC_WIRE='1'),
            capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = [line[2:] for line in result.stdout.splitlines()
                 if line.startswith('# {"mutation_failure_frames":')]
        self.assertEqual(len(lines), 1)
        wire = json.loads(lines[0])
        self.assertEqual(set(wire['steps']), shared.MUTATION_WORKFLOW_STEPS)
        self.assertEqual(set(wire['kinds']), shared.MUTATION_FAILURE_KINDS)
        self.assertEqual(set(wire['actions']), shared.MUTATION_ACTIONS)
        frames = wire['mutation_failure_frames']
        self.assertEqual(len(frames), 19)
        for frame in frames:
            self.assertEqual(shared.validate_child_failure(frame, consumer='mutations')['mutation_diagnostics'],
                             frame['mutation_diagnostics'])
        class Pipe(io.BytesIO):
            def fileno(self): return 100
        for invalid in (False, True):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                frame = copy.deepcopy(frames[0])
                if invalid: frame['mutation_diagnostics']['failures'][0]['url'] = 'PRIVATE'
                child = Mock(stdin=Pipe(), stdout=Pipe())
                selector = Mock()
                selector.select.return_value = [object()]
                with patch.object(shared.subprocess, 'Popen', return_value=child), \
                        patch.object(shared.selectors, 'DefaultSelector', return_value=selector), \
                        patch.object(shared.os, 'read', return_value=(json.dumps(frame)+'\n').encode()), \
                        patch.object(shared, 'stop_child') as stop:
                    with self.assertRaises(shared.HarnessError) as raised:
                        shared.run_node(node=Path('/node'), module=Path('/module'), work=root,
                            private_log=io.StringIO(), environment={}, input_data={'password': 'PRIVATE'},
                            checkpoint=shared.Checkpoints(lambda *_: None, consumer='mutations'),
                            probe_origin='http://127.0.0.1:9', consumer='mutations')
                    error = raised.exception
                    self.assertEqual(error.code, 'browser-mutation-diagnostic-protocol' if invalid
                                     else 'browser-child-failed')
                    if invalid:
                        self.assertIsNone(error.child_failure)
                    else:
                        self.assertEqual(error.child_failure['mutation_diagnostics'], frame['mutation_diagnostics'])
                    self.assertNotIn('PRIVATE', json.dumps(shared.public_error('runtime', error)))
                    stop.assert_called_once_with(child)
                    self.assertTrue(child.stdin.closed and child.stdout.closed)
                    self.assertFalse((root/'browser-input.json').exists())

    def test_fixed_route_selection_and_read_default(self):
        spec = consumer.authored_spec()
        for version in ('v1', 'v2'):
            backend = 'django-v1' if version == 'v1' else 'go-v2'
            for actor in consumer.READERS:
                path = '/api' + spec['actors'][actor]['delete']['path']
                self.assertEqual(shared.route_target(path, 'DELETE', version=version,
                    artifacts={}, consumer='mutations')['backend'], backend)
                for method, selected in (('GET', 'mutations'), ('POST', 'mutations'), ('DELETE', 'reads')):
                    with self.assertRaises(shared.HarnessError):
                        shared.route_target(path, method, version=version, artifacts={}, consumer=selected)
            with self.assertRaises(shared.HarnessError):
                shared.route_target('/api/communications/messages/00000000-0000-4000-8000-000000000000/delete',
                    'DELETE', version=version, artifacts={}, consumer='mutations')
        with self.assertRaises(shared.HarnessError):
            shared.route_target('/', 'GET', version='v1', artifacts={'index.html': 'a'*64}, consumer='arbitrary')
        for path, method in shared.browser.SELECTED_ROUTES.items():
            self.assertEqual(shared.route_target(path, method, version='v2', artifacts={}),
                shared.route_target(path, method, version='v2', artifacts={}, consumer='reads'))

    def test_private_checkpoint_parser_complete_missing_reordered_duplicate_and_poison(self):
        initial = controls.state_control()
        cache, contrasts = controls.cache_control()
        guard = {'running': True, 'pid': 123, 'instance': 'offline'}
        for version in ('v1', 'v2'):
            state = initial
            verifier = consumer.CheckpointVerifier(version=version, contrasts=contrasts,
                initial_models=state, initial_cache=cache)
            observations = []
            def snapshot(payload):
                nonlocal state
                expected, state, traffic = controls.checkpoint_control(payload['actor'], payload['action'], state, version=version)
                self.assertEqual(payload, expected)
                verifier.observe(payload, models=state, cache=cache, traffic=traffic,
                    started=controls.START, finished=controls.END, remote_addr='127.0.0.1',
                    process_before=guard, process_after=guard)
                observations.append((payload['actor'], payload['action']))
                return {'models': state, 'cache': cache}
            gate = shared.Checkpoints(snapshot, consumer='mutations')
            for number, (actor, action) in enumerate(consumer.CHECKPOINTS, 1):
                with self.assertRaises(shared.HarnessError): gate.complete()
                payload, _, _ = controls.checkpoint_control(actor, action, state, version=version)
                ack = gate.receive({'type': 'checkpoint', 'seq': number, 'payload': payload})
                self.assertEqual(len(observations), number)
                self.assertEqual(ack, {'type': 'ack', 'seq': number, 'success': True})
            gate.complete()
            self.assertEqual(len(gate.closed), 4)
            self.assertEqual(verifier.verified, 24)
        payload, _, _ = controls.checkpoint_control('owner', 'login', initial)
        frame = {'type': 'checkpoint', 'seq': 1, 'payload': payload}
        for bad in ('seq', 'bool', 'actor', 'action', 'missing', 'extra', 'outer', 'duplicate', 'snapshot'):
            take = Mock(return_value={'models': initial, 'cache': cache})
            gate = shared.Checkpoints(take, consumer='mutations')
            changed = copy.deepcopy(frame)
            if bad == 'seq': changed['seq'] = 2
            if bad == 'bool': changed['seq'] = True
            if bad == 'actor': changed['payload']['actor'] = 'foreign_owner'
            if bad == 'action': changed['payload']['action'] = 'read'
            if bad == 'missing': del changed['payload']['requests']
            if bad == 'extra': changed['payload']['secret'] = 'PRIVATE'
            if bad == 'outer': changed['actor'] = 'owner'
            if bad == 'duplicate': gate.receive(frame)
            if bad == 'snapshot': take.side_effect = ValueError('PRIVATE')
            with self.subTest(bad=bad), self.assertRaises((shared.HarnessError, ValueError)):
                gate.receive(changed)
            self.assertTrue(gate.failed)
            with self.assertRaises(shared.HarnessError): gate.receive(frame)
            with self.assertRaises(shared.HarnessError): gate.complete()

    def test_delete_dispatch_complete_private_bytes_real_peer_and_failure_containment(self):
        target = '/api' + consumer.authored_spec()['actors']['owner']['delete']['path']
        for version in ('v1', 'v2'):
            for mode in ('ok', 'method', 'target', 'body', 'incomplete', 'oversize', 'peer', 'cid'):
                journal = shared.ProxyJournal()
                handler_type = shared.proxy_handler(ui=Path('/unused'), artifacts={}, version=version,
                    django_port=1001, go_port=1002, journal=journal, consumer='mutations')
                handler = handler_type.__new__(handler_type)
                handler.command = 'DELETE'
                handler.path = target if mode != 'target' else target.replace('/delete', '/other')
                if mode == 'method': handler.path = '/api/communications/messages/add'
                handler.server = SimpleNamespace(server_port=9999)
                handler.headers = Message()
                handler.headers['Host'] = '127.0.0.1:9999'
                handler.headers['X-Correlation-ID'] = ('PRIVATE' if mode == 'cid' else
                    f'message-mutations-{version}-owner-delete-1')
                if mode == 'body': handler.headers['Content-Length'] = '1'
                handler.rfile, handler.wfile = io.BytesIO(b'x'), io.BytesIO()
                handler.send_response_only, handler.send_header, handler.end_headers = Mock(), Mock(), Mock()
                response = Mock(status=200, length=1 if mode == 'incomplete' else 0)
                response.read.return_value = b'x' * 65 if mode == 'oversize' else b'{"msg":"message deleted."}'
                response.isclosed.return_value = True
                response.getheaders.return_value = [('Content-Type', 'application/json'), ('X-Private', 'PRIVATE')]
                connection = Mock()
                connection.sock.getpeername.return_value = ('127.0.0.1',
                    1003 if mode == 'peer' else 1001 if version == 'v1' else 1002)
                connection.getresponse.return_value = response
                with patch.object(shared.http.client, 'HTTPConnection', return_value=connection) as factory, \
                        patch.object(shared, 'MAX_BODY', 64):
                    handler.do_DELETE()
                self.assertEqual(journal.active_http, 0)
                self.assertNotIn('PRIVATE', json.dumps(journal.events))
                self.assertNotIn('body', json.dumps(journal.events))
                if mode == 'ok':
                    self.assertEqual(journal.failures, [])
                    connection.putrequest.assert_called_once_with('DELETE', target.removeprefix('/api'),
                        skip_host=True, skip_accept_encoding=True)
                    connection.endheaders.assert_called_once_with(None)
                    self.assertEqual(handler.wfile.getvalue(), response.read.return_value)
                    self.assertEqual(journal.private_traffic[0]['body'], response.read.return_value)
                    self.assertTrue(journal.private_traffic[0]['complete'])
                    django = [] if version == 'v2' else [{key: journal.events[0][key]
                        for key in ('cid', 'method', 'path', 'status')} | {'seq': 1, 'error_type': None}]
                    evidence = runtime.observed_traffic(journal.private_traffic, journal.events, django, version=version)
                    self.assertEqual(evidence[0]['body'], response.read.return_value)
                else:
                    self.assertTrue(journal.failures)
                    self.assertEqual(journal.private_traffic, [])
                    handler.send_response_only.assert_called_with(502)
                if mode in ('method', 'target', 'body', 'cid'): factory.assert_not_called()
                else: connection.close.assert_called_once()

    def test_observed_gzip_wire_bytes_are_complete_bounded_and_independent(self):
        import gzip
        body = b'{"private":"unchanged complete bytes"}'
        wire = gzip.compress(body)
        self.assertEqual(shared.observed_body(wire, [('Content-Encoding', 'gzip')]), body)
        self.assertEqual(shared.observed_body(body, []), body)
        for data, headers in ((wire[:-1], [('Content-Encoding', 'gzip')]),
                (wire + b'tail', [('Content-Encoding', 'gzip')]),
                (body, [('Content-Encoding', 'br')]),
                (wire, [('Content-Encoding', 'gzip'), ('content-encoding', 'gzip')])):
            with self.subTest(headers=headers), self.assertRaises(shared.HarnessError):
                shared.observed_body(data, headers)
        with patch.object(shared, 'MAX_BODY', 8), self.assertRaises(shared.HarnessError):
            shared.observed_body(wire, [('Content-Encoding', 'gzip')])

    def test_full_window_multiplicity_byte_binding_and_journal_privacy(self):
        proof = controls.traffic_control('GET', consumer.LIST, [],
            cid='message-mutations-v2-owner-read-1')
        event = {key: proof['proxy'][key] for key in ('cid', 'method', 'path', 'status', 'backend', 'fallback')}
        event.update(intended_backend='go-v2', peer=['127.0.0.1', 1002], intended_peer=['127.0.0.1', 1002])
        private = event | {key: proof[key] for key in ('body', 'request_body', 'headers', 'complete')}
        private.update(wire_body=private['body'], response_headers=list(private['headers'].items()))
        for bad in ('none', 'body-type', 'decoded', 'wire', 'headers', 'incomplete', 'missing',
                    'duplicate', 'route', 'backend', 'asgi', 'coexistence'):
            rows, events, django = [copy.deepcopy(private)], [copy.deepcopy(event)], []
            if bad == 'body-type': rows[0]['body'] = 'PRIVATE'
            if bad == 'decoded': rows[0]['body'] += b'PRIVATE'
            if bad == 'wire': rows[0]['wire_body'] += b'PRIVATE'
            if bad == 'headers': rows[0]['headers']['Content-Type'] = 'text/plain'
            if bad == 'incomplete': rows[0]['complete'] = False
            if bad == 'missing': rows.clear()
            if bad == 'duplicate': events.append(copy.deepcopy(event))
            if bad == 'route': rows[0]['path'] = '/wrong'
            if bad == 'backend': events[0]['backend'] = 'django-v1'
            if bad == 'asgi': django.append({'method':'GET','path':consumer.LIST,'cid':event['cid'],'status':200,'error_type':None})
            if bad == 'coexistence': events.append(dict(event, backend='django-coexistence', path='/statistic/dashboard', cid=''))
            if bad == 'none':
                self.assertEqual(runtime.observed_traffic(rows, events, django, version='v2')[0]['body'], proof['body'])
            else:
                with self.subTest(bad=bad), self.assertRaises(shared.HarnessError):
                    runtime.observed_traffic(rows, events, django, version='v2')
        journal = shared.ProxyJournal()
        journal.record_private(private)
        self.assertEqual(journal.snapshot(), [])
        with self.assertRaises(shared.HarnessError): journal.record_private(private)
        journal.private_traffic = [dict(private, cid=str(i)) for i in range(25)]
        journal.record_private(private)
        self.assertEqual(len(journal.private_traffic), 26)
        with self.assertRaises(shared.HarnessError): journal.record_private(dict(private, cid='overflow'))

    def test_parent_composition_real_verifier_privacy_final_guard_and_cleanup(self):
        from TESTS import test_message_reads_browser as read_controls
        for version, mode in ((version, mode) for version in ('v1','v2')
                for mode in ('ok', 'snapshot', 'provenance', 'process', 'modules', 'native')):
            with self.subTest(version=version,mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                cache, contrasts = controls.cache_control()
                current = {'models': controls.state_control(), 'cache': cache}
                journal = shared.ProxyJournal()
                observer = shared.CompleteAsgiObserver(None)
                _, _, _, provenance = read_controls.browser_control(shared.browser.authored_spec())
                provenance['source_sha256'].update({name: 'a'*64 for name in consumer.EXTRA_SOURCES})
                build = {'invocation': ['offline-control'], 'record_sha256': 'a'*64,
                    'source_sha256': {key: value for key, value in provenance['source_sha256'].items() if key.startswith('UI/')},
                    'artifact_sha256': provenance['artifact_sha256']}
                guard = {'running': True, 'pid': 123, 'instance': 'offline'}
                lifecycle, report = [], {}
                @contextmanager
                def proxy(*_args, **_kwargs):
                    lifecycle.append('proxy-enter')
                    try: yield 'http://127.0.0.1:9999'
                    finally: lifecycle.append('proxy-exit')
                @contextmanager
                def probe():
                    lifecycle.append('probe-enter')
                    try: yield 'http://127.0.0.1:9998', {'cookie':2,'redirect':5,'followed':0,
                        'cookies_received':True,'post':1,'post_body_preserved':True}
                    finally: lifecycle.append('probe-exit')
                stamp = datetime.fromtimestamp(controls.START, timezone.utc)
                clock = Mock()
                clock.now.return_value = stamp
                def node(**kwargs):
                    self.assertEqual(kwargs['consumer'], 'mutations')
                    checkpoint = kwargs['checkpoint']
                    requests, acks, cookies = [], [], []
                    for number, (actor, action) in enumerate(consumer.CHECKPOINTS, 1):
                        payload, models, traffic = controls.checkpoint_control(actor, action, current['models'],version=version)
                        current['models'] = models
                        for proof in traffic:
                            port = 1001 if version=='v1' else 1002
                            event = dict(proof['proxy'], intended_backend='django-v1' if version=='v1' else 'go-v2',
                                peer=['127.0.0.1',port], intended_peer=['127.0.0.1',port], set_cookie_sha256=shared.json_digest([]))
                            if action == 'login':
                                event.update(bkn_auth_cookie_present=True, set_cookie_sha256='a'*64)
                                cookies.append({'actor':actor,'method':'POST','path':'/api/oauth2/login',
                                    'sha256':'a'*64,'bkn_auth_cookie_present':True})
                            journal.record(event)
                            observer.events.extend(copy.deepcopy(proof['django_events']))
                            journal.record_private(dict(event,
                                **{key: proof[key] for key in ('body','request_body','headers','complete')},
                                wire_body=proof['body'],response_headers=list(proof['headers'].items())))
                        if mode == 'snapshot' and action == 'read':
                            current['models']['offline.Model0'] = [{'unexpected':'PRIVATE'}]
                        checkpoint.receive({'type':'checkpoint','seq':number,'payload':payload})
                        requests.extend(payload['requests']);acks.append({'actor':actor,'action':action})
                    checkpoint.complete()
                    receipt = {'success':True,'version':version,'contexts_created':4,'contexts_closed':True,
                        'failures':[],'checkpoints':acks,'requests':requests,'browser_owned_by_parent':True,
                        'service_workers_blocked':True,'business_responses_mocked':False,
                        'authentication_state_seeded':False,'all_response_bodies_complete':True,
                        'external_integrations_excluded':True,'font_parity_excluded':True,
                        'complete_message_family_parity':False,'transport':shared.browser.TRANSPORT,
                        'artifact_sha256':provenance['artifact_sha256'],
                        'static_verified':['index.html','build-assets/main.js']}
                    if mode == 'process': guard['pid'] = 999
                    return {'receipt':receipt,'transport':{'redirect_blocked':True,'cookies_preserved':True,
                        'post_body_preserved':True},'cookies':cookies,'browser_binary':{'sha256':'a'*64}}
                identities = {actor:{'user_id':consumer.authored_spec()['actors'][actor]['user_id'],
                    'username':actor,'password':'PRIVATE-PASSWORD'} for actor in consumer.ACTORS}
                args = SimpleNamespace(ui_dir=root,build_observation=root/'build.json',node=Path('/node'),
                    playwright_module=Path('/module'),timeout=30, go_binary=Path('/go'),
                    image_codec=Path('/codec'),go_build_record=Path('/native.json'))
                changed = copy.deepcopy(provenance)
                if mode == 'provenance': changed['source_sha256'][consumer.EXTRA_SOURCES[0]] = 'b'*64
                with ExitStack() as stack:
                    from TESTS.adapters.django import production_runtime, message_mutations
                    stack.enter_context(patch.object(production_runtime,'require_production_python'))
                    stack.enter_context(patch.object(message_mutations,'require_private_database'))
                    stack.enter_context(patch.object(consumer,'capture_provenance',side_effect=[provenance,changed]))
                    stack.enter_context(patch.object(shared,'validate_build_record',return_value=build))
                    module_proof={'version':'1.0.0','files':{'module':'a'*64}}
                    stack.enter_context(patch.object(shared,'module_fingerprints',side_effect=[module_proof,
                        dict(module_proof,version='2.0.0') if mode=='modules' else module_proof]))
                    stack.enter_context(patch.object(shared,'validate_go_build_record',
                        side_effect=[{'source_stable':True},{'source_stable':mode!='native'}]))
                    stack.enter_context(patch.object(shared,'ProxyJournal',return_value=journal))
                    stack.enter_context(patch.object(shared,'durable_snapshot',side_effect=lambda *_:{'models':copy.deepcopy(current['models']),'cache':cache}))
                    stack.enter_context(patch.object(shared,'owned_http_server',proxy))
                    stack.enter_context(patch.object(shared,'transport_probe',probe))
                    stack.enter_context(patch.object(shared,'run_node',side_effect=node))
                    stack.enter_context(patch.object(runtime,'datetime',clock))
                    def run():
                        return runtime.run_version(version=version,args=args,work=root,private_log=io.StringIO(),
                            environment={},identities=identities,apps=None,raw=None,fixture={'contrasts':contrasts},
                            observer=observer,django_port=1001,go_port=1002,process_guard=lambda:copy.deepcopy(guard),
                            remote_addr='127.0.0.1',report=report)
                    if mode == 'ok':
                        result = run()
                        self.assertTrue(result['acceptance']['success'])
                        self.assertFalse(result['http_denials_verified'])
                        self.assertNotIn('PRIVATE', json.dumps(result))
                        self.assertEqual(result['acceptance']['checkpoints_verified'],24)
                        self.assertEqual(result['acceptance']['selected_requests_verified'],26)
                    else:
                        with self.assertRaises((AssertionError, shared.HarnessError)): run()
                self.assertEqual(journal.private_traffic, [])
                self.assertEqual(lifecycle,['proxy-enter','probe-enter','probe-exit','proxy-exit'])
                for path in (root/version).iterdir():
                    self.assertEqual(path.stat().st_mode & 0o777,0o600)
                self.assertNotIn('PRIVATE', json.dumps(report))

    def test_node_launch_selector_bound_frames_and_cleanup_without_process_launch(self):
        class Pipe(io.BytesIO):
            def fileno(self): return 100
        for mode in ('launch','frame','missing','failure'):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                child = Mock(stdin=Pipe(),stdout=Pipe())
                selector = Mock()
                selector.select.return_value = [object()]
                failure = {'type':'failure','stage':'browser-exercise',
                    'code':'browser-exercise-failed','error_class':'AssertionError'}
                data = (b'x'*65 if mode == 'frame' else b'' if mode == 'missing' else
                    (json.dumps(failure)+'\n').encode())
                with patch.object(shared.subprocess,'Popen',side_effect=OSError('PRIVATE') if mode=='launch' else None,
                        return_value=child) as launch, \
                        patch.object(shared.selectors,'DefaultSelector',return_value=selector), \
                        patch.object(shared.os,'read',return_value=data), \
                        patch.object(shared,'MAX_FRAME',64 if mode=='frame' else 1024), \
                        patch.object(shared,'stop_child') as stop:
                    with self.assertRaises((shared.HarnessError,OSError)) as error:
                        shared.run_node(node=Path('/node'),module=Path('/module'),work=root,
                            private_log=io.StringIO(),environment={},input_data={'password':'PRIVATE'},
                            checkpoint=shared.Checkpoints(lambda *_:None,consumer='mutations'),
                            probe_origin='http://127.0.0.1:9',consumer='mutations')
                    self.assertEqual(launch.call_args.args[0][-2:],['--consumer','mutations'])
                    if mode!='launch':
                        stop.assert_called_once_with(child)
                        self.assertTrue(child.stdin.closed and child.stdout.closed)
                    self.assertFalse((root/'browser-input.json').exists())
                    self.assertNotIn('PRIVATE',json.dumps(shared.public_error('runtime',error.exception)))

    def test_mutation_imports_are_inert_and_sources_are_in_provenance(self):
        script = r"""
import socket, subprocess, os
def forbidden(*args, **kwargs): raise RuntimeError('import side effect')
class GuardedSocket(socket.socket):
    def __new__(cls, *args, **kwargs): return forbidden()
socket.socket=GuardedSocket
socket.create_connection=forbidden
subprocess.Popen=forbidden
os.mkdir=forbidden
import TESTS.adapters.django.message_mutations_browser_runtime
"""
        result = subprocess.run([sys.executable,'-B','-c',script],cwd=shared.ROOT,
            capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        from TESTS import test_message_reads_browser as read_controls
        _, _, _, proof = read_controls.browser_control(shared.browser.authored_spec())
        with patch.object(shared.browser,'capture_provenance',return_value=copy.deepcopy(proof)):
            observed = consumer.capture_provenance(Path('/unused'))
        for name in consumer.EXTRA_SOURCES:
            self.assertEqual(observed['source_sha256'][name],shared.sha(shared.ROOT/name))
        self.assertIn('TESTS/browser/message-reads-runner.mjs',observed['source_sha256'])
        self.assertIn('TESTS/adapters/django/message_mutations_browser_runtime.py',observed['source_sha256'])

    def test_bridge_strict_ack_decoding_through_actual_rpc(self):
        script = r'''
import assert from 'node:assert/strict';
import {PassThrough} from 'node:stream';
import {test} from 'node:test';
import {createBridge} from './TESTS/browser/message-reads-runner.mjs';

async function exchange(raw, consumer, stage = 'checkpoint', fragmented = false) {
 const readable = new PassThrough(), sent = [];
 const options = {readable, send: frame => sent.push(frame), timeoutMs: 500};
 if (consumer !== undefined) options.consumer = consumer;
 const bridge = createBridge(options);
 const ack = frame => readable.write(JSON.stringify(frame) + '\n');
 try {
  let pending = bridge.ready({});
  if (stage !== 'ready') {
   ack({type:'ready_ack',success:true}); await pending;
   if (stage === 'context') {
    for (const [i, action] of ['login', 'read'].entries()) {
     pending = bridge.checkpoint({actor:'owner',action});
     ack({type:'ack',seq:i+1,success:true}); await pending;
    }
    pending = bridge.contextClosed('owner');
   } else {
    pending = bridge.checkpoint({actor:'owner',action:'login',
     ...(consumer === 'mutations' ? {requests:[]} : {})});
   }
  }
  assert.equal(sent.at(-1).type, {ready:'ready',checkpoint:'checkpoint',context:'context_closed'}[stage]);
  // Attach the rejection handler before delivering bytes to the real listener.
  const outcome = pending.then(value => ({value}), error => ({error}));
  const wire = Buffer.from(raw + '\n');
  if (fragmented) for (const byte of wire) readable.write(Buffer.from([byte]));
  else readable.write(wire);
  const result = await outcome;
  if (result.error) {
   assert.doesNotMatch(result.error.message, /deadline/, 'Decoder must reject without waiting for timeout');
   await assert.rejects(bridge.checkpoint({actor:'owner',action:'read',
    ...(consumer === 'mutations' ? {requests:[]} : {})}));
   assert.throws(() => bridge.complete());
   throw result.error;
  }
  return result.value;
 } finally {
  bridge.close();
  for (const event of ['data','end','error']) assert.equal(readable.listenerCount(event), 0);
  readable.destroy();
 }
}

const duplicateFields = [
 '"seq":99,"seq":1', '"seq":1,"seq":99', '"seq":1,"seq":1',
 '"seq":99,"se\\u0071":1', '"\\u0073eq":99,"seq":1',
 '"success":false,"success":true', '"success":true,"success":false',
 '"success":true,"success":true', '"success":false,"succe\\u0073s":true',
 '"type":"wrong","type":"ack"', '"type":"ack","type":"ack"',
 '"type":"wrong","\\u0074ype":"ack"',
];
for (const consumer of [undefined, 'reads', 'mutations']) {
 const label = consumer ?? 'default reads';
 for (const [i, fields] of duplicateFields.entries()) {
  const raw = fields.startsWith('"seq"') || fields.startsWith('"\\u0073eq"')
   ? '{"type":"ack",' + fields + ',"success":true}'
   : fields.startsWith('"success"') ? '{"type":"ack","seq":1,' + fields + '}'
   : '{' + fields + ',"seq":1,"success":true}';
  await test(`${label}: duplicate fields ${i}`, async () => {
   await assert.rejects(exchange(raw, consumer), /Duplicate ack key/);
  });
 }
 for (const stage of consumer === 'mutations' ? ['ready'] : ['ready','context']) {
  const type = stage === 'ready' ? 'ready_ack' : 'context_ack';
  const seq = stage === 'ready' ? '' : ',"seq":1';
  await test(`${label}: ${stage} duplicate escaped success`, async () => {
   await assert.rejects(exchange('{"type":"' + type + '"' + seq
    + ',"success":false,"succe\\u0073s":true}', consumer, stage), /Duplicate ack key/);
  });
 }
 const invalid = [
  '', 'null', '[]', '{}', '{"type":"ack","seq":1}',
  '{"type":"ack","seq":true,"success":true}',
  '{"type":"ack","seq":"1","success":true}',
  '{"type":"ack","seq":1.1,"success":true}',
  '{"type":"ack","seq":1,"success":1}',
  '{"type":"ack","seq":1,"success":true,"checkpoint":1}',
  '{"type":"ack","seq":1,"success":true,"result":{"success":false,"success":true}}',
  '{"type":"ack","seq":1,"success":true,"result":{"seq":99,"se\\u0071":1}}',
  '{"type":"ack","seq":1,"success":true,"result":[{"x":{"a":1,"a":2}}]}',
  '{"type":"ack","seq":1,"success":true,"result":{},"result":null}',
  '{"type":"ack","seq":1,"success":true,"__proto__":null}',
  '{"type":"ack","seq":1,"success":true,}',
  '{"type":"ack","seq":01,"success":true}',
  '{"type":"ack","seq":+1,"success":true}',
  '{"type":"ack","seq":NaN,"success":true}',
  '{"type":"ack","seq":Infinity,"success":true}',
  '{"type":"ack","seq":1,"success":true}{}',
  '{"type":"ack","seq":1,"success":true} trailing',
  '{"type":"ack","seq":1,"success":true',
  '{"type":"ack","seq":1 "success":true}',
  '{"type":"ack","seq":1,,"success":true}',
  '{type:"ack","seq":1,"success":true}',
  '{"type":"ack\\x20","seq":1,"success":true}',
  '{"type":"ack\t","seq":1,"success":true}',
  '{"type":"ack","seq":1,/* comment */"success":true}',
  '\u00a0{"type":"ack","seq":1,"success":true}',
  '{"type":"ack","seq":1,"success":true}\u000b',
 ];
 for (const [i, raw] of invalid.entries()) await test(`${label}: invalid frame ${i}`, async () => {
  await assert.rejects(exchange(raw, consumer));
 });
 // Delimiters and apparent keys inside a JSON string are ordinary string data.
 await test(`${label}: string delimiters reach exact schema validation`, async () => {
  await assert.rejects(exchange(String.raw`{"type":"ack\",\"seq\":99,{}[]\\","seq":1,"success":true}`,
   consumer), /Wrong\/missing checkpoint ack/);
 });
 for (const raw of [
  '{"type":"ack","seq":1,"success":true}',
  ' \t{ "success" : true, "seq": 1.0, "type":"ack" }\r ',
  '{"\\u0074ype":"\\u0061ck","se\\u0071":1e0,"succe\\u0073s":true}',
 ]) await test(`${label}: valid ACK ${raw}`, async () => {
  assert.deepEqual(await exchange(raw, consumer, 'checkpoint', true),
   consumer === 'mutations' ? {success:true,checkpoint:1} : {success:true});
 });
 await test(`${label}: 64 KiB bound includes newline`, async () => {
  const raw = '{"type":"ack","seq":1,"success":true}';
  assert.deepEqual(await exchange(' '.repeat(65535 - Buffer.byteLength(raw)) + raw, consumer),
   consumer === 'mutations' ? {success:true,checkpoint:1} : {success:true});
  await assert.rejects(exchange(' '.repeat(65536 - Buffer.byteLength(raw)) + raw, consumer), /Ack frame bound/);
  await assert.rejects(exchange('é'.repeat(32768), consumer), /Ack frame bound/);
 });
}
'''
        result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=shared.ROOT,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_bridge_mutation_sequence_payload_binding_bounds_and_read_default(self):
        script = r'''
import assert from 'node:assert/strict';
import {PassThrough} from 'node:stream';
import {createBridge} from './TESTS/browser/message-reads-runner.mjs';
const actors=['owner','full_collaborator','foreign_owner','restricted_collaborator'];
const order=actors.flatMap(actor=>(actor===actors.at(-1)?['login','read','closed']:
 ['login','read','create','create_reload','delete','delete_reload','closed']).map(action=>({actor,action})));
for(const selected of ['mutations','reads']) {
 const readable=new PassThrough(), frames=[];
 const bridge=createBridge({readable,consumer:selected,send:frame=>{
  frames.push(frame);readable.write(JSON.stringify(frame.type==='ready'?{type:'ready_ack',success:true}:
   {type:'ack',seq:frame.seq,success:true})+'\n');}});
 await bridge.ready({});
 if(selected==='mutations') {
  for(const [i,row] of order.entries()) {
   const payload={...row,requests:[],...(row.action==='login'?{identity:{user_id:'PRIVATE'}}:{})};
   assert.deepEqual(await bridge.checkpoint(payload),{success:true,checkpoint:i+1});
   assert.deepEqual(frames.at(-1),{type:'checkpoint',seq:i+1,payload});
  }
  bridge.complete();await assert.rejects(bridge.checkpoint(order[0]));
 } else {
  await assert.rejects(bridge.checkpoint({...order[0],requests:[]}));
  assert.deepEqual(await bridge.checkpoint(order[0]),{success:true});
 }
 bridge.close();
}
for(const mode of ['missing','reordered','duplicate','overflow','bad-ack']) {
 const readable=new PassThrough();let sent=0;
 const bridge=createBridge({readable,consumer:'mutations',timeoutMs:5,send:frame=>{
  if(frame.type==='ready')readable.write('{"type":"ready_ack","success":true}\n');
  else {sent++;if(mode!=='missing')readable.write(mode==='overflow'?'x'.repeat(65537):
   JSON.stringify({type:'ack',seq:mode==='bad-ack'?9:frame.seq,success:true})+'\n');}
 }});
 await bridge.ready({});
 if(mode==='reordered') {
  await assert.rejects(bridge.checkpoint({...order[1],requests:[]}));
  await assert.rejects(bridge.checkpoint({...order[0],requests:[]}));
 }
 else if(mode==='duplicate') {await bridge.checkpoint({...order[0],requests:[]});
  await assert.rejects(bridge.checkpoint({...order[0],requests:[]}));}
 else await assert.rejects(bridge.checkpoint({...order[0],requests:[]}));
 bridge.close();
}
const readable=new PassThrough();let sends=0;
const bridge=createBridge({readable,consumer:'mutations',send:frame=>{sends++;
 if(frame.type==='ready')readable.write('{"type":"ready_ack","success":true}\n');}});
await bridge.ready({});
await assert.rejects(bridge.checkpoint({...order[0],requests:[],identity:{secret:'x'.repeat(4*1024*1024)}}));
assert.equal(sends,1);bridge.close();
assert.throws(()=>createBridge({readable:new PassThrough(),send:()=>{},consumer:'arbitrary'}));
'''
        result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=shared.ROOT,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
