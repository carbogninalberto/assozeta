"""Offline controls only; synthetic receipts never claim browser acceptance."""
import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType

from TESTS.adapters.django import message_reads_browser as browser
from TESTS.adapters.django.historical_read_cache import (
    HistoricalReadCacheContrasts, HistoricalReadCacheEntry,
    HistoricalReadCacheSnapshot, PERSISTENT_CONTRAST_KEY, EXPIRING_CONTRAST_KEY,
)


class MessageBrowserControls(unittest.TestCase):
    def test_expected_visible_headings_preserve_existing_uppercase_theme(self):
        for actor in self.spec['actors'].values():
            self.assertEqual(actor['controls']['headings'],
                             ['CONTENUTO', 'TIPO', 'OGGETTO', 'DATA'])

    def setUp(self):
        self.spec = browser.authored_spec()
        self.receipt, self.states, self.attribution, self.provenance = browser_control(self.spec)
        self.contrasts = HistoricalReadCacheContrasts(
            PERSISTENT_CONTRAST_KEY, EXPIRING_CONTRAST_KEY, 2000000000000,
            b'persistent-dump', b'expiring-dump')

    def verify(self):
        return browser.verify_acceptance(
            self.receipt, state_checkpoints=self.states, contrasts=self.contrasts,
            attribution=self.attribution, provenance_before=self.provenance,
            provenance_after=self.provenance, spec=self.spec,
            build_observation=self.build_observation())

    def build_observation(self):
        return {'invocation': ['offline-synthetic-control'], 'record_sha256': 'd' * 64,
                'source_sha256': {k: v for k, v in self.provenance['source_sha256'].items()
                                  if k.startswith('UI/')},
                'artifact_sha256': copy.deepcopy(self.provenance['artifact_sha256'])}

    def test_complete_synthetic_control_validates_only_as_a_control(self):
        self.assertEqual(self.verify()['selected_list_requests'], 6)

    def test_v1_requires_real_correlated_django_observation(self):
        self.receipt['version'] = 'v1'
        for row in self.receipt['responses']:
            row['backend'] = 'django-v1'
            if 'cid' in row:
                row['cid'] = row['cid'].replace('-v2-', '-v1-')
        for row in self.receipt['checks']:
            if 'cid' in row:
                row['cid'] = row['cid'].replace('-v2-', '-v1-')
        for row in self.receipt['list_requests']:
            row['cid'] = row['cid'].replace('-v2-', '-v1-')
        for index, proof in enumerate(self.attribution):
            proof['cid'] = proof['cid'].replace('-v2-', '-v1-')
            proof['proxy'].update(cid=proof['cid'], backend='django-v1')
            proof['django_events'] = [{
                'seq': index, 'method': 'GET', 'path': browser.UPSTREAM_PATH,
                'cid': proof['cid'], 'status': 200, 'error_type': None,
            }]
        self.assertEqual(self.verify()['version'], 'v1')
        self.attribution[0]['django_events'] = []
        with self.assertRaises(AssertionError):
            self.verify()

    def test_provenance_capture_rejects_changed_embedded_ui_source(self):
        with tempfile.TemporaryDirectory(prefix='message-browser-offline-') as folder:
            artifact = Path(folder)
            (artifact / 'index.html').write_text('<html></html>')
            (artifact / 'vendor.js').write_text('void 0;\n//# sourceMappingURL=absent-vendor.js.map\n')
            contents = [(browser.ROOT / 'UI' / name).read_text()
                        for name in browser.WORKFLOW_SOURCES]
            source_map = {'version': 3, 'file': 'control.js', 'sources': list(browser.WORKFLOW_SOURCES),
                          'sourcesContent': contents}
            (artifact / 'control.js').write_text('void 0;\n//# sourceMappingURL=control.js.map\n')
            path = artifact / 'control.js.map'
            path.write_text(json.dumps(source_map))
            first = browser.capture_provenance(artifact)
            self.assertTrue('UI/postcss.config.cjs' in first['source_sha256'], 'Build configuration missing')
            self.assertEqual(first['artifact_sha256'], browser.capture_provenance(artifact)['artifact_sha256'])
            source_map['sourcesContent'][0] += '\n// changed control\n'
            path.write_text(json.dumps(source_map))
            with self.assertRaises(AssertionError):
                browser.capture_provenance(artifact)

    def test_orphan_and_unrelated_generated_js_cannot_supply_workflow_maps(self):
        with tempfile.TemporaryDirectory(prefix='message-browser-map-controls-') as folder:
            artifact = Path(folder)
            (artifact / 'index.html').write_text('<html></html>')
            data = {'version': 3, 'file': 'control.js',
                    'sources': list(browser.WORKFLOW_SOURCES),
                    'sourcesContent': [(browser.ROOT / 'UI' / name).read_text()
                                       for name in browser.WORKFLOW_SOURCES]}
            path = artifact / 'control.js.map'
            path.write_text(json.dumps(data))
            with self.assertRaises(AssertionError):  # orphan map, no generated code
                browser.capture_provenance(artifact)
            (artifact / 'unrelated.js').write_text('void "unrelated";')
            with self.assertRaises(AssertionError):
                browser.capture_provenance(artifact)
            generated = artifact / 'control.js'
            generated.write_text('void "unrelated, detached map";')
            with self.assertRaises(AssertionError):
                browser.capture_provenance(artifact)
            for directive in ('missing.js.map', '../control.js.map',
                              'https://example.test/control.js.map'):
                generated.write_text('void 0;\n//# sourceMappingURL=' + directive + '\n')
                with self.subTest(directive=directive), self.assertRaises(AssertionError):
                    browser.capture_provenance(artifact)
            generated.write_text('void 0;\n//# sourceMappingURL=control.js.map\n')
            data['file'] = 'unrelated.js'
            path.write_text(json.dumps(data))
            with self.assertRaises(AssertionError):
                browser.capture_provenance(artifact)
            data['file'] = 'control.js'
            path.write_text(json.dumps(data))
            proof = browser.capture_provenance(artifact)
            self.assertEqual(proof['source_map_bindings'], {'control.js.map': ['control.js']})
            generated.write_text('void 0;\n//# sourceMappingURL=control.js.map\n'
                                 '//# sourceMappingURL=control.js.map\n')
            with self.assertRaises(AssertionError):
                browser.capture_provenance(artifact)

    def test_vite_entry_map_accepts_exact_output_root_file_binding(self):
        with tempfile.TemporaryDirectory(prefix='message-vite-map-') as folder:
            artifact = Path(folder)
            (artifact / 'index.html').write_text('<html></html>')
            assets = artifact / 'build-assets'
            assets.mkdir()
            (assets / 'entry.js').write_text('void 0;\n//# sourceMappingURL=entry.js.map\n')
            data = {'version': 3, 'file': 'build-assets/entry.js',
                    'sources': list(browser.WORKFLOW_SOURCES),
                    'sourcesContent': [(browser.ROOT / 'UI' / name).read_text()
                                       for name in browser.WORKFLOW_SOURCES]}
            path = assets / 'entry.js.map'
            path.write_text(json.dumps(data))
            proof = browser.capture_provenance(artifact)
            self.assertEqual(proof['source_map_bindings'],
                             {'build-assets/entry.js.map': ['build-assets/entry.js']})
            data['file'] = '../entry.js'
            path.write_text(json.dumps(data))
            with self.assertRaises(AssertionError):
                browser.capture_provenance(artifact)

    def test_parent_build_observation_and_served_map_binding_are_required(self):
        for mutate in ('record', 'invocation', 'source', 'artifact'):
            proof = self.build_observation()
            if mutate == 'record':
                proof['record_sha256'] = ''
            elif mutate == 'invocation':
                proof['invocation'] = []
            elif mutate == 'source':
                proof['source_sha256']['UI/package.json'] = 'e' * 64
            else:
                proof['artifact_sha256']['index.html'] = 'e' * 64
            with self.subTest(mutate=mutate), self.assertRaises(AssertionError):
                browser.verify_acceptance(self.receipt, state_checkpoints=self.states,
                    contrasts=self.contrasts, attribution=self.attribution,
                    provenance_before=self.provenance, provenance_after=self.provenance,
                    build_observation=proof)
        self.receipt['static_verified'] = ['index.html', 'unrelated.js']
        self.receipt['artifact_sha256']['unrelated.js'] = 'e' * 64
        with self.assertRaises(AssertionError):
            self.verify()

    def test_external_exclusions_and_complete_bodies_are_mandatory(self):
        for key in ('all_response_bodies_complete', 'external_integrations_excluded',
                    'font_parity_excluded'):
            self.receipt[key] = False
            with self.subTest(key=key), self.assertRaises(AssertionError):
                self.verify()
            self.receipt[key] = True
        self.receipt['responses'][0]['body_complete'] = False
        with self.assertRaises(AssertionError):
            self.verify()
        self.receipt['responses'][0]['body_complete'] = True
        self.receipt['blocked_fixture_dependencies'] = [{
            'actor': 'owner', 'action': 'login', 'url': 'https://external.test/',
            'method': 'GET', 'resource_type': 'script', 'blocked': True}]
        with self.assertRaises(AssertionError):
            self.verify()

    def test_known_blocked_dependencies_pass_with_explicit_exclusions(self):
        for resource in browser.BLOCKED_EXTERNAL_RESOURCES:
            self.receipt['blocked_fixture_dependencies'].append(dict(
                resource, actor='owner', action='login', blocked=True))
        self.assertEqual(self.verify()['selected_list_requests'], 6)
        for key, value in (('method', 'POST'), ('resource_type', 'fetch'), ('blocked', False)):
            before = copy.deepcopy(self.receipt['blocked_fixture_dependencies'])
            self.receipt['blocked_fixture_dependencies'][0][key] = value
            with self.subTest(key=key), self.assertRaises(AssertionError):
                self.verify()
            self.receipt['blocked_fixture_dependencies'] = before

    def test_loopback_connect_reset_signature_and_actual_dispatcher_failure_stay_private(self):
        program = r'''
import assert from 'node:assert/strict';
import {networkErrorCategory, createRouteDiagnostics, routeRequest} from './TESTS/browser/message-reads.mjs';
import {childFailure} from './TESTS/browser/message-reads-runner.mjs';
for (const port of [1, 52293, 65535]) {
    const line=`connect ECONNRESET 127.0.0.1:${port}`;
    assert.equal(networkErrorCategory(new Error(line)), 'connection-reset');
    assert.equal(networkErrorCategory(new Error(`route.fetch: ${line}\nCall log:\nTOKEN-SECRET`)), 'connection-reset');
}
const signature='connect ECONNRESET 127.0.0.1:52293';
for (const message of [
    `TOKEN-SECRET ${signature}`, `${signature} TOKEN-SECRET`, ` ${signature}`, `${signature} `,
    `route.fetch: ${signature}\r`, `page.goto: ${signature}`, `route.fetch: route.fetch: ${signature}`,
    `TOKEN-SECRET\nroute.fetch: ${signature}`, `connect ECONNRESET localhost:52293`,
    'connect ECONNRESET 127.0.0.2:52293', 'connect ECONNRESET [::1]:52293',
    'connect ECONNRESET 127x0x0x1:52293', 'connect ECONNRESET 127.0.0.1:0',
    'connect ECONNRESET 127.0.0.1:052293', 'connect ECONNRESET 127.0.0.1:01',
    'connect ECONNRESET 127.0.0.1:65536', 'connect ECONNRESET 127.0.0.1:99999',
    'connect ECONNRESET 127.0.0.1:100000', 'connect ECONNRESET 127.0.0.1:-1',
    'connect ECONNRESET 127.0.0.1:52293/path?TOKEN-SECRET',
    'connect ECONNRESET http://127.0.0.1:52293', 'connect ECONNRESET 127.0.0.1:52293.5',
    'connect ECONNRESET 127.0.0.1:52293\u0000TOKEN-SECRET',
]) assert.equal(networkErrorCategory(new Error(message)), 'other');
const input={origin:'http://127.0.0.1:9',version:'v2',selected_routes:{},coexistence_routes:[],
    artifact_sha256:{'build-assets/main.js':'a'.repeat(64)}};
const request={url:()=>input.origin+'/build-assets/main.js',method:()=>'GET',resourceType:()=>'script',
    redirectedFrom:()=>null,isNavigationRequest:()=>false};
const error=new Error(`route.fetch: ${signature}\nCall log:\nTOKEN-SECRET`), diagnostics=createRouteDiagnostics(input);
let aborts=0;
const route={request:()=>request,fetch:async options=>{
    assert.deepEqual(options,{maxRedirects:0,maxRetries:0,timeout:30000});throw error;
},fulfill:()=>assert.fail('failed fetch must not fulfill'),abort:async code=>{
    assert.equal(code,'blockedbyclient');aborts++;
}};
await assert.rejects(()=>routeRequest(route,{input,actor:'owner',action:'login',diagnostics,
    fail:()=>assert.fail(),blocked:new WeakSet(),dependencies:[]}), actual=>actual===error);
assert.equal(aborts,1);
assert.deepEqual(diagnostics.report.counts.fetch,{completed:0,failed:1});
assert.deepEqual(diagnostics.report.counts.abort,{completed:1,failed:0});
assert.deepEqual(diagnostics.report.counts.fulfill,{completed:0,failed:0});
assert.equal(diagnostics.report.failure_count,1);
assert.equal(diagnostics.report.failures[0].category,'connection-reset');
const frame=childFailure('browser-exercise',error,{workflow_step:'login-network-idle',
    failures:[{kind:'response-or-route-validation',actor:'owner',action:'login',error_class:'Error'}],
    network_diagnostics:diagnostics.report});
const serialized=JSON.stringify(frame);
for (const privateValue of ['TOKEN-SECRET','ECONNRESET','52293','127.0.0.1','Call log'])
    assert(!serialized.includes(privateValue));
assert.equal(frame.code,'browser-exercise-failed');
assert.equal(frame.network_diagnostics.failures[0].category,'connection-reset');
'''
        result = subprocess.run(['node', '--input-type=module', '-e', program], cwd=browser.ROOT,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')

    def test_actual_dispatcher_stage_errors_completion_and_navigation_correlation(self):
        program = r'''
import assert from 'node:assert/strict';
import {createRouteDiagnostics, routeRequest, networkErrorCategory, createCompletionBarrier}
    from './TESTS/browser/message-reads.mjs';
import {childFailure, sanitizedNetworkDiagnostics} from './TESTS/browser/message-reads-runner.mjs';
let text='';for await (const chunk of process.stdin)text+=chunk;
const input=JSON.parse(text), unhandled=[];
process.on('unhandledRejection', error=>unhandled.push(error));
const origin=input.origin, request=(path='/api/profile/info',nav=false)=>({
    url:()=>origin+path,method:()=> 'GET',resourceType:()=>nav?'document':'fetch',
    redirectedFrom:()=>null,isNavigationRequest:()=>nav,frame:()=>({parentFrame:()=>null}),
});
const signatures=[['route.fetch: socket hang up\nCall log:\nTOKEN-SECRET','socket-hang-up'],
    ['route.fulfill: Fetch response has been disposed','response-disposed'],
    ['apiResponse.body: Response has been disposed','response-disposed'],
    ['route.abort: Route is already handled!','route-already-handled'],
    ['route.fetch: Target page, context or browser has been closed','target-closed'],
    ['route.fetch: Request context disposed.','context-disposed'],
    ['net::ERR_ABORTED','browser-aborted'],['net::ERR_BLOCKED_BY_CLIENT','blocked-by-client'],
    ['net::ERR_FAILED','network-failed'],['route.fetch: read ECONNRESET','connection-reset'],
    ['route.fetch: connect ECONNREFUSED 127.0.0.1:1234','connection-refused'],
    ['route.fetch: Timeout 30000ms exceeded.','timeout'],['route.fetch: aborted','upstream-aborted']];
for(const [message,category] of signatures)assert.equal(networkErrorCategory(new Error(message)),category);
for(const message of ['TOKEN-SECRET net::ERR_ABORTED','net::ERR_ABORTED TOKEN-SECRET',
    'page.goto: net::ERR_ABORTED','socket hang up TOKEN-SECRET','aborted TOKEN-SECRET'])
    assert.equal(networkErrorCategory(new Error(message)),'other');
for(const mode of ['normal','fetch','fulfill','dispose','observe','pairing','redirect','all-cleanup-errors']) {
    const diagnostics=createRouteDiagnostics(input), events=[], req=request();
    diagnostics.start(req,'owner','login');
    const primary=new Error(mode==='fetch'?'route.fetch: socket hang up':'route.fulfill: Response has been disposed');
    const secondary=new Error('route.abort: Route is already handled!');
    const cleanup=new Error('TOKEN-SECRET disposal');
    const body=Buffer.from('BODY-SECRET'), headers=[{name:'Set-Cookie',value:'COOKIE-SECRET'}];
    const response={status:()=>mode==='redirect'?302:200,
        url:()=>mode==='pairing'?origin+'/wrong?TOKEN-SECRET':req.url(),
        body:async()=>body,headersArray:async()=>headers,
        dispose:async()=>{events.push('dispose');if(['dispose','all-cleanup-errors'].includes(mode))throw cleanup;}};
    const route={request:()=>req,fetch:async options=>{
        events.push('fetch');assert.deepEqual(options,{maxRedirects:0,maxRetries:0,timeout:30000});
        if(mode==='fetch')throw primary;return response;
    },fulfill:async options=>{
        events.push('fulfill');assert.deepEqual(Object.keys(options),['response']);
        assert.equal(options.response,response);assert.equal(await options.response.body(),body);
        assert.equal(await options.response.headersArray(),headers);
        if(['fulfill','all-cleanup-errors'].includes(mode))throw primary;
    },abort:async code=>{events.push('abort');assert.equal(code,'blockedbyclient');
        if(mode==='all-cleanup-errors')throw secondary;}};
    const run=()=>routeRequest(route,{input,actor:'owner',action:'login',diagnostics,
        fail:()=>assert.fail(),blocked:new WeakSet(),dependencies:[],
        onForwardedResponse:async(actor,request,actual)=>{
            events.push('observe');assert.equal(actor,'owner');assert.equal(request,req);assert.equal(actual,response);
            if(mode==='observe')throw primary;
        }});
    if(mode==='normal') {
        await run();assert.deepEqual(events,['fetch','observe','fulfill','dispose']);
        assert.equal(diagnostics.report.failure_count,0);
        for(const stage of ['fetch','observe','fulfill','dispose'])
            assert.deepEqual(diagnostics.report.counts[stage],{completed:1,failed:0});
    } else {
        const error=await run().then(()=>assert.fail(),error=>error);
        if(['fetch','fulfill','observe','all-cleanup-errors'].includes(mode))assert.equal(error,primary);
        if(mode==='dispose') {assert.equal(error,cleanup);assert(!events.includes('abort'));}
        if(mode==='fetch')assert(!events.includes('dispose'));
        if(['pairing','redirect'].includes(mode))assert(!events.includes('observe'));
        if(['fetch','observe','pairing','redirect'].includes(mode))assert(!events.includes('fulfill'));
        const rows=diagnostics.report.failures;
        const expected=mode==='all-cleanup-errors'?'fulfill':['pairing'].includes(mode)?'observe':mode==='redirect'?'fetch':mode;
        assert.equal(rows[0].stage,expected);
        if(mode==='all-cleanup-errors')assert.deepEqual(rows.map(row=>row.stage),['fulfill','abort','dispose']);
        const frame=childFailure('browser-exercise',error,{workflow_step:'login-network-idle',
            failures:[{kind:'response-or-route-validation',actor:'owner',action:'login',error_class:'Error'}],
            network_diagnostics:diagnostics.report});
        assert(frame.network_diagnostics);assert(!JSON.stringify(frame).includes('SECRET'));
        assert.equal(frame.network_diagnostics.failures[0].target,'/api/profile/info');
    }
    assert.deepEqual(sanitizedNetworkDiagnostics(diagnostics.report),diagnostics.report);
}
// Same exact request before and after an observed real main-frame commit. This
// is evidence only: even a matched ERR_ABORTED remains a failed dependency.
const diagnostics=createRouteDiagnostics(input), req=request(), first=request('/',true), next=request('/',true);
diagnostics.start(first,'owner','login');diagnostics.navigation('owner',origin+'/#/login');
const original=diagnostics.start(req,'owner','login');
diagnostics.start(next,'owner','login');diagnostics.navigation('owner',origin+'/');
diagnostics.stage(req,'owner','login','requestfailed',{message:'net::ERR_ABORTED'});
const row=diagnostics.report.failures[0];
assert.equal(row.request_id,original.request_id);assert.equal(row.request_navigation_epoch,1);
assert.equal(row.navigation_epoch,2);assert.equal(row.navigation_state,'root');
assert.equal(row.navigation_request_id,3);assert(row.navigation_event<row.event);
assert.equal(row.category,'browser-aborted');
const failed=[],barrier=createCompletionBarrier(kind=>failed.push(kind),25);
barrier.start(req);barrier.failed(req,false);await barrier.drain();assert.deepEqual(failed,['requestfailed']);
// Unknown destinations never expose even their path and never reach fetch.
const unknown=request('/TOKEN-SECRET?COOKIE-SECRET');let aborts=0;
await routeRequest({request:()=>unknown,abort:async()=>aborts++,fetch:()=>assert.fail()},
    {input,actor:'owner',action:'login',diagnostics,blocked:new WeakSet(),dependencies:[],fail:()=>{}});
assert.equal(aborts,1);
diagnostics.stage(unknown,'owner','login','requestfailed',{message:'TOKEN-SECRET'});
assert.equal(diagnostics.report.failures.at(-1).target,null);
assert.equal(diagnostics.report.failures.at(-1).kind,'unknown');
for(let i=0;i<24;i++)diagnostics.stage(req,'owner','login','fulfill',new Error('TOKEN-SECRET'));
assert.equal(diagnostics.report.failure_count,26);assert.equal(diagnostics.report.failures.length,16);
assert.equal(diagnostics.report.failures[0].category,'browser-aborted');
assert.equal(diagnostics.report.failures_truncated,true);
assert(!JSON.stringify(diagnostics.report).includes('SECRET'));
assert(sanitizedNetworkDiagnostics(diagnostics.report));
for(const key of Object.keys(diagnostics.report.failures[0])) {
    const unsafe=structuredClone(diagnostics.report);unsafe.failures[0][key]='TOKEN-SECRET';
    assert.equal(sanitizedNetworkDiagnostics(unsafe),null,key);
}
const unsafe=structuredClone(diagnostics.report);unsafe.failures[0].url='TOKEN-SECRET';
assert.equal(sanitizedNetworkDiagnostics(unsafe),null);
await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(unhandled,[]);
'''
        result = subprocess.run(['node', '--input-type=module', '-e', program], cwd=browser.ROOT,
            input=json.dumps({'version': 'v2', 'origin': 'http://127.0.0.1:9',
                'selected_routes': browser.SELECTED_ROUTES, 'coexistence_routes': browser.COEXISTENCE_ROUTES,
                'artifact_sha256': {'index.html': 'a' * 64}}), capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_js_transport_completion_and_waiters_offline(self):
        program = r'''            import assert from 'node:assert/strict';
            import {EventEmitter} from 'node:events';
            import {forwardContained, withResponseAction, createCompletionBarrier,
                classifyBlockedExternal, routeRequest, readCompleteBody, BLOCKED_EXTERNAL_RESOURCES} from './TESTS/browser/message-reads.mjs';
            let text = ''; for await (const chunk of process.stdin) text += chunk;
            const input = JSON.parse(text);
            assert.deepEqual(input.blocked_external_resources, BLOCKED_EXTERNAL_RESOURCES);
            const unhandled = [];
            process.on('unhandledRejection', error => unhandled.push(error));
            const tick = () => new Promise(resolve => setImmediate(resolve));
            for (const [url, type] of input.blocked_external_resources.map(x => [x.url, x.resource_type])) {
                assert(classifyBlockedExternal(url, 'GET', type));
                assert.equal(classifyBlockedExternal(url, 'POST', type), false);
                assert.equal(classifyBlockedExternal(url + '?extra=1', 'GET', type), false);
                assert.equal(classifyBlockedExternal(url, 'GET', 'fetch'), false);
            }
            assert.equal(classifyBlockedExternal('https://unknown.test/', 'GET', 'script'), false);
            const upstream = status => ({status: () => status, dispose: async () => {}});
            for (const status of [301, 302, 303, 307, 308]) {
                let fetched = 0, aborted = 0, fulfilled = 0;
                const route = {fetch: async options => {
                    assert.equal(options.maxRedirects, 0); assert.equal(options.maxRetries, 0);
                    fetched++; return upstream(status);
                }, abort: async () => aborted++, fulfill: async () => fulfilled++};
                await assert.rejects(forwardContained(route));
                assert.deepEqual([fetched, aborted, fulfilled], [1, 1, 0]);
            }
            const real = upstream(200);
            let options, delivered;
            const headers = {'x-correlation-id': 'public-control', cookie: 'synthetic'};
            await forwardContained({fetch: async value => {options = value; return real;},
                fulfill: async value => {delivered = value;}, abort: async () => assert.fail()}, headers);
            assert.deepEqual(options, {maxRedirects: 0, maxRetries: 0, timeout: 30000, headers});
            assert.deepEqual(delivered, {response: real}); // no body/status/header overrides
            for (const fetch of [undefined, async () => {throw new Error('unsupported/timeout');}]) {
                let aborted = 0, continued = 0;
                await assert.rejects(forwardContained({fetch, abort: async () => aborted++,
                    continue: async () => continued++, fulfill: async () => assert.fail()}));
                assert.equal(aborted, 1); assert.equal(continued, 0);
            }
            // Production route dispatcher must abort both known and unknown externals,
            // and never reach fetch/continue, even when a known URL is redirected.
            for (const [url, method, type, redirected, known] of [
                [input.blocked_external_resources[0].url, 'GET', 'stylesheet', null, true],
                [input.blocked_external_resources[0].url, 'POST', 'stylesheet', null, false],
                ['https://unknown.test/', 'GET', 'script', null, false],
                [input.blocked_external_resources[0].url, 'GET', 'stylesheet', {}, false],
            ]) {
                const failures = [], dependencies = []; let aborts = 0;
                const request = {url: () => url, method: () => method, resourceType: () => type,
                    redirectedFrom: () => redirected};
                await routeRequest({request: () => request, abort: async () => aborts++,
                    fetch: async () => assert.fail(), continue: async () => assert.fail()},
                    {input, actor: 'owner', action: 'login', fail: kind => failures.push(kind),
                     blocked: new WeakSet(), dependencies});
                assert.equal(aborts, 1); assert.equal(dependencies.length, known ? 1 : 0);
                assert.equal(failures.length, known ? 0 : 1);
            }
            // Request without response headers and response with a pending body both
            // prevent a barrier checkpoint; ERR_ABORTED is an error, not success.
            const failures = [];
            const barrier = createCompletionBarrier(kind => failures.push(kind), 25);
            const request = {};
            barrier.start(request);
            await assert.rejects(barrier.drain());
            barrier.failed(request, false);
            assert.deepEqual(failures, ['requestfailed']);
            let completeBody, bodyObserved = false, finishedObserved = false;
            const bodyResponse = {body: () => new Promise(resolve => {completeBody = resolve;}),
                finished: async () => {finishedObserved = true; return null;}};
            barrier.track(readCompleteBody(bodyResponse).then(() => {bodyObserved = true;}));
            await assert.rejects(barrier.drain());
            assert.equal(bodyObserved, false); assert.equal(finishedObserved, false);
            completeBody(Buffer.from('exact real response')); await barrier.drain();
            assert.equal(bodyObserved, true); assert.equal(finishedObserved, true);
            await assert.rejects(readCompleteBody({body: async () => {throw new Error('ERR_ABORTED');},
                finished: async () => null}));
            await assert.rejects(readCompleteBody({body: async () => Buffer.from('partial'),
                finished: async () => new Error('ERR_ABORTED')}));
            const known = {}; barrier.start(known); barrier.failed(known, true);
            await barrier.drain(); assert.equal(failures.length, 1);
            barrier.track(Promise.reject(new Error('body aborted')));
            await barrier.drain(); assert.equal(failures.length, 2);
            // A synchronous click failure, reload rejection, waiter timeout, page
            // closure and predicate error must all detach listeners and settle.
            for (const act of [() => {throw new Error('click');},
                () => Promise.reject(new Error('reload')), async () => {}]) {
                const page = new EventEmitter();
                await assert.rejects(withResponseAction(page, () => true, act, 5));
                assert.equal(page.listenerCount('response'), 0);
                assert.equal(page.listenerCount('close'), 0);
            }
            for (const close of [false, true]) {
                const page = new EventEmitter();
                await assert.rejects(withResponseAction(page, () => {throw new Error('predicate');},
                    async () => page.emit(close ? 'close' : 'response', {}), 5));
            }
            const failedAfterResponse = new EventEmitter();
            await assert.rejects(withResponseAction(failedAfterResponse, () => true, async () => {
                failedAfterResponse.emit('response', {}); throw new Error('action failed after response');
            }));
            const page = new EventEmitter(), response = {};
            assert.equal(await withResponseAction(page, r => r === response,
                async () => page.emit('response', response)), response);
            await tick(); await tick(); assert.deepEqual(unhandled, []);
        '''
        result = subprocess.run(['node', '--input-type=module', '-e', program],
            input=json.dumps(dict(self.spec, origin='http://127.0.0.1:9',
                                  artifact_sha256={'index.html': 'b' * 64})),
            text=True, capture_output=True, cwd=browser.ROOT, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_exercise_completion_and_failure_paths_with_offline_events(self):
        program = r'''            import assert from 'node:assert/strict';
            import {EventEmitter} from 'node:events';
            import {exercise} from './TESTS/browser/message-reads.mjs';
            let text = ''; for await (const chunk of process.stdin) text += chunk;
            const spec = JSON.parse(text);
            const unhandled = [];
            process.on('unhandledRejection', error => unhandled.push(error));
            const tick = () => new Promise(resolve => setImmediate(resolve));
            for (const mode of ['complete', 'abort', 'click-fails', 'body-fails']) {
                const input = {...spec, origin: 'http://127.0.0.1:9', version: 'v2',
                    artifact_sha256: {'index.html': 'a'.repeat(64)}};
                let release, checkpointCount = 0, bodyDone = false, finishedDone = false;
                const steps = [];
                const body = new Promise(resolve => {release = () => {bodyDone = true; resolve(Buffer.from('{}'));};});
                const context = new EventEmitter(), page = new EventEmitter();
                context.routeWebSocket = async () => {};
                context.route = async () => {};
                context.newPage = async () => page;
                context.close = async () => {page.emit('close');};
                page.setDefaultTimeout = page.setDefaultNavigationTimeout = () => {};
                page.goto = page.waitForURL = page.waitForLoadState = async () => {};
                page.evaluate = async () => null;
                page.getByRole = () => ({isVisible: async () => false});
                const makeResponse = (path, method, pending = false) => {
                    const request = {url: () => input.origin + path, method: () => method,
                        redirectedFrom: () => null, failure: () => ({errorText: 'net::ERR_ABORTED'})};
                    const response = {request: () => request, url: request.url,
                        status: () => 200, fromServiceWorker: () => false,
                        headerValue: async () => 'go-v2',
                        body: () => pending ? body : Promise.resolve(Buffer.from('{}')),
                        finished: async () => {
                            if (pending) finishedDone = true;
                            return pending && mode === 'body-fails' ? new Error('ERR_ABORTED') : null;
                        },
                        json: async () => ({user_data: {user_id: input.actors.owner.user_id,
                            requires_welcome: false}})};
                    context.emit('request', request);
                    if (pending && mode === 'abort') context.emit('requestfailed', request);
                    else {context.emit('response', response); page.emit('response', response);}
                };
                page.locator = selector => ({fill: async () => {}, click: async () => {
                    if (mode === 'click-fails') throw new Error('offline click failure');
                    makeResponse('/api/oauth2/login', 'POST');
                    makeResponse('/api/profile/info', 'GET', true);
                }});
                const expect = () => ({toBeVisible: async () => {}});
                expect.poll = () => ({toBe: async () => {}});
                const running = exercise({browser: {newContext: async () => context}, expect, input,
                    onWorkflowStep: step => steps.push(step),
                    checkpoint: async () => {
                        checkpointCount++;
                        assert.equal(bodyDone, true); assert.equal(finishedDone, true);
                        return {success: false}; // intentionally stop at the first baseline
                    }});
                await tick(); await tick();
                assert.equal(checkpointCount, 0, 'Pending body must hold the real checkpoint callback');
                release();
                const receipt = await running;
                assert.equal(receipt.success, false); // none of these fakes constitutes live evidence
                assert.equal(checkpointCount, mode === 'complete' ? 1 : 0);
                assert.equal(receipt.workflow_failure.step,
                    mode === 'click-fails' ? 'login-form' : mode === 'complete' ? 'checkpoint' : 'login-network-idle');
                assert.equal(steps.at(-1), 'context-close');
                assert.deepEqual(receipt.workflow_failure.controls_mismatches, []);
                if (mode === 'abort') assert(receipt.failures.some(x => x.kind === 'requestfailed'));
                if (mode === 'body-fails') assert(receipt.failures.some(x => x.kind === 'response-or-route-validation'));
                assert.equal(page.listenerCount('response'), 0, 'Action waiter detached after failure');
            }
            await tick(); await tick(); assert.deepEqual(unhandled, []);
        '''
        result = subprocess.run(['node', '--input-type=module', '-e', program],
            input=json.dumps(self.spec), text=True, capture_output=True,
            cwd=browser.ROOT, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_wrong_backend_and_proxy_fallback_fail(self):
        for target, key, value in (
            (self.receipt['responses'][0], 'backend', 'django-v1'),
            (self.attribution[0]['proxy'], 'fallback', True),
            (self.attribution[0]['proxy'], 'backend', 'django-v1'),
        ):
            old = target[key]
            target[key] = value
            with self.subTest(key=key), self.assertRaises(AssertionError):
                self.verify()
            target[key] = old

    def test_django_misroute_on_v2_fails(self):
        self.attribution[0]['django_events'] = [{'unexpected': True}]
        with self.assertRaises(AssertionError):
            self.verify()

    def test_missing_case_and_missing_state_proof_fail(self):
        self.receipt['checks'].pop()
        with self.assertRaises(AssertionError):
            self.verify()
        self.setUp()
        self.states.pop()
        with self.assertRaises(AssertionError):
            self.verify()

    def test_foreign_row_and_missing_content_fail(self):
        rows = self.receipt['checks'][0]['rows']
        rows[0] = copy.deepcopy(self.spec['actors']['foreign_owner']['visible_rows'][0])
        with self.assertRaises(AssertionError):
            self.verify()
        self.setUp()
        del self.receipt['checks'][0]['rows'][0]['message_title']
        with self.assertRaises(AssertionError):
            self.verify()

    def test_managed_model_write_and_model_scope_fail(self):
        self.states[1]['models']['model.0'] = [{'changed': True}]
        with self.assertRaises(AssertionError):
            self.verify()
        self.setUp()
        for state in self.states:
            state['models'].pop('model.0')
        with self.assertRaises(AssertionError):
            self.verify()

    def test_cache_type_dump_and_absolute_expiry_changes_fail(self):
        for changed in (
            HistoricalReadCacheEntry(b'hash', b'expiring-dump', 2000000000000),
            HistoricalReadCacheEntry(b'string', b'changed-dump', 2000000000000),
            HistoricalReadCacheEntry(b'string', b'expiring-dump', 2000000000001),
        ):
            original = self.states[1]['cache']
            entries = dict(original.entries)
            entries[EXPIRING_CONTRAST_KEY] = changed
            self.states[1]['cache'] = HistoricalReadCacheSnapshot(MappingProxyType(entries))
            with self.subTest(changed=changed), self.assertRaises(AssertionError):
                self.verify()
            self.states[1]['cache'] = original

    def test_fabricated_restricted_api_denial_is_rejected(self):
        self.receipt['responses'].append({
            'actor': 'restricted_collaborator', 'action': 'denied', 'method': 'GET',
            'path': browser.LIST_PATH, 'status': 403, 'backend': 'go-v2', 'cid': 'invented',
            'body_complete': True})
        with self.assertRaises(AssertionError):
            self.verify()

    def test_extra_aborted_ui_list_request_is_rejected(self):
        self.receipt['list_requests'].append({
            'actor': 'owner', 'action': 'read', 'cid': 'message-browser-v2-owner-read-99'})
        with self.assertRaises(AssertionError):
            self.verify()

    def test_missing_or_changed_provenance_fails(self):
        self.provenance['artifact_sha256'] = {}
        with self.assertRaises(AssertionError):
            self.verify()

    def test_unexpected_api_and_missing_login_fail(self):
        self.receipt['responses'].append({'actor': 'owner', 'action': 'read',
            'method': 'GET', 'path': '/api/not-allowlisted', 'status': 200,
            'backend': 'django-coexistence', 'body_complete': True})
        with self.assertRaises(AssertionError):
            self.verify()
        self.setUp()
        self.receipt['responses'] = [row for row in self.receipt['responses']
                                     if row['path'] != '/api/oauth2/login']
        with self.assertRaises(AssertionError):
            self.verify()

    def test_case_correlation_cannot_claim_another_version(self):
        old = self.receipt['responses'][0]['cid']
        forged = old.replace('-v2-', '-v1-')
        self.receipt['responses'][0]['cid'] = forged
        self.receipt['checks'][0]['cid'] = forged
        self.receipt['list_requests'][0]['cid'] = forged
        self.attribution[0]['cid'] = forged
        self.attribution[0]['proxy']['cid'] = forged
        with self.assertRaises(AssertionError):
            self.verify()

    def test_js_request_allowlist_offline_without_browser(self):
        program = r'''
            import assert from 'node:assert/strict';
            import {classifyRequest} from './TESTS/browser/message-reads.mjs';
            let text = ''; for await (const chunk of process.stdin) text += chunk;
            const input = JSON.parse(text), origin = input.origin;
            assert.deepEqual(classifyRequest(input, origin + '/api/communications/messages/list', 'GET'),
                {kind: 'selected'});
            assert.deepEqual(classifyRequest(input, origin + '/', 'GET'),
                {kind: 'static', file: 'index.html'});
            for (const [url, method] of [
                ['https://example.test/font.woff2', 'GET'],
                [origin + '/api/communications/messages/list', 'POST'],
                [origin + '/api/communications/messages/id/details', 'GET'],
                [origin + '/api/unknown', 'GET'],
                [origin + '/unknown.js', 'GET'],
                [origin + '/%E0%A4%A', 'GET'],
            ]) assert.equal(classifyRequest(input, url, method), null);
        '''
        result = subprocess.run(['node', '--input-type=module', '-e', program],
            input=json.dumps(dict(self.spec, origin='http://127.0.0.1:9',
                                  artifact_sha256={'index.html': 'b' * 64})),
            text=True, capture_output=True, cwd=browser.ROOT, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unexpected_requests_serviceworkers_and_missing_controls_fail(self):
        self.receipt['failures'].append({'kind': 'unexpected-request'})
        with self.assertRaises(AssertionError):
            self.verify()
        self.setUp()
        self.receipt['service_workers_blocked'] = False
        with self.assertRaises(AssertionError):
            self.verify()
        self.setUp()
        self.receipt['checks'][0]['controls'].pop('search_empty')
        with self.assertRaises(AssertionError):
            self.verify()


def browser_control(spec):
    """Build independent in-memory verifier input, never a runtime receipt."""
    checks, states, responses, attribution = [], [], [], []
    cache = HistoricalReadCacheSnapshot(MappingProxyType({
        PERSISTENT_CONTRAST_KEY: HistoricalReadCacheEntry(b'string', b'persistent-dump', -1),
        EXPIRING_CONTRAST_KEY: HistoricalReadCacheEntry(b'string', b'expiring-dump', 2000000000000),
    }))
    for actor, action in browser.CHECKPOINTS:
        states.append({'actor': actor, 'action': action,
                       'models': {f'model.{i}': [] for i in range(128)}, 'cache': cache})
        if action == 'login':
            continue
        if action == 'denied':
            checks.append({'actor': actor, 'action': action, 'sidebar_link_visible': False,
                           'redirect_hash': '#/404', 'list_requests': 0, 'table_rows': 0})
            continue
        case = spec['actors'][actor]
        cid = f'message-browser-v2-{actor}-{action}-1'
        checks.append({'actor': actor, 'action': action,
                       'rows': copy.deepcopy(case['visible_rows']),
                       'foreign_ids_absent': case['forbidden_ids'],
                       'controls': copy.deepcopy(case['controls']), 'cid': cid})
        responses.append({'actor': actor, 'action': action, 'method': 'GET',
                          'path': browser.LIST_PATH, 'status': 200, 'backend': 'go-v2', 'cid': cid})
        attribution.append({'cid': cid, 'django_events': [], 'proxy': {
            'cid': cid, 'method': 'GET', 'path': browser.UPSTREAM_PATH, 'status': 200,
            'backend': 'go-v2', 'fallback': False}})
    provenance = {
        'source_sha256': {name: 'a' * 64 for name in browser.required_source_paths()},
        'artifact_sha256': {'index.html': 'b' * 64, 'build-assets/main.js': 'c' * 64,
                            'build-assets/main.js.map': 'd' * 64},
        'workflow_source_maps': {name: ['build-assets/main.js.map'] for name in browser.WORKFLOW_SOURCES},
        'source_map_bindings': {'build-assets/main.js.map': ['build-assets/main.js']},
    }
    receipt = {'success': True, 'version': 'v2', 'contexts_closed': True,
               'browser_owned_by_parent': True, 'service_workers_blocked': True,
               'business_responses_mocked': False, 'authentication_state_seeded': False,
               'transport': browser.TRANSPORT, 'blocked_fixture_dependencies': [],
               'external_integrations_excluded': True, 'font_parity_excluded': True,
               'all_response_bodies_complete': True,
               'failures': [], 'checks': checks, 'responses': responses,
               'list_requests': [{key: row[key] for key in ('actor', 'action', 'cid')}
                                 for row in responses],
               'checkpoints': [{'actor': a, 'action': b} for a, b in browser.CHECKPOINTS],
               'artifact_sha256': provenance['artifact_sha256'],
               'static_verified': ['index.html', 'build-assets/main.js']}
    responses.extend({'actor': actor, 'action': 'login', 'method': 'POST',
                      'path': '/api/oauth2/login', 'status': 200, 'backend': 'go-v2'}
                     for actor in browser.ACTORS)
    for row in responses:
        row['body_complete'] = True
    return receipt, states, attribution, provenance


if __name__ == '__main__':
    unittest.main()
