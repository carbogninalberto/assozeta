<script>
    import {setContext} from 'svelte';
    import ManualMarkdown from './ManualMarkdown.svelte';
    import ManualImage from './ManualImage.svelte';
    import ManualContent from './ManualContent.svelte';
    import {Info, Lightbulb, Warning} from 'phosphor-svelte';
    export let section;
    export let compact = false;
    export let idPrefix = '';
    setContext('manualPage', () => section.page);
    setContext('manualNewTab', () => compact);
</script>

<div class="manual-steps" class:compact>
    {#each section.reader || [] as block, index}
        <section class="manual-step" class:prose={!block.title} class:note={Boolean(block.kind)} class:warning={block.kind === 'warning'} id={idPrefix ? idPrefix + index : undefined}>
            {#if block.title}
                <h3><span class="step-number">{section.reader.slice(0, index + 1).filter(item => item.title).length}</span>{block.title}</h3>
            {/if}
            <div class="step-body">
                {#if block.kind}<div class="callout-label"><svelte:component this={({warning: Warning, tip: Lightbulb})[block.kind] || Info} size={16} weight="fill" />{({warning: 'Attenzione', note: 'Nota', tip: 'Suggerimento', info: 'Informazioni'})[block.kind]}</div>{/if}
                {#if block.content}
                    <ManualContent content={block.content} {section} {compact} />
                {:else}
                    {#if block.markdown || block.text}<ManualMarkdown source={block.markdown || block.text} />{/if}
                    {#each block.screenshots || [] as imagePath (imagePath)}
                        {@const image = section.screenshots.find(item => item.path === imagePath)}
                        {#if image}{#key image.url + image.sha256}<ManualImage url={image.url} alt={block.title || section.title} {compact} />{/key}{/if}
                    {/each}
                {/if}
            </div>
        </section>
    {/each}
</div>

<style>
    .manual-step { position: relative; scroll-margin-top: 2rem; padding-bottom: 2rem; }
    .manual-step:not(:last-child):not(.prose)::before { content: ''; position: absolute; left: 12px; top: 34px; bottom: 4px; width: 2px; border-radius: 2px; background: var(--border-color, #e8e9ef); }
    .manual-step:last-child { padding-bottom: 0; }
    h3 { display: flex; align-items: flex-start; gap: .85rem; font-size: 18px; line-height: 1.5; font-weight: 650; letter-spacing: -.005em; margin: 0 0 .75rem; }
    .manual-steps .step-number { display: inline-grid; place-items: center; flex: 0 0 26px; height: 26px; margin-top: 0; border-radius: 999px; background: var(--primary); color: var(--on-primary, #fff);
        font-size: 13px; font-weight: 700; font-variant-numeric: tabular-nums; box-shadow: 0 0 0 4px var(--bg-surface, #fff); }
    .step-body { margin-left: 40px; }
    .prose .step-body { margin-left: 0; }
    .note .step-body { margin: 0; padding: 1rem 1.2rem; border: 1px solid color-mix(in srgb, var(--primary) 18%, transparent); border-radius: .75rem; background: var(--primary-light, #f5f3ff); }
    .warning .step-body { border-color: color-mix(in srgb, #d9961a 35%, transparent); background: var(--light-warning, #fff4de); }
    .manual-steps .callout-label { display: flex; align-items: center; gap: .4rem; font-size: 13px; font-weight: 700; letter-spacing: .01em; margin-bottom: .4rem; color: var(--primary-subtle-text, var(--primary)); }
    .manual-steps .warning .callout-label { color: #a56b14; }
    :global([data-theme="dark"]) .manual-steps .warning .callout-label { color: var(--warning, #fcd34d); }
    .compact h3 { font-size: .92rem; gap: .6rem; }
    .compact .step-body { margin-left: 0; font-size: .88rem; }
    .compact .manual-step::before { display: none; }
    .compact .manual-step { padding-bottom: 1.35rem; }
    .manual-steps.compact .step-number { flex-basis: 1.5rem; height: 1.5rem; font-size: .75rem; box-shadow: none; }
    @media (max-width: 600px) { .step-body { margin-left: 0; } .manual-step::before { display: none; } }
</style>
