import moment from 'moment';

// The API stores a period string; the range picker needs separate date values.
export function initializeInstructorPeriod(row) {
    if (row.compensation_type !== 'percentage' || typeof row.period !== 'string') return row;
    const parts = row.period.split(' al ');
    if (parts.length !== 2) return row;
    const dates = parts.map(value => moment(value, 'DD/MM/YYYY', true));
    if (dates.some(value => !value.isValid()) || dates[0].isAfter(dates[1])) return row;
    return {...row,
        period_start: row.period_start ?? parts[0],
        period_end: row.period_end ?? parts[1],
    };
}
