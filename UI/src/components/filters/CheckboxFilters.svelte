<script>
    import {createEventDispatcher, tick} from 'svelte';
    import {Filter, ChevronDown} from 'lucide-svelte';
    import {clickOutside} from 'components/formBuilder/utils';
    import {v4 as uuidv4} from 'uuid';
    export let values = {};
    export let options = [];
    const dispatch = createEventDispatcher();
    const panelId = `checkbox-filters-${uuidv4()}`;
    let open = false;
    let trigger;
    let panel;
    let panelStyle = '';
    async function toggle() {
        open = !open;
        if (!open) return;
        await tick();
        const rect = trigger.getBoundingClientRect();
        const left = Math.max(8 - rect.left, Math.min(0, window.innerWidth - 8 - rect.left - panel.offsetWidth));
        const below = window.innerHeight - rect.bottom - 8;
        const above = rect.top - 8;
        const upwards = below < Math.min(panel.scrollHeight, above);
        panelStyle = `left:${left}px;right:auto;max-height:${Math.max(44, Math.min(window.innerHeight * .65, upwards ? above : below))}px;${upwards ? 'top:auto;bottom:calc(100% + 4px);' : ''}`;
    }
    function change(option, checked) {
        values = {...values, [option.key]: checked};
        if (checked && option.excludes) values[option.excludes] = false;
        dispatch('change', values);
    }
</script>

<svelte:window on:resize={() => { open = false; panelStyle = ''; }} />

<!-- Escape is delegated from the native controls inside this group. -->
<!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
<div class="checkbox-filters" role="group" aria-label="Altri filtri" use:clickOutside on:click_outside={() => (open = false)}
    on:keydown={event => {
        if (event.key === 'Escape' && open) {
            event.preventDefault(); event.stopPropagation(); open = false; trigger?.focus();
        }
    }}>
    <button type="button" bind:this={trigger} class="btn btn-light m-0 checkbox-filter-trigger"
        class:active={Object.values(values).some(Boolean)} aria-expanded={open} aria-controls={panelId}
        on:click={toggle}>
        <Filter size={16} /> Altri filtri <ChevronDown size={16} />
    </button>
    <fieldset bind:this={panel} style={panelStyle} id={panelId} class="checkbox-filter-panel" class:expanded={open}>
        <legend>Altri filtri</legend>
        {#each options as option}
            <label>
                <input type="checkbox" checked={Boolean(values[option.key])}
                    on:change={event => change(option, event.currentTarget.checked)} />
                <span>{option.label}</span>
            </label>
        {/each}
    </fieldset>
</div>

<style>
    .checkbox-filters { position: relative; text-align: left; }
    .checkbox-filter-trigger { display: inline-flex; align-items: center; gap: .5rem; min-height: 44px; }
    .checkbox-filter-trigger.active { border: 1px solid var(--primary, #3699ff); }
    .checkbox-filter-panel { display: none; position: absolute; right: 0; top: calc(100% + 4px); z-index: 100; width: min(26rem, calc(100vw - 2rem)); max-height: 65vh; overflow-y: auto; background: var(--bg-surface, white); border: 1px solid var(--border-color, #ebedf3); border-radius: .75rem; padding: .5rem 1rem; box-shadow: 0 4px 16px #0002; }
    .checkbox-filter-panel.expanded { display: block; }
    legend { float: none; width: auto; font-size: 1rem; font-weight: 700; margin: 0; padding: 0 .25rem; }
    label { display: flex; align-items: center; gap: .75rem; min-height: 44px; margin: 0; white-space: normal; cursor: pointer; }
    input { flex: none; width: 18px; height: 18px; accent-color: var(--primary, #3699ff); }
    @media (max-width: 767px) {
        .checkbox-filter-trigger { display: none; }
        .checkbox-filter-panel { display: block; position: static; width: 100%; max-height: none; box-shadow: none; }
    }
</style>
