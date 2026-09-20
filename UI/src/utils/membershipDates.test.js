import {test} from 'node:test';
import assert from 'node:assert/strict';
import {membershipDisplayDate, membershipBillingDates} from './membershipDates.js';

test('untouched Italian defaults never become dates in 2001', () => {
    const from = membershipDisplayDate(undefined, '01/09/2026');
    assert.equal(membershipBillingDates({from, until:'31/08/2027', seasonal:true}).billed_from, '2026-09-01');
});
test('editing preserves custom saved dates and clearing never restores defaults', () => {
    assert.equal(membershipDisplayDate('2026-11-15', '31/08/2027'), '15/11/2026');
    for (const value of [null, '', '2026-02-30', '01/09/2026']) assert.equal(membershipDisplayDate(value, '01/09/2026'), '');
});
test('strict dates, complete ranges, and durations are required', () => {
    for (const from of ['', '2026-09-01', '31/02/2026']) assert.throws(() => membershipBillingDates({from, seasonal:false, frequency:1}));
    for (const until of ['', '31/02/2027', '01/08/2026']) assert.throws(() => membershipBillingDates({from:'01/09/2026', until, seasonal:true}));
    for (const frequency of [null, 0, 13, 'bad']) assert.throws(() => membershipBillingDates({from:'01/09/2026', seasonal:false, frequency}));
});
test('monthly dates clamp to the last day and accept leap years', () => {
    assert.deepEqual(membershipBillingDates({from:'31/01/2028', seasonal:false, frequency:1}),
        {billed_from:'2028-01-31', billed_until:'2028-02-29', billed_frequency:1});
});
