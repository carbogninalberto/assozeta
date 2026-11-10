/**
 * Where the family wizard goes to pay online after its registrations are created.
 *
 * The answer lists one payment per registration that has a fee. One unpaid payment opens
 * its pay page; several open the cart checkout with all of them. Returns null when there
 * is nothing to pay.
 *
 * @param {object} response - Answer of the family registration request.
 * @param {string} returnUrl - Page to come back to after a single payment.
 * @returns {string|null} Location to open, or null.
 */
export function familyPayLocation(response, returnUrl) {
    const payments = Array.isArray(response?.payments) ? response.payments : [];
    const unpaid = [...new Set(payments.filter(payment => payment?.payment_id && !payment.paid)
        .map(payment => String(payment.payment_id)))];
    if (unpaid.length === 1) {
        return `/#/stripe/pay/${unpaid[0]}?redir=${returnUrl}`;
    }
    if (unpaid.length > 1) {
        return `/#/stripe/cart-pay/?payments_ids=${unpaid.join(',')}`;
    }
    // A single registration answers with its payment id.
    if (!payments.length && response?.payment_id) {
        return `/#/stripe/pay/${response.payment_id}?redir=${returnUrl}`;
    }
    return null;
}
