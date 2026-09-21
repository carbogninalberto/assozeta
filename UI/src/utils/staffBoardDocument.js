// Render only the known Tiptap schema. Legacy content remains escaped text.
export const escapeHTML = value =>
    String(value ?? '').replace(
        /[&<>"']/g,
        c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c])
    );
export function safeMediaUrl(value, image = false) {
    if (typeof value !== 'string' || /[\x00-\x20\x7f]/.test(value)) return '';
    if (image && /^data:image\/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/]+={0,2}$/.test(value)) return value;
    try {
        const url = new URL(value);
        return ['http:', 'https:', ...(!image ? ['mailto:', 'tel:'] : [])].includes(url.protocol) ? value : '';
    } catch {
        return '';
    }
}
export function plainTextDocument(text = '') {
    return {
        type: 'doc',
        content: String(text)
            .split('\n')
            .map(line => ({type: 'paragraph', ...(line ? {content: [{type: 'text', text: line}]} : {})})),
    };
}
export function documentSummary(document) {
    let text = '',
        hasImage = false;
    const visit = node => {
        if (node?.type === 'text') text += node.text || '';
        if (node?.type === 'image') hasImage = true;
        (node?.content || []).forEach(visit);
        if (['paragraph', 'heading', 'hardBreak', 'listItem'].includes(node?.type)) text += '\n';
    };
    visit(document);
    return {text: text.trim(), hasImage, empty: !text.trim() && !hasImage};
}
const tags = {
    paragraph: 'p',
    heading: 'h3',
    bulletList: 'ul',
    orderedList: 'ol',
    listItem: 'li',
    blockquote: 'blockquote',
    codeBlock: 'pre',
    taskList: 'ul',
    taskItem: 'li',
    table: 'table',
    tableRow: 'tr',
    tableCell: 'td',
    tableHeader: 'th',
};
const markTags = {bold: 'strong', italic: 'em', underline: 'u', strike: 's', code: 'code'};
export function documentHTML(document) {
    function render(node, depth = 0) {
        if (!node || depth > 30) return '';
        if (node.type === 'text') {
            let text = escapeHTML(node.text);
            for (const mark of node.marks || []) {
                const tag = markTags[mark.type];
                if (tag) text = `<${tag}>${text}</${tag}>`;
                if (mark.type === 'link') {
                    const href = safeMediaUrl(mark.attrs?.href);
                    if (href)
                        text = `<a href="${escapeHTML(href)}" target="_blank" rel="noopener noreferrer">${text}</a>`;
                }
            }
            return text;
        }
        if (node.type === 'image') {
            const src = safeMediaUrl(node.attrs?.src, true);
            return src
                ? `<img src="${escapeHTML(src)}" alt="${escapeHTML(
                      node.attrs?.alt || 'Immagine allegata'
                  )}" loading="lazy">`
                : '';
        }
        if (node.type === 'hardBreak') return '<br>';
        if (node.type === 'horizontalRule' || node.type === 'pageBreak') return '<hr>';
        const children = (node.content || []).map(child => render(child, depth + 1)).join('');
        if (node.type === 'doc') return children;
        let tag = tags[node.type];
        if (!tag) return '';
        if (node.type === 'heading') tag = `h${Math.min(6, Math.max(1, Number(node.attrs?.level) || 3))}`;
        let attributes = '';
        if (['left', 'center', 'right', 'justify'].includes(node.attrs?.textAlign))
            attributes += ` style="text-align:${node.attrs.textAlign}"`;
        for (const key of ['colspan', 'rowspan', 'start']) {
            const value = node.attrs?.[key];
            if (Number.isInteger(value) && value > 0 && value <= 1000) attributes += ` ${key}="${value}"`;
        }
        if (node.type === 'taskList') attributes += ' data-type="taskList"';
        const checkbox =
            node.type === 'taskItem'
                ? `<input type="checkbox" disabled aria-label="Attività" ${node.attrs?.checked ? 'checked' : ''}>`
                : '';
        return `<${tag}${attributes}>${checkbox}${children}</${tag}>`;
    }
    return render(document);
}
