import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {parse} from 'svelte/compiler';
import {serializeCalendarEvents} from '../../utils/eventCalendar.js';

const source = fs.readFileSync(new URL('./Calendar.svelte', import.meta.url), 'utf8');
const fn = parse(source).instance.content.body.find(n => n.type === 'FunctionDeclaration' && n.id.name === 'saveCalendarDirectly');
function fixture(error = false) {
    const requests = [];
    let refreshes = 0;
    const context = vm.createContext({serializeCalendarEvents,
        getClassNameFromEvent: () => 'ec-event-solid-primary',
        UiApp: {blockPage() {}, unblockPage() {}},
        apiFetch: async (url, options) => {requests.push({url, ...JSON.parse(options.body)}); return {error};},
        replaceUID: (url, id) => url.replace('{uid}', id),
        __bakney: {env: {API: {CALENDAR: {UPDATE: '/global'}, COURSE: {CALENDAR_UPDATE: '/course/{uid}'}}}},
        calendar: {refetchEvents() {refreshes++;}}, fetchInstructors: async () => {},
        toast: {success() {}, error() {}},
    });
    vm.runInContext(source.slice(fn.start, fn.end), context);
    return {save: context.saveCalendarDirectly, requests, refreshes: () => refreshes};
}
const events = ['first', 'second'].map(id => ({id, title:id, start:'2026-09-20T10:00:00Z', end:'2026-09-20T11:00:00Z'}));
for (const action of ['create', 'update']) test(`${action} submits only the selected global event`, async () => {
    const f = fixture(); await f.save(events, null, action, 'second');
    assert.equal(f.requests[0].action, action);
    assert.deepEqual(f.requests[0].events.map(e => e.event_id), ['second']);
});
test('global deletion is explicit and never a replacement snapshot', async () => {
    const f = fixture(); await f.save([], null, 'delete', 'second');
    assert.deepEqual(f.requests[0], {url:'/global', action:'delete', event_id:'second', events:[]});
});
test('course writes keep the existing publication contract', async () => {
    const f = fixture(); await f.save(events, 'course-id');
    assert.equal(f.requests[0].url, '/course/course-id');
    assert.equal(f.requests[0].events.length, 2);
    assert.equal(f.requests[0].status, 2);
});
test('failed optimistic mutations restore server state', async () => {
    const f = fixture(true); await f.save(events, null, 'update', 'second');
    assert.equal(f.refreshes(), 1);
});
