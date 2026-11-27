export function sortPaymentCategories(categories) {
    return [...(categories ?? [])].sort((a, b) => {
        if (!a?.name) return b?.name ? 1 : 0;
        if (!b?.name) return -1;
        return a.name.localeCompare(b.name, 'it', {sensitivity: 'base', numeric: true});
    });
}
