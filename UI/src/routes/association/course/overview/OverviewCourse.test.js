import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const source = readFileSync(new URL('./OverviewCourse.svelte', import.meta.url), 'utf8');
const fetchBody = source.split('export let fetchData = async function () {')[1].split('\n    };')[0];
const toggleBody = source.split('function toggleEdit(e) {')[1].split('\n    }\n</script>')[0];
function fixture() {
    const pending = [];
    const apiFetch = url => new Promise(resolve => pending.push({url, resolve}));
    const create = new Function('apiFetch', `
        let overviewRequest=0, overviewLoaded=false, editingInfo=false, calendarStatus=1;
        let courseInfo={course:{title:'Original',fee:'90,00',one_fee:'0,00',events:[]}};
        const id='owned-course', $sessionToken='fixture';
        const __bakney={env:{API:{COURSE:{OVERVIEW:'overview',CALENDAR:'calendar'}}}};
        const replaceUID=url=>url, fetchRegistryData=()=>{};
        const fetchData=async function(){${fetchBody}};
        const toggleEdit=function(e){${toggleBody}};
        return {fetchData,edit:()=>toggleEdit({target:{blur(){}}}),
          title:value=>courseInfo.course.title=value,
          state:()=>({courseInfo,overviewLoaded,editingInfo})};
    `);
    return {app:create(apiFetch),pending};
}
const response = title => ({error:false,response:{data:{course:{title,fee:'90.00',one_fee:'0.00',creation_date:'2026-01-01',multiple_quotes:null,events:[]}}}});
const tick = () => new Promise(resolve => setImmediate(resolve));
async function finishCalendar(pending, index) {
    pending[index].resolve({error:false,response:{data:{status:1}}});
}

test('a late course read cannot overwrite an edit, even after edit mode closes', async () => {
    const {app,pending}=fixture();
    const load=app.fetchData();
    app.edit();app.title('Edited');app.edit();
    pending[0].resolve(response('Stale'));await tick();
    await finishCalendar(pending,1);await load;
    assert.equal(app.state().courseInfo.course.title,'Edited');
});

test('course fees are ready before editing while calendar status is still loading', async () => {
    const {app,pending}=fixture();const load=app.fetchData();
    pending[0].resolve(response('Loaded'));await tick();
    assert.equal(app.state().overviewLoaded,true);
    assert.equal(app.state().courseInfo.course.fee,'90,00');
    app.edit();app.title('Edited');
    await finishCalendar(pending,1);await load;
    assert.equal(app.state().courseInfo.course.title,'Edited');
    assert.equal(app.state().courseInfo.course.fee,'90,00');
});

test('an older overlapping course read cannot replace the latest response', async () => {
    const {app,pending}=fixture();const older=app.fetchData(),newer=app.fetchData();
    pending[1].resolve(response('Newest'));await tick();
    pending[0].resolve(response('Old'));await tick();
    await finishCalendar(pending,2);await finishCalendar(pending,3);
    await Promise.all([older,newer]);assert.equal(app.state().courseInfo.course.title,'Newest');
});
