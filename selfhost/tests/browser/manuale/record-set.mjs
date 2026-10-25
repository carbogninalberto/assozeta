// Frozen fixture timestamps can leave list order undefined. Pagination maps use
// contiguous positions (0, 1, ...); other map keys remain part of record identity.
export function recordSet(rows) {
    const keys = Object.keys(rows);
    const positional = keys.every((key, index) => key === String(index));
    const values = Array.isArray(rows) || positional ? Object.values(rows) : Object.entries(rows);
    return values.map(row => JSON.stringify(row)).sort();
}

export const sameRecords = (left, right) => JSON.stringify(recordSet(left)) === JSON.stringify(recordSet(right));
