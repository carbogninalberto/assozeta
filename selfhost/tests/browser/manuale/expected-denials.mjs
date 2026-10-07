// An explicitly asserted browser rejection consumes exactly one observed failure.
export function reconcileBrowserDenials(failures, declared) {
    const remaining = declared.filter(item => item.observed_browser === true).map(item => ({...item}));
    const unexpected = [];
    for (const failure of failures) {
        const index = remaining.findIndex(item => item.path === failure.path && item.method === failure.method &&
            item.status === failure.status && item.identity === failure.identity);
        if (index === -1) unexpected.push(failure);
        else remaining.splice(index, 1);
    }
    return {unexpected, unobserved: remaining};
}
