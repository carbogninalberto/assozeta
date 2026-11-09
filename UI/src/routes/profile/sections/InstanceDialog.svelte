<script>
    // Native <dialog>: the browser handles the focus trap, Escape, the top layer
    // and returning focus to the opener. variant="drawer" slides in from the right.
    import {createEventDispatcher, onDestroy, tick} from 'svelte';
    import {X} from 'phosphor-svelte';

    export let open = false;
    export let title;
    export let variant = 'dialog';
    export let size = 'md';
    // While true the dialog cannot be dismissed (an operation is running).
    export let locked = false;
    // Element to focus first; defaults to the first control in the body.
    export let initialFocus = null;

    const dispatch = createEventDispatcher();
    const id = `si-dialog-${Math.random().toString(36).slice(2)}`;
    let element;
    let opener = null;

    $: if (element) sync(open);

    async function sync(value) {
        if (value && !element.open) {
            opener = document.activeElement;
            element.showModal();
            await tick();
            (initialFocus || element.querySelector('[data-autofocus]') || element.querySelector('.si-dialog-body input, .si-dialog-body select, .si-dialog-body textarea') || element.querySelector('.si-dialog-close'))?.focus();
        } else if (!value && element.open) {
            element.close();
        }
    }

    function close() {
        if (locked) return;
        open = false;
        dispatch('close');
    }

    function onCancel(event) {
        event.preventDefault();
        close();
    }

    function onClose() {
        if (open) { open = false; dispatch('close'); }
        if (opener?.isConnected) opener.focus();
        opener = null;
    }

    function onBackdrop(event) {
        if (event.target === element) close();
    }

    onDestroy(() => { if (element?.open) element.close(); });
</script>

<!-- svelte-ignore a11y-click-events-have-key-events a11y-no-noninteractive-element-interactions -->
<dialog bind:this={element} class="si-dialog {variant} {size}" aria-labelledby={`${id}-title`} on:cancel={onCancel} on:close={onClose} on:click={onBackdrop}>
    {#if open}
        <div class="si-dialog-panel">
            <header class="si-dialog-head">
                <h2 id={`${id}-title`}>{title}</h2>
                <button type="button" class="si-dialog-close" aria-label="Chiudi" disabled={locked} on:click={close}><X size={18} weight="bold" /></button>
            </header>
            <div class="si-dialog-body"><slot /></div>
            {#if $$slots.footer}<footer class="si-dialog-foot"><slot name="footer" /></footer>{/if}
        </div>
    {/if}
</dialog>

<style>
    .si-dialog { padding: 0; border: 0; border-radius: .875rem; background: var(--bg-modal, var(--bg-card)); color: var(--text-primary); box-shadow: var(--shadow-lg); width: min(32rem, calc(100vw - 2rem)); max-height: calc(100dvh - 2rem); overflow: hidden; }
    .si-dialog.lg { width: min(48rem, calc(100vw - 2rem)); }
    .si-dialog::backdrop { background: rgba(15, 18, 34, .45); }
    .si-dialog[open] { animation: si-dialog-in .16s ease-out; }
    .si-dialog.drawer { margin: 0 0 0 auto; height: 100dvh; max-height: 100dvh; width: min(36rem, 100vw); border-radius: 0; }
    .si-dialog.drawer[open] { animation: si-drawer-in .2s ease-out; }
    .si-dialog-panel { display: flex; flex-direction: column; max-height: inherit; height: 100%; }
    .si-dialog:not(.drawer) .si-dialog-panel { max-height: calc(100dvh - 2rem); }
    .si-dialog-head { display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding: 1.125rem 1.25rem .5rem; }
    .si-dialog-head h2 { font-size: 1.35rem; font-weight: 600; margin: 0; overflow-wrap: anywhere; }
    .si-dialog-close { display: inline-flex; padding: .4rem; border: 0; border-radius: .5rem; background: none; color: var(--text-secondary); }
    .si-dialog-close:hover { background: var(--bg-hover); color: var(--text-primary); }
    .si-dialog-close:focus-visible { outline: 2px solid var(--primary); outline-offset: 1px; }
    .si-dialog-body { padding: .25rem 1.25rem 1.25rem; overflow-y: auto; overscroll-behavior: contain; overflow-wrap: anywhere; }
    .si-dialog-body :global(p:last-child) { margin-bottom: 0; }
    .si-dialog-foot { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: .5rem; padding: .875rem 1.25rem; border-top: 1px solid var(--border-color); }
    @keyframes si-dialog-in { from { opacity: 0; transform: translateY(.5rem) scale(.98); } }
    @keyframes si-drawer-in { from { transform: translateX(2rem); opacity: 0; } }
    @media (prefers-reduced-motion: reduce) { .si-dialog[open], .si-dialog.drawer[open] { animation: none; } }
    @media (max-width: 575px) {
        .si-dialog:not(.drawer) { margin: auto 0 0; width: 100vw; max-width: 100vw; border-radius: .875rem .875rem 0 0; }
        .si-dialog-foot { flex-direction: column-reverse; }
        .si-dialog-foot :global(.btn) { width: 100%; }
    }
</style>
