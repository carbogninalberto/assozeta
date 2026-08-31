export const UTC_TIME_CONTRACT = 'utc-v1';

export function normalizeCalendarEvents(value, options = {}) {
    if (Array.isArray(value)) return value.map(event => normalizeCalendarEvent(event, options));
    if (Array.isArray(value?.events)) return value.events.map(event => normalizeCalendarEvent(event, options));
    if (Array.isArray(value?.data?.events)) return value.data.events.map(event => normalizeCalendarEvent(event, options));
    if (Array.isArray(value?.data)) return value.data.map(event => normalizeCalendarEvent(event, options));
    return [];
}

export function normalizeCalendarEvent(event, {legacyTimezone = 'utc'} = {}) {
    const hasUtcContract = event?.extendedProps?.timeContract === UTC_TIME_CONTRACT;
    const isCourseEvent = Boolean(event?.extendedProps?.course);
    const usesUtc = hasUtcContract || legacyTimezone === 'utc' || (
        legacyTimezone === 'auto' && isCourseEvent
    );
    const start = usesUtc ? toEventCalendarUtc(event?.start) : event?.start;
    const rawEnd = isMissingCalendarDate(event?.end) ? event?.start : event?.end;

    return {
        ...event,
        id: event?.id || event?.event_id,
        start,
        end: usesUtc ? toEventCalendarUtc(rawEnd) : rawEnd,
    };
}

export function serializeCalendarEvents(events) {
    return events.map(serializeCalendarEvent);
}

export function serializeCalendarEvent(event) {
    const allDay = Boolean(event?.allDay);
    return {
        event_id: event?.id || event?.event_id,
        start: toApiTimestamp(event?.start, allDay),
        end: toApiTimestamp(event?.end, allDay),
        ...(event?.className ? {className: event.className} : {}),
        allDay,
        title: event?.title,
        extendedProps: {
            ...(event?.extendedProps || {}),
            timeContract: UTC_TIME_CONTRACT,
        },
    };
}

function toEventCalendarUtc(value) {
    if (typeof value !== 'string') return value;
    return value.replace(/Z$/i, '+00:00');
}

function isMissingCalendarDate(value) {
    if (value == null) return true;
    if (typeof value !== 'string') return false;
    return ['', 'none', 'null', 'undefined'].includes(value.trim().toLowerCase());
}

function toApiTimestamp(value, allDay) {
    if (value == null) return null;
    const date = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(date.getTime())) return value;

    if (allDay) {
        const year = date.getFullYear();
        const month = String(date.getMonth() + 1).padStart(2, '0');
        const day = String(date.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}T00:00:00.000Z`;
    }
    return date.toISOString();
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
