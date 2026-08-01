export function normalizeCalendarEvents(value) {
    if (Array.isArray(value)) return value.map(normalizeCalendarEvent);
    if (Array.isArray(value?.events)) return value.events.map(normalizeCalendarEvent);
    if (Array.isArray(value?.data?.events)) return value.data.events.map(normalizeCalendarEvent);
    if (Array.isArray(value?.data)) return value.data.map(normalizeCalendarEvent);
    return [];
}

export function normalizeCalendarEvent(event) {
    return {
        ...event,
        id: event?.id || event?.event_id,
    };
}

export function getCalendarClassName(event, fallback = 'fc-event-solid-primary') {
    const value = event?.className || event?.classNames;
    const values = Array.isArray(value) ? value : [value];
    const className = values
        .filter(Boolean)
        .flatMap(item => String(item).split(/\s+/))
        .find(Boolean);

    return className || fallback;
}
