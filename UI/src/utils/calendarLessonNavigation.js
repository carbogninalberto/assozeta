export function lessonCalendarHref(courseId, eventId) {
    return `#/course/overview/${encodeURIComponent(courseId)}/calendar?event_id=${encodeURIComponent(eventId)}`;
}

export function openCalendarLesson(calendar, eventId, openDetail) {
    const event = calendar.getEvents().find(item => String(item.id) === eventId);
    if (!event) return false;
    calendar.setOption('date', event.start);
    openDetail(event);
    return true;
}
