import test from 'node:test';
import assert from 'node:assert/strict';
import {createUserContextLoader} from './userContext.js';
const deferred = () => {let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve};};
const store = value => ({value,set(next){this.value=next;}});
function fixture(accountRole='association') {
    const profile = deferred(), billing = deferred();
    const state = {userData:store({}),billingData:store(null),permissions:store([]),role:store(null)};
    let profileCalls=0,billingCalls=0;
    const loader=createUserContextLoader({...state,
        fetchProfile:()=>{profileCalls++;return profile.promise;},
        fetchBilling:()=>{billingCalls++;return billing.promise;},
        setPermissions:()=>state.permissions.set(state.userData.value.collaborator_permissions || ['association.dashboard.read']),
    });
    return {loader,profile,billing,state,calls:()=>({profileCalls,billingCalls}),
        readyProfile:data=>profile.resolve({error:false,response:{info:{role:accountRole},user_data:{user_id:'selected-account',...data}}}),
        readyBilling:()=>billing.resolve({error:false,response:{data:{active_plan:{billing_type:3}}}})};
}
test('concurrent startup hooks wait for one complete association context before routing',async()=>{
    const f=fixture();const first=f.loader(),second=f.loader();
    assert.equal(first,second);
    f.readyProfile();await Promise.resolve();
    assert.equal(f.state.role.value,null);
    assert.deepEqual(f.state.userData.value,{});
    f.state.role.set=function(value){
        assert.equal(f.state.userData.value.user_id,'selected-account');
        assert.equal(f.state.billingData.value.active_plan.billing_type,3);
        assert.deepEqual(f.state.permissions.value,['association.dashboard.read']);
        this.value=value;
    };
    f.readyBilling();await first;
    assert.deepEqual(f.calls(),{profileCalls:1,billingCalls:1});
    assert.equal(f.state.role.value,'association');
});
test('collaborator routing uses the selected collaborator permissions',async()=>{
    const f=fixture();const pending=f.loader();
    f.readyProfile({collaborator_permissions:['association.calendar.read']});f.readyBilling();await pending;
    assert.deepEqual(f.state.permissions.value,['association.calendar.read']);
});
test('failed billing does not expose a partially initialized association',async()=>{
    const f=fixture();const pending=f.loader();f.readyProfile();
    f.billing.resolve({error:true,status:503});
    assert.equal((await pending).error,true);
    assert.equal(f.state.role.value,null);
    assert.deepEqual(f.state.userData.value,{});
});
for(const role of ['administrator','athlete'])test(`${role} context needs no association billing and clears tenant permissions`,async()=>{
    const f=fixture(role);f.state.permissions.set(['association.dashboard.read']);
    const pending=f.loader();f.readyProfile();await pending;
    assert.equal(f.calls().billingCalls,0);
    assert.equal(f.state.role.value,role);
    assert.deepEqual(f.state.permissions.value,[]);
});
