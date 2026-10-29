import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {observeInitialPairing} from './sso-initial-diagnostics.mjs';

const secret = 'DO-NOT-RETAIN-session-code-or-personal-data';
const assetOrigin = `https://${secret}.test`;
const assetURL = (name = 'Profile-CqxElnf4.js') => `${assetOrigin}/build-assets/${name}`;
const response = (url, status = 200, type = 'text/javascript') => ({
    url: () => url, status: () => status,
    headers: () => ({'content-type': type, authorization: secret, 'set-cookie': secret}),
    body: () => assert.fail('must not read response bodies'),
});
const failedRequest = (url, errorText = 'net::ERR_FAILED') => ({
    url: () => url, failure: () => ({errorText}),
});
class Page extends EventEmitter {
    url() { return `https://${secret}.test/#/profile?page=self-instance&code=${secret}`; }
    getByRole() { return {count: async()=>1,isVisible:async()=>true,isEnabled:async()=>false}; }
    getByText() { return {first:()=>({isVisible:async()=>false})}; }
}

test('initial evidence retains only fixed route, endpoint, status and error categories', async()=>{
    const page=new Page(),diagnostics=observeInitialPairing(page);
    const response=(url,status)=>({url:()=>url,status:()=>status});
    page.emit('response',response(`https://club.test/api/instance/access?token=${secret}`,403));
    page.emit('response',response(`https://club.test/api/instance/admin/bakney-pairing?code=${secret}`,500));
    page.emit('response',response(`https://${secret}.test/bakney/v1/callback?code=${secret}`,401));
    page.emit('response',response(`https://club.test/api/users/${secret}`,200));
    page.emit('requestfailed',{url:()=>`https://club.test/api/instance/admin?code=${secret}`,failure:()=>({errorText:secret})});
    page.emit('pageerror',Object.assign(new Error(secret),{name:'TypeError',stack:secret}));
    page.emit('pageerror',Object.assign(new Error(secret),{name:secret}));
    const report=await diagnostics.snapshot();
    assert.deepEqual(report,{version:1,phase:'initial-pairing',route:'profile',button:{count:1,visible:true,enabled:false},messages:[],responses:{'/api/instance/access':403,'/api/instance/admin/bakney-pairing':500},failedRequests:{'/api/instance/admin':1},pageErrors:{TypeError:1,Other:1},pageErrorDetails:[{name:'TypeError',category:'unclassified',assetFrames:[]},{name:'Other',category:'unclassified',assetFrames:[]}],assetResponses:[],assetFailures:[],assetEventsDropped:{responses:0,failedRequests:0}});
    assert(!JSON.stringify(report).includes(secret));
    diagnostics.stop();
    assert.equal(page.listenerCount('response'),0);assert.equal(page.listenerCount('requestfailed'),0);assert.equal(page.listenerCount('pageerror'),0);
    page.emit('response',response('https://club.test/api/instance/access',200));
    assert.deepEqual(await diagnostics.snapshot(),report);
});

test('bounded counters and missing/closed page produce usable sanitized evidence',async()=>{
    const page=new Page(),diagnostics=observeInitialPairing(page);
    page.url=()=>`https://club.test/bakney/v1/callback?code=${secret}`;
    page.getByRole=()=>{throw new Error(secret);};
    page.getByText=()=>{throw new Error(secret);};
    for(let i=0;i<1500;i++)page.emit('pageerror',new Error(secret));
    page.emit('response',{url:()=>secret,status:()=>200});
    page.emit('response',{url:()=>`https://club.test/api/instance/access`,status:()=>secret});
    const report=await diagnostics.snapshot();
    assert.equal(report.route,'other');assert.deepEqual(report.button,{count:0,visible:false,enabled:false});
    assert.equal(report.pageErrorDetails.length,5);
    assert.deepEqual(report.pageErrors,{Error:1000});assert.deepEqual(report.responses,{});assert.deepEqual(report.messages,[]);
    assert(!JSON.stringify(report).includes(secret));diagnostics.stop();
});

test('visible diagnostic messages are recorded as fixed identifiers',async()=>{
    const page=new Page(),diagnostics=observeInitialPairing(page);
    page.getByText=(text)=>({first:()=>({isVisible:async()=>text==='Impossibile leggere il collegamento con Bakney.'})});
    assert.deepEqual((await diagnostics.snapshot()).messages,['pairing-unavailable']);diagnostics.stop();
});


