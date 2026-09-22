import test from 'node:test';
import assert from 'node:assert/strict';
import {createMembershipDraft, membershipEndDate, validateMembershipForm, membershipApiError} from './membershipForm.js';

const monthly = {course_id: 'course', billed_from_subscription_date: true, billed_frequency: 1, fee: '25.00'};
const selected = [{value: 'athlete'}];
const user = {subscription_start_day: 1, subscription_start_month: 9};
const initial = () => createMembershipDraft({}, monthly, user, '2026-01-31');

test('unchanged monthly defaults produce real API dates and a non-null renewal day', () => {
    const {errors, billing} = validateMembershipForm(initial(), selected, monthly);
    assert.deepEqual(errors, {});
    assert.equal(billing.billed_from, '2026-01-31');
    assert.equal(billing.billed_until, '2026-02-28');
    assert.equal(billing.billed_from_day_of_month, 1);
    assert.equal(billing.membership_fee, '25.00');
});

test('changing dates retains fee, frequency, selected athletes and flags without mutating the record', () => {
    const source = {membership_fee: '25.00', auto_renewal: false};
    const draft = createMembershipDraft(source, monthly, user, '2026-01-31');
    Object.assign(draft, {membership_fee: '1.234,50', billed_frequency: 3, membership_active: false, auto_renewal: true});
    draft.billed_from = '2026-02-10';
    const {errors, billing} = validateMembershipForm(draft, selected, monthly);
    assert.deepEqual(errors, {});
    assert.equal(billing.membership_fee, '1234.50');
    assert.equal(billing.billed_until, '2026-05-10');
    assert.equal(billing.billed_frequency, 3);
    assert.equal(billing.membership_active, false);
    assert.equal(billing.auto_renewal, true);
    assert.deepEqual(selected, [{value: 'athlete'}]);
    assert.deepEqual(source, {membership_fee: '25.00', auto_renewal: false});
});

test('season defaults use the season containing today, even before September', () => {
    const info = {...monthly, billed_duration_is_sport_season: true};
    const draft = createMembershipDraft({}, info, user, '2026-02-10');
    assert.equal(draft.billed_from, '2025-09-01');
    assert.equal(draft.billed_until, '2026-08-31');
    assert.equal(validateMembershipForm(draft, selected, info).billing.billed_frequency, 12);
});

test('editing preserves actual end date and ISO timestamp date until schedule changes', () => {
    const draft = createMembershipDraft({course_subscription_id: 'id', billed_from: '2026-01-31T00:00:00+02:00', billed_until: '2026-03-15T00:00:00Z', membership_fee: 0}, monthly, user);
    assert.equal(draft.billed_from, '2026-01-31');
    assert.equal(membershipEndDate(draft, monthly), '2026-03-15');
    assert.equal(validateMembershipForm(draft, selected, monthly).billing.membership_fee, '0.00');
    draft.billed_frequency = 2;
    assert.equal(membershipEndDate(draft, monthly), '2026-03-31');
});

test('seasonal end edits survive validation, including an existing custom end', () => {
    const info = {...monthly, billed_duration_is_sport_season: true};
    const draft = createMembershipDraft({billed_from: '2026-09-01', billed_until: '2027-05-31'}, info, user);
    assert.equal(membershipEndDate(draft, info), '2027-05-31');
    draft.billed_until = '2027-04-30';
    assert.equal(validateMembershipForm(draft, selected, info).billing.billed_until, '2027-04-30');
});

test('invalid dates, reversed ranges, fee, frequency, renewal day and athlete selection are rejected', () => {
    for(const fee of ['', '-1', '12abc', '1,234', 'Infinity', '10000000', 'NaN']) {
        assert.ok(validateMembershipForm({...initial(), membership_fee: fee}, selected, monthly).errors.membership_fee, fee);
    }
    assert.ok(validateMembershipForm({...initial(), billed_from: '2026-02-30'}, selected, monthly).errors.billed_from);
    assert.ok(validateMembershipForm({...initial(), billed_from: ''}, selected, monthly).errors.billed_from);
    assert.ok(validateMembershipForm({...initial(), billed_frequency: 0}, selected, monthly).errors.billed_frequency);
    assert.ok(validateMembershipForm({...initial(), billed_from_day_of_month: 29}, selected, {...monthly, billed_from_subscription_date: false}).errors.billed_from_day_of_month);
    assert.ok(validateMembershipForm(initial(), [], monthly).errors.selectedAthletes);
    assert.ok(validateMembershipForm(initial(), [...selected, ...selected], monthly).errors.selectedAthletes);
    assert.ok(validateMembershipForm({...initial(), billed_until: '2025-01-01'}, selected, {...monthly, billed_duration_is_sport_season: true}).errors.billed_until);
});

test('server list and field errors remain readable to the user', () => {
    assert.equal(membershipApiError([{}, {billed_from: ['Invalid date.']}]), 'billed_from: Invalid date.');
});

test('seasonal defaults respect the association custom end date', () => {
    const info = {...monthly, billed_duration_is_sport_season: true};
    const draft = createMembershipDraft({}, info, {...user, custom_end_date: true, subscription_end_month: 6, subscription_end_day: 30}, '2026-02-10');
    assert.equal(draft.billed_from, '2025-09-01');
    assert.equal(draft.billed_until, '2026-06-30');
});
