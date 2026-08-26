const DISPLAY_DATE = /^(\d{2})\/(\d{2})\/(\d{4})$/;
const API_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

function isRealDate(year, month, day) {
    const date = new Date(Date.UTC(year, month - 1, day));
    return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
}

export function normalizeDateForApi(value) {
    if (!value) return null;

    const text = String(value).trim();
    let match = text.match(API_DATE);
    if (match) {
        const [, year, month, day] = match;
        return isRealDate(Number(year), Number(month), Number(day)) ? `${year}-${month}-${day}` : null;
    }

    match = text.match(DISPLAY_DATE);
    if (!match) return null;

    const [, day, month, year] = match;
    return isRealDate(Number(year), Number(month), Number(day)) ? `${year}-${month}-${day}` : null;
}

export function recurringDates(startValue, endValue, weekdayNames) {
    const start = normalizeDateForApi(startValue);
    const end = normalizeDateForApi(endValue);
    if (!start || !end || !weekdayNames?.length) return [];

    const weekdays = {
        sunday: 0,
        monday: 1,
        tuesday: 2,
        wednesday: 3,
        thursday: 4,
        friday: 5,
        saturday: 6,
    };
    const selectedWeekdays = new Set(weekdayNames.map(name => weekdays[name]).filter(day => day !== undefined));
    const cursor = new Date(`${start}T12:00:00Z`);
    const lastDate = new Date(`${end}T12:00:00Z`);
    const dates = [];

    // The UI explicitly describes the end date as excluded.
    while (cursor < lastDate) {
        if (selectedWeekdays.has(cursor.getUTCDay())) dates.push(cursor.toISOString().slice(0, 10));
        cursor.setUTCDate(cursor.getUTCDate() + 1);
    }

    return dates;
}