test('error frames identify public build coordinates without retaining exception text or URLs',async()=>{
    const page=new Page(),diagnostics=observeInitialPairing(page);
    page.emit('pageerror',{name:'TypeError',message:`Cannot read properties ${secret}`,
        stack:`TypeError: ${secret}\n    at render (https://${secret}.test/build-assets/Profile-CqxElnf4.js:8:342)\n    at private (https://${secret}.test/private/${secret}:3:4)`});
    const report=await diagnostics.snapshot();
    assert.deepEqual(report.pageErrorDetails,[{name:'TypeError',category:'Cannot read properties',
        assetFrames:[{asset:'Profile-CqxElnf4.js',line:8,column:342}]}]);
    assert(!JSON.stringify(report).includes(secret));
    report.pageErrorDetails[0].assetFrames[0].line=999;
    assert.equal((await diagnostics.snapshot()).pageErrorDetails[0].assetFrames[0].line,8);
    diagnostics.stop();
});

test('Chromium dynamic-import failure without frames identifies the asset and HTML fallback', async()=>{
    const page = new Page(), diagnostics = observeInitialPairing(page);
    const url = `${assetURL()}?token=${secret}#${secret}`;
    page.emit('response', response(url, 200, `text/html; charset=utf-8; private=${secret}`));
    page.emit('pageerror', {name:'TypeError', message:`Failed to fetch dynamically imported module: ${url}`,
        stack:`TypeError: Failed to fetch dynamically imported module: ${url}`});
    const report = await diagnostics.snapshot();
    assert.deepEqual(report.pageErrorDetails, [{name:'TypeError', category:'Failed to fetch dynamically imported module',
        assetFrames:[], importAsset:'Profile-CqxElnf4.js'}]);
    assert.deepEqual(report.assetResponses, [{asset:'Profile-CqxElnf4.js',status:200,contentType:'html',count:1}]);
    assert(!JSON.stringify(report).includes(secret));
    diagnostics.stop();
});

test('asset responses and transport failures use only fixed categories and aggregate identical events', async()=>{
    const page = new Page(), diagnostics = observeInitialPairing(page);
    for (const [type, category] of [
        ['text/javascript; charset=UTF-8','javascript'], ['application/javascript','javascript'],
        ['text/css','css'], ['text/html','html'], ['application/json','json'],
        ['image/svg+xml','image'], ['font/woff2','font'], ['application/octet-stream','binary'],
        ['', 'missing'], [secret,'other'],
    ]) {
        page.emit('response', response(assetURL(), 200, type));
        assert.equal((await diagnostics.snapshot()).assetResponses.find(row => row.contentType === category)?.contentType, category);
    }
    for (const [errorText, category] of [
        ['net::ERR_FAILED','failed'], ['net::ERR_ABORTED','aborted'], ['net::ERR_NETWORK_CHANGED','network-changed'],
        ['net::ERR_NAME_NOT_RESOLVED','dns'], ['net::ERR_CONNECTION_REFUSED','connection'],
        ['net::ERR_CONNECTION_RESET','connection'], ['net::ERR_INTERNET_DISCONNECTED','offline'],
        ['net::ERR_TIMED_OUT','timeout'], ['net::ERR_BLOCKED_BY_CLIENT','blocked'],
        ['net::ERR_CERT_AUTHORITY_INVALID','certificate'], [`net::ERR_FAILED ${secret}`,'other'],
    ]) {
        page.emit('requestfailed', failedRequest(assetURL(), errorText));
        assert.equal((await diagnostics.snapshot()).assetFailures.find(row => row.category === category)?.category, category);
    }
    page.emit('response', response(assetURL('index-AbCd_123.css'), 404, 'text/html'));
    page.emit('response', response(assetURL(), 99));
    page.emit('response', response(assetURL(), 600));
    page.emit('response', response(assetURL(), secret));
    const report = await diagnostics.snapshot();
    assert.equal(report.assetResponses.find(row => row.contentType === 'javascript').count, 2);
    assert.equal(report.assetFailures.find(row => row.category === 'connection').count, 2);
    assert(report.assetResponses.some(row => row.status === 404 && row.asset === 'index-AbCd_123.css'));
    assert(report.assetResponses.every(row => row.status >= 100 && row.status <= 599));
    assert(!JSON.stringify(report).includes(secret));
    diagnostics.stop();
});

