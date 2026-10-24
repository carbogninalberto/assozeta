// The unchanged compiled UI performs all identity changes and real logins.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
const input = JSON.parse(await fs.readFile(process.env.TESTS_FIXTURE, 'utf8'));
const output = process.env.TESTS_OUTPUT;
await fs.mkdir(output, {recursive: true});
const contexts = [], pending = [], responses = [], failures = [], checks = [];
const report = {success: false, browser_created: false, contexts_closed: false, browser_closed: false, checks, responses, failures};
let browser = null, chromium, expect;
let phase = 'start', sequence = 0, activePage;
const selected = input.version === 'v1' ? 'django-v1' : 'go-v2';
const digest = value => createHash('sha256').update(value).digest('hex');
const deadline = setTimeout(() => {failures.push({kind:'deadline',phase}); if(browser) void browser.close().catch(error=>failures.push({kind:'deadline-close',error_class:error.name}));}, 240000);
async function save() { await fs.writeFile(path.join(output,'results.json'),JSON.stringify(report,null,2)); }
async function checkpoint(actor, action, context = null) {
    const id = ++sequence;
    const file = path.join(input.checkpoints, `${id}.json`);
    await fs.writeFile(file+'.pending', JSON.stringify({id,actor,action,context}), {mode:0o600});
    await fs.rename(file+'.pending',file);
    const ack = path.join(input.checkpoints, `${id}.ack.json`);
    await expect.poll(async () => {try {return JSON.parse(await fs.readFile(ack,'utf8'));} catch {return null;}}, {timeout:30000}).not.toBeNull();
    assert.equal(JSON.parse(await fs.readFile(ack,'utf8')).success,true,'Durable checkpoint failed');
    checks.push({actor,action,checkpoint:id});
}
async function record(response) {
    const url = new URL(response.url());
    if (url.origin !== input.origin) return;
    const row = {path:url.pathname,method:response.request().method(),status:response.status(),backend:response.headers()['x-fixture-backend'] || null};
    responses.push(row);
    if (input.selected_routes.includes(url.pathname.slice(5))) {
        assert.equal(row.backend,selected);
        assert([200,201,204,401,403].includes(row.status), 'Selected request failed');
    } else if (input.coexistence_routes.includes(url.pathname.slice(5))) {
        assert.equal(row.backend,'django-coexistence');
        assert([200,403,404].includes(row.status),'Coexistence request failed');
    } else {
        assert(!url.pathname.startsWith('/api/'),'Unknown API dependency');
        assert(row.status >= 200 && row.status < 400, 'Static asset failed');
    }
}
async function profile(page) {
    return page.evaluate(async () => {
        const response = await fetch('/api/profile/info');
        return {status:response.status,body:await response.json()};
    });
}
async function identity(page, person, actor, token) {
    await expect(page.locator('#bkn_aside')).toBeVisible();
    await expect.poll(async () => {
        try {return await page.evaluate(() => JSON.parse(localStorage.getItem('userData') || '{}').user_id);} catch {return null;}
    }).toBe(person.id);
    const actual = await profile(page);
    assert.equal(actual.status,200); assert.equal(actual.body.user_data.user_id,person.id);
    assert.equal(Boolean(actual.body.user_data.is_superuser),person.name === 'admin');
    assert.equal(await page.evaluate(() => localStorage.getItem('sessionToken')),token.access,'Actor access credential changed');
    assert.equal(await page.evaluate(() => localStorage.getItem('refreshToken')),token.refresh,'Actor refresh credential changed');
    const context = await page.evaluate(() => JSON.parse(localStorage.getItem('impersonationContext') || 'null'));
    if (person.name === actor) {
        assert.equal(context,null);
        await expect(page.locator('.identity-summary')).toContainText('Gestisci accessi');
    } else {
        assert.equal(context.target.user_id,person.id);
        await expect(page.locator('.identity-summary')).toContainText(person.username);
        assert.equal(context.actor_role,actor === 'owner' ? 'association' : 'administrator');
    }
    if (person.name === 'staff') {
        assert.deepEqual(actual.body.user_data.collaborator_permissions,['bookeeping.payments.read']);
        await expect(page.locator('#bkn_aside a[href="/#/payment/list"]')).toBeVisible();
        await expect(page.locator('#bkn_aside a[href="/#/members/list"]')).toHaveCount(0);
        await expect(page.locator('#bkn_aside a[href="/#/calendar"]')).toHaveCount(0);
    }
    return context;
}
async function picker(page, actor) {
    const summary = page.locator('.identity-summary');
    if (await summary.getAttribute('aria-expanded') !== 'true') await summary.click();
    const initialResponse = page.waitForResponse(response => {
        const url = new URL(response.url());
        return url.pathname.endsWith('/impersonation/users') && !url.searchParams.get('query[generalSearch]') && !url.searchParams.get('query[role]');
    });
    await page.getByRole('button',{name:/^(Impersona un utente|Cambia utente)$/}).click();
    const modal = page.locator('#admin-impersonation-modal');
    await expect(modal).toBeVisible();
    const response = await initialResponse; assert.equal(response.status(),200);
    const initial = await response.json();
    const names = actor === 'owner' ? ['athlete','staff'] : ['owner','athlete','staff','foreign','foreign-athlete'];
    const ids = names.map(name=>input.people[name].id).sort();
    assert.deepEqual(initial.users.map(user=>user.user_id).sort(),ids);
    assert.equal(initial.meta.total,ids.length); assert.equal(initial.next_page,null);
    await expect(modal.locator('[data-row]')).toHaveCount(names.length);
    for (const name of names) await expect(modal.getByRole('button',{name:'Impersona '+input.people[name].username,exact:true})).toBeVisible();
    checks.push({actor,action:'picker-exact-eligible-ids',user_ids:ids});
    const options = await modal.locator('#impersonation-modal-role').locator('option').evaluateAll(rows => rows.map(row=>row.value));
    assert.deepEqual(options, actor === 'owner' ? ['','athlete','collaborator'] : ['','association','athlete','collaborator']);
    return modal;
}
async function choose(page,actor,target,filterCheck=false) {
    const modal = await picker(page,actor);
    if (filterCheck && actor === 'owner') {
        const search = modal.getByRole('textbox',{name:'Cerca nella tabella',exact:true});
        const denied = page.waitForResponse(response => response.url().includes('/impersonation/users') && new URL(response.url()).searchParams.get('query[generalSearch]') === input.people['foreign-athlete'].username);
        await search.fill(input.people['foreign-athlete'].username);
        const data = await (await denied).json(); assert.deepEqual(data.users,[]);
        await expect(modal.locator('[data-row]')).toHaveCount(0);
        checks.push({actor,action:'foreign-athlete-excluded',target:input.people['foreign-athlete'].id});
    }
    await modal.locator('#impersonation-modal-role').selectOption(target.role);
    await modal.getByRole('textbox',{name:'Cerca nella tabella',exact:true}).fill(target.username);
    const row = modal.locator('[data-row]').filter({hasText:target.username});
    await expect(row).toBeVisible();
    const response = page.waitForResponse(r => new URL(r.url()).pathname.endsWith('/impersonation') && r.request().method()==='POST');
    await row.getByRole('button',{name:'Impersona '+target.username}).click();
    const changed = await response; assert.equal(changed.status(),201);
    // The UI consumes this body and immediately reloads. Read back the actual
    // persisted identity after navigation instead of racing CDP response-body eviction.
    return;
}
try {
    await save();
    phase='browser-launch';
    const playwright=await import(pathToFileURL(input.playwright_module).href);
    chromium=playwright.chromium;expect=playwright.expect.configure({timeout:25000});
    browser=await chromium.launch();report.browser_created=true;
    assert.equal(process.env.PLAYWRIGHT_BROWSERS_PATH || null,input.browser_cache_path);
    if(input.browser_cache_path && input.browser_cache_path !== '0') {
        const relative=path.relative(path.resolve(input.browser_cache_path),chromium.executablePath());
        assert(relative && !relative.startsWith('..') && !path.isAbsolute(relative),'Browser ignored invocation cache');
    }
    report.browser_cache_matches_invocation=true;
    for (const actor of ['owner','admin']) {
        phase = actor+'-login';
        const context = await browser.newContext({viewport:{width:1440,height:1000}}); contexts.push(context);
        await context.route('**/*', route => new URL(route.request().url()).origin === input.origin ? route.continue() : route.abort('blockedbyclient'));
        const page = await context.newPage(); activePage=page; page.setDefaultTimeout(25000);
        page.on('response',r=>pending.push(record(r).catch(()=>failures.push({kind:'response',phase,path:new URL(r.url()).pathname,status:r.status()}))));
        page.on('pageerror',error=>failures.push({kind:'pageerror',phase,error_class:error.name}));
        await page.goto(input.origin+'/#/login');
        await expect(page.locator('input[name="username_login"]')).toBeVisible();
        assert.equal(await page.evaluate(()=>localStorage.getItem('sessionToken')),null);
        await page.locator('input[name="username_login"]').fill(input.people[actor].username);
        await page.locator('input[name="password_login"]').fill(input.password);
        const login = page.waitForResponse(r=>new URL(r.url()).pathname==='/api/oauth2/login' && r.request().method()==='POST');
        await page.locator('#bkn_login_signin_submit').click();
        assert.equal((await login).status(),200);
        await expect(page.locator('#bkn_aside')).toBeVisible();
        const notice=page.getByRole('button',{name:/Ok, grazie/}); if(await notice.isVisible()) await notice.click();
        const token = await page.evaluate(()=>({access:localStorage.getItem('sessionToken'),refresh:localStorage.getItem('refreshToken')}));
        assert(token.access && token.refresh); await identity(page,input.people[actor],actor,token);
        await checkpoint(actor,'login');
        if (actor==='admin') {
            phase=actor+'-owner';
            await choose(page,actor,input.people.owner);
            const current=await identity(page,input.people.owner,actor,token);
            await checkpoint(actor,'start',current);
        }
        phase=actor+'-athlete';
        await choose(page,actor,input.people.athlete,true);
        let current=await identity(page,input.people.athlete,actor,token);
        await checkpoint(actor,actor==='admin'?'change':'start',current);
        phase=actor+'-reload'; await page.reload();
        const reloaded=await identity(page,input.people.athlete,actor,token);
        assert.equal(reloaded.session_id,current.session_id);
        await checkpoint(actor,'reload',reloaded);
        phase=actor+'-staff'; await choose(page,actor,input.people.staff);
        current=await identity(page,input.people.staff,actor,token);
        await checkpoint(actor,'change',current);
        phase=actor+'-stop';
        if(await page.locator('.identity-summary').getAttribute('aria-expanded')!=='true') await page.locator('.identity-summary').click();
        await page.getByRole('button',{name:actor==='owner'?'Torna all’associazione':'Torna all’amministrazione',exact:true}).click();
        await identity(page,input.people[actor],actor,token); await checkpoint(actor,'stop');
        phase=actor+'-expiry'; await choose(page,actor,input.people.athlete);
        current=await identity(page,input.people.athlete,actor,token); await checkpoint(actor,'start',current);
        await checkpoint(actor,'expire',current);
        // The real expired response drives the unchanged middleware recovery.
        await page.evaluate(()=>{void fetch('/api/profile/info').catch(()=>{});});
        await identity(page,input.people[actor],actor,token); await checkpoint(actor,'recovered');
        checks.push({actor,action:'credentials-unchanged',access_token_sha256:digest(token.access),refresh_token_sha256:digest(token.refresh)});
        await context.close();
    }
    await Promise.all(pending); assert.deepEqual(failures,[]); report.success=true;
} catch(error) {
    report.error={phase,error_class:error.name,message:String(error.message).replace(/eyJ[A-Za-z0-9_.-]+/g,'<redacted>')};
    try {
        report.debug=await activePage.evaluate(()=>{const data=JSON.parse(localStorage.getItem('userData')||'{}');return {hash:location.hash,user_id:data.user_id,requires_welcome:data.requires_welcome,dashboard_layout:data.dashboard_layout,role:localStorage.getItem('role'),body:document.body.innerText.slice(0,6000)};});
        await activePage.screenshot({path:path.join(output,'failure.png'),fullPage:true});
    } catch {}
    process.exitCode=1;
} finally {
    clearTimeout(deadline);
    try {
        for(const context of contexts) {try{await context.close();}catch(error){failures.push({kind:'context-close',error_class:error.name});}}
        report.contexts_closed=!failures.some(row=>row.kind==='context-close');
        try {if(browser)await browser.close();report.browser_closed=true;report.contexts_closed=true;} catch(error){failures.push({kind:'browser-close',error_class:error.name});}
        await Promise.allSettled(pending);
    } finally {
        report.success=report.success && failures.length===0 && report.contexts_closed && report.browser_closed;
        if(!report.success)process.exitCode=1;
        await save();
    }
}
