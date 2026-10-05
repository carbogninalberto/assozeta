import {test} from 'node:test';
import assert from 'node:assert/strict';
import {focusRegion, unionFocusBounds} from './focus.mjs';
test('focus bounds retain every real control and intentional context padding', () => {
    assert.deepEqual(unionFocusBounds([{x:100,y:80,width:220,height:35},
        {x:120,y:160,width:250,height:44}]), {x:90,y:70,width:290,height:144});
    assert.throws(() => unionFocusBounds([null]), /not visible/);
    assert.throws(() => unionFocusBounds([{x:0,y:0,width:0,height:20}]), /not visible/);
    assert.throws(() => unionFocusBounds([]), /real controls/);
});
test('region measures current bounds after frame scroll/settling, not construction-time geometry', async () => {
    let moved = false;
    const locator = {scrollIntoViewIfNeeded: async () => {moved = true;},
        boundingBox: async () => moved ? {x:30,y:40,width:100,height:50} : null};
    const region = focusRegion(locator);
    await region.scrollIntoViewIfNeeded();
    assert.deepEqual(await region.boundingBox(), {x:20,y:30,width:120,height:70});
    assert.throws(() => focusRegion(), /real locators/);
});
test('long-control close-up trims unused trailing surface and fails for hidden controls', async () => {
    const {focusStart} = await import('./focus.mjs');
    const control = {scrollIntoViewIfNeeded: async () => {},
        boundingBox: async () => ({x:250,y:140,width:1640,height:45})};
    assert.deepEqual(await focusStart(control).boundingBox(), {x:250,y:140,width:640,height:45});
    assert.deepEqual(await focusStart(control,1800).boundingBox(), {x:250,y:140,width:1640,height:45});
    await assert.rejects(focusStart({...control,boundingBox:async()=>null}).boundingBox(), /not visible/);
    assert.throws(() => focusStart(control,0), /positive/);
});
