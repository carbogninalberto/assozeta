// Real self-hosted backend/UI against a disposable Bakney contract peer on a separate HTTPS site.
// No deployed account, real Bakney server, shared cookies, or persisted browser credentials.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import http from 'node:http';
import https from 'node:https';
import net from 'node:net';
import crypto from 'node:crypto';
import {spawn, spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium, expect} from '@playwright/test';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const origin = 'https://club.assozeta.test:5443';
const authority = 'https://login.bakney.test:5444';
const callback = origin + '/api/instance/sso/v1/callback';
const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'assozeta-sso-'));
const output = process.env.ASSOZETA_SSO_BROWSER_OUTPUT || path.join(root, 'quality-reports/bakney-sso');
const database = `sso_browser_${process.pid}`;
const container = `assozeta-sso-browser-${process.pid}`;
const envFile = process.env.ASSOZETA_SSO_ENV_FILE || path.join(root, 'selfhost/.env.dev');
const docker = process.env.DOCKER_BIN || 'docker';
const postgres = process.env.ASSOZETA_SSO_POSTGRES || 'assozeta-dev-postgres-1';
let appServer, peerServer, backend, browser, databaseCreated = false;
const codes = new Map(), sessions = new Map();
let pairing, users, mode = 'ok', redemptionCount = 0;
const proxySockets = new Set();
const checks = [];
function command(program, args) {
    const result = spawnSync(program, args, {encoding: 'utf8'});
    if (result.status !== 0) throw new Error(`${program} failed: ${result.stderr}`);
    return result.stdout;
}
function canonical(data) {
    return JSON.stringify(Object.fromEntries(Object.keys(data).sort().map(key => [key, data[key]])))
        .replace(/[\u007f-\uffff]/g, char => '\\u' + char.charCodeAt(0).toString(16).padStart(4, '0'));
}
function signature(secret, purpose, data) {
    return crypto.createHmac('sha256', secret).update(`assozeta-bakney-sso:v1:${purpose}\n${canonical(data)}`).digest('hex');
}
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const challenge = value => crypto.createHash('sha256').update(value).digest('base64url');
function json(res, status, data) { res.writeHead(status, {'Content-Type': 'application/json', 'Cache-Control': 'no-store'}); res.end(JSON.stringify(data)); }
async function body(req) { const chunks = []; for await (const chunk of req) chunks.push(chunk); return JSON.parse(Buffer.concat(chunks).toString() || '{}'); }
let certificate;
function request(url, {method = 'GET', data, token} = {}) {
    return new Promise((resolve, reject) => {
        const payload = data ? JSON.stringify(data) : null;
        const req = https.request(url, {method, ca: certificate, lookup: (_host, _options, cb) => cb(null, [{address: '127.0.0.1', family: 4}]),
            headers: {'Content-Type': 'application/json', ...(payload ? {'Content-Length': Buffer.byteLength(payload)} : {}), ...(token ? {Authorization: `Bearer ${token}`} : {})}}, res => {
            const chunks = []; res.on('data', c => chunks.push(c)); res.on('end', () => {
                try { resolve({status: res.statusCode, data: JSON.parse(Buffer.concat(chunks).toString())}); } catch (e) { reject(e); }
            });
        });
        req.on('error', reject); req.end(payload);
    });
}
async function register(secret) {
    const metadata = await request(origin + '/api/instance/sso/v1/metadata');
    assert.equal(metadata.status, 200);
    const binding = metadata.data;
    const payload = {...binding, nonce: crypto.randomBytes(32).toString('base64url'), expires_at: Math.floor(Date.now()/1000) + 60, association_name: 'Browser Club'};
    let result = await request(origin + '/api/instance/sso/v1/proof', {method: 'POST', data: {...payload, signature: signature(secret, 'proof:request', payload)}});
    assert.equal(result.status, 200, JSON.stringify(result.data));
    assert.equal(result.data.signature, signature(secret, 'proof:response', payload));
    // Bakney retains only the hash after setup; all later calls authenticate using HTTP Basic.
    pairing = {...binding, secretHash: hash(secret), state: 'pending', forwarding_enabled: false, revision: 1, association_name: 'Browser Club'};
    const confirm = {...payload, nonce: crypto.randomBytes(32).toString('base64url')};
    result = await request(origin + '/api/instance/sso/v1/confirm', {method: 'POST', data: {...confirm, signature: signature(secret, 'confirm:request', confirm)}});
    assert.equal(result.status, 200, JSON.stringify(result.data));
    pairing.forwarding_enabled = true;
    pairing.revision++;
}
async function peer(req, res) {
    const url = new URL(req.url, authority);
    if (url.pathname === '/launch') {
        const session = crypto.randomBytes(32).toString('base64url');
        sessions.set(session, url.searchParams.get('user') || 'alice');
        res.writeHead(302, {'Set-Cookie': `bakney-fixture=${session}; Path=/; HttpOnly; Secure; SameSite=Lax`, Location: origin + '/api/instance/sso/v1/start'}); res.end(); return;
    }
    if (url.pathname.endsWith('/authorize')) {
        const sid = req.headers.cookie?.match(/bakney-fixture=([^;]+)/)?.[1];
        const user = sessions.get(sid);
        assert(user);
        assert.equal(url.searchParams.get('pairing_id'), pairing.pairing_id);
        assert.equal(url.searchParams.get('redirect_uri'), callback);
        assert.equal(url.searchParams.get('code_challenge_method'), 'S256');
        const code = crypto.randomBytes(32).toString('base64url');
        codes.set(code, {challenge: mode === 'bad-pkce' ? challenge('wrong-verifier') : url.searchParams.get('code_challenge'),
            user, pairing_id: pairing.pairing_id, expires: Date.now() + (mode === 'expired-code' ? -1000 : 60000)});
        const state = mode === 'bad-state' ? crypto.randomBytes(32).toString('base64url') : url.searchParams.get('state');
        res.writeHead(302, {Location: `${callback}?${new URLSearchParams({state, code})}`, 'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'}); res.end(); return;
    }
    const basic = Buffer.from((req.headers.authorization || '').replace(/^Basic /, ''), 'base64').toString();
    const separator = basic.indexOf(':');
    if (!pairing || basic.slice(0, separator) !== pairing.pairing_id || hash(basic.slice(separator+1)) !== pairing.secretHash) return json(res, 403, {error: 'invalid_client'});
    const data = await body(req);
    assert.equal(data.protocol, 1);
    if (url.pathname.endsWith('/disconnect')) { pairing.state = 'disconnected'; return json(res, 200, {protocol: 1, state: 'disconnected'}); }
    const fields = ['pairing_id','instance_id','association_id','origin','callback_uri'];
    assert(fields.every(key => data[key] === pairing[key]));
    if (url.pathname.endsWith('/acknowledge')) pairing.state = 'paired';
    if (url.pathname.endsWith('/redeem')) {
        const entry = codes.get(data.code);
        if (!entry || entry.expires < Date.now() || entry.pairing_id !== pairing.pairing_id || challenge(data.code_verifier) !== entry.challenge) return json(res, 400, {error:'invalid_grant'});
        codes.delete(data.code); redemptionCount++;
        if (mode === 'unavailable') return json(res, 503, {error: 'unavailable'});
        return json(res, 200, {protocol:1, ...Object.fromEntries(fields.map(k => [k, pairing[k]])),
            user_id: mode === 'unknown' ? crypto.randomUUID() : users[entry.user].user_id, role:'athlete', is_active:true,
            is_superuser:false, is_instructor:false, authentication_complete:true});
    }
    const {secretHash, ...status} = pairing;
    json(res, 200, status);
}
async function seed(page, session, extras = {}) {
    await page.goto(origin + '/?fixture=' + crypto.randomUUID() + '#/bakney-login?error=fixture');
    await page.evaluate(({session, extras}) => {
        localStorage.clear();
        for (const [key, value] of Object.entries({sessionToken:session.access_token, refreshToken:session.refresh_token,
            expires:Date.now()+session.expires_in*1000, userData:{...session.user_data, requires_welcome:false},
            role:session.role, currentPage:'dashboard', ...extras})) localStorage.setItem(key, JSON.stringify(value));
    }, {session, extras});
}
async function enterApp(page, action) {
    const loaded = page.waitForEvent('domcontentloaded', {predicate: () => page.url() === origin + '/', timeout:30000});
    await action();
    await loaded;
    await expect(page.locator('.bakney-login')).toHaveCount(0);
}
try {
    await fs.mkdir(output, {recursive:true});
    const vector = JSON.parse(await fs.readFile(path.join(root,'BE/instance/tests/fixtures/bakney-sso-v1.json'),'utf8'));
    assert.equal(canonical(vector.payload), vector.canonical);
    for (const [purpose, expected] of Object.entries(vector.signatures)) assert.equal(signature(vector.secret, purpose, vector.payload), expected);
    command('openssl', ['req','-x509','-newkey','rsa:2048','-nodes','-keyout',path.join(temp,'key.pem'),'-out',path.join(temp,'cert.pem'),'-days','1','-subj','/CN=login.bakney.test','-addext','subjectAltName=DNS:login.bakney.test,DNS:club.assozeta.test']);
    certificate = await fs.readFile(path.join(temp,'cert.pem'));
    const tls = {key:await fs.readFile(path.join(temp,'key.pem')), cert:certificate};
    peerServer = https.createServer(tls, (req,res) => peer(req,res).catch(error => {console.error(error.message); json(res,500,{error:'fixture_error'});}));
    await new Promise(resolve => peerServer.listen(5444,'0.0.0.0',resolve));
    appServer = https.createServer(tls, async (req,res) => {
        if (req.url.startsWith('/api/')) {
            const proxy = http.request({host:'127.0.0.1',port:5445,path:req.url.slice(4),method:req.method,
                headers:{...req.headers, host:'club.assozeta.test:5443', 'x-forwarded-proto':'https', 'x-forwarded-host':'club.assozeta.test:5443'}}, upstream => {
                    res.writeHead(upstream.statusCode,upstream.headers); upstream.pipe(res);
                });
            proxy.on('error',()=>{res.writeHead(503);res.end();}); req.pipe(proxy); return;
        }
        try {
            const pathname = new URL(req.url, origin).pathname;
            const directory = path.join(root,'UI/dist/public');
            let file = path.resolve(directory,'.' + pathname);
            if (!file.startsWith(directory + path.sep) || pathname === '/') file=path.join(directory,'index.html');
            let content;
            try { content=await fs.readFile(file); } catch { file=path.join(directory,'index.html'); content=await fs.readFile(file); }
            const types={'.js':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml','.png':'image/png','.woff2':'font/woff2','.json':'application/json'};
            res.writeHead(200, {'Content-Type':types[path.extname(file)] || 'application/octet-stream','Cache-Control':'no-store'});res.end(content);
        } catch {res.writeHead(500);res.end();}
    });
    appServer.on('upgrade', (req, socket, head) => {
        const upstream = net.connect(5445,'127.0.0.1', () => {
            const headers = {...req.headers, host:'club.assozeta.test:5443', 'x-forwarded-proto':'https'};
            upstream.write(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n` + Object.entries(headers).map(([k,v])=>`${k}: ${v}\r\n`).join('') + '\r\n');
            if(head.length) upstream.write(head);
            upstream.pipe(socket); socket.pipe(upstream);
        });
        proxySockets.add(socket); proxySockets.add(upstream);
        socket.on('error',()=>upstream.destroy());upstream.on('error',()=>socket.destroy());
        socket.on('close',()=>{proxySockets.delete(socket);upstream.destroy();});
        upstream.on('close',()=>{proxySockets.delete(upstream);socket.destroy();});
    });
    await new Promise(resolve => appServer.listen(5443,'0.0.0.0',resolve));
    command(docker,['exec',postgres,'sh','-c',`createdb -U "$POSTGRES_USER" ${database}`]); databaseCreated=true;
    const log = await fs.open(path.join(temp,'backend.log'),'w');
    backend=spawn(docker,['run','--rm','--name',container,'--network','assozeta-dev_dev','--add-host','login.bakney.test:host-gateway',
        '--env-file',envFile,'-e',`DBNAME=${database}`,'-e','DEBUG=False','-e','SSO_BROWSER_FIXTURE=1','-e',`APP_URL=${origin}`,
        '-e',`BAKNEY_SSO_AUTHORITY=${authority}`,'-e','ALLOWED_HOSTS=club.assozeta.test,127.0.0.1','-p','127.0.0.1:5445:8000',
        '-v',`${root}/BE:/app`,'-v',`${temp}:/fixture-tls:ro`,'--entrypoint','python','assozeta-backend-dev','-m','instance.tests.sso_browser_server'],{stdio:['ignore',log.fd,log.fd]});
    let fixture;
    for(let attempt=0;attempt<180;attempt++) {
        if(backend.exitCode!==null) throw new Error('Fixture backend stopped; inspect temporary backend log.');
        try {const r=await request(origin+'/api/fixture'); if(r.status===200){fixture=r.data;break;}} catch {}
        await new Promise(resolve=>setTimeout(resolve,1000));
    }
    assert(fixture,'Fixture backend readiness deadline exceeded'); users=fixture.users;
    browser=await chromium.launch({headless:true,args:['--no-proxy-server','--host-resolver-rules=MAP *.test 127.0.0.1']});
    const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1360,height:1000}});
    const page=await context.newPage();
    page.on('pageerror', error => console.error('Fixture UI error:', error.message));
    // Keep unrelated release discovery local to the fixture; all pairing/session requests use Django.
    await page.route('**/api/instance/admin/releases*',r=>r.fulfill({json:{relation:'current',history:[],pending:[],page:1,next_page:null,total:0,latest:null}}));
    await seed(page,fixture.owner);
    await page.goto(origin+'/#/profile?page=self-instance&tab=bakney');
    await page.reload();
    try {
        await page.getByRole('button',{name:'Genera segreto di collegamento',exact:true}).click({timeout:30000});
    } catch (error) {
        console.error('Pairing settings did not render:', page.url(), await page.locator('body').innerText());
        await page.screenshot({path:path.join(output,'pairing-load-failure.png')});
        throw error;
    }
    const secret=await page.getByLabel('Segreto di collegamento',{exact:true}).inputValue();
    assert.equal(secret.length,43);
    await register(secret);
    await page.getByRole('button',{name:'Ho copiato il segreto: nascondi'}).click();
    await page.getByRole('button',{name:'Aggiorna stato',exact:true}).click();
    await expect(page.getByText('Collegato',{exact:true})).toBeVisible();
    await page.getByRole('button',{name:'Verifica con Bakney',exact:true}).click();
    await expect(page.getByText('Abilitato su Bakney',{exact:true})).toBeVisible();
    await page.screenshot({path:path.join(output,'pairing-desktop.png')});
    await page.setViewportSize({width:390,height:844});
    await page.getByRole('heading',{name:'Collegamento con Bakney',exact:true}).scrollIntoViewIfNeeded();
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1));
    await page.screenshot({path:path.join(output,'pairing-mobile.png'),fullPage:true});
    checks.push('Pairing UI: secret generated once, real HMAC verification/confirmation, authenticated status, desktop/mobile');
    await context.close();
    const normal=await browser.newContext({ignoreHTTPSErrors:true});
    const userPage=await normal.newPage();
    await enterApp(userPage, () => userPage.goto(authority+'/launch?user=alice'));
    assert.equal(await userPage.evaluate(()=>JSON.parse(localStorage.getItem('userData')).user_id),users.alice.user_id);
    const cookies=await normal.cookies();
    assert(cookies.some(c=>c.name==='BKN_AUTH'&&c.httpOnly&&c.secure&&c.sameSite==='Strict'));
    assert(!cookies.some(c=>c.name==='bakney-fixture'&&c.domain==='club.assozeta.test'));
    checks.push('Cross-site HTTPS: host-only central session, Lax handoff cookie, PKCE redemption, Strict local cookie, real frontend startup');
    // Existing identity is preserved until the user explicitly accepts a switch.
    await seed(userPage,users.bob,{permissions:['old-permission'],selectedGroup:'old-group',impersonationContext:{userId:'old'},switched_superuser:true});
    await normal.addCookies([{name:'BKN_AUTH',value:users.bob.access_token,url:origin,httpOnly:true,secure:true,sameSite:'Strict'}]);
    const otherTab = await normal.newPage();
    await otherTab.goto(origin + '/');
    assert.equal(await otherTab.evaluate(()=>JSON.parse(localStorage.getItem('userData')).user_id),users.bob.user_id);
    await userPage.goto(authority+'/launch?user=alice');
    await expect(userPage.getByRole('button',{name:'Conferma cambio account'})).toBeVisible();
    assert.equal(await userPage.evaluate(()=>JSON.parse(localStorage.getItem('userData')).user_id),users.bob.user_id);
    await userPage.screenshot({path:path.join(output,'account-switch.png')});
    const otherTabReloaded = otherTab.waitForEvent('domcontentloaded', {timeout:30000});
    await enterApp(userPage, () => userPage.getByRole('button',{name:'Conferma cambio account'}).click());
    await otherTabReloaded;
    assert.equal(await otherTab.evaluate(()=>JSON.parse(localStorage.getItem('userData')).user_id),users.alice.user_id);
    await otherTab.close();
    assert.equal(await userPage.evaluate(()=>JSON.parse(localStorage.getItem('userData')).user_id),users.alice.user_id);
    assert.equal(await userPage.evaluate(()=>localStorage.getItem('impersonationContext')),null);
    assert.equal(await userPage.evaluate(()=>JSON.parse(localStorage.getItem('selectedGroup'))),null);
    checks.push('Explicit account switch, cleared permissions/group/impersonation, fresh app document, and other-tab reload');
    for(const failure of ['bad-state','bad-pkce','expired-code','unknown','unavailable']) {
        mode=failure;
        const before=redemptionCount;
        const token=await userPage.evaluate(()=>localStorage.getItem('sessionToken'));
        await userPage.goto(authority+'/launch?user=alice');
        await expect(userPage.getByRole('alert')).toBeVisible();
        assert.equal(await userPage.evaluate(()=>localStorage.getItem('sessionToken')),token);
        assert(!userPage.url().includes('code='));
        if(['bad-state','bad-pkce','expired-code'].includes(failure)) assert.equal(redemptionCount,before);
        await enterApp(userPage, () => userPage.getByRole('link',{name:'Mantieni la sessione attuale'}).click());
    }
    checks.push('Bad state, wrong PKCE, expired code, missing local identity, and unavailable Bakney preserve the existing session without loops');
    mode='ok';pairing.forwarding_enabled=false;pairing.revision++;
    await userPage.goto(authority+'/launch?user=alice');
    await expect(userPage.getByRole('alert')).toContainText('disabilitato');
    checks.push('Forwarding disabled centrally is enforced before authorization');
    await normal.close();
    await fs.writeFile(path.join(output,'report.json'),JSON.stringify({checks,scope:'Real Assozeta backend and built UI; synthetic Bakney v1 contract peer; separate HTTPS sites with fixture certificate. Production Bakney interoperability is not exercised.'},null,2));
    console.log(JSON.stringify({passed:checks.length,checks},null,2));
} finally {
    if(browser) await browser.close();
    if(backend && backend.exitCode === null) command(docker,['stop','-t','2',container]);
    if(databaseCreated) command(docker,['exec',postgres,'sh','-c',`dropdb -U "$POSTGRES_USER" ${database}`]);
    for(const socket of proxySockets) socket.destroy();
    appServer?.closeAllConnections();peerServer?.closeAllConnections();
    if(appServer) await new Promise(r=>appServer.close(r));
    if(peerServer) await new Promise(r=>peerServer.close(r));
    await fs.rm(temp,{recursive:true,force:true});
}
