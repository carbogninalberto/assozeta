import test from 'node:test';
import assert from 'node:assert/strict';
import {lessonCalendarHref, openCalendarLesson} from './calendarLessonNavigation.js';

test('lesson link preserves the course and event through hash-router navigation', () => {
    const link = lessonCalendarHref('course/one', 'lesson & special');
    const [route, query] = link.split('?');
    assert.equal(route, '#/course/overview/course%2Fone/calendar');
    assert.equal(new URLSearchParams(query).get('event_id'), 'lesson & special');
});

test('a linked lesson moves the calendar to its date and opens the selected event', () => {
    const target = {id: 'lesson', start: new Date('2026-11-03T18:00:00Z'), title: 'Lesson'};
    const calls = [];
    const calendar = {
        getEvents: () => [{id: 'other'}, target],
        setOption: (...args) => calls.push(args),
    };
    assert.equal(openCalendarLesson(calendar, 'lesson', event => {
        assert.equal(event, target);
        assert.deepEqual(calls, [['date', target.start]]);
        event.title = 'Updated';
    }), true);
    assert.equal(target.title, 'Updated');
});

test('deleted or foreign-calendar lesson does not open an unrelated event', () => {
    const calendar = {
        getEvents: () => [{id: 'other'}],
        setOption: () => assert.fail('must not move the calendar'),
    };
    assert.equal(openCalendarLesson(calendar, 'missing', () => assert.fail('must not open details')), false);
});
