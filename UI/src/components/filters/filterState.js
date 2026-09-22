import moment from 'moment';

// Copy editable state without cloning operation callbacks or mutating applied filters.
export function copyFilter(filter) {
    return {
        ...filter,
        data: {
            ...(filter.data || {}),
            ...(filter.data?.options ? {options: filter.data.options.map(option => ({...option}))} : {}),
        },
    };
}

export function resetFilter(filter) {
    const next = copyFilter(filter);
    next.active = false;
    next.value = '';
    if (next.data.options) next.data.options = next.data.options.map(option => ({...option, checked: false}));
    if (filter.type === 'tags') next.data.and = false;
    if (filter.type === 'age') {
        next.data.from_age = null;
        next.data.to_age = null;
    }
    if (filter.type === 'date-range') {
        next.data.from_date = '';
        next.data.to_date = '';
    }
    return next;
}

export const hasFilterValue = value => value !== '' && value !== null && value !== undefined;

// Rehydrate the advanced controls whose applied values live in list stores.
// Run again when asynchronous tag options arrive, retaining the stored selection.
export function restoreStoredFilters(filters, state) {
    const tagIds = new Set((Array.isArray(state.tags) ? state.tags : String(state.tags || '').split(','))
        .filter(hasFilterValue).map(String));
    return filters.map(filter => {
        const next = copyFilter(filter);
        if (next.type === 'age') {
            next.data.from_age = state.from_age ?? null;
            next.data.to_age = state.to_age ?? null;
            next.active = isFilterActive(next);
        } else if (next.type === 'tags') {
            next.data.and = Boolean(Number(state.tags_and));
            next.data.options = (next.data.options || []).map(option => ({
                ...option, checked: tagIds.has(String(option.tag_id)),
            }));
            next.active = tagIds.size > 0;
        }
        return next;
    });
}

export function validateFilter(filter) {
    if (filter.type === 'date-range') {
        const {from_date: from, to_date: to} = filter.data || {};
        const parse = value => moment(value, ['DD/MM/YYYY', 'YYYY-MM-DD', 'YYYY/MM/DD'], true);
        if ([from, to].some(value => hasFilterValue(value) && !parse(value).isValid())) return 'Inserisci una data valida.';
        if (hasFilterValue(from) && hasFilterValue(to) && parse(from).isAfter(parse(to), 'day')) return 'La data iniziale non può superare quella finale.';
        return '';
    }
    if (filter.type !== 'age') return '';
    const {from_age: from, to_age: to} = filter.data || {};
    for (const value of [from, to]) {
        if (hasFilterValue(value) && (!Number.isInteger(Number(value)) || Number(value) < 0 || Number(value) > 120)) {
            return 'Inserisci un’età intera tra 0 e 120 anni.';
        }
    }
    if (hasFilterValue(from) && hasFilterValue(to) && Number(from) > Number(to)) {
        return 'L’età minima non può superare l’età massima.';
    }
    return '';
}

export function isFilterActive(filter) {
    const data = filter.data || {};
    if (filter.type === 'tags' || filter.type === 'checkbox') return Boolean(data.options?.some(option => option.checked));
    if (filter.type === 'age') return hasFilterValue(data.from_age) || hasFilterValue(data.to_age);
    if (filter.type === 'date-range') return hasFilterValue(data.from_date) || hasFilterValue(data.to_date);
    return hasFilterValue(filter.value);
}
