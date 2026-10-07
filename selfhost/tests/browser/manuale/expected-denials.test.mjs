import test from 'node:test';
import assert from 'node:assert/strict';
import {reconcileBrowserDenials} from './expected-denials.mjs';
const failure = {path:'/api/course/owned/attendees/day/update',method:'POST',status:412,identity:'owner'};
const declaration = {...failure,observed_browser:true};
test('exact asserted rejection consumes one observed browser failure',()=>{
    assert.deepEqual(reconcileBrowserDenials([failure],[declaration]),{unexpected:[],unobserved:[]});
});
test('different path, method, status or actor remains unexpected',()=>{
    for(const changed of [{path:'/api/foreign'},{method:'GET'},{status:500},{identity:'reader'}]) {
        const result=reconcileBrowserDenials([{...failure,...changed}],[declaration]);
        assert.equal(result.unexpected.length,1);assert.equal(result.unobserved.length,1);
    }
});
test('duplicate, unasserted and declared-but-unobserved failures cannot pass',()=>{
    assert.equal(reconcileBrowserDenials([failure,failure],[declaration]).unexpected.length,1);
    assert.equal(reconcileBrowserDenials([failure],[{...failure,observed_browser:false}]).unexpected.length,1);
    assert.equal(reconcileBrowserDenials([],[declaration]).unobserved.length,1);
});
