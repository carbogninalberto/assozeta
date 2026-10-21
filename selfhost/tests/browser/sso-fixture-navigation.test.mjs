import assert from 'node:assert/strict';
import test from 'node:test';
import {openSSOFixture} from './sso-fixture-navigation.mjs';

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
