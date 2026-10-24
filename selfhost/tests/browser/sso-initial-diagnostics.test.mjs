import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {observeInitialPairing} from './sso-initial-diagnostics.mjs';

const secret = 'DO-NOT-RETAIN-session-code-or-personal-data';
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
    assert.deepEqual(report,{version:1,phase:'initial-pairing',route:'profile',button:{count:1,visible:true,enabled:false},messages:[],responses:{'/api/instance/access':403,'/api/instance/admin/bakney-pairing':500},failedRequests:{'/api/instance/admin':1},pageErrors:{TypeError:1,Other:1}});
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
    assert.deepEqual(report.pageErrors,{Error:1000});assert.deepEqual(report.responses,{});assert.deepEqual(report.messages,[]);
    assert(!JSON.stringify(report).includes(secret));diagnostics.stop();
});

test('visible diagnostic messages are recorded as fixed identifiers',async()=>{
    const page=new Page(),diagnostics=observeInitialPairing(page);
    page.getByText=(text)=>({first:()=>({isVisible:async()=>text==='Impossibile leggere il collegamento con Bakney.'})});
    assert.deepEqual((await diagnostics.snapshot()).messages,['pairing-unavailable']);diagnostics.stop();
});
