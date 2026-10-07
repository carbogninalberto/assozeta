import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('./NotificationService.js', import.meta.url),'utf8')
    .replace(/^import .*;\n/gm,'').replace('export default notificationService;', 'globalThis.service = notificationService;');
function fixture(accountRole) {
    let calls=0, resets=0;
    const sandbox={role:{value:accountRole},get:store=>store.value,
        apiFetch:async()=>{calls++;return {error:false,response:{active:false}};},
        exportProgress:{reset:()=>resets++},console,
        __bakney:{env:{API:{ASSOCIATION:{EXPORT:{ACTIVE:'/export/active'}}}}}};
    vm.runInNewContext(source,sandbox);
    return {service:sandbox.service,calls:()=>calls,resets:()=>resets};
}
test('athlete notification connections do not request association export state',async()=>{
    const f=fixture('athlete');await f.service.syncActiveExport();assert.equal(f.calls(),0);
});
test('association connections still recover their export state',async()=>{
    const f=fixture('association');await f.service.syncActiveExport();assert.equal(f.calls(),1);assert.equal(f.resets(),1);
});
