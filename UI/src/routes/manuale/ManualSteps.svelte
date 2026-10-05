<script>
    import {setContext} from 'svelte';
    import ManualMarkdown from './ManualMarkdown.svelte';
    import ManualImage from './ManualImage.svelte';
    import ManualContent from './ManualContent.svelte';
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
                {#if block.kind}<div class="callout-label">{({warning: 'Attenzione', note: 'Nota', tip: 'Suggerimento', info: 'Informazioni'})[block.kind]}</div>{/if}
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
    .manual-step:not(:last-child):not(.prose)::before { content: ''; position: absolute; left: 1rem; top: 2.6rem; bottom: .4rem; width: 1px; background: var(--border-color, #e8e9ef); }
    .manual-step:last-child { padding-bottom: 0; }
    h3 { display: flex; align-items: center; gap: .8rem; font-size: 18px; line-height: 1.5; font-weight: 700; margin: 0 0 .85rem; }
    .step-number { display: inline-grid; place-items: center; flex: 0 0 2rem; height: 2rem; border-radius: .65rem; background: var(--primary-light, #f0f3fa); color: var(--primary); font-size: .88rem; }
    .step-body { margin-left: 2.8rem; }
    .prose .step-body { margin-left: 0; }
    .note .step-body { margin: 0; padding: 1rem 1.15rem; border: 1px solid var(--border-color, #e8e9ef); border-left: 3px solid var(--primary); border-radius: .65rem; background: var(--primary-light, #f5f3ff); }
    .warning .step-body { border-left-color: #c78b24; background: var(--bg-surface, #fff); }
    .callout-label { font-size: .78rem; font-weight: 700; margin-bottom: .45rem; color: var(--primary); }
    .warning .callout-label { color: #a56b14; }
    .compact h3 { font-size: .92rem; gap: .6rem; }
    .compact .step-body { margin-left: 0; font-size: .88rem; }
    .compact .manual-step::before { display: none; }
    .compact .manual-step { padding-bottom: 1.35rem; }
    .compact .step-number { flex-basis: 1.65rem; height: 1.65rem; border-radius: .5rem; font-size: .8rem; }
    @media (max-width: 600px) { .step-body { margin-left: 0; } .manual-step::before { display: none; } }
</style>
