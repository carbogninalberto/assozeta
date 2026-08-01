<script>
    import {onDestroy} from 'svelte';
    import {loadingState} from 'store/loadingStore.js';

    let scrollLockCount = 0;
    let scrollPreviousOverflow = '';

    function lockBodyScroll() {
        if (typeof document === 'undefined') {
            return () => {};
        }

        if (scrollLockCount === 0) {
            scrollPreviousOverflow = document.body.style.overflow;
            document.body.style.overflow = 'hidden';
        }

        scrollLockCount += 1;

        let released = false;

        return () => {
            if (released) return;

            released = true;
            scrollLockCount = Math.max(0, scrollLockCount - 1);

            if (scrollLockCount === 0) {
                document.body.style.overflow = scrollPreviousOverflow || 'auto';
                scrollPreviousOverflow = '';
            }
        };
    }

    let releaseScrollLock;
    let blurredElements = [];

    function setBlur(active) {
        if (typeof document === 'undefined') return;

        if (active && blurredElements.length === 0) {
            blurredElements = Array.from(document.querySelectorAll('.drawer-backdrop, .flex-root'));
            blurredElements.forEach(element => element.classList.add('blur-filter'));
        } else if (!active && blurredElements.length > 0) {
            blurredElements.forEach(element => element.classList.remove('blur-filter'));
            blurredElements = [];
        }
    }

    $: page = $loadingState.page;

    $: if (page.active && !releaseScrollLock) {
        releaseScrollLock = lockBodyScroll();
        setBlur(true);
    } else if (!page.active && releaseScrollLock) {
        releaseScrollLock();
        releaseScrollLock = null;
        setBlur(false);
    }

    onDestroy(() => {
        releaseScrollLock?.();
        setBlur(false);
    });
</script>

{#if page.active}
    <div
        class="loading-overlay"
        role="status"
        aria-live="polite"
        style="--loading-overlay-color: {page.overlayColor}; --loading-overlay-opacity: {page.opacity};">
        <div class="blockui loading-overlay-content">
            <span>{page.message}</span>
            <span class="spinner spinner-{page.state}" aria-hidden="true" />
        </div>
    </div>
{/if}

<style>
    .loading-overlay {
        position: fixed;
        inset: 0;
        z-index: 10100;
        display: flex;
        align-items: center;
        justify-content: center;
        background: rgba(0, 0, 0, var(--loading-overlay-opacity, 0.05));
        cursor: wait;
    }

    .loading-overlay-content {
        display: inline-flex;
        align-items: center;
        gap: 0.75rem;
        min-height: 3rem;
        padding: 0.75rem 1.25rem;
        box-shadow: 0 0.5rem 1.5rem rgba(0, 0, 0, 0.08);
    }

    .loading-overlay-content :global(.spinner) {
        min-width: 1.25rem;
        min-height: 1.25rem;
    }
</style>
