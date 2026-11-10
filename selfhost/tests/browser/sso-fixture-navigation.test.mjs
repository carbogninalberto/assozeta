import assert from 'node:assert/strict';
import test from 'node:test';
import {openSSOFixture,readHostNetwork,waitForStableHostNetwork} from './sso-fixture-navigation.mjs';

const base='https://club.assozeta.test';
const network=()=>new Error('page.goto: net::ERR_NETWORK_CHANGED at '+base+'/fixture-blank.html');
test('successful blank fixture navigation runs once',async()=>{
    const calls=[];const result={ok:true};
    assert.equal(await openSSOFixture({goto:async url=>{calls.push(url);return result;}},base,async()=>assert.fail('unexpected wait')),result);
    assert.deepEqual(calls,[base+'/fixture-blank.html']);
});
test('a network change repeats only the blank GET once',async()=>{
    const calls=[],waits=[];const result={ok:true};
    const page={goto:async url=>{calls.push(url);if(calls.length===1)throw network();return result;}};
    assert.equal(await openSSOFixture(page,base,async ms=>waits.push(ms)),result);
    assert.deepEqual(calls,[base+'/fixture-blank.html',base+'/fixture-blank.html']);assert.deepEqual(waits,[250]);
});
test('a repeated network change propagates the last error',async()=>{
    const failures=[network(),network()];let calls=0;
    await assert.rejects(openSSOFixture({goto:async()=>{throw failures[calls++];}},base,async()=>{}),error=>error===failures[1]);
    assert.equal(calls,2);
});
test('other navigation failures and closed pages are never retried',async()=>{
    for(const message of ['Timeout 30000ms exceeded','net::ERR_NAME_NOT_RESOLVED','net::ERR_CONNECTION_REFUSED','Target page has been closed','net::ERR_NETWORK_CHANGED_EXTRA']){
        const error=new Error(message);let calls=0;
        await assert.rejects(openSSOFixture({goto:async()=>{calls++;throw error;}},base,async()=>assert.fail('unexpected wait')),value=>value===error);
        assert.equal(calls,1);
    }
});
test('a different failure after the first network change is propagated',async()=>{
    const failure=new Error('net::ERR_CONNECTION_REFUSED');let calls=0;
    await assert.rejects(openSSOFixture({goto:async()=>{if(++calls===1)throw network();throw failure;}},base,async()=>{}),error=>error===failure);
    assert.equal(calls,2);
});

// A fake clock: each wait advances it, reads return scripted host states.
function clock(states){let time=0,reads=0;return {now:()=>time,wait:async ms=>{time+=ms;},read:()=>states[Math.min(reads++,states.length-1)],reads:()=>reads};}
const settled='1: lo: <LOOPBACK,UP>\n2: eth0 inet6 fe80::1/64 scope link';
test('the browser waits for a full quiet window without tentative addresses',async()=>{
    const c=clock([settled+' tentative',settled+' tentative',settled]);
    assert.equal(await waitForStableHostNetwork(c.read,{now:c.now,wait:c.wait,quietMs:1000,intervalMs:250}),true);
    assert.ok(c.now()>=1500,'returned before the quiet window after the last change');
});
test('a new interface restarts the quiet window',async()=>{
    const c=clock([settled,settled,settled+'\n3: veth0 inet6 fe80::2/64 scope link',settled+'\n3: veth0 inet6 fe80::2/64 scope link']);
    await waitForStableHostNetwork(c.read,{now:c.now,wait:c.wait,quietMs:1000,intervalMs:250});
    assert.ok(c.now()>=1500);
});
test('a network that never settles or becomes unreadable fails the run',async()=>{
    let n=0;const changing=()=>settled+String(n++);const c=clock([]);
    await assert.rejects(waitForStableHostNetwork(changing,{now:c.now,wait:c.wait,quietMs:1000,timeoutMs:5000}),/did not settle/);
    const t=clock([settled+' tentative']);
    await assert.rejects(waitForStableHostNetwork(t.read,{now:t.now,wait:t.wait,quietMs:1000,timeoutMs:5000}),/did not settle/);
    const u=clock([settled,null]);
    await assert.rejects(waitForStableHostNetwork(u.read,{now:u.now,wait:u.wait,quietMs:1000}),/unreadable/);
});
test('hosts without the ip tool skip the wait',async()=>{
    assert.equal(readHostNetwork(()=>({status:null,error:new Error('ENOENT')})),null);
    assert.equal(await waitForStableHostNetwork(()=>null,{wait:async()=>assert.fail('unexpected wait')}),false);
});
test('address lifetimes are ignored, state changes are kept',()=>{
    const run=(lft,state)=>(_cmd,args)=>({status:0,stdout:args[1]==='link'?`2: eth0: <UP> state ${state}\n`:`2: eth0 inet 10.1.0.4/16 scope global eth0\\       valid_lft ${lft}sec preferred_lft ${lft}sec\n`});
    assert.equal(readHostNetwork(run(100,'UP')),readHostNetwork(run(99,'UP')));
    assert.notEqual(readHostNetwork(run(100,'UP')),readHostNetwork(run(100,'DOWN')));
});
