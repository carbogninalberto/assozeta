<script>
    export let id;
    export let title;
    export let description = '';
    export let count = null;
    export let collapsible = true;
    export let open = false;
</script>

{#if collapsible}
    <details class="instance-accordion" bind:open>
        <summary>
            <div class="accordion-heading">
                <h2 {id}>{title}</h2>
                {#if description}<p>{description}</p>{/if}
            </div>
            {#if count !== null}<span class="accordion-count">{count}</span>{/if}
            <span class="accordion-chevron" aria-hidden="true"></span>
        </summary>
        <div class="accordion-content" role="region" aria-labelledby={id} tabindex="0">
            <slot />
        </div>
    </details>
{:else}
    <h2 {id}>{title}</h2>
    <slot />
{/if}

<style>
    .instance-accordion { border: 1px solid var(--border-color, #dce1e7); border-radius: .75rem; background: var(--bg-card, #fff); overflow: hidden; }
    summary { display: flex; align-items: center; gap: 1rem; padding: 1.25rem; cursor: pointer; list-style: none; }
    summary::-webkit-details-marker { display: none; }
    summary:hover { background: var(--bg-hover, #f8fafc); }
    summary:focus-visible, .accordion-content:focus-visible { outline: 2px solid var(--primary); outline-offset: -3px; }
    .accordion-heading { flex: 1; min-width: 0; }
    h2 { font-size: 1.2rem; font-weight: 600; margin: 0; overflow-wrap: anywhere; }
    p { color: var(--text-secondary, #536171); margin: .35rem 0 0; font-size: .95rem; }
    .accordion-count { padding: .2rem .65rem; border-radius: 1rem; background: var(--bg-hover, #f3f6fa); color: var(--text-secondary, #536171); font-size: .85rem; font-weight: 600; }
    .accordion-chevron { width: .55rem; height: .55rem; flex-shrink: 0; border-right: 2px solid currentColor; border-bottom: 2px solid currentColor; transform: rotate(45deg); margin: -.25rem .25rem 0; }
    details[open] .accordion-chevron { transform: rotate(225deg); margin-top: .25rem; }
    .accordion-content { max-height: min(32rem, 65vh); overflow-y: auto; overscroll-behavior-y: contain; scrollbar-gutter: stable; padding: 1.25rem; border-top: 1px solid var(--border-color, #dce1e7); overflow-wrap: anywhere; }
    @media (max-width: 575px) { summary, .accordion-content { padding: 1rem; } }
</style>
