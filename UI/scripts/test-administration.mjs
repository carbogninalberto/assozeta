// Isolated UI fixture: real navigation, API middleware and identity-switching
// components; all API data is generated here, with no live backend access.
import assert from 'node:assert/strict';
import path from 'node:path';
import fs from 'node:fs';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {createServer} from 'vite';
import {svelte} from '@sveltejs/vite-plugin-svelte';
const ui = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const {chromium, expect} = await import(pathToFileURL(path.join(ui, '../selfhost/tests/browser/node_modules/@playwright/test/index.mjs')));
const users = [
    {user_id:'owner',username:'TEST-ASSOCIATION',email:'owner@example.test',role:'association',first_name:'Test',last_name:'Owner',association:{id:'club',name:'Test Club'}},
    {user_id:'collaborator',username:'TEST-COLLABORATOR',email:'collaborator@example.test',role:'collaborator',first_name:'Test',last_name:'Collaborator',association:{id:'club',name:'Test Club'}},
    {user_id:'athlete',username:'TEST-ATHLETE',email:'athlete@example.test',role:'athlete',first_name:'Test',last_name:'Athlete',association:null},
];
const sessions = new Map();
const requests = [];
let counter = 0;
const config = {build:{VERSION:'test-unstable'},VERSION_UI:'test-unstable',OEM_CONFIG:{name:'Test',selfHosted:true,displaySettings:{}},env:{HOST:'/api',DOMAIN:'',API:{
    OAUTH2:{LOGIN:'/api/login',SIGNUP:'/api/signup',RESET:'/api/reset'},BILLING:{ACTIVE_PLAN:'/api/billing'},PROFILE:{INFO:'/api/profile/info'},
}}};
const entry = `
import Sidebar from '/src/components/Sidebar.svelte';
import ProfileMenu from '/src/routes/profile/ProfileMenu.svelte';
import Picker from '/src/routes/tools/SportAssociationsManager.svelte';
import {role,userData,permissions,currentPage} from '/src/store/stores.js';
import {apiFetch} from '/src/utils/ApiMiddleware.js';
import {readImpersonation,observeIdentityChanges} from '/src/utils/impersonation.js';
role.useLocalStorage();userData.useLocalStorage();permissions.useLocalStorage();currentPage.useLocalStorage();
observeIdentityChanges();
const result=await apiFetch('/api/profile/info',{skipForbidden:true});
if(!result.error){role.set(result.response.info.role);userData.set(result.response.user_data);}
permissions.set([]);
setTimeout(()=>permissions.set(['association.dashboard.read','other.settings.read','other.settings.update']),150);
new Sidebar({target:document.getElementById('sidebar')});
if(readImpersonation())new ProfileMenu({target:document.getElementById('profile'),props:{instanceOwner:false}});
if(!readImpersonation())new Picker({target:document.getElementById('app')});
else {const heading=document.createElement('h1');heading.textContent=result.error?'Sessione scaduta':result.response.user_data.username;document.getElementById('app').append(heading);}
`;
const server = await createServer({root:ui,configFile:false,logLevel:'error',
    define:{__bakney:JSON.stringify(config)},
    server:{host:'127.0.0.1',port:5204,strictPort:true},
    resolve:{alias:{utils:`${ui}/src/utils`,store:`${ui}/src/store`,components:`${ui}/src/components`,shim:`${ui}/src/shim`}},
    plugins:[{name:'administration-fixture',enforce:'pre',
        resolveId(id){if(id==='/fixture.js')return '\0fixture';},
        load(id){if(id==='\0fixture')return entry;},
        configureServer(vite){vite.middlewares.use(async(req,res,next)=>{
            const url=new URL(req.url,'http://fixture');
            if(url.pathname==='/'){
                res.setHeader('Content-Type','text/html');
                return res.end(await vite.transformIndexHtml('/',`<html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/bootstrap.min.css"><style>body{padding:16px}#sidebar{width:260px}#profile{max-width:400px}.position-fixed{position:relative!important}#portal-elements{position:fixed;top:0;left:0;z-index:1000}</style></head><body><div id="sidebar"></div><div id="profile"></div><main id="app"></main><div id="portal-elements"></div><script type="module" src="/fixture.js"></script></body></html>`));
            }
            if(!url.pathname.startsWith('/api/'))return next();
            let raw='';for await(const chunk of req)raw+=chunk;
            const body=raw?JSON.parse(raw):{};
            const session=req.headers['x-impersonation-id'];
            requests.push({path:url.pathname,method:req.method,session,target:req.headers['user-id'],authorization:req.headers.authorization});
            const send=(value,status=200)=>{res.statusCode=status;res.setHeader('Content-Type','application/json');res.end(status===204?'':JSON.stringify(value));};
            if(url.pathname==='/api/profile/info'){
                const target=sessions.get(session);
                if(session&&!target)return send({detail:'Sessione scaduta'},403);
                return send({info:{role:target?(target.role==='collaborator'?'association':target.role):'administrator'},user_data:target?{...target,collaborator_role:3,collaborator_permissions:['other.settings.read']}:{user_id:'admin',username:'ADMIN',is_superuser:true}});
            }
            if(url.pathname==='/api/administration/impersonation/users'){
                const query=(url.searchParams.get('query[generalSearch]')||'').toLowerCase(),filter=url.searchParams.get('query[role]');
                return send({users:users.filter(user=>(!filter||user.role===filter)&&JSON.stringify(user).toLowerCase().includes(query)),next_page:null,meta:{total:users.filter(user=>(!filter||user.role===filter)&&JSON.stringify(user).toLowerCase().includes(query)).length,pages:1}});
            }
            if(url.pathname==='/api/administration/impersonation'){
                if(req.method==='DELETE'){sessions.delete(session);return send(null,204);}
                const target=users.find(user=>user.user_id===body.target_user_id);
                if(!target)return send({detail:'Unknown target'},400);
                const id='fixture-session-'+(++counter);sessions.delete(session);sessions.set(id,target);
                return send({session_id:id,target,expires_in:3600},201);
            }
            if(url.pathname==='/api/billing')return send({data:{active_plan:{billing_type:3}}});
            send({detail:'Unexpected fixture API'},404);
        });},
    },svelte({configFile:false})]});
