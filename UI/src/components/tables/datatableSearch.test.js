import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {parse} from 'svelte/compiler';

const source = readFileSync(new URL('./BKNDatatable.svelte', import.meta.url), 'utf8');
const ast = parse(source);
function find(node, predicate) {
    if (!node || typeof node !== 'object') return null;
    if (predicate(node)) return node;
    for (const value of Object.values(node)) {
        for (const child of Array.isArray(value) ? value : [value]) {
            const result = find(child, predicate);
            if (result) return result;
        }
    }
    return null;
}

// Execute the actual component callbacks with their dependencies stubbed.
// This catches the old split between a DOM search value and empty Svelte state.
test('a prefiltered table initializes its displayed search before the first request', () => {
    const init = find(ast.instance, node => node.type === 'VariableDeclarator' && node.id?.name === 'loadDatatable').init;
    const calls = [];
    const context = vm.createContext({
        searchValue: '', columns: [], sortField: '', sortDirection: 'asc', datatable: null,
        getQueryParams: () => ({generalSearch: 'Corso nuoto'}),
        createDatatableController: () => ({}),
        loadRows: () => calls.push(context.searchValue), dispatch: () => {},
    });
    vm.runInContext(`(${source.slice(init.start, init.end)})()`, context);
    assert.equal(context.searchValue, 'Corso nuoto');
    assert.deepEqual(calls, ['Corso nuoto']);
});

test('a synthetic keyup reads the visible input instead of sending an empty stale filter', () => {
    const callback = find(ast.html, node => node.type === 'EventHandler' && node.name === 'keyup').expression;
    const searches = [];
    const context = vm.createContext({searchValue: '', debouncedSearch: value => searches.push(value)});
    context.event = {currentTarget: {value: 'Corso nuoto'}};
    vm.runInContext(`(${source.slice(callback.start, callback.end)})(event)`, context);
    assert.deepEqual(searches, ['Corso nuoto']);
    context.event.currentTarget.value = '';
    vm.runInContext(`(${source.slice(callback.start, callback.end)})(event)`, context);
    assert.deepEqual(searches, ['Corso nuoto', '']);
});
