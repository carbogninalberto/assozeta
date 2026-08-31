import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';


const modalSource = readFileSync(
    new URL('./modals/EditCalendarEvent.svelte', import.meta.url),
    'utf8',
);
const calendarSource = readFileSync(new URL('./Calendar.svelte', import.meta.url), 'utf8');


test('all four delete actions use one modal-owned deletion lifecycle', () => {
    assert.equal((modalSource.match(/requestDelete\(/g) || []).length, 5);
    assert.doesNotMatch(modalSource, /deleteEvent\([^;]*;\s*closeModal\(\)/s);

    const deleteButtons = [...modalSource.matchAll(/<button[\s\S]*?>[\s\S]*?<\/button>/g)]
        .map(match => match[0])
        .filter(button => button.includes('requestDelete('));
    assert.equal(deleteButtons.length, 4);
    deleteButtons.forEach(button => assert.doesNotMatch(button, /data-dismiss="modal"/));
});

test('the modal closes through hidden.bs.modal before the parent destroys it', () => {
    assert.match(modalSource, /hiddenHandler = \(\) => \{[\s\S]*dispatch\('close'\)[\s\S]*};/);
    assert.match(modalSource, /addEventListener\('hidden\.bs\.modal', hiddenHandler\)/);
    assert.doesNotMatch(calendarSource, /if \(!response\.error\)[\s\S]{0,500}editEventModal\.\$destroy\(\)/);
});

test('successful deletion awaits exactly one calendar reload', () => {
    const deleteHandler = calendarSource.match(
        /editEventModal\.\$on\('delete',[\s\S]*?\n\s*}\);\n\s*}\);/,
    )?.[0] || '';

    assert.equal((deleteHandler.match(/await initPage\(\)/g) || []).length, 1);
    assert.doesNotMatch(deleteHandler, /calendar\.removeEventById/);
});