test('asset validation rejects cross-origin, malformed, encoded, traversal and prefix/suffix lookalikes everywhere', async()=>{
    const page = new Page(), diagnostics = observeInitialPairing(page);
    const invalid = [
        'not a URL', '/build-assets/Profile-CqxElnf4.js', '//club.test/build-assets/Profile-CqxElnf4.js',
        assetURL().replace('https:', 'http:'), assetURL().replace('.test/', '.test.evil/'),
        assetURL().replace('.test/', '.test:444/'), `https://elsewhere.test/build-assets/${secret}-AbCd1234.js`,
        assetURL().replace('https://', `https://${secret}@`),
        assetURL().replace('https://', 'https://@'),
        assetURL().replace('https://DO', 'https://%44O'),
        `${assetOrigin}/private/../build-assets/Profile-CqxElnf4.js`,
        `${assetOrigin}/build-assets/../build-assets/Profile-CqxElnf4.js`,
        `${assetOrigin}/build-assets/%2e%2e/build-assets/Profile-CqxElnf4.js`,
        `${assetOrigin}/build-assets/%50rofile-CqxElnf4.js`,
        `${assetOrigin}/build-assets/dir%2fProfile-CqxElnf4.js`,
        `${assetOrigin}/build-assets//Profile-CqxElnf4.js`,
        `${assetOrigin}/private/build-assets/Profile-CqxElnf4.js`,
        `${assetOrigin}/build-assets\\Profile-CqxElnf4.js`,
        assetURL('Profile..private-CqxElnf4.js'), assetURL('.Profile-CqxElnf4.js'),
        assetURL('Profile-CqxElnf4.js.map'), assetURL('Profile.js'), assetURL('Profile-CqxElnf4.js/extra'),
        assetURL(`${'a'.repeat(117)}-CqxElnf4.js`), // 129 bytes, including extension.
        `${assetURL()}\n${secret}`, `${assetURL()}\n`, `${assetURL()}\r`, `${assetURL()}\t`,
        `${assetURL()} ${secret}`, `${assetOrigin}/\tbuild-assets/Profile-CqxElnf4.js`,
        `${assetURL()}?${'x'.repeat(4096)}`,
    ];
    for (const url of invalid) {
        page.emit('response', response(url));
        page.emit('requestfailed', failedRequest(url));
        page.emit('pageerror', {message:`Failed to fetch dynamically imported module: ${url}`,
            stack:`Error\n    at render (${url}:1:2)`});
    }
    const report = await diagnostics.snapshot();
    assert.deepEqual(report.assetResponses, []);
    assert.deepEqual(report.assetFailures, []);
    assert(report.pageErrorDetails.every(row => row.assetFrames.length === 0 && !('importAsset' in row)));
    assert(!JSON.stringify(report).includes(secret));
    // Test every import/frame candidate independently; the five-detail limit must not mask unsafe cases.
    diagnostics.stop();
    for (const url of invalid) {
        const probe = new Page(), observer = observeInitialPairing(probe);
        probe.emit('pageerror', {message:`Failed to fetch dynamically imported module: ${url}`,
            stack:`Error\n    at render (${url}:1:2)`});
        const row = (await observer.snapshot()).pageErrorDetails[0];
        assert.deepEqual(row.assetFrames, [], url);
        assert(!('importAsset' in row), url);
        observer.stop();
    }
});

test('unavailable headers/failure details remain bounded categories and raw exception text is never copied', async()=>{
    const page = new Page(), diagnostics = observeInitialPairing(page);
    page.emit('response', {...response(assetURL()),headers:()=>{throw new Error(secret);}});
    page.emit('requestfailed', {url:()=>assetURL(),failure:()=>{throw new Error(secret);}});
    page.emit('requestfailed', {url:()=>assetURL(),failure:()=>null});
    page.emit('pageerror', {message:`private ${secret}: Failed to fetch dynamically imported module: ${assetURL()}`,
        stack:`Error: ${secret}\n    at private (https://elsewhere.test/build-assets/${secret}-AbCd1234.js:1:2)`});
    const report = await diagnostics.snapshot();
    assert.deepEqual(report.assetResponses, [{asset:'Profile-CqxElnf4.js',status:200,contentType:'missing',count:1}]);
    assert.deepEqual(report.assetFailures, [{asset:'Profile-CqxElnf4.js',category:'other',count:2}]);
    assert.deepEqual(report.pageErrorDetails, [{name:'Other',category:'Failed to fetch dynamically imported module',assetFrames:[]}]);
    assert(!JSON.stringify(report).includes(secret));
    diagnostics.stop();
});

