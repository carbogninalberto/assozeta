<script>
    import { createEventDispatcher } from 'svelte';

    const dispatch = createEventDispatcher();

    /** @type {Array<{key:string, label:string}>} */
    export let presets = [];
    /** @type {string|null} */
    export let activePreset = null;
</script>

<div class="preset-list">
    {#each presets as preset (preset.key)}
        <button
            class="preset-btn"
            class:active={preset.key === activePreset}
            on:click={() => dispatch('selectPreset', preset.key)}
            type="button"
        >
            {preset.label}
        </button>
    {/each}
</div>

<style>
    .preset-list {
        display: flex;
        flex-direction: column;
        gap: 0.2rem;
        padding: 0.25rem 0;
        min-width: 11rem;
    }

    .preset-btn {
        appearance: none;
        border: none;
        background: transparent;
        text-align: left;
        padding: 0.35rem 0.75rem;
        border-radius: 0.42rem;
        font-size: 0.88rem;
        cursor: pointer;
        color: var(--text-secondary);
        transition: background 0.15s, color 0.15s;
        white-space: nowrap;
    }

    .preset-btn:hover {
        background: var(--bg-hover, rgba(0,0,0,0.05));
        color: var(--text-primary);
    }

    .preset-btn.active {
        background: var(--primary);
        color: var(--white);
        font-weight: 600;
    }
</style>
