<script>
    import SvelteMarkdown from 'svelte-markdown';
    import ManualLink from './ManualLink.svelte';
    import ManualHtml from './ManualHtml.svelte';
    export let source = '';
</script>

<div class="manual-markdown">
    <SvelteMarkdown {source} options={{mangle: false}} renderers={{link: ManualLink, html: ManualHtml}} />
</div>

<style>
    .manual-markdown { font-size: inherit; line-height: 1.75; overflow-wrap: anywhere; }
    .manual-markdown :global(p) { margin: 0 0 .75rem; }
    .manual-markdown :global(p:last-child) { margin-bottom: 0; }
    .manual-markdown :global(ul), .manual-markdown :global(ol) { padding-left: 1.4rem; margin: .65rem 0; }
    .manual-markdown :global(li + li) { margin-top: .3rem; }
    .manual-markdown :global(a) { color: var(--primary-text, var(--primary)); font-weight: 650; text-decoration: underline; text-decoration-thickness: 1px; text-underline-offset: 3px; }
    .manual-markdown :global(strong) { font-weight: 750; }
    .manual-markdown :global(a:hover) { text-decoration-thickness: 2px; }
    .manual-markdown :global(code) { font-size: .88em; padding: .12rem .3rem; border-radius: .25rem; background: var(--primary-light, #f0f3fa); color: inherit; }
    .manual-markdown :global(pre) { padding: .85rem; overflow-x: auto; background: var(--bg-surface, #f5f6fa); border-radius: .5rem; }
    .manual-markdown :global(blockquote) { margin: 1rem 0; padding: .5rem 1rem; border-left: 3px solid var(--primary); color: var(--text-secondary, #637083); }
    .manual-markdown :global(kbd) { padding: .1rem .4rem; font-size: .85em; border: 1px solid var(--border-color, #e8e9ef); border-bottom-width: 2px; border-radius: .3rem; background: var(--bg-surface, #fff); }
    .manual-markdown :global(table) { display: table; width: 100%; margin: 1rem 0; border: 1px solid var(--border-color, #e8e9ef);
        border-radius: .75rem; border-collapse: separate; border-spacing: 0; font-size: .94em; line-height: 1.55; }
    .manual-markdown :global(th), .manual-markdown :global(td) { padding: .65rem .9rem; text-align: left !important; vertical-align: top; border: 0; border-bottom: 1px solid var(--border-color, #e8e9ef); }
    .manual-markdown :global(th + th), .manual-markdown :global(td + td) { border-left: 1px solid var(--border-color, #e8e9ef); }
    .manual-markdown :global(tr:last-child td) { border-bottom: 0; }
    .manual-markdown :global(th) { font-size: .82em; font-weight: 700; letter-spacing: .02em; color: var(--text-secondary, #637083); background: var(--bg-surface-secondary, #f5f6fa); }
    .manual-markdown :global(tbody tr:hover td) { background: color-mix(in srgb, var(--bg-surface-secondary, #f5f6fa) 60%, transparent); }
</style>
