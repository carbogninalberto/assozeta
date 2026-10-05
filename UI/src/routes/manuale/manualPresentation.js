export function manualLinkUrl(value, page = '') {
    if (typeof value !== 'string' || /[\u0000-\u0020\\]/.test(value)) return null;
    if (value.startsWith('//')) return null;
    try {
        const absolute = /^[a-z][a-z0-9+.-]*:/i.test(value);
        const url = new URL(value, 'https://manual.invalid/' + page);
        if (!['https:', 'http:'].includes(url.protocol)) return null;
        if (absolute) return url.href;
        if (/^\/(docs|faq|tutorials)\/[a-z0-9/-]+(?:\.mdx)?$/i.test(url.pathname)) {
            const target = url.pathname.slice(1).replace(/\.mdx$/, '');
            return url.hash ? '/#/manuale?section=' + encodeURIComponent(target + '#' + decodeURIComponent(url.hash.slice(1)))
                : '/#/manuale?page=' + encodeURIComponent(target);
        }
        if (value.startsWith('#') || value.startsWith('/')) return value;
        return null;
    } catch { return null; }
}

export function attachManualSection(messages, streamingId, section) {
    // Only an active response can receive a guide. Late events cannot decorate
    // a welcome message, a completed response, or a subsequent user message.
    if (streamingId == null || !section?.id || !Array.isArray(section.reader) ||
        !Array.isArray(section.screenshots) || !section.embedded_url?.startsWith('/#/manuale?section=')) return messages;
    return messages.map(message => message.id === streamingId && message.role === 'agent' && message.streaming
        ? {...message, manualSection: section} : message);
}

export function manualSectionDomId(id) { return 'manual-section-' + encodeURIComponent(id); }

export function manualChapters(sections) {
    const pages = new Map();
    for (const section of sections) {
        if (!pages.has(section.page)) pages.set(section.page, {page: section.page, title: section.page_title,
            description: section.page_description || '', order: section.page_order ?? Number.MAX_SAFE_INTEGER, sections: []});
        pages.get(section.page).sections.push(section);
    }
    return [...pages.values()].map(chapter => ({...chapter,
        sections: chapter.sections.sort((a, b) => (a.section_order ?? Number.MAX_SAFE_INTEGER) - (b.section_order ?? Number.MAX_SAFE_INTEGER))}))
        .sort((a, b) => a.order - b.order);
}
