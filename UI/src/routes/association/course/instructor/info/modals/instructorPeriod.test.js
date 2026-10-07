import test from 'node:test';
import assert from 'node:assert/strict';
import {initializeInstructorPeriod} from './instructorPeriod.js';

test('reopening a stored percentage entry restores both picker dates without changing accounting data', () => {
    const saved = {compensation_type: 'percentage', period: '01/02/2028 al 29/02/2028',
        amount: 24, percentage_billing: 20, paid: false, courses: ['course-1']};
    assert.deepEqual(initializeInstructorPeriod(saved), {...saved,
        period_start: '01/02/2028', period_end: '29/02/2028'});
    assert.equal(saved.period_start, undefined);
});

test('existing picker values survive reopening, including intentional blank values', () => {
    const row = {compensation_type: 'percentage', period: '01/02/2028 al 29/02/2028',
        period_start: '', period_end: '25/02/2028'};
    assert.deepEqual(initializeInstructorPeriod(row), row);
});

test('hourly, malformed, invalid and reversed periods are left untouched', () => {
    for (const row of [
        {compensation_type: 'hourly', period: '01/02/2028 al 29/02/2028'},
        {compensation_type: 'percentage', period: null},
        ...['wrong', '01/02/2027 al 29/02/2027', '29/02/2028 al 01/02/2028',
            '01/02/2028 al 29/02/2028 al 01/03/2028'].map(period => ({compensation_type: 'percentage', period})),
    ]) assert.equal(initializeInstructorPeriod(row), row);
});
