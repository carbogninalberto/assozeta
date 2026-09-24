export const profilePages = ['info', 'twofa', 'stripe', 'password', 'settings', 'integrations', 'data-management', 'self-instance'];

export function readProfileLocation(hash = window.location.hash) {
    const [path, query = ''] = hash.replace(/^#/, '').split('?');
    const params = new URLSearchParams(query);
    return {page: path === '/profile' && profilePages.includes(params.get('page')) ? params.get('page') : null,
        tab: path === '/profile' ? params.get('tab') : null};
}

export function navigateProfile(page, tab, replace = false) {
    if (!profilePages.includes(page)) return;
    const current = readProfileLocation();
    const params = new URLSearchParams({page});
    const nextTab = tab === undefined && current.page === page ? current.tab : tab;
    if (nextTab) params.set('tab', nextTab);
    const hash = `#/profile?${params}`;
    if (window.location.hash === hash) return;
    const oldURL = window.location.href;
    window.history[replace ? 'replaceState' : 'pushState'](window.history.state, '',
        `${window.location.pathname}${window.location.search}${hash}`);
    window.dispatchEvent(new HashChangeEvent('hashchange', {oldURL, newURL: window.location.href}));
}

export function profileTab(page, allowed, fallback) {
    const location = readProfileLocation();
    return location.page === page && allowed.includes(location.tab) ? location.tab : fallback;
}
