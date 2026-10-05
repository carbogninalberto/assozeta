// This function is serialized into each real browser document by Playwright.
export function initializeActorSession({identity, input, captureMode = false}) {
    // Popup construction and document previews can create opaque documents,
    // where browser storage is unavailable. Initialize the real HTTP app only.
    if (!['http:', 'https:'].includes(window.location.protocol)) return;
    // Observe real application requests without replacing their responses.
    // Abort events belong to the document and are reliable even when a browser
    // protocol request never emits requestfinished during table cancellation.
    const requests = {pending: 0, changed: performance.now()};
    window.__manualeRequestActivity = requests;
    const begin = signal => {
        requests.pending++; requests.changed = performance.now();
        let completed = false;
        const done = () => {
            if (completed) return;
            completed = true; requests.pending--; requests.changed = performance.now();
            signal?.removeEventListener('abort', done);
        };
        if (signal?.aborted) done();
        else signal?.addEventListener('abort', done, {once: true});
        return done;
    };
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (...args) => {
        const done = begin(args[1]?.signal || args[0]?.signal);
        return nativeFetch(...args).then(response => {
            if (/\battachment\b/i.test(response.headers.get('content-disposition') || '') ||
                /text\/event-stream/i.test(response.headers.get('content-type') || '')) done();
            else response.clone().arrayBuffer().catch(() => {}).finally(done);
            return response;
        }, error => { done(); throw error; });
    };
    const nativeSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function (...args) {
        const done = begin();
        this.addEventListener('loadend', done, {once: true});
        this.addEventListener('abort', done, {once: true});
        try { return nativeSend.apply(this, args); } catch (error) { done(); throw error; }
    };
    const NativeDate = Date;
    const fixed = new NativeDate(input.reference_date + 'T12:00:00Z').getTime();
    window.Date = class extends NativeDate {
        constructor(...args) {super(...(args.length ? args : [fixed]));}
        static now() {return fixed;}
    };
    // Seed credentials once. A reload must retain the role/profile/permissions
    // that the application loaded, and logout must not silently authenticate again.
    if (!sessionStorage.getItem('manualeActorInitialized') && !localStorage.getItem('sessionToken')) {
        const values = {sessionToken: identity.token, refreshToken: identity.refresh_token,
            role: null, expires: fixed + 3600000, userData: {}, billingData: null,
            isExpired: false, currentPage: 'dashboard', subPage: '', sidebarCollapsed: false};
        for (const [key, value] of Object.entries(values)) localStorage.setItem(key, JSON.stringify(value));
    }
    sessionStorage.setItem('manualeActorInitialized', 'true');
    localStorage.setItem('seenUpdatesToast', 'true');
    if (captureMode) sessionStorage.setItem('manualeCapture', 'true');
}
