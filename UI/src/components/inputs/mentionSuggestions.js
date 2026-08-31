function normalizeMentionQuery(value) {
    return String(value || '')
        .trim()
        .replace(/^@/, '')
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .toLowerCase()
        .replace(/[^a-z0-9]/g, '');
}

export function filterMentionSuggestions(items, query) {
    const normalizedQuery = normalizeMentionQuery(query);
    if (!normalizedQuery) return items;

    return items.filter(item => [item.label, ...(item.aliases || [])]
        .map(normalizeMentionQuery)
        .some(candidate => candidate.startsWith(normalizedQuery)));
}
