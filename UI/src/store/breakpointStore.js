import {readable} from 'svelte/store';

function createBreakpointStore(query) {
    return readable(
        typeof window !== 'undefined' ? window.matchMedia(query).matches : false,
        set => {
            if (typeof window === 'undefined') return;

            const mql = window.matchMedia(query);
            set(mql.matches);
            const handler = event => set(event.matches);
            if (mql.addEventListener) {
                mql.addEventListener('change', handler);
                return () => mql.removeEventListener('change', handler);
            }
            mql.addListener(handler);
            return () => mql.removeListener(handler);
        }
    );
}

export const isMobile = createBreakpointStore('(max-width: 767px)');
export const isTablet = createBreakpointStore('(min-width: 768px) and (max-width: 1024px)');
export const isDesktop = createBreakpointStore('(min-width: 1025px)');
