<script>
    import {getContext} from 'svelte';
    import {ArrowUpRight, BookOpen, Users, CalendarBlank, Ticket, ListChecks, Wallet, Receipt, Calculator, ChartBar, Megaphone, Archive, GearSix, GraduationCap, GridFour, TreeStructure, Tent, Info, Lightbulb, Warning} from 'phosphor-svelte';
    import ManualMarkdown from './ManualMarkdown.svelte';
    import ManualImage from './ManualImage.svelte';
    import {manualLinkUrl} from './manualPresentation.js';
    export let content = [];
    export let section;
    export let compact = false;
    const manualPage = getContext('manualPage');
    const icons = {users: Users, 'users-gear': Users, calendar: CalendarBlank, ticket: Ticket,
        'grid-2': GridFour, 'diagram-cells': TreeStructure, 'chalkboard-user': GraduationCap, campground: Tent,
        'list-check': ListChecks, wallet: Wallet, 'file-lines': Receipt, calculator: Calculator,
        'chart-simple': ChartBar, 'party-horn': Megaphone, 'box-archive': Archive, gear: GearSix,
        'graduation-cap': GraduationCap};
    const labels = {warning: 'Attenzione', note: 'Nota', tip: 'Suggerimento', info: 'Informazioni'};
</script>

<div class="manual-content" class:compact>
    {#each content as part}
        {#if part.kind === 'markdown'}
            <div class="content-prose"><ManualMarkdown source={part.markdown} /></div>
        {:else if part.kind === 'image'}
            {@const image = section.screenshots.find(item => item.path === part.path)}
            {#if image}{#key image.url + image.sha256}<ManualImage url={image.url} alt={part.alt || section.title} {compact} />{/key}{/if}
        {:else if part.kind === 'callout'}
            <aside class="content-callout" class:warning={part.style === 'warning'} aria-label={labels[part.style] || 'Nota'}>
                <div class="callout-label"><svelte:component this={({warning: Warning, tip: Lightbulb})[part.style] || Info} size={16} weight="fill" />{labels[part.style] || 'Nota'}</div>
                <svelte:self content={part.content} {section} {compact} />
            </aside>
        {:else if part.kind === 'cards' || part.kind === 'card'}
            <div class="manual-cards" style={'--columns: ' + Math.min(3, Math.max(1, Number(part.columns) || 2))}>
                {#each part.kind === 'cards' ? part.cards : [part] as card}
                    {@const href = manualLinkUrl(card.href, manualPage?.() || section.page)}
                    <article class="manual-card">
                        <div class="card-icon"><svelte:component this={icons[card.icon] || BookOpen} size={21} weight="duotone" /></div>
                        {#if href}
                            <a class="card-title" {href}>{card.title}<ArrowUpRight size={17} /></a>
                        {:else}<h4 class="card-title">{card.title}</h4>{/if}
                        <svelte:self content={card.content} {section} {compact} />
                    </article>
                {/each}
            </div>
        {/if}
    {/each}
</div>

<style>
    .manual-content { min-width: 0; }
    .manual-content > :global(* + *) { margin-top: 1rem; }
    .content-callout { padding: 1rem 1.2rem; border: 1px solid color-mix(in srgb, var(--primary) 18%, transparent); border-radius: .75rem; background: var(--primary-light, #f5f3ff); }
    .content-callout.warning { border-color: color-mix(in srgb, #d9961a 35%, transparent); background: var(--light-warning, #fff4de); }
    .manual-content .callout-label { display: flex; align-items: center; gap: .4rem; color: var(--primary-subtle-text, var(--primary)); font-size: 13px; font-weight: 700; letter-spacing: .01em; margin-bottom: .4rem; }
    .manual-content .warning .callout-label { color: #a56b14; }
    :global([data-theme="dark"]) .manual-content .warning .callout-label { color: var(--warning, #fcd34d); }
    .manual-cards { display: grid; grid-template-columns: repeat(var(--columns), minmax(0, 1fr)); gap: 1rem; }
    .manual-card { padding: 1.25rem; border: 1px solid var(--border-color, #e8e9ef); border-radius: .85rem; background: var(--bg-surface, #fff); transition: border-color .15s, box-shadow .15s; }
    .manual-card:hover, .manual-card:focus-within { border-color: color-mix(in srgb, var(--primary) 55%, var(--border-color, #e8e9ef)); box-shadow: 0 14px 30px -18px rgba(15, 23, 42, .35); }
    .manual-content .card-icon { display: grid; place-items: center; width: 38px; height: 38px; border-radius: .65rem; background: var(--primary-light, #f0f3fa); color: var(--primary-subtle-text, var(--primary)); margin-bottom: .8rem; }
    .card-title { display: flex; align-items: center; justify-content: space-between; gap: .6rem; font-size: 17px; font-weight: 700; line-height: 1.5; color: inherit; margin: 0 0 .6rem; text-decoration: none; }
    .card-title :global(svg) { flex: 0 0 auto; color: var(--text-muted, #637083); }
    a.card-title { color: var(--primary-text, var(--primary)); text-decoration: none; }
    a.card-title:hover { color: var(--primary-text, var(--primary)); text-decoration: underline; text-underline-offset: 3px; }
    a.card-title:focus-visible { outline: 2px solid var(--primary); outline-offset: 4px; border-radius: .2rem; }
    .manual-card > :global(.manual-content) { color: var(--text-muted, #637083); font-size: 15px; }
    .compact .manual-cards { grid-template-columns: minmax(0, 1fr); gap: .7rem; }
    .compact .manual-card { padding: .9rem; }
    @media (max-width: 700px) { .manual-cards { grid-template-columns: minmax(0, 1fr); } }
</style>
