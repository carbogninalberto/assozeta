// Separate HTTPS sites, real receiver backend/built UI. Optional real Bakney repositories.
// Synthetic data only; no traces, storage state, credentials or callback URLs in artifacts.
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
const origin = 'https://club.assozeta.test', authority = 'https://login.bakney.test';
const callback = origin + '/bakney/v1/callback', protocol = 'bakney-pairing-v1';
const real = !!process.env.ASSOZETA_BAKNEY_BACKEND;
if(real) assert(process.env.ASSOZETA_BAKNEY_UI, 'Both Bakney repository paths are required');
const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'assozeta-sso-'));
const output = process.env.ASSOZETA_SSO_BROWSER_OUTPUT || path.join(root, 'quality-reports/bakney-sso');
const envFile = process.env.ASSOZETA_SSO_ENV_FILE || path.join(root, 'selfhost/.env.dev');
const docker = process.env.DOCKER_BIN || 'docker';
const postgres = process.env.ASSOZETA_SSO_POSTGRES || 'assozeta-dev-postgres-1';
const proxySockets = new Set(), databases = [], containers = [], checks = [], sources = {};
let server, browser, certificate, users, receiver, central, pairing, failure = '', lastCallback;
const codes = new Map(), attempts = new Map();
function command(program, args, options={}) {
    const result = spawnSync(program, args, {encoding:'utf8', ...options});
    if(result.status !== 0) throw new Error(`${program} failed: ${result.stderr?.slice(-3000)}`);
    return result.stdout;
}
const random = () => crypto.randomBytes(32).toString('base64url');
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const challenge = value => crypto.createHash('sha256').update(value).digest('base64url');
function signature(secret, phase, binding) {
    const fields = [protocol, phase, ...['pairing_id','association_id','instance_id','origin','callback_uri','generation','nonce','expires_at'].map(k=>String(binding[k]))];
    return crypto.createHmac('sha256',Buffer.from(secret,'base64url')).update(fields.join('\n')).digest('hex');
}
function json(res, status, value) {res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store','Referrer-Policy':'no-referrer'});res.end(JSON.stringify(value));}
async function body(req) {const chunks=[];for await(const chunk of req)chunks.push(chunk);return JSON.parse(Buffer.concat(chunks).toString()||'{}');}
function request(url,{method='GET',data,token}={}) {
    return new Promise((resolve,reject)=>{
        const payload=data?JSON.stringify(data):null;
        const req=https.request(url,{method,ca:certificate,lookup:(_h,_o,cb)=>cb(null,[{address:'127.0.0.1',family:4}]),headers:{'Content-Type':'application/json',...(payload?{'Content-Length':Buffer.byteLength(payload)}:{}),...(token?{Authorization:`Bearer ${token}`}:{})}},res=>{
            const chunks=[];res.on('data',c=>chunks.push(c));res.on('end',()=>{try{resolve({status:res.statusCode,data:JSON.parse(Buffer.concat(chunks).toString())});}catch{reject(new Error(`Non-JSON fixture response: ${res.statusCode}`));}});
        });req.on('error',reject);req.end(payload);
    });
}
async function snapshot(repo,label) {
    const destination=path.join(temp,label);await fs.mkdir(destination);
    const revision=command('git',['rev-parse','HEAD'],{cwd:repo}).trim();
    const archive=path.join(temp,label+'.tar');
    command('git',['archive','--format=tar','--output='+archive,revision],{cwd:repo});command('tar',['-xf',archive,'-C',destination]);
    const diff=command('git',['diff',revision,'--binary'],{cwd:repo});
    if(diff) command('git',['apply','--unsafe-paths','-'],{cwd:destination,input:diff});
    sources[label]={commit:revision,working_diff_sha256:diff?hash(diff):null};
    return destination;
}
async function checkProductionProxy() {
    const receiverContainer=containers.find(c=>c.name.includes('-receiver-'));
    const ip=command(docker,['inspect','--format','{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}',receiverContainer.name]).trim();
    const name=`assozeta-sso-caddy-${process.pid}`;
    const log=await fs.open(path.join(temp,'caddy.log'),'w');
    const processHandle=spawn(docker,['run','--rm','--name',name,'--network','assozeta-dev_dev','--add-host',`api:${ip}`,
        '-e','SITE_ADDRESS=:80','-p','127.0.0.1:5447:80','-v',`${root}/selfhost/caddy/Caddyfile:/etc/caddy/Caddyfile:ro`,
        '-v',`${root}/UI/dist/public:/srv:ro`,'caddy:2.9.1-alpine'],{stdio:['ignore',log.fd,log.fd]});
    containers.push({name,processHandle});await log.close();
    const read=(url,method='GET')=>new Promise((resolve,reject)=>{
        const req=http.request({host:'127.0.0.1',port:5447,path:url,method,headers:{Host:'club.assozeta.test','Content-Type':'application/json'}},res=>{
            const chunks=[];res.on('data',c=>chunks.push(c));res.on('end',()=>resolve({status:res.statusCode,headers:res.headers,text:Buffer.concat(chunks).toString()}));
        });req.on('error',reject);req.end(method==='POST'?'{}':undefined);
    });
    let available=false;
    for(let i=0;i<30;i++){try{available=(await read('/healthz')).status===200;if(available)break;}catch{}await new Promise(r=>setTimeout(r,1000));}
    assert(available,'Production Caddy did not become ready');
    for(const route of ['pairing/challenge','pairing/commit','session']){
        const result=await read('/bakney/v1/'+route,'POST');
        assert.equal(result.status,409);assert.equal(JSON.parse(result.text).error,route==='session'?'configuration_required':'revalidation_required');
        assert.equal(result.headers['cache-control'],'no-store');assert.equal(result.headers['referrer-policy'],'no-referrer');
    }
    for(const route of ['login-start','callback'])assert.equal((await read('/bakney/v1/'+route)).status,302);
    assert.equal((await read('/api/instance/admin/bakney-pairing')).status,401);
    checks.push('Actual production Caddy configuration routes every root protocol endpoint to Django with privacy headers');
}
async function launchBackend(label,source,script,port,extra=[]) {
    const database=`sso_browser_${label}_${process.pid}`,name=`assozeta-sso-${label}-${process.pid}`;
    command(docker,['exec',postgres,'sh','-c',`createdb -U "$POSTGRES_USER" ${database}`]);databases.push(database);
    const log=await fs.open(path.join(temp,label+'.log'),'w');
    const args=['run','--rm','--name',name,'--network','assozeta-dev_dev','--add-host','login.bakney.test:host-gateway','--add-host','club.assozeta.test:host-gateway',
        '--env-file',envFile,'-e',`DBNAME=${database}`,'-e','DEBUG=False','-e','SSO_BROWSER_FIXTURE=1',
        '-e',`APP_URL=${origin}`,'-e',`BAKNEY_SSO_API_BASE=${authority}/api`,'-e',`BAKNEY_SSO_UI_ORIGIN=${authority}`,
        '-e','ALLOWED_HOSTS=club.assozeta.test,login.bakney.test,127.0.0.1','-p',`127.0.0.1:${port}:8000`,
        '-v',`${source}:/app:ro`,'-v',`${temp}:/fixture-tls:ro`,'-v',`${root}/BE/instance/tests:/fixture:ro`,...extra,
        '--entrypoint','python',label === 'provider' ? 'assozeta-bakney-sso-provider' : 'assozeta-backend-dev',...script];
    const processHandle=spawn(docker,args,{stdio:['ignore',log.fd,log.fd]});containers.push({name,processHandle});await log.close();
    return processHandle;
}
async function ready(url,processHandle,label) {
    for(let i=0;i<180;i++) {
        if(processHandle.exitCode!==null) throw new Error(`${label} backend stopped:\n`+(await fs.readFile(path.join(temp,label+'.log'),'utf8')).slice(-5000));
        try{const r=await request(url);if(r.status===200)return r.data;}catch{}
        await new Promise(r=>setTimeout(r,1000));
    }throw new Error(label+' readiness timeout');
}
function proxy(req,res,port,url=req.url) {
    const upstream=http.request({host:'127.0.0.1',port,path:url,method:req.method,headers:{...req.headers,'x-forwarded-proto':'https','x-forwarded-host':req.headers.host}},r=>{res.writeHead(r.statusCode,r.headers);r.pipe(res);});
    upstream.on('error',()=>json(res,503,{error:'fixture_unavailable'}));req.pipe(upstream);
}
async function staticFile(req,res,directory) {
    const pathname=new URL(req.url,origin).pathname;
    let file=path.resolve(directory,'.'+pathname);
    if(!file.startsWith(directory+path.sep)||pathname==='/')file=path.join(directory,'index.html');
    let content;try{content=await fs.readFile(file);}catch{file=path.join(directory,'index.html');content=await fs.readFile(file);}
    const types={'.js':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml','.png':'image/png','.woff2':'font/woff2'};
    res.writeHead(200,{'Content-Type':types[path.extname(file)]||'application/octet-stream','Cache-Control':'no-store','Referrer-Policy':'no-referrer'});res.end(content);
}
// The default CI peer is explicitly simulated. Real mode uses Bakney's Django views and UI sources.
async function peer(req,res) {
    const url=new URL(req.url,authority),data=req.method==='POST'||req.method==='PATCH'?await body(req):{};
    const publicPairing=()=>{const {secretHash,...safe}=pairing;return safe;};
    if(url.pathname==='/api/pairing/v1/settings') {
        if(req.method==='POST') {
            pairing={protocol,pairing_id:crypto.randomUUID(),association_id:receiver.association_id,instance_id:'',origin,callback_uri:callback,generation:crypto.randomUUID(),status:'pending',forwarding:false,secretHash:hash(data.secret)};
            const binding={...Object.fromEntries(['protocol','pairing_id','association_id','instance_id','origin','callback_uri','generation'].map(k=>[k,pairing[k]])),nonce:random(),expires_at:Math.floor(Date.now()/1000)+60};
            let result=await request(origin+'/bakney/v1/pairing/challenge',{method:'POST',data:binding});assert.equal(result.status,200);
            binding.instance_id=result.data.instance_id;assert.equal(result.data.proof,signature(data.secret,'challenge',binding));
            binding.nonce=random();result=await request(origin+'/bakney/v1/pairing/commit',{method:'POST',data:{...binding,proof:signature(data.secret,'commit',binding)}});
            assert.equal(result.status,200);assert.equal(result.data.proof,signature(data.secret,'ack',binding));
            pairing.instance_id=binding.instance_id;pairing.status='verified';
        } else if(req.method==='PATCH') {pairing.forwarding=data.forwarding;if(!data.forwarding){pairing.generation=crypto.randomUUID();codes.clear();attempts.clear();}}
        return json(res,200,publicPairing());
    }
    if(url.pathname==='/launch') {
        const attempt=crypto.randomUUID(),nonce=random();attempts.set(attempt,{nonce,user:url.searchParams.get('user')||'alice'});
        const start=origin+'/bakney/v1/login-start?'+new URLSearchParams({protocol,pairing_id:pairing.pairing_id,attempt_id:attempt});
        res.writeHead(200,{'Content-Type':'text/html','Cache-Control':'no-store','Referrer-Policy':'no-referrer'});
        return res.end(`<script>sessionStorage.setItem('pending',${JSON.stringify(JSON.stringify({attempt_id:attempt,browser_nonce:nonce}))});location.replace(${JSON.stringify(start)});</script>`);
    }
    if(url.pathname==='/handoff.html') {
        res.writeHead(200,{'Content-Type':'text/html','Cache-Control':'no-store','Referrer-Policy':'no-referrer'});
        return res.end(`<script type="module">const q=Object.fromEntries(new URLSearchParams(location.hash.slice(1)));history.replaceState(null,'','/handoff.html');const p=JSON.parse(sessionStorage.getItem('pending'));const r=await fetch('/api/pairing/v1/authorize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...q,browser_nonce:p.browser_nonce})});const d=await r.json();location.replace(d.redirect_uri);</script>`);
    }
    if(url.pathname.endsWith('/authorize')) {
        const attempt=attempts.get(data.attempt_id);assert(attempt&&attempt.nonce===data.browser_nonce);attempts.delete(data.attempt_id);
        const code=random();codes.set(code,{challenge:data.code_challenge,user:attempt.user,expires:Date.now()+60000});
        return json(res,200,{redirect_uri:callback+'?'+new URLSearchParams({code,state:data.state})});
    }
    if(!pairing||data.pairing_id!==pairing.pairing_id||hash(data.secret||'')!==pairing.secretHash)return json(res,401,{error:'invalid_client'});
    if(url.pathname.endsWith('/status')) {
        if(data.action==='disconnect'){pairing.status='revoked';pairing.forwarding=false;codes.clear();}
        return json(res,200,publicPairing());
    }
    if(url.pathname.endsWith('/token')) {
        const code=codes.get(data.code);
        if(!code||code.expires<Date.now()||failure==='expired-code'||failure==='bad-pkce'||challenge(data.code_verifier)!==code.challenge||data.callback_uri!==callback||!pairing.forwarding||pairing.status!=='verified')return json(res,400,{error:'invalid_grant'});
        codes.delete(data.code);
        return json(res,200,{protocol,user_id:users[code.user].user_id,association_id:pairing.association_id,pairing_id:pairing.pairing_id,instance_id:pairing.instance_id,auth_time:Math.floor(Date.now()/1000),amr:['authenticated']});
    }
    return json(res,404,{error:'not_found'});
}
async function seed(page,base,session,extras={}) {
    await page.goto(base+'/fixture-blank.html');
    await page.evaluate(({session,extras})=>{localStorage.clear();sessionStorage.clear();for(const [key,value]of Object.entries({sessionToken:session.access_token,refreshToken:session.refresh_token,expires:Date.now()+session.expires_in*1000,userData:{...session.user_data,requires_welcome:false},role:session.role,currentPage:'dashboard',...extras}))localStorage.setItem(key,JSON.stringify(value));},{session,extras});
}
async function enterApp(page,action) {const loaded=page.waitForEvent('domcontentloaded',{predicate:()=>page.url()===origin+'/',timeout:30000});await action();await loaded;await expect(page.locator('.bakney-login')).toHaveCount(0);}
async function launch(page,user='alice') {
    if(real){await seed(page,authority,central[user]);await page.goto(authority+'/testing/pairing/harness.html');}
    else await page.goto(authority+'/launch?user='+user);
}
let bakneyDist;
try {
    await fs.mkdir(output,{recursive:true});
    const vector=JSON.parse(await fs.readFile(path.join(root,'BE/instance/tests/fixtures/bakney-sso-v1.json'),'utf8'));
    for(const [phase,expected]of Object.entries(vector.proofs))assert.equal(signature(vector.secret,phase,vector.binding),expected);
    command('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-keyout',path.join(temp,'key.pem'),'-out',path.join(temp,'cert.pem'),'-days','1','-subj','/CN=login.bakney.test','-addext','subjectAltName=DNS:login.bakney.test,DNS:club.assozeta.test']);
    certificate=await fs.readFile(path.join(temp,'cert.pem'));
    if(real) {
        const ui=await snapshot(process.env.ASSOZETA_BAKNEY_UI,'bakney-ui');
        await fs.symlink(path.join(process.env.ASSOZETA_BAKNEY_UI,'node_modules'),path.join(ui,'node_modules'));
        // Compile the actual pairing components/transport and dedicated handoff entry unchanged.
        await fs.writeFile(path.join(ui,'sso.integration.config.mjs'),`import {defineConfig} from 'vite';import {svelte} from '@sveltejs/vite-plugin-svelte';import path from 'node:path';import {buildConfig} from './endpoints.js';export default defineConfig({plugins:[svelte()],resolve:{alias:{utils:path.resolve('src/utils'),store:path.resolve('src/store')}},define:buildConfig(undefined,{name:'Bakney',selfHosted:false}),build:{outDir:'sso-dist',rollupOptions:{input:{harness:'testing/pairing/harness.html',handoff:'handoff.html'}}}});`);
        command(process.execPath,[path.join(ui,'node_modules/vite/bin/vite.js'),'build','--config','sso.integration.config.mjs'],{cwd:ui,maxBuffer:20*1024*1024});
        bakneyDist=path.join(ui,'sso-dist');
    }
    server=https.createServer({key:await fs.readFile(path.join(temp,'key.pem')),cert:certificate},async(req,res)=>{
        try {
            if(req.url==='/fixture-blank.html'){res.writeHead(200,{'Content-Type':'text/html','Cache-Control':'no-store'});return res.end('<html><body>Fixture</body></html>');}
            if(req.headers.host==='club.assozeta.test') {
                if(req.url.startsWith('/bakney/v1/callback?')) {
                    lastCallback=origin+req.url;
                    if(failure==='bad-state'){const u=new URL(req.url,origin);u.searchParams.set('state',random());req.url=u.pathname+u.search;}
                    if(real&&['expired-code','bad-pkce'].includes(failure))await request(authority+'/api/fixture',{method:'POST',data:{[failure==='expired-code'?'expire_codes':'wrong_pkce']:true}});
                }
                if(req.url.startsWith('/api/'))return proxy(req,res,5445,req.url.slice(4));
                if(req.url.startsWith('/bakney/v1/'))return proxy(req,res,5445);
                return await staticFile(req,res,path.join(root,'UI/dist/public'));
            }
            if(req.url==='/api/pairing/v1/token'&&failure==='unavailable')return json(res,503,{error:'unavailable'});
            if(real){if(req.url.startsWith('/api/'))return proxy(req,res,5446,req.url.slice(4));return await staticFile(req,res,bakneyDist);}
            return await peer(req,res);
        }catch(error){console.error('Fixture handler:',error.message);json(res,500,{error:'fixture_error'});}
    });
    server.on('upgrade',(req,socket,head)=>{
        const upstream=net.connect(5445,'127.0.0.1',()=>{upstream.write(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n`+Object.entries({...req.headers,'x-forwarded-proto':'https'}).map(([k,v])=>`${k}: ${v}\r\n`).join('')+'\r\n');if(head.length)upstream.write(head);upstream.pipe(socket);socket.pipe(upstream);});
        proxySockets.add(socket);proxySockets.add(upstream);socket.on('error',()=>upstream.destroy());upstream.on('error',()=>socket.destroy());socket.on('close',()=>{proxySockets.delete(socket);upstream.destroy();});upstream.on('close',()=>{proxySockets.delete(upstream);socket.destroy();});
    });
    await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(443,'0.0.0.0',resolve);});
    const backend=await launchBackend('receiver',path.join(root,'BE'),['-m','instance.tests.sso_browser_server'],5445);
    receiver=await ready(origin+'/api/fixture',backend,'receiver');users=receiver.users;
    await checkProductionProxy();
    if(real) {
        const backendSource=await snapshot(process.env.ASSOZETA_BAKNEY_BACKEND,'bakney-backend');
        assert.equal(await fs.readFile(path.join(backendSource,'application/pairing/fixtures.json'),'utf8'),await fs.readFile(path.join(root,'BE/instance/tests/fixtures/bakney-sso-v1.json'),'utf8'));
        await fs.writeFile(path.join(temp,'identities.json'),JSON.stringify({association:receiver.association_id,owner:receiver.owner_id,alice:users.alice.user_id,bob:users.bob.user_id}));
        command(docker,['build','-t','assozeta-bakney-sso-provider','-'],{input:'FROM assozeta-backend-dev\nUSER root\nRUN pip install --no-cache-dir smsapi-client==2.9.6\n',maxBuffer:10*1024*1024});
        const provider=await launchBackend('provider',backendSource,['/fixture/bakney_provider_server.py'],5446);
        const fixture=await ready(authority+'/api/fixture',provider,'provider');
        central={};
        for(const name of ['owner','alice','bob']) {
            const data={username:fixture.users[name],password:fixture.password};
            if(name==='alice'){
                const denied=await request(authority+'/api/oauth2/login',{method:'POST',data});assert.equal(denied.status,401);
                data.otp=(await request(authority+'/api/fixture')).data.otp;
            }
            const response=await request(authority+'/api/oauth2/login',{method:'POST',data});assert.equal(response.status,200);central[name]=response.data;
        }
        checks.push('Real Bakney password authentication and required TOTP before handoff');
    }
    browser=await chromium.launch({headless:true,args:['--no-proxy-server','--host-resolver-rules=MAP *.test 127.0.0.1']});
    const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1360,height:1000},permissions:['clipboard-read','clipboard-write']});
    const admin=await context.newPage();
    await admin.route('**/api/instance/admin/releases*',r=>r.fulfill({json:{relation:'current',history:[],pending:[],page:1,next_page:null,total:0,latest:null}}));
    await seed(admin,origin,receiver.owner);await admin.goto(origin+'/#/profile?page=self-instance&tab=overview');
    await admin.getByRole('button',{name:'Genera codice di collegamento',exact:true}).click({timeout:30000});
    let secret=await admin.getByLabel('Codice di collegamento',{exact:true}).inputValue();assert.equal(secret.length,43);
    await admin.getByRole('button',{name:'Copia codice',exact:true}).click();assert(await admin.evaluate(()=>navigator.clipboard.readText())===secret,'Copied pairing code differs');
    let setupPage;
    async function register() {
        if(real){
            if(!setupPage){setupPage=await context.newPage();await seed(setupPage,authority,central.owner);await setupPage.goto(authority+'/testing/pairing/harness.html?settings');}
            await setupPage.getByLabel('URL HTTPS dell’istanza',{exact:true}).fill(origin);
            await setupPage.getByLabel('Segreto di abbinamento',{exact:true}).fill(secret);
            const verified=setupPage.waitForResponse(r=>new URL(r.url()).pathname==='/api/pairing/v1/settings'&&r.request().method()==='POST');
            await setupPage.getByRole('button',{name:'Verifica e collega',exact:true}).click();
            assert.equal((await verified).status(),200);
            await expect(setupPage.getByRole('button',{name:'Verifica e collega',exact:true})).toBeEnabled();
            await expect(setupPage.getByText('Verificata',{exact:true})).toBeVisible();
            await expect(setupPage.getByRole('checkbox')).not.toBeChecked();
            const enabled=setupPage.waitForResponse(r=>new URL(r.url()).pathname==='/api/pairing/v1/settings'&&r.request().method()==='PATCH');
            await setupPage.getByRole('checkbox').check();assert.equal((await enabled).status(),200);
            await expect(setupPage.getByRole('checkbox')).toBeEnabled();
        }else{
            assert.equal((await request(authority+'/api/pairing/v1/settings',{method:'POST',data:{origin,secret}})).status,200);
            await request(authority+'/api/pairing/v1/settings',{method:'PATCH',data:{forwarding:true}});
        }
    }
    await register();await admin.getByRole('button',{name:'Nascondi codice'}).click();
    await admin.getByRole('button',{name:'Aggiorna stato',exact:true}).click();
    await expect(admin.getByText('Collegato',{exact:true})).toBeVisible();await expect(admin.getByText('Abilitato su Bakney',{exact:true})).toBeVisible();
    await admin.screenshot({path:path.join(output,'pairing-desktop.png')});await admin.setViewportSize({width:390,height:844});
    assert(await admin.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await admin.screenshot({path:path.join(output,'pairing-mobile.png'),fullPage:true});
    checks.push('Panoramica: generated/copyable code, challenge/commit, verified status and separate forwarding status; desktop/mobile');
    const normal=await browser.newContext({ignoreHTTPSErrors:true});const page=await normal.newPage();
    page.on('response',response=>{const url=new URL(response.url());if(response.status()>=400 && url.pathname.includes('/pairing/'))console.log('Pairing response:',url.pathname,response.status());});
    await enterApp(page,()=>launch(page));assert.equal(await page.evaluate(()=>JSON.parse(localStorage.getItem('userData')).user_id),users.alice.user_id);
    assert((await normal.cookies()).some(c=>c.name==='BKN_AUTH'&&c.domain==='club.assozeta.test'&&c.httpOnly&&c.secure&&c.sameSite==='Strict'));
    checks.push('Cross-site HTTPS: same-tab Bakney handoff page, state/PKCE redemption and ordinary local login without a second login');
    await seed(page,origin,users.bob,{selectedGroup:'old',impersonationContext:{userId:'old'},permissions:['old']});
    await normal.addCookies([{name:'BKN_AUTH',value:users.bob.access_token,url:origin,httpOnly:true,secure:true,sameSite:'Strict'}]);
    const other=await normal.newPage();await other.goto(origin+'/');await launch(page);
    await expect(page.getByRole('button',{name:'Conferma cambio account'})).toBeVisible();
    assert.equal(await page.evaluate(()=>JSON.parse(localStorage.getItem('userData')).user_id),users.bob.user_id);
    await page.screenshot({path:path.join(output,'account-switch.png')});const reloaded=other.waitForEvent('domcontentloaded');
    await enterApp(page,()=>page.getByRole('button',{name:'Conferma cambio account'}).click());await reloaded;await other.close();
    assert.equal(await page.evaluate(()=>localStorage.getItem('impersonationContext')),null);
    checks.push('Explicit account switch preserves identity until confirmation, clears caches and reloads other tabs');
    const completedCallback=lastCallback,token=await page.evaluate(()=>localStorage.getItem('sessionToken'));
    await page.goto(completedCallback);await expect(page.getByRole('alert')).toBeVisible();assert(await page.evaluate(()=>localStorage.getItem('sessionToken'))===token,'Existing session was replaced');
    for(const problem of ['bad-state','bad-pkce','expired-code','unavailable','inactive-local']) {
        if(real)await request(authority+'/api/fixture',{method:'POST',data:{reset_throttles:true}});
        await request(origin+'/api/fixture',{method:'POST',data:{reset_throttles:true}});
        failure=problem;console.log('Checking failure:',problem);
        if(problem==='inactive-local'){
            await seed(page,origin,users.bob);
            await normal.addCookies([{name:'BKN_AUTH',value:users.bob.access_token,url:origin,httpOnly:true,secure:true,sameSite:'Strict'}]);
            await request(origin+'/api/fixture',{method:'POST',data:{eligible:false}});
        }
        const before=await page.evaluate(()=>localStorage.getItem('sessionToken'));await launch(page);
        try { await expect(page.getByRole('alert')).toBeVisible({timeout:15000}); }
        catch(error){ console.error('Failure location:', new URL(page.url()).origin + new URL(page.url()).pathname, await page.locator('body').innerText());throw error; }
        assert(!page.url().includes('code='));assert(await page.evaluate(()=>localStorage.getItem('sessionToken'))===before,'Existing session was replaced');
        await enterApp(page,()=>page.getByRole('link',{name:'Mantieni la sessione attuale'}).click());
        if(problem==='inactive-local')await request(origin+'/api/fixture',{method:'POST',data:{eligible:true}});
    }
    failure='';
    if(real)await request(authority+'/api/fixture',{method:'POST',data:{reset_throttles:true}});
    checks.push('Replayed callback, bad state/PKCE, expired code, unavailable Bakney and inactive local account preserve the session without loops');
    // Regeneration/removal are immediately local, with server-to-server revocation retried explicitly by the fixture.
    await admin.getByRole('button',{name:'Rigenera codice',exact:true}).click();await admin.getByRole('button',{name:'Continua',exact:true}).click();
    secret=await admin.getByLabel('Codice di collegamento',{exact:true}).inputValue();
    await request(origin+'/api/fixture',{method:'POST',data:{deliver_revocations:true}});
    await register();await admin.getByRole('button',{name:'Nascondi codice'}).click();await admin.getByRole('button',{name:'Aggiorna stato',exact:true}).click();
    await expect(admin.getByText('Collegato',{exact:true})).toBeVisible();
    await expect(admin.getByText('Abilitato su Bakney',{exact:true})).toBeVisible();
    await launch(page);
    await expect(page.getByRole('button',{name:'Conferma cambio account'})).toBeVisible();
    await enterApp(page,()=>page.getByRole('button',{name:'Conferma cambio account'}).click());
    await admin.getByRole('button',{name:'Rimuovi collegamento',exact:true}).click();await admin.getByRole('button',{name:'Continua',exact:true}).click();
    await expect(admin.getByText('Non collegato',{exact:true})).toBeVisible();await request(origin+'/api/fixture',{method:'POST',data:{deliver_revocations:true}});
    const status=await request(authority+'/api/pairing/v1/settings',{token:central?.owner.access_token});assert.equal(status.data.status,'revoked');
    const retained=await page.evaluate(()=>localStorage.getItem('sessionToken'));
    await page.goto(origin+'/bakney/v1/login-start?'+new URLSearchParams({protocol,pairing_id:status.data.pairing_id,attempt_id:crypto.randomUUID()}));
    await expect(page.getByRole('alert')).toBeVisible();
    assert(await page.evaluate(()=>localStorage.getItem('sessionToken'))===retained,'Removal replaced an established session');
    checks.push('Regeneration re-pairs successfully; removal immediately disables local acceptance and authenticated retry revokes Bakney');
    await normal.close();await context.close();
    const scope=real?'Real Assozeta backend/built UI + real Bakney Django backend, pairing components, transport and handoff.html; isolated data and fixture private-DNS allowance with verified TLS. Bakney full dashboard startup is outside this component harness.':'Real Assozeta backend/built UI + simulated Bakney contract peer; not cross-repository verification.';
    await fs.writeFile(path.join(output,'report.json'),JSON.stringify({checks,scope,sources},null,2));console.log(JSON.stringify({passed:checks.length,checks,scope,sources},null,2));
} finally {
    if(browser)await browser.close();
    for(const {name,processHandle}of containers.reverse())if(processHandle.exitCode===null)command(docker,['stop','-t','2',name]);
    for(const database of databases)command(docker,['exec',postgres,'sh','-c',`dropdb -U "$POSTGRES_USER" ${database}`]);
    for(const socket of proxySockets)socket.destroy();server?.closeAllConnections();if(server)await new Promise(r=>server.close(r));
    await fs.rm(temp,{recursive:true,force:true});
}
