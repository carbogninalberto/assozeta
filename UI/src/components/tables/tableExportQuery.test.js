import test from 'node:test';
import assert from 'node:assert/strict';
import {tableExportQuery} from './tableExportQuery.js';

test('current-view export preserves applied search, false, zero, selected year and mandatory scope', () => {
    const table = {
        getDataSourceQuery: () => ({generalSearch:'A&B + C',current_year:'0',from_age:0,paid:false,empty:'',missing:null}),
        getDataSourceParam: key => key === 'sort' ? {field:'name',sort:'asc'} : null,
    };
    const query = new URLSearchParams(tableExportQuery(table,{course_id:'course-1',type:'athletes'}));
    assert.equal(query.get('query[generalSearch]'),'A&B + C');
    assert.equal(query.get('query[current_year]'),'0');
    assert.equal(query.get('query[from_age]'),'0');
    assert.equal(query.get('query[paid]'),'false');
    assert.equal(query.get('course_id'),'course-1');
    assert.equal(query.get('type'),'athletes');
    assert.equal(query.get('sort[field]'),'name');
    assert.equal(query.has('current_year'),false);
    assert.equal(query.has('query[empty]'),false);
    assert.equal(query.has('query[missing]'),false);
    assert.equal(query.has('pagination[page]'),false);
});
