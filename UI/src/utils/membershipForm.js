import moment from 'moment';

function apiDate(value) {
    if (!value) return '';
    const parsed = moment.parseZone(value, moment.ISO_8601, true);
    return parsed.isValid() ? parsed.format('YYYY-MM-DD') : '';
}

export function createMembershipDraft(data, info, user, now = moment()) {
    user = user || {};
    const today = moment(now);
    const month = Number(user.subscription_start_month || 1);
    const day = Number(user.subscription_start_day || 1);
    let seasonStart = moment([today.year(), month - 1, day]);
    if (seasonStart.isAfter(today, 'day')) seasonStart.subtract(1, 'year');
    let seasonEnd = seasonStart.clone().add(1, 'year').subtract(1, 'day');
    if (user.custom_end_date && user.subscription_end_month && user.subscription_end_day) {
        seasonEnd = moment([seasonStart.year(), Number(user.subscription_end_month) - 1, Number(user.subscription_end_day)]);
        if (seasonEnd.isBefore(seasonStart, 'day')) seasonEnd.add(1, 'year');
    }
    const defaultStart = info.billed_duration_is_sport_season ? seasonStart
        : info.billed_from_subscription_date ? today : today.clone().date(Number(info.billed_from_day_of_month || 1));
    const from = data.billed_from == null ? defaultStart.format('YYYY-MM-DD') : apiDate(data.billed_from);
    const frequency = Number(data.billed_frequency ?? info.billed_frequency ?? 1);
    const until = data.billed_until == null
        ? (info.billed_duration_is_sport_season ? (seasonEnd.isValid() ? seasonEnd.format('YYYY-MM-DD') : '') : '')
        : apiDate(data.billed_until);
    return {
        billed_from: from, billed_until: until, billed_frequency: frequency,
        billed_from_day_of_month: Number(data.billed_from_day_of_month ?? info.billed_from_day_of_month ?? 1),
        membership_fee: String(data.membership_fee ?? info.fee ?? '').replace('.', ','),
        membership_active: data.membership_active ?? true,
        auto_renewal: data.auto_renewal ?? info.auto_renewal ?? false,
        original_from: from, original_frequency: frequency,
        preserve_until: Boolean(data.course_subscription_id && until),
    };
}

export function membershipEndDate(draft, info) {
    if (info.billed_duration_is_sport_season) return draft.billed_until;
    if (draft.preserve_until && draft.billed_from === draft.original_from && Number(draft.billed_frequency) === draft.original_frequency) return draft.billed_until;
    const start = moment(draft.billed_from, 'YYYY-MM-DD', true);
    const months = Number(draft.billed_frequency);
    return start.isValid() && Number.isInteger(months) && months >= 1 && months <= 12
        ? start.add(months, 'months').format('YYYY-MM-DD') : '';
}

export function validateMembershipForm(draft, selected, info, edit = false) {
    const errors = {};
    if (!Array.isArray(selected) || !selected.length || selected.some(item => !item?.value)) errors.selectedAthletes = 'Seleziona almeno un tesserato.';
    else if (edit && selected.length !== 1) errors.selectedAthletes = 'Seleziona un solo tesserato.';
    else if (new Set(selected.map(item => item.value)).size !== selected.length) errors.selectedAthletes = 'Ogni tesserato può essere selezionato una sola volta.';
    const start = moment(draft.billed_from, 'YYYY-MM-DD', true);
    const until = membershipEndDate(draft, info);
    const end = moment(until, 'YYYY-MM-DD', true);
    if (!start.isValid()) errors.billed_from = 'Inserisci una data di inizio valida.';
    if (!end.isValid() || (start.isValid() && end.isBefore(start, 'day'))) errors.billed_until = 'La data di fine deve essere valida e non precedente alla data di inizio.';
    const frequency = info.billed_duration_is_sport_season ? 12 : Number(draft.billed_frequency);
    if (!Number.isInteger(frequency) || frequency < 1 || frequency > 12) errors.billed_frequency = 'Seleziona una durata da 1 a 12 mesi.';
    const renewalDay = Number(draft.billed_from_day_of_month);
    if (!info.billed_duration_is_sport_season && !info.billed_from_subscription_date && (!Number.isInteger(renewalDay) || renewalDay < 1 || renewalDay > 28)) errors.billed_from_day_of_month = 'Seleziona un giorno da 1 a 28.';
    const feeText = String(draft.membership_fee).trim();
    const feeValid = /^\d+(?:[.,]\d{1,2})?$/.test(feeText) || /^\d{1,3}(?:\.\d{3})+,\d{1,2}$/.test(feeText);
    const normalizedFee = feeText.includes(',') ? feeText.replaceAll('.', '').replace(',', '.') : feeText;
    const fee = Number(normalizedFee);
    if (!feeValid || !Number.isFinite(fee) || fee < 0 || fee > 9999999.99) errors.membership_fee = 'Inserisci una quota valida, da 0 a 9.999.999,99 €, con massimo due decimali.';
    return {errors, billing: {
        billed_from: draft.billed_from, billed_until: until, billed_frequency: frequency,
        // The API field is non-nullable, including modes where the renewal day is unused.
        billed_from_day_of_month: info.billed_duration_is_sport_season || info.billed_from_subscription_date ? 1 : renewalDay,
        membership_fee: feeValid ? fee.toFixed(2) : null,
        membership_active: Boolean(draft.membership_active), auto_renewal: Boolean(draft.auto_renewal),
    }};
}

export function membershipApiError(value) {
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) return value.map(membershipApiError).filter(Boolean).join(' ');
    if (value && typeof value === 'object') return Object.entries(value).map(([key, item]) => `${key}: ${membershipApiError(item)}`).join(' ');
    return '';
}
