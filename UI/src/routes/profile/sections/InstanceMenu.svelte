<script>
    // Compact "⋯" menu for secondary actions. Items: {label, hint?, danger?, disabled?, onSelect}.
    // A disabled item keeps its hint visible, so the reason is never hidden.
    import {tick} from 'svelte';
    import {DotsThree} from 'phosphor-svelte';

    export let items = [];
    export let label = 'Altre azioni';
    export let disabled = false;
    export let align = 'end';

    const id = `si-menu-${Math.random().toString(36).slice(2)}`;
    let open = false;
    let root;
    let button;
    let list;

    $: visible = items.filter(Boolean);

    async function toggle(focusIndex = 0) {
        open = !open;
        if (open) { await tick(); focusItem(focusIndex); }
    }

    function entries() { return [...(list?.querySelectorAll('[role="menuitem"]:not([aria-disabled="true"])') || [])]; }
    function focusItem(index) { const all = entries(); all[(index + all.length) % all.length]?.focus(); }

    function close(returnFocus = true) {
        if (!open) return;
        open = false;
        if (returnFocus) button?.focus();
    }

    function select(item) {
        if (item.disabled) return;
        close();
        item.onSelect?.();
    }

    function onKeydown(event) {
        const all = entries();
        const index = all.indexOf(document.activeElement);
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
        else if (event.key === 'ArrowDown') { event.preventDefault(); focusItem(index + 1); }
        else if (event.key === 'ArrowUp') { event.preventDefault(); focusItem(index - 1); }
        else if (event.key === 'Home') { event.preventDefault(); focusItem(0); }
        else if (event.key === 'End') { event.preventDefault(); focusItem(-1); }
        else if (event.key === 'Tab') close(false);
    }

    function onButtonKeydown(event) {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            if (!open) toggle(event.key === 'ArrowDown' ? 0 : -1);
        }
    }

    function onWindowClick(event) {
        if (open && root && !root.contains(event.target)) close(false);
    }
</script>

<svelte:window on:click={onWindowClick} />

<div class="si-menu" bind:this={root}>
    <button bind:this={button} type="button" class="si-menu-button" aria-label={label} aria-haspopup="menu" aria-expanded={open} aria-controls={id}
        {disabled} on:click={() => toggle()} on:keydown={onButtonKeydown}>
        <slot name="button"><DotsThree size={22} weight="bold" /></slot>
    </button>
    {#if open}
        <ul bind:this={list} {id} class="si-menu-list {align}" role="menu" aria-label={label} on:keydown={onKeydown}>
            {#each visible as item, index}
                <li role="none">
                    <button type="button" role="menuitem" class:danger={item.danger} aria-label={item.label} aria-disabled={item.disabled ? 'true' : undefined} aria-describedby={item.hint ? `${id}-hint-${index}` : undefined} tabindex="-1" on:click={() => select(item)}>
                        <span>{item.label}</span>
                        {#if item.hint}<small id={`${id}-hint-${index}`}>{item.hint}</small>{/if}
                    </button>
                </li>
            {/each}
        </ul>
    {/if}
</div>

<style>
    .si-menu { position: relative; display: inline-flex; }
    .si-menu-button { display: inline-flex; align-items: center; justify-content: center; min-width: 2.25rem; height: 2.25rem; padding: 0 .4rem; border: 1px solid var(--border-color); border-radius: .5rem; background: var(--bg-card); color: var(--text-primary); }
    .si-menu-button:hover:not(:disabled), .si-menu-button[aria-expanded='true'] { background: var(--bg-hover); }
    .si-menu-button:disabled { opacity: .5; }
    .si-menu-button:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
    .si-menu-list { position: absolute; top: calc(100% + .35rem); z-index: 20; min-width: 14rem; max-width: min(20rem, calc(100vw - 2rem)); margin: 0; padding: .35rem; list-style: none; background: var(--bg-dropdown, var(--bg-card)); border: 1px solid var(--border-color); border-radius: .625rem; box-shadow: var(--shadow-md); }
    .si-menu-list.end { right: 0; }
    .si-menu-list.start { left: 0; }
    [role='menuitem'] { display: flex; flex-direction: column; align-items: flex-start; width: 100%; padding: .5rem .65rem; border: 0; border-radius: .4rem; background: none; color: var(--text-primary); text-align: left; font-weight: 500; }
    [role='menuitem']:hover, [role='menuitem']:focus { background: var(--bg-hover); outline: none; }
    [role='menuitem']:focus-visible { box-shadow: inset 0 0 0 2px var(--primary); }
    [role='menuitem'].danger { color: var(--danger, #d92d20); }
    [role='menuitem'][aria-disabled='true'] { color: var(--text-muted); cursor: not-allowed; }
    [role='menuitem'] small { font-weight: 400; color: var(--text-secondary); white-space: normal; }
</style>
