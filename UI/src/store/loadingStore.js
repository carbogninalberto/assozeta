import {derived, writable} from 'svelte/store';

const defaultPageState = {
    active: false,
    message: 'Caricamento...',
    state: 'primary',
    overlayColor: '#000000',
    opacity: 0.05,
};

export const loadingState = writable({
    page: defaultPageState,
    scopes: {},
});

export const isPageBlocked = derived(loadingState, $loadingState => $loadingState.page.active);

export function blockPage(options = {}) {
    loadingState.update(state => ({
        ...state,
        page: {
            ...defaultPageState,
            ...options,
            active: true,
            message: options.message || defaultPageState.message,
        },
    }));
}

export function unblockPage() {
    loadingState.update(state => ({
        ...state,
        page: defaultPageState,
    }));
}

export function blockScope(scope, options = {}) {
    if (!scope) return;

    loadingState.update(state => ({
        ...state,
        scopes: {
            ...state.scopes,
            [scope]: {
                ...defaultPageState,
                ...options,
                active: true,
                message: options.message || defaultPageState.message,
            },
        },
    }));
}

export function unblockScope(scope) {
    if (!scope) return;

    loadingState.update(state => {
        const nextScopes = {...state.scopes};
        delete nextScopes[scope];

        return {
            ...state,
            scopes: nextScopes,
        };
    });
}