let browser;
try{
    await server.listen();browser=await chromium.launch({headless:true});
    for(const viewport of [{width:1440,height:1000},{width:390,height:844}]){
        const context=await browser.newContext({viewport});
        await context.addInitScript(()=>{
            if(!localStorage.getItem('sessionToken')){
                for(const [key,value]of Object.entries({sessionToken:'fixture-admin-token',refreshToken:'fixture-refresh',expires:Date.now()+3600000,role:'administrator',userData:{user_id:'admin',is_superuser:true},currentPage:'profile'}))localStorage.setItem(key,JSON.stringify(value));
            }
        });
        const page=await context.newPage(),errors=[];
        page.on('pageerror',error=>{errors.push(error.message);console.error(error.stack);});
        page.on('console',message=>{if(message.type()==='error')console.error(message.text());});
        await page.goto('http://127.0.0.1:5204/');
        await expect(page.getByRole('heading',{name:/^Impersona utenti/})).toBeVisible({timeout:30000});
        await expect(page.locator('.menu-nav a')).toHaveText(['Self Instance','Gestione Dati','Impersona utenti']);
        await expect(page.getByText('Sei in modalità superuser, attenzione!', {exact:true})).toHaveCount(0);
        for(const target of users){
            let picker = page;
            if(target.role === 'collaborator') {
                await page.locator('.identity-summary').click();
                await page.getByRole('button',{name:'Impersona un utente',exact:true}).click();
                picker = page.getByRole('dialog',{name:'Impersona utenti',exact:true});
                await expect(picker).toBeVisible();
            }
            await picker.getByRole('textbox',{name:'Cerca nella tabella'}).fill(target.username);
            await expect(picker.locator('.datatable-body .datatable-row')).toHaveCount(1);
            await page.evaluate(()=>{localStorage.setItem('selectedGroup','"old-tenant"');localStorage.setItem('bkn_datatable-1-meta','stale');});
            await picker.getByRole('button',{name:`Impersona ${target.username}`,exact:true}).click();
            await expect(page.getByRole('heading',{name:target.username,exact:true})).toBeVisible({timeout:30000});
            await expect(page.locator('.identity-summary')).toContainText(target.username);
            await expect(page.locator('.menu-nav').getByRole('link',{name:'Bacheca',exact:true})).toBeVisible();
            await expect(page.locator('#bkn_profile_aside')).not.toContainText('Self Instance');
            assert.equal(await page.evaluate(()=>localStorage.getItem('bkn_datatable-1-meta')),null);
            assert.equal(await page.evaluate(()=>localStorage.getItem('selectedGroup')),'null');
            assert.equal(await page.evaluate(()=>JSON.parse(localStorage.getItem('sessionToken'))),'fixture-admin-token');
            const profile=requests.filter(request=>request.path==='/api/profile/info').at(-1);
            assert.equal(profile.target,target.user_id);assert.ok(profile.session);
            await page.locator('.identity-summary').click();
            await page.getByRole('button',{name:'Torna all’amministrazione'}).click();
            await expect(page.getByRole('heading',{name:/^Impersona utenti/})).toBeVisible({timeout:30000});
            await expect(page.locator('.menu-nav a')).toHaveText(['Self Instance','Gestione Dati','Impersona utenti']);
            assert.equal(sessions.size,0);
        }
        assert.deepEqual(errors,[]);
        const output=path.join(ui,'../quality-reports/administration');fs.mkdirSync(output,{recursive:true});
        await page.screenshot({path:path.join(output,`administrator-${viewport.width}.png`),fullPage:true});
        await context.close();
    }
    console.log('PASS: minimal administrator navigation, searchable targets, association/user/collaborator switching, visible identity, cache reset and administrator restoration on desktop/mobile.');
}finally{await browser?.close();await server.close();}
