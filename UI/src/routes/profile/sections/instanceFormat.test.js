import assert from 'node:assert/strict';
import {test} from 'node:test';
import {formatSize, formatDate, formatRelative, latestBackup, backupFreshness} from './instanceFormat.js';

const now = Date.parse('2026-11-09T12:00:00Z');
const hoursAgo = hours => new Date(now - hours * 3600000).toISOString();

test('sizes use Italian separators and the largest fitting unit', () => {
    assert.equal(formatSize(3.5 * 1024 ** 3), '3,5 GB');
    assert.equal(formatSize(200 * 1024 ** 2), '200 MB');
    assert.equal(formatSize(512), '1 KB');
    assert.equal(formatSize(null), '—');
});

test('dates share one short format and tolerate missing values', () => {
    assert.match(formatDate('2026-11-09T02:00:00Z'), /9 nov 2026/);
    assert.equal(formatDate(null), '—');
    assert.equal(formatDate('not a date'), '—');
});

test('relative times read naturally', () => {
    assert.equal(formatRelative(hoursAgo(0), now), 'adesso');
    assert.equal(formatRelative(hoursAgo(3), now), '3 ore fa');
    assert.equal(formatRelative(hoursAgo(48), now), 'l’altro ieri');
    assert.equal(formatRelative(hoursAgo(24 * 5), now), '5 giorni fa');
});

test('the latest backup is the newest of any origin', () => {
    const archives = [{name: 'a', created_at: hoursAgo(50)}, {name: 'b', created_at: hoursAgo(2)}, {name: 'c', created_at: null}];
    assert.equal(latestBackup(archives).name, 'b');
    assert.equal(latestBackup([]), null);
});

test('freshness follows the schedule, or a week without one', () => {
    assert.equal(backupFreshness(null, null, now), 'missing');
    assert.equal(backupFreshness({created_at: hoursAgo(24 * 6)}, null, now), 'ok');
    assert.equal(backupFreshness({created_at: hoursAgo(24 * 8)}, null, now), 'stale');
    const daily = {enabled: true, frequency: 'daily'};
    assert.equal(backupFreshness({created_at: hoursAgo(24.5)}, daily, now), 'ok');
    assert.equal(backupFreshness({created_at: hoursAgo(26)}, daily, now), 'stale');
    assert.equal(backupFreshness({created_at: hoursAgo(24 * 6)}, {enabled: true, frequency: 'weekly'}, now), 'ok');
});