test('asset origin follows initial navigation from about:blank and fails closed without a valid page origin', async()=>{
    const page = new Page(); page.url = () => 'about:blank';
    const diagnostics = observeInitialPairing(page);
    page.emit('response', response(assetURL()));
    assert.deepEqual((await diagnostics.snapshot()).assetResponses, []);
    page.url = () => `${assetOrigin}/fixture-blank.html`;
    page.emit('response', response(assetURL()));
    assert.equal((await diagnostics.snapshot()).assetResponses.length, 1);
    page.url = () => {throw new Error(secret);};
    page.emit('requestfailed', failedRequest(assetURL()));
    assert.deepEqual((await diagnostics.snapshot()).assetFailures, []);
    diagnostics.stop();
});

test('network rows, counters, error details and frame counts remain bounded under event floods', async()=>{
    const page = new Page(), diagnostics = observeInitialPairing(page);
    const longest = `${'a'.repeat(116)}-CqxElnf4.js`;
    assert.equal(longest.length, 128);
    page.emit('response', response(assetURL(longest)));
    for (let i=0; i<1500; i++) {
        page.emit('response', response(assetURL()));
        page.emit('requestfailed', failedRequest(assetURL()));
        page.emit('pageerror', {name:'TypeError', message:`Failed to fetch dynamically imported module: ${assetURL()}`,
            stack:'Error\n' + Array.from({length:20}, () => `    at render (${assetURL()}:123456789:987654321)`).join('\n')});
    }
    for (let i=0; i<1500; i++) {
        page.emit('response', response(assetURL(`chunk${i}-AbCd1234.js`)));
        page.emit('requestfailed', failedRequest(assetURL(`chunk${i}-AbCd1234.js`)));
    }
    const report = await diagnostics.snapshot();
    assert.equal(report.assetResponses.length, 64);
    assert.equal(report.assetFailures.length, 64);
    assert.equal(report.assetResponses.find(row => row.asset === 'Profile-CqxElnf4.js').count, 1000);
    assert.equal(report.assetFailures.find(row => row.asset === 'Profile-CqxElnf4.js').count, 1000);
    assert.deepEqual(report.assetEventsDropped, {responses:1000,failedRequests:1000});
    assert(report.assetResponses.every(row => row.asset.length <= 128));
    assert.equal(report.pageErrorDetails.length, 5);
    assert(report.pageErrorDetails.every(row => row.assetFrames.length === 5));
    assert.deepEqual(report.pageErrors, {TypeError:1000});
    diagnostics.stop();
});

test('snapshots detach all new nested data and stop removes and disables every observer listener', async()=>{
    const page = new Page(), diagnostics = observeInitialPairing(page);
    const listener = () => {};
    page.on('response', listener);
    page.emit('response', response(assetURL()));
    page.emit('requestfailed', failedRequest(assetURL()));
    page.emit('pageerror', {message:`Failed to fetch dynamically imported module: ${assetURL()}`});
    const before = await diagnostics.snapshot();
    const mutated = await diagnostics.snapshot();
    mutated.assetResponses[0].asset = secret;
    mutated.assetResponses.push({asset:secret});
    mutated.assetFailures[0].count = 0;
    mutated.assetEventsDropped.responses = 999;
    mutated.pageErrorDetails[0].importAsset = secret;
    assert.deepEqual(await diagnostics.snapshot(), before);
    const queued = ['response','requestfailed','pageerror'].map(event =>
        [event, page.listeners(event).filter(value => value !== listener)]);
    diagnostics.stop(); diagnostics.stop();
    assert.equal(page.listenerCount('response'), 1);
    assert.equal(page.listenerCount('requestfailed'), 0);
    assert.equal(page.listenerCount('pageerror'), 0);
    for (const [event, listeners] of queued) for (const fn of listeners) {
        fn(event === 'response' ? response(assetURL(),500) : event === 'requestfailed' ? failedRequest(assetURL()) : new Error(secret));
    }
    page.emit('response', response(assetURL(),500));
    page.emit('requestfailed', failedRequest(assetURL()));
    page.emit('pageerror', new Error(secret));
    assert.deepEqual(await diagnostics.snapshot(), before);
});
