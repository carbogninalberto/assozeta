<script>
    import {onMount, onDestroy, tick} from 'svelte';
    import {querystring} from 'svelte-spa-router';
    import {BookOpen, MagnifyingGlass, ArrowRight, House, List} from 'phosphor-svelte';
    import {getApiHost} from 'store/instanceStore.js';
    import ManualSteps from './ManualSteps.svelte';
    import {manualChapters, manualSectionDomId} from './manualPresentation.js';
    let sections = [];
    let query = '';
    let suggestionsOpen = false;
    let suggestionIndex = -1;
    let loading = true;
    let message = '';
    let preview = false;
    let navigationOpen = false;
    let topbarHeight = 76;
    let activeSectionId = null;
    let contentsSidebar;
    let navigationGeneration = 0;
    let scrollFrame;
    let searchResults = [];
    let submittedQuery = '';
    let searching = false;
    let searchMessage = '';
    let searchGeneration = 0;
    const categories = [{prefix: 'docs/', title: 'Documentazione'}, {prefix: 'faq/', title: 'Domande frequenti'}, {prefix: 'tutorials/', title: 'Guide e tutorial'}];
    const searchWords = text => (text || '').toLocaleLowerCase('it').match(/[\p{L}\p{N}]+/gu) || [];
    $: suggestionTerms = searchWords(query);
    $: suggestions = query.trim().length < 2 || !suggestionTerms.length ? [] : sections.map(section => {
        const heading = searchWords(section.title);
        const title = searchWords(section.page_title);
        const words = [...heading, ...title, ...searchWords(section.text)];
        const matches = (tokens, term) => tokens.some(word => word.startsWith(term));
        return {section, score: suggestionTerms.every(term => matches(words, term))
            ? 1 + suggestionTerms.filter(term => matches(heading, term)).length * 3
                + suggestionTerms.filter(term => matches(title, term)).length : 0};
    }).filter(item => item.score).sort((a, b) => b.score - a.score).slice(0, 6).map(item => item.section);
    $: showSuggestions = suggestionsOpen && suggestions.length > 0;
    function chooseSuggestion(section) {
        suggestionsOpen = false;
        suggestionIndex = -1;
        submittedQuery = '';
        window.location.hash = sectionUrl(section).split('#').slice(1).join('#');
    }
    async function suggestionKeydown(event) {
        if (event.key === 'Escape') { suggestionsOpen = false; suggestionIndex = -1; return; }
        if (!suggestions.length) return;
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            suggestionsOpen = true;
            suggestionIndex = suggestionIndex < 0 ? (event.key === 'ArrowDown' ? 0 : suggestions.length - 1)
                : (suggestionIndex + (event.key === 'ArrowDown' ? 1 : -1) + suggestions.length) % suggestions.length;
            await tick();
            const option = document.getElementById('manual-suggestion-' + suggestionIndex);
            const list = option?.parentElement;
            if (list) {
                const item = option.getBoundingClientRect(), panel = list.getBoundingClientRect();
                if (item.bottom > panel.bottom) list.scrollTop += item.bottom - panel.bottom;
                else if (item.top < panel.top) list.scrollTop += item.top - panel.top;
            }
        } else if (event.key === 'Enter' && showSuggestions && suggestionIndex >= 0) {
            event.preventDefault();
            chooseSuggestion(suggestions[suggestionIndex]);
        }
    }
    $: requestedId = new URLSearchParams($querystring).get('section');
    $: requestedPage = new URLSearchParams($querystring).get('page');
    $: selected = requestedId ? sections.find(section => section.id === requestedId) : sections.find(section => section.page === requestedPage);
    $: chapters = manualChapters(sections);
    $: chapter = chapters.find(item => item.page === (selected?.page || requestedPage));
    $: groups = categories.map(category => ({...category, chapters: chapters.filter(chapter => chapter.page.startsWith(category.prefix))})).filter(group => group.chapters.length);
    $: contents = chapter?.sections || [];
    $: category = categories.find(item => chapter?.page.startsWith(item.prefix))?.title || 'Manuale d’uso';
    $: selectedIndex = chapters.findIndex(item => item.page === chapter?.page);
    $: nextSection = chapters[selectedIndex + 1];
    $: previousSection = chapters[selectedIndex - 1];
    $: if (!loading) navigateToGuide(chapter?.page, requestedId);
    $: if (contentsSidebar && activeSectionId) revealActiveSection(activeSectionId);
    const pageUrl = page => '/#/manuale?page=' + encodeURIComponent(page);
    const sectionUrl = section => section.embedded_url || '/#/manuale?section=' + encodeURIComponent(section.id);
    async function navigateToGuide(page, id) {
        const generation = ++navigationGeneration;
        await tick();
        if (generation !== navigationGeneration) return;
        const target = id && page && document.getElementById(manualSectionDomId(id));
        if (target) target.scrollIntoView({block: 'start', behavior: 'instant'});
        else window.scrollTo({top: 0, behavior: 'instant'});
        updateActiveSection();
    }
    function updateActiveSection() {
        const targets = contents.map(section => ({id: section.id, element: document.getElementById(manualSectionDomId(section.id))})).filter(item => item.element);
        let current = targets[0]?.id || null;
        for (const target of targets) {
            if (target.element.getBoundingClientRect().top <= topbarHeight + 48) current = target.id;
            else break;
        }
        activeSectionId = current;
    }
    async function revealActiveSection(id) {
        await tick();
        if (!contentsSidebar?.isConnected || id !== activeSectionId || !contentsSidebar.clientHeight) return;
        const link = contentsSidebar.querySelector('[aria-current="location"]');
        if (!link) return;
        const panel = contentsSidebar.getBoundingClientRect();
        const item = link.getBoundingClientRect();
        const inset = 12;
        const offset = item.top < panel.top + inset ? item.top - panel.top - inset
            : item.bottom > panel.bottom - inset ? item.bottom - panel.bottom + inset : 0;
        if (id === contents[0]?.id) contentsSidebar.scrollTo({top: 0, behavior: 'instant'});
        else if (offset) contentsSidebar.scrollBy({top: offset,
            behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
    }
    function trackScroll() {
        if (scrollFrame) return;
        scrollFrame = requestAnimationFrame(() => { scrollFrame = null; updateActiveSection(); });
    }
    onDestroy(() => { navigationGeneration++; cancelAnimationFrame(scrollFrame); });
    async function load() {
        loading = true;
        message = '';
        try {
            const response = await fetch(getApiHost() + '/manuale/sections');
            if (!response.ok) throw new Error('Impossibile caricare il manuale. Riprova.');
            const result = await response.json();
            sections = result.results || [];
            preview = result.status === 'development_preview';
            message = result.message || '';
        } catch (error) {
            sections = [];
            preview = false;
            message = error.message;
        } finally { loading = false; }
    }
    function reset() { searchGeneration++; query = ''; submittedQuery = ''; searching = false; searchResults = []; searchMessage = ''; window.location.hash = '#/manuale'; if (!sections.length && !loading) load(); }
    async function search() {
        suggestionsOpen = false;
        const term = query.trim();
        if (!term) { reset(); return; }
        const generation = ++searchGeneration;
        submittedQuery = term; searching = true; searchMessage = ''; searchResults = [];
        window.location.hash = '#/manuale';
        try {
            const response = await fetch(getApiHost() + '/manuale/sections?query=' + encodeURIComponent(term));
            if (!response.ok) throw new Error('Impossibile completare la ricerca. Riprova.');
            const result = await response.json();
            if (generation === searchGeneration) { searchResults = result.results || []; searchMessage = result.message || ''; }
        } catch (error) { if (generation === searchGeneration) searchMessage = error.message; }
        finally { if (generation === searchGeneration) searching = false; }
    }
    onMount(load);
</script>

<svelte:window on:scroll={trackScroll} on:resize={() => { trackScroll(); revealActiveSection(activeSectionId); }} />

<svelte:head>
    <title>{chapter ? chapter.title + ' · Manuale d’uso' : 'Manuale d’uso · Assozeta'}</title>
</svelte:head>

<div class="manual-reader" style:--manual-topbar-height={topbarHeight + 'px'}>
    <header class="manual-topbar" bind:clientHeight={topbarHeight}>
        <button class="manual-brand" on:click={reset}><BookOpen size={23} weight="duotone" /><span>Manuale d’uso</span></button>
        <form class="manual-search" on:submit|preventDefault={search} on:focusout={event => { if (!event.currentTarget.contains(event.relatedTarget)) suggestionsOpen = false; }}>
            <MagnifyingGlass size={18} />
            <label class="sr-only" for="manual-search">Cerca nel manuale</label>
            <input id="manual-search" type="search" maxlength="2000" bind:value={query} placeholder="Cerca nel manuale…"
                role="combobox" aria-autocomplete="list" aria-expanded={showSuggestions} aria-controls="manual-suggestions"
                aria-activedescendant={showSuggestions && suggestionIndex >= 0 ? 'manual-suggestion-' + suggestionIndex : undefined}
                autocomplete="off" on:focus={() => suggestionsOpen = true}
                on:input={() => { suggestionsOpen = true; suggestionIndex = -1; }} on:keydown={suggestionKeydown} />
            <button class="search-submit" disabled={loading || searching}>Cerca</button>
            {#if showSuggestions}
                <div class="manual-suggestions" id="manual-suggestions" role="listbox" aria-label="Suggerimenti di ricerca">
                    {#each suggestions as suggestion, index}
                        <button type="button" role="option" id={'manual-suggestion-' + index} aria-selected={suggestionIndex === index}
                            class:chosen={suggestionIndex === index} on:click={() => chooseSuggestion(suggestion)}>
                            <strong>{suggestion.title}</strong><span>{suggestion.page_title}</span>
                        </button>
                    {/each}
                </div>
            {/if}
        </form>
        <a class="back-to-app" href="/#/"><House size={17} /><span>Torna all’app</span></a>
    </header>
    <div class="manual-layout">
        <aside class="manual-sidebar">
            <button class="mobile-navigation" aria-expanded={navigationOpen} aria-controls="manual-navigation" on:click={() => navigationOpen = !navigationOpen}><List size={20} />Sfoglia il manuale</button>
            <nav id="manual-navigation" aria-label="Sezioni del manuale" class:open={navigationOpen}>
                <button class="navigation-home" class:active={!selected} on:click={reset}><BookOpen size={17} />Tutte le guide</button>
                {#each groups as group}
                    <div class="manual-group"><h3>{group.title}</h3>
                        {#each group.chapters as item}
                            <div class="manual-chapter"><a class="chapter-link" href={pageUrl(item.page)} aria-current={chapter?.page === item.page ? 'page' : undefined} class:active={chapter?.page === item.page} on:click={() => navigationOpen = false}>{item.title}</a></div>
                        {/each}
                    </div>
                {/each}
            </nav>
        </aside>
        <main class="manual-main" aria-busy={loading}>
            {#if preview}<p class="manual-preview-notice" role="status">Anteprima locale del manuale. Alcune procedure e immagini sono ancora in corso di verifica.</p>{/if}
            {#if loading}<p role="status" class="manual-state">Caricamento del manuale…</p>
            {:else if !sections.length}
                <div class="manual-state"><BookOpen size={32} weight="duotone" /><p role="status">{message}</p>{#if query}<button class="text-action" on:click={reset}>Tutte le guide</button>{/if}</div>
            {:else if chapter}
                <article class="manual-article" aria-label={chapter.title}>
                    <div class="manual-eyebrow">{category} <span>/</span> {chapter.title}</div>
                    <h1>{chapter.title}</h1>
                    {#if chapter.description}<p class="chapter-description">{chapter.description}</p>{/if}
                    <div class="article-meta"><span><BookOpen size={15} />Guida pratica</span><span>{chapter.sections.length} {chapter.sections.length === 1 ? 'sezione' : 'sezioni'}</span></div>
                    {#each chapter.sections as section (section.id)}
                        <section class="chapter-section" id={manualSectionDomId(section.id)} aria-label={section.title}>
                            <div class="section-heading"><h2>{section.title}</h2><a class="section-anchor" href={sectionUrl(section)} aria-label={'Apri sezione: ' + section.title}>#</a></div>
                            <ManualSteps {section} />
                        </section>
                    {/each}
                    <nav class="guide-pagination" aria-label="Altre guide">
                        {#if previousSection}<a href={pageUrl(previousSection.page)}><span>Guida precedente</span><strong>{previousSection.title}</strong></a>{/if}
                        {#if nextSection}<a class="next-guide" href={pageUrl(nextSection.page)}><span>Guida successiva</span><strong>{nextSection.title}<ArrowRight size={17} /></strong></a>{/if}
                    </nav>
                </article>
            {:else if submittedQuery}
                <div class="search-results"><div class="manual-eyebrow">Ricerca nel manuale</div><h1>Risultati per “{submittedQuery}”</h1>
                    {#if searching}<p role="status">Ricerca in corso…</p>
                    {:else if searchResults.length}
                        <p>{searchResults.length} {searchResults.length === 1 ? 'sezione trovata' : 'sezioni trovate'}</p>
                        {#each searchResults as section}<a class="search-result" href={sectionUrl(section)}><div><span>{section.page_title}</span><h2>{section.title}</h2><p>{(section.reader?.find(block => block.text)?.text || section.text).slice(0, 170)}…</p></div><ArrowRight size={18} /></a>{/each}
                    {:else}<p role="status">{searchMessage || 'Nessuna guida trovata. Prova con altre parole.'}</p>{/if}
                    <button class="text-action" on:click={reset}>Tutte le guide</button>
                </div>
            {:else}
                <div class="manual-introduction">
                    <div class="manual-hero">
                        <div class="manual-eyebrow"><BookOpen size={17} weight="duotone" />Assozeta · Manuale d’uso</div>
                        <h1>{query ? 'Risultati della ricerca' : 'Come possiamo aiutarti?'}</h1>
                        <p>{query ? 'Scegli una guida per leggere i passaggi.' : 'Le risposte, un passo alla volta. Scegli un argomento o cerca quello che vuoi fare.'}</p>
                        <span class="guide-count">{chapters.length} {chapters.length === 1 ? 'guida disponibile' : 'guide disponibili'}</span>
                    </div>
                    {#if query}<button class="text-action" on:click={reset}>Tutte le guide</button>{/if}
                    {#each groups as group}
                        <section class="topic-group" aria-label={group.title}><h2>{group.title}</h2>
                            <div class="topic-grid">{#each group.chapters as chapter}
                                <a class="topic-card" href={pageUrl(chapter.page)}><span class="topic-icon"><BookOpen size={22} weight="duotone" /></span><div><h3>{chapter.title}</h3><p>{chapter.description || chapter.sections[0].title}</p><span class="topic-count">Apri la guida</span></div><ArrowRight size={18} /></a>
                            {/each}</div>
                        </section>
                    {/each}
                </div>
            {/if}
        </main>
        <aside class="manual-contents" bind:this={contentsSidebar} aria-label="In questa pagina">
            {#if chapter && contents.length}<h3>In questa pagina</h3>{#each contents as item}<a href={sectionUrl(item)} class:active={activeSectionId === item.id} aria-current={activeSectionId === item.id ? 'location' : undefined}>{item.title}</a>{/each}{/if}
        </aside>
    </div>
</div>

<style>
    .manual-reader { --manual-border: var(--border-color, #e8e9ef); --manual-muted: var(--text-muted, #637083); width: 100%; min-height: 100vh; min-height: 100dvh; font-size: 16px; padding-top: var(--manual-topbar-height); color: var(--text-primary, #253145); background: var(--bg-surface, #fff); font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    .manual-topbar { position: fixed; top: 0; left: 0; right: 0; z-index: 10; display: flex; align-items: center; gap: 2rem; padding: 1.1rem clamp(1.5rem, 4vw, 4rem); border-bottom: 1px solid var(--manual-border); background: var(--bg-surface, #fff); }
    .manual-brand { display: inline-flex; align-items: center; gap: .7rem; padding: 0; font-weight: 700; font-size: 1.15rem; white-space: nowrap; }
    button { font: inherit; color: inherit; background: none; border: 0; cursor: pointer; }
    button:focus-visible, a:focus-visible { outline: 2px solid var(--primary); outline-offset: 4px; border-radius: .35rem; }
    button:disabled { cursor: wait; opacity: .65; }
    .manual-brand :global(svg) { color: var(--primary); }
    .manual-topbar button, .manual-search input { margin: 0; }
    .manual-preview-notice { padding: .85rem 1rem; margin: 0 0 1.5rem; border: 1px solid #e4c68b; border-radius: .6rem; background: #fff7e8; color: #78551c; font-size: .88rem; line-height: 1.6; }
    .manual-search { position: relative; display: flex; align-items: center; gap: .65rem; flex: 1; max-width: 600px; margin: 0 auto; padding: .4rem .5rem .4rem .9rem; border: 1px solid var(--manual-border); border-radius: .7rem; background: var(--bg-surface, #fff); }
    .manual-suggestions { position: absolute; top: calc(100% + 8px); left: 0; right: 0; max-height: min(380px, 60dvh); overflow-y: auto; padding: .4rem; border: 1px solid var(--manual-border); border-radius: .7rem; background: var(--bg-surface, #fff); box-shadow: 0 8px 24px #19213a20; scrollbar-width: thin; }
    .manual-suggestions button { display: block; width: 100%; text-align: left; padding: .8rem; border-radius: .4rem; }
    .manual-suggestions button:hover, .manual-suggestions button.chosen { background: var(--primary-light, #eeeafa); color: var(--primary); }
    .manual-suggestions strong, .manual-suggestions span { display: block; }
    .manual-suggestions strong { font-size: 15px; line-height: 1.4; }
    .manual-suggestions span { font-size: 13px; color: var(--manual-muted); margin-top: .3rem; }
    .manual-search:focus-within { border-color: var(--primary); box-shadow: 0 0 0 3px var(--primary-light, #eae6fb); }
    .manual-search input { min-width: 0; flex: 1; border: 0; outline: 0; background: transparent; color: inherit; padding: .4rem 0; }
    .manual-search input:focus, .manual-search input:focus-visible { border: 0; outline: none !important; box-shadow: none; }
    .search-submit { font-size: .9rem; font-weight: 600; padding: .4rem .7rem; border-radius: .4rem; color: var(--primary); background: var(--primary-light); }
    .back-to-app { display: inline-flex; align-items: center; gap: .45rem; color: var(--manual-muted); white-space: nowrap; font-size: .9rem; }
    .manual-layout { display: grid; grid-template-columns: clamp(220px, 18vw, 340px) minmax(0, 1fr) clamp(200px, 17vw, 320px); gap: clamp(1.5rem, 2vw, 3rem); width: 100%; margin: 0; padding: 2.5rem clamp(1rem, 2vw, 2.5rem); align-items: start; }
    .manual-sidebar, .manual-contents { position: sticky; top: calc(var(--manual-topbar-height) + 2rem); max-height: calc(100dvh - var(--manual-topbar-height) - 4rem); overflow-y: auto; scrollbar-width: thin; scrollbar-color: #b7b1ce transparent; scrollbar-gutter: stable; overscroll-behavior-y: contain; }
    .manual-sidebar:hover, .manual-sidebar:focus-within, .manual-contents:hover, .manual-contents:focus-within { scrollbar-color: var(--primary, #351dc2) transparent; }
    .manual-sidebar::-webkit-scrollbar, .manual-contents::-webkit-scrollbar { width: 6px; }
    .manual-sidebar::-webkit-scrollbar-track, .manual-contents::-webkit-scrollbar-track { background: transparent; }
    .manual-sidebar::-webkit-scrollbar-thumb, .manual-contents::-webkit-scrollbar-thumb { background: #b7b1ce; border-radius: 999px; }
    .manual-sidebar::-webkit-scrollbar-thumb:hover, .manual-contents::-webkit-scrollbar-thumb:hover { background: var(--primary, #351dc2); }
    .manual-sidebar { padding-right: 1rem; border-right: 1px solid var(--manual-border); }
    .navigation-home { display: flex; align-items: center; gap: .6rem; width: 100%; padding: .65rem .75rem; font-size: .9rem; font-weight: 600; border-radius: .5rem; margin-bottom: 1.75rem; }
    .navigation-home.active { color: var(--primary); background: var(--primary-light); }
    .manual-group + .manual-group { margin-top: 1.75rem; }
    .manual-group h3, .manual-contents h3 { font-size: .72rem; font-weight: 700; margin: .25rem 0 1rem; letter-spacing: .07em; text-transform: uppercase; color: var(--manual-muted); }
    .manual-chapter + .manual-chapter { margin-top: .15rem; }
    .manual-chapter a { display: block; padding: .55rem .75rem; font-size: 14px; line-height: 1.55; border-radius: .45rem; color: var(--manual-muted); border-left: 2px solid transparent; }
    .manual-chapter a:hover { background: var(--bg-surface, #f5f6fa); color: var(--primary); text-decoration: none; }
    .manual-chapter a.active { background: var(--primary-light); color: var(--primary); font-weight: 600; border-left-color: var(--primary); }
    .manual-main { min-width: 0; }
    .manual-eyebrow { display: flex; align-items: center; flex-wrap: wrap; gap: .4rem; color: var(--primary); font-size: .8rem; font-weight: 600; margin-bottom: .85rem; }
    .manual-eyebrow span { color: var(--manual-muted); padding: 0 .4rem; }
    h1 { font-size: clamp(1.8rem, 2.5vw, 2.4rem); line-height: 1.25; font-weight: 700; letter-spacing: -.04rem; margin: 0 0 1.2rem; }
    .manual-hero { padding: 2rem; border: 1px solid var(--manual-border); border-radius: 1rem; background: linear-gradient(120deg, var(--primary-light, #f0f3fa), var(--bg-surface, #fff)); }
    .manual-hero p { font-size: 17px; line-height: 1.75; color: var(--manual-muted); max-width: 550px; margin-bottom: 1.2rem; }
    .guide-count { display: inline-block; font-size: .76rem; font-weight: 600; padding: .35rem .6rem; background: var(--bg-surface, #fff); border-radius: .4rem; color: var(--manual-muted); }
    .article-meta { display: flex; align-items: center; gap: 1.2rem; color: var(--manual-muted); font-size: .8rem; padding-bottom: 1.25rem; border-bottom: 1px solid var(--manual-border); }
    .article-meta span { display: inline-flex; align-items: center; gap: .4rem; }
    .chapter-description { color: var(--manual-muted); font-size: 17px; line-height: 1.75; margin-bottom: 1.5rem; }
    .chapter-section { scroll-margin-top: calc(var(--manual-topbar-height) + 1.5rem); padding-top: 2.25rem; }
    .chapter-section + .chapter-section { border-top: 1px solid var(--manual-border); margin-top: 2.25rem; }
    .section-heading { display: flex; align-items: center; gap: .6rem; margin-bottom: 1.5rem; }
    .section-heading h2 { font-size: 22px; font-weight: 700; line-height: 1.4; margin: 0; }
    .section-anchor { font-size: 1rem; color: var(--manual-muted); padding: .2rem .4rem; opacity: .45; }
    .section-anchor:hover, .section-anchor:focus { opacity: 1; color: var(--primary); }
    .search-result { display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding: 1.25rem 0; border-bottom: 1px solid var(--manual-border); color: inherit; }
    .search-result span { font-size: .75rem; color: var(--primary); }
    .search-result h2 { font-size: 18px; margin: .4rem 0; font-weight: 600; }
    .search-result p { font-size: 15px; line-height: 1.65; color: var(--manual-muted); margin: 0; }
    .search-result:hover { text-decoration: none; } .search-result:hover h2 { color: var(--primary); }
    .search-result :global(svg) { flex-shrink: 0; color: var(--primary); }
    .guide-pagination { display: flex; gap: 1rem; border-top: 1px solid var(--manual-border); margin-top: 2.5rem; padding-top: 1.5rem; }
    .guide-pagination a { flex: 1; padding: .9rem 1rem; border: 1px solid var(--manual-border); border-radius: .65rem; color: inherit; }
    .guide-pagination a:hover { border-color: var(--primary); text-decoration: none; }
    .guide-pagination span { display: block; font-size: .75rem; color: var(--manual-muted); margin-bottom: .4rem; }
    .guide-pagination strong { display: flex; align-items: center; justify-content: space-between; gap: .6rem; font-size: .85rem; font-weight: 600; }
    .next-guide { margin-left: auto; }
    .topic-group { margin-top: 2.25rem; }
    .topic-group > h2 { font-size: 20px; font-weight: 700; margin-bottom: 1rem; }
    .topic-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .85rem; }
    .topic-card { display: flex; align-items: flex-start; gap: .8rem; border: 1px solid var(--manual-border); border-radius: .8rem; padding: 1.2rem; color: inherit; background: var(--bg-surface, #fff); transition: border-color .15s, box-shadow .15s; }
    .topic-card:hover { border-color: var(--primary); box-shadow: 0 4px 14px rgba(15,23,42,.05); text-decoration: none; }
    .topic-icon { display: grid; place-items: center; flex-shrink: 0; width: 40px; height: 40px; border-radius: .6rem; background: var(--primary-light, #f0f3fa); }
    .topic-count { display: block; margin-top: .6rem; font-size: 14px; font-weight: 700; color: var(--primary); text-decoration: underline; text-underline-offset: 3px; }
    .topic-card :global(svg) { flex-shrink: 0; color: var(--primary); }
    .topic-card > :global(svg) { margin-left: auto; }
    .topic-card h3 { font-size: 17px; font-weight: 700; line-height: 1.4; margin: 0 0 .25rem; }
    .topic-card p { color: var(--manual-muted); font-size: 15px; line-height: 1.6; margin: 0; }
    .topic-card > div { min-width: 0; }
    .topic-icon :global(svg) { display: block; margin: 0; }
    .manual-contents { border-left: 1px solid var(--manual-border); padding-left: 1rem; padding-right: .5rem; }
    .manual-contents a { display: block; text-align: left; font-size: 14px; line-height: 1.5; padding: .45rem 0; color: var(--manual-muted); }
    .manual-contents a:hover, .manual-contents a.active, .text-action { color: var(--primary); }
    .manual-contents a.active { font-weight: 700; border-left: 2px solid var(--primary); padding-left: .65rem; background: var(--primary-light); border-radius: .25rem; }
    .text-action { padding: .3rem 0; font-weight: 600; font-size: .9rem; }
    .manual-state { padding: 3rem 0; color: var(--manual-muted); line-height: 1.75; }
    .mobile-navigation { display: none; }
    @media (max-width: 1023px) { .manual-layout { grid-template-columns: 220px minmax(0, 1fr); gap: 2rem; } .manual-contents { display: none; } }
    @media (max-width: 767px) {
        .manual-topbar { flex-wrap: wrap; gap: 1rem; padding: 1rem; }
        .manual-search { order: 3; flex-basis: 100%; max-width: none; }
        .back-to-app { margin-left: auto; } .back-to-app span { display: none; }
        .manual-layout { display: block; padding: 1.25rem 1rem; }
        .manual-sidebar { position: static; max-height: none; margin-bottom: 1.5rem; border-right: 0; padding-right: 0; }
        .manual-hero { padding: 1.3rem; }
        .mobile-navigation { display: flex; align-items: center; gap: .6rem; padding: .5rem 0; font-weight: 600; }
        #manual-navigation { display: none; padding: 1rem 0; } #manual-navigation.open { display: block; }
        .topic-grid { grid-template-columns: 1fr; }
    }
</style>
