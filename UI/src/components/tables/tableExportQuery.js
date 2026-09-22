// Export all matching rows with the same applied filters and sort as the table.
// Scope parameters belong to the endpoint and must remain outside query[].
export function tableExportQuery(table, scope = {}) {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(table.getDataSourceQuery())) {
        if (value !== null && value !== undefined && value !== '') params.set(`query[${key}]`, String(value));
    }
    for (const [key, value] of Object.entries(scope)) params.set(key, String(value));
    const sort = table.getDataSourceParam('sort');
    if (sort) {
        params.set('sort[field]', sort.field);
        params.set('sort[sort]', sort.sort);
    }
    return params.toString();
}
