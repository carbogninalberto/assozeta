import test from 'node:test';
import assert from 'node:assert/strict';
import {createUserContextLoader} from './userContext.js';
const deferred = () => {let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve};};
const store = value => ({value,set(next){this.value=next;}});
function fixture(accountRole='association') {
    const profile = deferred();
    const state = {userData:store({}),billingData:store(null),permissions:store([]),role:store(null)};
    let profileCalls=0,billingCalls=0;
    const loader=createUserContextLoader({...state,
        fetchProfile:()=>{profileCalls++;return profile.promise;},
        fetchBilling:()=>{billingCalls++;return Promise.resolve({error:true,status:410});},
        setPermissions:role=>{
            assert.equal(role,accountRole);
            state.permissions.set(state.userData.value.collaborator_permissions || ['association.dashboard.read']);
        },
    });
    return {loader,profile,state,calls:()=>({profileCalls,billingCalls}),
        readyProfile:data=>profile.resolve({error:false,response:{info:{role:accountRole},user_data:{user_id:'selected-account',...data}}})};
}
test('concurrent startup publishes complete permissions without requesting billing',async()=>{
    const f=fixture();const first=f.loader(),second=f.loader();
    assert.equal(first,second);
    f.state.role.set=function(value){
        assert.equal(f.state.userData.value.user_id,'selected-account');
        assert.deepEqual(f.state.permissions.value,['association.dashboard.read']);
        this.value=value;
    };
    f.readyProfile();await first;
    assert.deepEqual(f.calls(),{profileCalls:1,billingCalls:0});
    assert.equal(f.state.role.value,'association');
});
test('collaborator routing retains selected collaborator permissions without a plan',async()=>{
    const f=fixture();const pending=f.loader();
    f.readyProfile({collaborator_permissions:['association.calendar.read']});await pending;
    assert.deepEqual(f.state.permissions.value,['association.calendar.read']);
});
test('failed profile does not publish an incomplete identity',async()=>{
    const f=fixture();const pending=f.loader();f.profile.resolve({error:true,status:503});
    assert.equal((await pending).error,true);
    assert.equal(f.state.role.value,null);
    assert.deepEqual(f.state.userData.value,{});
    assert.equal(f.calls().billingCalls,0);
});
for(const role of ['administrator','athlete'])test(`${role} clears tenant permissions without billing`,async()=>{
    const f=fixture(role);f.state.permissions.set(['association.dashboard.read']);
    const pending=f.loader();f.readyProfile();await pending;
    assert.equal(f.calls().billingCalls,0);
    assert.equal(f.state.role.value,role);
    assert.deepEqual(f.state.permissions.value,[]);
});
