<script>
    import {onMount} from 'svelte';
    import {Filter, X} from 'lucide-svelte';
    import Portal from 'svelte-portal';
    import {BasicDrawer} from '../drawer';
    import {v4 as uuidv4} from 'uuid';

    export let onReset = null;

    let isOpen = false;
    let panel;
    let sheet;
    let drawer;
    let viewportHeight = typeof window === 'undefined' ? 0 : window.innerHeight;
    let keyboardOffset = 0;
    const panelId = `mobile-filters-${uuidv4()}`;

    // Move the mounted controls, preserving values and imperative filter listeners.
    function moveFilters(host) {
        const marker = document.createComment('filter-position');
        panel.before(marker);
        host.append(panel);
        drawer = host.closest('.drawer');
        drawer?.classList.add('mobile-filter-drawer');
        drawer?.setAttribute('role', 'dialog');
        drawer?.setAttribute('aria-modal', 'true');
        drawer?.setAttribute('aria-labelledby', `${panelId}-title`);
        updateViewport();
        return {
            destroy() {
                panel.dispatchEvent(new CustomEvent('filter-panel-close'));
                marker.replaceWith(panel);
                drawer = null;
            },
        };
    }

    function updateViewport() {
        const viewport = window.visualViewport;
        viewportHeight = Math.min(window.innerHeight, viewport?.height ?? window.innerHeight);
        keyboardOffset = Math.max(0, window.innerHeight - viewportHeight - (viewport?.offsetTop ?? 0));
    }

    onMount(() => {
        updateViewport();
        window.visualViewport?.addEventListener('resize', updateViewport);
        window.visualViewport?.addEventListener('scroll', updateViewport);
        window.addEventListener('resize', updateViewport);
        const desktop = window.matchMedia('(min-width: 768px)');
        const closeOnDesktop = () => { if (desktop.matches) isOpen = false; };
        desktop.addEventListener('change', closeOnDesktop);
        return () => {
            desktop.removeEventListener('change', closeOnDesktop);
            window.visualViewport?.removeEventListener('resize', updateViewport);
            window.visualViewport?.removeEventListener('scroll', updateViewport);
            window.removeEventListener('resize', updateViewport);
        };
    });
</script>

<svelte:window on:keydown={event => {
    if (isOpen && event.key === 'Escape' && !event.defaultPrevented &&
        document.activeElement?.closest('.drawer') === sheet?.closest('.drawer')) {
        isOpen = false;
    }
}} />

<button
    type="button"
    class="btn btn-icon btn-light border border-secondary border-dashed bg-white datatable-filter-toggle d-md-none mb-0"
    aria-label="Filtri"
    aria-haspopup="dialog"
    aria-expanded={isOpen}
    aria-controls={isOpen ? panelId : undefined}
    on:click={() => (isOpen = true)}>
    <Filter size={16} />
</button>
<div class="datatable-extra-filters" bind:this={panel} on:filter-panel-dismiss={() => (isOpen = false)}>
    <slot />
</div>
{#if onReset}
    <button type="button" class="btn btn-light desktop-filter-reset filter-reset mb-0" on:click={onReset}>Ripristina filtri</button>
{/if}

<Portal target="body">
    <BasicDrawer bind:isOpen closeOnEsc={false} position="bottom" bottomOffset={`${keyboardOffset}px`} width="100%" height={viewportHeight ? `${viewportHeight * 0.85}px` : '85dvh'} maxHeight="85dvh">
        <div slot="header" class="filter-sheet-heading">
            <h2 id={`${panelId}-title`}>Filtri</h2>
            <button type="button" class="btn btn-icon btn-light mb-0" aria-label="Chiudi filtri" on:click={() => (isOpen = false)}>
                <X size={22} />
            </button>
        </div>
        <div slot="content" bind:this={sheet} id={panelId} class="mobile-filter-sheet">
            <div class="filter-sheet-fields" use:moveFilters />
            <div class="filter-sheet-footer">
                {#if onReset}
                    <button type="button" class="btn btn-light filter-reset w-100 mb-2" on:click={onReset}>Ripristina filtri</button>
                {/if}
                <button type="button" class="btn btn-primary w-100 mb-0" on:click={() => (isOpen = false)}>Mostra risultati</button>
            </div>
        </div>
    </BasicDrawer>
</Portal>

<style>
    .filter-reset { color: var(--text-primary, #181c32) !important; }
    .desktop-filter-reset { display: inline-flex; }
    @media (max-width: 767px) { .desktop-filter-reset { display: none; } }
    .filter-sheet-heading { display: flex; align-items: center; justify-content: space-between; width: 100%; }
    h2 { margin: 0; font-size: 1.5rem; font-weight: 700; }
    :global(.mobile-filter-drawer) { display: flex; flex-direction: column; overflow: hidden !important; transition: none !important; }
    :global(.mobile-filter-drawer > .drawer-header) { flex-shrink: 0; }
    .mobile-filter-sheet { display: flex; flex-direction: column; flex: 1; min-height: 0; }
    .filter-sheet-fields { flex: 1; min-height: 0; overflow-y: auto; overscroll-behavior-y: contain; padding: 0.5rem 1.5rem 1.5rem; }
    .filter-sheet-footer { flex-shrink: 0; padding: 1rem 1.5rem calc(1rem + env(safe-area-inset-bottom)); background: var(--bg-surface, white); }
    .filter-sheet-footer button { min-height: 44px; }
</style>
