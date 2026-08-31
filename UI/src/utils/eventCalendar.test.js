import test from 'node:test';
import assert from 'node:assert/strict';

import {
    normalizeCalendarEvents,
    serializeCalendarEvents,
} from './eventCalendar.js';


test('normalizes canonical UTC timestamps for EventCalendar 5', () => {
    const [event] = normalizeCalendarEvents({data: {events: [{
        event_id: 'lesson-1',
        start: '2026-08-27T13:00:00.000Z',
        end: '2026-08-27T14:00:00.000Z',
        extendedProps: {},
    }]}});

    assert.equal(event.id, 'lesson-1');
    assert.equal(event.start, '2026-08-27T13:00:00.000+00:00');
    assert.equal(event.end, '2026-08-27T14:00:00.000+00:00');
});

test('repairs legacy missing end values so one bad event cannot empty the calendar', () => {
    const events = normalizeCalendarEvents({data: {events: [
        {
            event_id: 'legacy-all-day',
            start: '2024-05-29T17:00:00.000Z',
            end: 'None',
            allDay: true,
            extendedProps: {},
        },
        {
            event_id: 'new-periodic-lesson',
            start: '2026-09-01T13:00:00.000Z',
            end: '2026-09-01T14:00:00.000Z',
            allDay: false,
            extendedProps: {timeContract: 'utc-v1'},
        },
    ]}});

    assert.equal(events.length, 2);
    assert.equal(events[0].end, events[0].start);
    assert.equal(events[0].end, '2024-05-29T17:00:00.000+00:00');
    assert.equal(events[1].start, '2026-09-01T13:00:00.000+00:00');
    assert.equal(events[1].end, '2026-09-01T14:00:00.000+00:00');
});

test('auto mode preserves floating global legacy events but converts course events', () => {
    const events = normalizeCalendarEvents([
        {
            event_id: 'legacy-global',
            start: '2026-08-27T15:00:00.000Z',
            extendedProps: {},
        },
        {
            event_id: 'legacy-course',
            start: '2026-08-27T13:00:00.000Z',
            extendedProps: {course: 'course-1'},
        },
        {
            event_id: 'canonical-global',
            start: '2026-08-27T13:00:00.000Z',
            extendedProps: {timeContract: 'utc-v1'},
        },
    ], {legacyTimezone: 'auto'});

    assert.equal(events[0].start, '2026-08-27T15:00:00.000Z');
    assert.equal(events[1].start, '2026-08-27T13:00:00.000+00:00');
    assert.equal(events[2].start, '2026-08-27T13:00:00.000+00:00');
});

test('serializes summer and winter local instants as real UTC and marks the contract', () => {
    const events = serializeCalendarEvents([
        {
            id: 'summer',
            title: 'Summer lesson',
            start: new Date('2026-08-27T15:00:00+02:00'),
            end: new Date('2026-08-27T16:00:00+02:00'),
            allDay: false,
            extendedProps: {},
        },
        {
            id: 'winter',
            title: 'Winter lesson',
            start: new Date('2026-12-10T15:00:00+01:00'),
            end: new Date('2026-12-10T16:00:00+01:00'),
            allDay: false,
            extendedProps: {},
        },
    ]);

    assert.equal(events[0].start, '2026-08-27T13:00:00.000Z');
    assert.equal(events[0].end, '2026-08-27T14:00:00.000Z');
    assert.equal(events[1].start, '2026-12-10T14:00:00.000Z');
    assert.equal(events[1].end, '2026-12-10T15:00:00.000Z');
    assert.equal(events[0].extendedProps.timeContract, 'utc-v1');
});
