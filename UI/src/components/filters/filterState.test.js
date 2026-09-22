import test from 'node:test';
import assert from 'node:assert/strict';
import {copyFilter, resetFilter, validateFilter, hasFilterValue, isFilterActive, restoreStoredFilters} from './filterState.js';

test('editing and resetting a draft never changes applied options or operation callbacks', () => {
    const operation = () => {};
    const applied = {type: 'tags', active: true, items: [{onClick: operation}], data: {and: true, options: [{tag_id: 3, checked: true}]}};
    const draft = copyFilter(applied);
    draft.data.options[0].checked = false;
    draft.data.and = false;
    assert.equal(applied.data.options[0].checked, true);
    assert.equal(applied.data.and, true);
    assert.equal(draft.items[0].onClick, operation);
    const reset = resetFilter(applied);
    assert.equal(reset.active, false);
    assert.equal(reset.data.and, false);
    assert.equal(reset.data.options[0].checked, false);
    assert.equal(applied.active, true);
});

test('removing an age, date, radio or checkbox filter clears hidden values', () => {
    assert.deepEqual(resetFilter({type:'age', data:{from_age:0,to_age:12}}).data, {from_age:null,to_age:null});
    assert.deepEqual(resetFilter({type:'date-range', data:{from_date:'2026-01-01',to_date:'2026-02-01'}}).data, {from_date:'',to_date:''});
    assert.equal(resetFilter({type:'radio',value:false}).value, '');
    assert.equal(resetFilter({type:'checkbox',data:{options:[{value:'x',checked:true}]}}).data.options[0].checked,false);
});

test('zero is a valid age bound; invalid, inverted and fractional ages are rejected', () => {
    assert.equal(hasFilterValue(0), true);
    assert.equal(hasFilterValue(false), true);
    assert.equal(hasFilterValue(null), false);
    for (const [from_age,to_age] of [[0,0],[0,12],[null,0],['',120]]) {
        assert.equal(validateFilter({type:'age',data:{from_age,to_age}}),'');
    }
    for (const [from_age,to_age] of [[12,0],[-1,20],[0,121],[0.5,12],['bad',10]]) {
        assert.notEqual(validateFilter({type:'age',data:{from_age,to_age}}),'');
    }
});

test('date filters reject impossible and inverted dates but accept open ranges', () => {
    for (const data of [{from_date:'31/02/2026'}, {from_date:'2026-10-01',to_date:'30/09/2026'}]) {
        assert.notEqual(validateFilter({type:'date-range',data}), '');
    }
    for (const data of [{from_date:'29/02/2024'}, {to_date:'2026-09-30'}, {}]) {
        assert.equal(validateFilter({type:'date-range',data}), '');
    }
});

test('active state reflects effective values, including zero and false', () => {
    assert.equal(isFilterActive({type:'age',data:{from_age:0}}),true);
    assert.equal(isFilterActive({type:'radio',value:false}),true);
    assert.equal(isFilterActive({type:'tags',data:{and:true,options:[]}}),false);
    assert.equal(isFilterActive({type:'date-range',data:{from_date:'',to_date:null}}),false);
});


test('restoring list controls retains zero, tag selections and matching mode after navigation', () => {
    const controls = [{type:'age',data:{}}, {type:'tags',data:{options:[{tag_id:1},{tag_id:2}]}}];
    const [age,tags] = restoreStoredFilters(controls, {from_age:0,to_age:12,tags:'2',tags_and:1});
    assert.equal(age.active,true);
    assert.equal(age.data.from_age,0);
    assert.equal(tags.active,true);
    assert.equal(tags.data.and,true);
    assert.deepEqual(tags.data.options.map(option=>option.checked),[false,true]);
    assert.equal(controls[1].data.options[1].checked,undefined);
    const reset = restoreStoredFilters(controls, {tags:[],tags_and:0,from_age:null,to_age:null});
    assert.equal(reset[0].active,false);
    assert.equal(reset[1].active,false);
});
