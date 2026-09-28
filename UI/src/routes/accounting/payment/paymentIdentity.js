const escapeHtml = value =>
    String(value ?? '').replace(
        /[&<>"']/g,
        character =>
            ({
                '&': '&amp;',
                '<': '&lt;',
                '>': '&gt;',
                '"': '&quot;',
                "'": '&#39;',
            }[character])
    );

const personName = person => [person?.first_name, person?.last_name].filter(Boolean).join(' ').trim();

// A subscription reference does not guarantee that the payment has an associate.
export function renderPaymentIdentity(row, {archive = false} = {}) {
    const associateName = personName(row.associate);
    const supplierName = row.supplier?.name?.trim();
    const instructorName = personName(row.instructor);
    let name = associateName || supplierName || instructorName;
    if (!associateName && supplierName && row.supplier.tax_code) name += ` (${row.supplier.tax_code})`;

    let content;
    if (name) {
        content = `<b${associateName && !archive ? ' class="text-primary"' : ''}>${escapeHtml(name.toUpperCase())}</b>`;
        if (archive && associateName && row.subscription_id) {
            content = `<a href="/#/members/list/detail/${encodeURIComponent(row.subscription_id)}/info">${content}</a>`;
        }
        if (archive && associateName && !row.subscription_id) {
            content = `<b>${escapeHtml(name.toUpperCase())}<br><span class="font-size-xs text-dark-65">${escapeHtml(
                row.description
            )}</span></b>`;
        }
        if (!archive && row.description)
            content += `<br><span class="font-size-xs text-dark">${escapeHtml(row.description)}</span>`;
    } else {
        content = `<b>${escapeHtml(row.description || '-')}</b>`;
    }
    return `<div class="font-size-sm" style="line-height:1.2;">${content}</div>`;
}
