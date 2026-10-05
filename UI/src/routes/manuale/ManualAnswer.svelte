<script>
    import {BookOpen, ArrowUpRight} from 'phosphor-svelte';
    import ManualSteps from './ManualSteps.svelte';
    export let section;
</script>

<article class="manual-answer" aria-label={section.title}>
    <div class="guide-label"><BookOpen size={15} weight="duotone" />Dal manuale d’uso <span>· {section.page_title}</span></div>
    <h2>{section.title}</h2>
    <ManualSteps {section} compact />
    <a class="guide-source" href={section.embedded_url} target="_blank" rel="noopener noreferrer"><BookOpen size={16} />Leggi nel manuale<ArrowUpRight size={15} /></a>
</article>

<style>
    .manual-answer { min-width: 0; }
    .guide-label { display: flex; align-items: center; flex-wrap: wrap; gap: .35rem; color: var(--text-muted, #637083); font-size: .75rem; margin-bottom: .65rem; }
    .guide-label :global(svg) { color: var(--primary); }
    h2 { font-size: 1.08rem; line-height: 1.45; font-weight: 700; margin: 0 0 1.3rem; }
    .guide-source { display: flex; align-items: center; gap: .45rem; font-size: .85rem; font-weight: 600; color: var(--primary); margin-top: 1.2rem; padding-top: .85rem; border-top: 1px solid var(--border-color, #e8e9ef); }
    .guide-source :global(svg:last-child) { margin-left: auto; }
</style>
