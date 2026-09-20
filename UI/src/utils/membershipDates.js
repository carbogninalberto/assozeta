import moment from 'moment';

export function membershipDisplayDate(value, fallback = '') {
    if (value === undefined) return fallback;
    const parsed = moment(value, 'YYYY-MM-DD', true);
    return parsed.isValid() ? parsed.format('DD/MM/YYYY') : '';
}

export function membershipBillingDates({from, until, seasonal, frequency}) {
    const start = moment(from, 'DD/MM/YYYY', true);
    const months = Number(frequency);
    if (!start.isValid()) throw new Error('Data inizio abbonamento non valida.');
    if (!seasonal && (!Number.isInteger(months) || months < 1 || months > 12)) {
        throw new Error('Seleziona una durata valida da 1 a 12 mesi.');
    }
    const end = seasonal ? moment(until, 'DD/MM/YYYY', true) : start.clone().add(months, 'months');
    if (!end.isValid() || end.isBefore(start, 'day')) throw new Error('Data fine abbonamento non valida.');
    return {billed_from: start.format('YYYY-MM-DD'), billed_until: end.format('YYYY-MM-DD'),
        billed_frequency: seasonal ? 12 : months};
}
