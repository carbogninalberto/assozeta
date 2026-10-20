<script>
    import {onMount, onDestroy, tick} from 'svelte';
    import {querystring} from 'svelte-spa-router';
    import {BookOpen, MagnifyingGlass, ArrowRight, ArrowLeft, ArrowUp, House, List, CaretDown, LinkSimple, Clock, Question, Lightbulb, Compass,
        Archive, SquaresFour, Calculator, CalendarBlank, Tent, Ticket, UsersThree, Megaphone, Bank, GraduationCap, GearSix,
        ChalkboardTeacher, IdentificationCard, CreditCard, ListChecks, Receipt, Tag, ClipboardText, Export, FirstAid, TreeStructure, ShieldCheck} from 'phosphor-svelte';
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
    let searchInput;
    let readingProgress = 0;
    let scrolledDown = false;
    const categories = [{prefix: 'docs/', title: 'Documentazione', icon: BookOpen, id: 'manual-topic-docs'},
        {prefix: 'faq/', title: 'Domande frequenti', icon: Question, id: 'manual-topic-faq'},
        {prefix: 'tutorials/', title: 'Guide e tutorial', icon: Lightbulb, id: 'manual-topic-tutorials'}];
    // First matching keyword of the page slug picks the chapter icon; the category icon is the fallback.
    const chapterIcons = [[/introduzione/, Compass], [/archivi/, Archive], [/bacheca/, SquaresFour], [/bilancio/, Calculator],
        [/calendario/, CalendarBlank], [/camp/, Tent], [/presenze/, ListChecks], [/carnet/, Ticket], [/collaboratori/, UsersThree],
        [/comunicazioni|email/, Megaphone], [/contabilita/, Bank], [/istruttori/, ChalkboardTeacher], [/corsi/, GraduationCap],
        [/impostazioni|anno-sportivo/, GearSix], [/soci|codici-fiscali|tessere/, IdentificationCard], [/stripe|pagamenti|rateizzati/, CreditCard],
        [/ricevute/, Receipt], [/tag/, Tag], [/moduli|link-iscrizioni/, ClipboardText], [/esportare/, Export], [/certificati/, FirstAid],
        [/workflow|automazioni/, TreeStructure], [/sicuro|autenticazione/, ShieldCheck]];
    const chapterIcon = page => chapterIcons.find(([pattern]) => pattern.test(page))?.[1]
        || categories.find(item => page.startsWith(item.prefix))?.icon || BookOpen;
    const chapterPreview = item => item.description || item.sections.map(section => section.title)
        .filter(title => title.toLocaleLowerCase('it') !== 'introduzione').slice(0, 3).join(' · ') || item.sections[0]?.title;
    const sectionCount = count => count + (count === 1 ? ' sezione' : ' sezioni');
    function highlight(text, terms) {
        const words = terms.filter(term => term.length > 1).map(term => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
        if (!words.length) return [{text}];
        // A capturing split puts every match at an odd index.
        return text.split(new RegExp('(' + words.join('|') + ')', 'giu'))
            .map((part, index) => ({text: part, match: index % 2 === 1})).filter(part => part.text);
    }
    function focusSearch(event) {
        const target = event.target;
        if (event.key !== '/' || event.metaKey || event.ctrlKey || event.altKey || target?.isContentEditable
            || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target?.tagName)) return;
        event.preventDefault();
        searchInput?.focus();
    }
    function jumpTo(id) {
        document.getElementById(id)?.scrollIntoView({block: 'start',
            behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
    }
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
    $: readingMinutes = chapter ? Math.max(1, Math.round(chapter.sections.reduce((total, section) => total + searchWords(section.text).length, 0) / 200)) : 0;
    $: introduction = chapters.find(item => item.page === 'docs/introduzione');
    $: resultTerms = searchWords(submittedQuery);
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
        const scrollable = document.documentElement.scrollHeight - window.innerHeight;
        readingProgress = scrollable > 0 ? Math.min(1, Math.max(0, window.scrollY / scrollable)) : 0;
        scrolledDown = window.scrollY > 600;
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

<svelte:window on:scroll={trackScroll} on:keydown={focusSearch} on:resize={() => { trackScroll(); revealActiveSection(activeSectionId); }} />

<svelte:head>
    <title>{chapter ? chapter.title + ' · Manuale d’uso' : 'Manuale d’uso · Assozeta'}</title>
</svelte:head>

<div class="manual-reader" style:--manual-topbar-height={topbarHeight + 'px'}>
    <header class="manual-topbar" bind:clientHeight={topbarHeight}>
        <button class="manual-brand" on:click={reset}><span class="brand-mark"><BookOpen size={20} weight="duotone" /></span><span>Manuale d’uso</span></button>
        <form class="manual-search" on:submit|preventDefault={search} on:focusout={event => { if (!event.currentTarget.contains(event.relatedTarget)) suggestionsOpen = false; }}>
            <MagnifyingGlass size={18} />
            <label class="sr-only" for="manual-search">Cerca nel manuale</label>
            <input id="manual-search" type="search" maxlength="2000" bind:value={query} bind:this={searchInput} placeholder="Cerca nel manuale…"
                role="combobox" aria-autocomplete="list" aria-expanded={showSuggestions} aria-controls="manual-suggestions"
                aria-activedescendant={showSuggestions && suggestionIndex >= 0 ? 'manual-suggestion-' + suggestionIndex : undefined}
                autocomplete="off" on:focus={() => suggestionsOpen = true}
                on:input={() => { suggestionsOpen = true; suggestionIndex = -1; }} on:keydown={suggestionKeydown} />
            {#if !query}<kbd class="search-shortcut" aria-hidden="true">/</kbd>{/if}
            <button class="search-submit" disabled={loading || searching}>Cerca</button>
            {#if showSuggestions}
                <div class="manual-suggestions" id="manual-suggestions" role="listbox" aria-label="Suggerimenti di ricerca">
                    {#each suggestions as suggestion, index}
                        <button type="button" role="option" id={'manual-suggestion-' + index} aria-selected={suggestionIndex === index}
                            class:chosen={suggestionIndex === index} on:click={() => chooseSuggestion(suggestion)}>
                            <span class="suggestion-icon"><svelte:component this={chapterIcon(suggestion.page)} size={17} weight="duotone" /></span>
                            <span class="suggestion-text"><strong>{suggestion.title}</strong><span>{suggestion.page_title}</span></span>
                        </button>
                    {/each}
                </div>
            {/if}
        </form>
        <a class="back-to-app menu-link" href="/#/"><House size={17} /><span>Torna all’app</span></a>
        {#if chapter}<div class="reading-progress" style:transform={'scaleX(' + readingProgress + ')'} aria-hidden="true"></div>{/if}
    </header>
    <div class="manual-layout" class:without-contents={!chapter}>
        <aside class="manual-sidebar">
            <button class="mobile-navigation" aria-expanded={navigationOpen} aria-controls="manual-navigation" on:click={() => navigationOpen = !navigationOpen}>
                <List size={20} /><span>{chapter ? chapter.title : 'Sfoglia il manuale'}</span><CaretDown size={16} class="mobile-caret" />
            </button>
            <nav id="manual-navigation" aria-label="Sezioni del manuale" class:open={navigationOpen}>
                <button class="navigation-home" class:active={!selected} on:click={() => { navigationOpen = false; reset(); }}><House size={17} />Tutte le guide</button>
                {#each groups as group}
                    <div class="manual-group"><h3><svelte:component this={group.icon} size={14} weight="bold" />{group.title}</h3>
                        {#each group.chapters as item}
                            <div class="manual-chapter"><a class="chapter-link menu-link" href={pageUrl(item.page)} aria-current={chapter?.page === item.page ? 'page' : undefined} class:active={chapter?.page === item.page} on:click={() => navigationOpen = false}>{item.title}</a></div>
                        {/each}
                    </div>
                {/each}
            </nav>
        </aside>
        <main class="manual-main" aria-busy={loading}>
            {#if preview}<p class="manual-preview-notice" role="status">Anteprima locale del manuale. Alcune procedure e immagini sono ancora in corso di verifica.</p>{/if}
            {#if loading}
                <div class="manual-skeleton" aria-hidden="true"><span></span><span></span><span></span><span></span></div>
                <p role="status" class="sr-only">Caricamento del manuale…</p>
            {:else if !sections.length}
                <div class="manual-state"><span class="state-icon"><BookOpen size={30} weight="duotone" /></span><p role="status">{message}</p>{#if query}<button class="text-action" on:click={reset}>Tutte le guide</button>{/if}</div>
            {:else if chapter}
                <article class="manual-article" aria-label={chapter.title}>
                    <div class="manual-eyebrow"><button class="crumb" on:click={reset}>Manuale</button><span aria-hidden="true">/</span>{category}</div>
                    <div class="article-title">
                        <span class="title-icon"><svelte:component this={chapterIcon(chapter.page)} size={26} weight="duotone" /></span>
                        <h1>{chapter.title}</h1>
                    </div>
                    {#if chapter.description}<p class="chapter-description">{chapter.description}</p>{/if}
                    <div class="article-meta"><span><BookOpen size={15} />{sectionCount(chapter.sections.length)}</span><span><Clock size={15} />{readingMinutes} min di lettura</span></div>
                    {#each chapter.sections as section (section.id)}
                        <section class="chapter-section" id={manualSectionDomId(section.id)} aria-label={section.title}>
                            <div class="section-heading"><h2>{section.title}</h2><a class="section-anchor menu-link" href={sectionUrl(section)} aria-label={'Apri sezione: ' + section.title}><LinkSimple size={18} /></a></div>
                            <ManualSteps {section} />
                        </section>
                    {/each}
                    <nav class="guide-pagination" aria-label="Altre guide">
                        {#if previousSection}<a class="menu-link" href={pageUrl(previousSection.page)}><span><ArrowLeft size={14} />Guida precedente</span><strong>{previousSection.title}</strong></a>{/if}
                        {#if nextSection}<a class="next-guide menu-link" href={pageUrl(nextSection.page)}><span>Guida successiva<ArrowRight size={14} /></span><strong>{nextSection.title}</strong></a>{/if}
                    </nav>
                </article>
            {:else if submittedQuery}
                <div class="search-results"><div class="manual-eyebrow"><button class="crumb" on:click={reset}>Manuale</button><span aria-hidden="true">/</span>Ricerca</div><h1>Risultati per “{submittedQuery}”</h1>
                    {#if searching}<div class="manual-skeleton compact" aria-hidden="true"><span></span><span></span><span></span></div><p role="status" class="sr-only">Ricerca in corso…</p>
                    {:else if searchResults.length}
                        <p class="results-count">{searchResults.length} {searchResults.length === 1 ? 'sezione trovata' : 'sezioni trovate'}</p>
                        <div class="search-result-list">
                            {#each searchResults as section}<a class="search-result menu-link" href={sectionUrl(section)}>
                                <span class="result-icon"><svelte:component this={chapterIcon(section.page)} size={20} weight="duotone" /></span>
                                <div><span class="result-page">{section.page_title}</span><h2>{#each highlight(section.title, resultTerms) as part}{#if part.match}<mark>{part.text}</mark>{:else}{part.text}{/if}{/each}</h2>
                                    <p>{#each highlight((section.reader?.find(block => block.text)?.text || section.text).slice(0, 170) + '…', resultTerms) as part}{#if part.match}<mark>{part.text}</mark>{:else}{part.text}{/if}{/each}</p></div>
                                <ArrowRight size={18} /></a>{/each}
                        </div>
                    {:else}<div class="manual-state compact"><span class="state-icon"><MagnifyingGlass size={26} /></span><p role="status">{searchMessage || 'Nessuna guida trovata. Prova con altre parole.'}</p></div>{/if}
                    <button class="text-action" on:click={reset}><ArrowLeft size={15} />Tutte le guide</button>
                </div>
            {:else}
                <div class="manual-introduction">
                    <div class="manual-hero">
                        <div class="hero-copy">
                            <div class="manual-eyebrow"><BookOpen size={17} weight="duotone" />Assozeta · Manuale d’uso</div>
                            <h1>{query ? 'Risultati della ricerca' : 'Come possiamo aiutarti?'}</h1>
                            <p>{query ? 'Scegli una guida per leggere i passaggi.' : 'Le risposte, un passo alla volta. Scegli un argomento o cerca quello che vuoi fare.'}</p>
                            <div class="hero-topics">
                                {#each groups as group}<button on:click={() => jumpTo(group.id)}><svelte:component this={group.icon} size={16} weight="duotone" />{group.title}<span>{group.chapters.length}</span></button>{/each}
                            </div>
                        </div>
                        {#if introduction}
                            <a class="hero-start menu-link" href={pageUrl(introduction.page)}>
                                <span class="topic-icon"><Compass size={22} weight="duotone" /></span>
                                <span class="start-label">Nuovo su Assozeta?</span>
                                <strong>Inizia dall’introduzione</strong>
                                <span class="start-link">Apri la guida<ArrowRight size={15} /></span>
                            </a>
                        {/if}
                    </div>
                    {#if query}<button class="text-action" on:click={reset}>Tutte le guide</button>{/if}
                    {#each groups as group}
                        <section class="topic-group" id={group.id} aria-label={group.title}>
                            <div class="group-heading"><h2>{group.title}</h2><span>{group.chapters.length} {group.chapters.length === 1 ? 'guida' : 'guide'}</span></div>
                            {#if group.prefix === 'faq/'}
                                <div class="faq-list">{#each group.chapters as chapter}
                                    <a class="faq-item menu-link" href={pageUrl(chapter.page)}><Question size={19} weight="duotone" /><span>{chapter.title}</span><ArrowRight size={16} /></a>
                                {/each}</div>
                            {:else}
                                <div class="topic-grid">{#each group.chapters as chapter}
                                    <a class="topic-card menu-link" href={pageUrl(chapter.page)}>
                                        <span class="topic-icon"><svelte:component this={chapterIcon(chapter.page)} size={22} weight="duotone" /></span>
                                        <h3>{chapter.title}</h3>
                                        <p>{chapterPreview(chapter)}</p>
                                        <span class="topic-footer">{sectionCount(chapter.sections.length)}<ArrowRight size={16} /></span>
                                    </a>
                                {/each}</div>
                            {/if}
                        </section>
                    {/each}
                </div>
            {/if}
        </main>
        <aside class="manual-contents" bind:this={contentsSidebar} aria-label="In questa pagina">
            {#if chapter && contents.length}<h3>In questa pagina</h3><div class="contents-list">{#each contents as item}<a class="menu-link" href={sectionUrl(item)} class:active={activeSectionId === item.id} aria-current={activeSectionId === item.id ? 'location' : undefined}>{item.title}</a>{/each}</div>{/if}
        </aside>
    </div>
    {#if chapter && scrolledDown}<button class="back-to-top" aria-label="Torna all’inizio" on:click={() => window.scrollTo({top: 0, behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'})}><ArrowUp size={18} weight="bold" /></button>{/if}
</div>

<style>
    .manual-reader { --manual-border: var(--border-color, #e8e9ef); --manual-muted: var(--text-secondary, #637083); --manual-accent: var(--primary-text, var(--primary));
        --manual-tint: var(--primary-light, #eeeafa); --manual-tint-text: var(--primary-subtle-text, var(--primary)); --manual-soft: var(--bg-surface-secondary, #f5f6fa);
        --manual-radius: .85rem; width: 100%; min-height: 100vh; min-height: 100dvh; font-size: 16px; padding-top: var(--manual-topbar-height);
        color: var(--text-primary, #253145); background: var(--bg-surface, #fff); font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    button { font: inherit; color: inherit; background: none; border: 0; cursor: pointer; }
    button:focus-visible, a:focus-visible { outline: 2px solid var(--manual-accent); outline-offset: 3px; border-radius: .4rem; }
    button:disabled { cursor: wait; opacity: .65; }
    a:hover { text-decoration: none; }

    /* Top bar */
    .manual-topbar { position: fixed; top: 0; left: 0; right: 0; z-index: 10; display: flex; align-items: center; gap: 2rem; padding: .8rem clamp(1rem, 3vw, 2.5rem);
        border-bottom: 1px solid var(--manual-border); background: color-mix(in srgb, var(--bg-surface, #fff) 88%, transparent); backdrop-filter: saturate(1.6) blur(12px); -webkit-backdrop-filter: saturate(1.6) blur(12px); }
    .manual-topbar button, .manual-search input { margin: 0; }
    .manual-brand { display: inline-flex; align-items: center; gap: .65rem; padding: 0; font-weight: 700; font-size: 17px; letter-spacing: -.01em; white-space: nowrap; }
    .manual-reader .brand-mark { display: grid; place-items: center; width: 34px; height: 34px; border-radius: .6rem; background: var(--primary); color: var(--on-primary, #fff); }
    .brand-mark :global(svg) { display: block; }
    .reading-progress { position: absolute; left: 0; right: 0; bottom: -1px; height: 2px; background: var(--primary); transform-origin: left; transition: transform .1s linear; }
    .manual-search { position: relative; display: flex; align-items: center; gap: .6rem; flex: 1; max-width: 620px; margin: 0 auto; padding: .3rem .35rem .3rem .9rem;
        border: 1px solid transparent; border-radius: .75rem; background: var(--manual-soft); color: var(--manual-muted); transition: border-color .15s, box-shadow .15s, background-color .15s; }
    .manual-search:hover { border-color: var(--manual-border); }
    .manual-search:focus-within { border-color: var(--manual-accent); background: var(--bg-surface, #fff); box-shadow: 0 0 0 4px var(--manual-tint); }
    .manual-search input { min-width: 0; flex: 1; border: 0; outline: 0; background: transparent; color: var(--text-primary, inherit); padding: .45rem 0; }
    .manual-search input::placeholder { color: var(--manual-muted); }
    .manual-search input:focus, .manual-search input:focus-visible { border: 0; outline: none !important; box-shadow: none; }
    .search-shortcut { display: grid; place-items: center; min-width: 1.5rem; height: 1.5rem; padding: 0 .35rem; font-family: inherit; font-size: 12px; font-weight: 600; line-height: 1; color: var(--manual-muted);
        border: 1px solid var(--manual-border); border-bottom-width: 2px; border-radius: .35rem; background: var(--bg-surface, #fff); }
    .manual-search:focus-within .search-shortcut { display: none; }
    .search-submit { font-size: 14px; font-weight: 600; padding: .45rem .9rem; border-radius: .5rem; color: var(--on-primary, #fff); background: var(--primary); transition: filter .15s; }
    .search-submit:hover { filter: brightness(1.08); }
    .manual-suggestions { position: absolute; top: calc(100% + 8px); left: 0; right: 0; max-height: min(400px, 60dvh); overflow-y: auto; padding: .4rem; border: 1px solid var(--manual-border);
        border-radius: var(--manual-radius); background: var(--bg-dropdown, var(--bg-surface, #fff)); box-shadow: 0 16px 40px -12px rgba(15, 23, 42, .25); scrollbar-width: thin; }
    .manual-suggestions button { display: flex; align-items: center; gap: .75rem; width: 100%; text-align: left; padding: .65rem .7rem; border-radius: .55rem; color: var(--text-primary, inherit); }
    .manual-suggestions button:hover, .manual-suggestions button.chosen { background: var(--manual-tint); }
    .manual-reader .suggestion-icon { display: grid; place-items: center; flex: 0 0 32px; height: 32px; border-radius: .5rem; background: var(--manual-soft); color: var(--manual-accent); }
    .chosen .suggestion-icon, .manual-suggestions button:hover .suggestion-icon { background: var(--bg-surface, #fff); }
    .suggestion-text { min-width: 0; }
    .suggestion-text strong, .suggestion-text span { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .suggestion-text strong { font-size: 14.5px; font-weight: 600; line-height: 1.4; }
    .manual-reader .suggestion-text span { font-size: 12.5px; color: var(--manual-muted); margin-top: .1rem; }
    .back-to-app { display: inline-flex; align-items: center; gap: .45rem; padding: .5rem .75rem; border-radius: .55rem; color: var(--manual-muted); white-space: nowrap; font-size: 14px; font-weight: 500; transition: background-color .15s, color .15s; }
    .back-to-app:hover { background: var(--bg-hover, #f4f6f9); color: var(--text-primary, inherit); }
    .manual-preview-notice { padding: .85rem 1rem; margin: 0 0 1.5rem; border: 1px solid #e4c68b; border-radius: .6rem; background: var(--light-warning, #fff7e8); color: #78551c; font-size: 14px; line-height: 1.6; }

    /* Layout */
    .manual-layout { display: grid; grid-template-columns: clamp(230px, 17vw, 290px) minmax(0, 1fr) clamp(200px, 16vw, 260px); gap: clamp(1.5rem, 3vw, 3.5rem);
        width: 100%; max-width: 1560px; margin: 0 auto; padding: 2rem clamp(1rem, 2.5vw, 2.5rem) 4rem; align-items: start; }
    .manual-layout.without-contents { grid-template-columns: clamp(230px, 17vw, 290px) minmax(0, 1fr); }
    .manual-layout.without-contents .manual-contents { display: none; }
    .manual-sidebar, .manual-contents { position: sticky; top: calc(var(--manual-topbar-height) + 2rem); max-height: calc(100dvh - var(--manual-topbar-height) - 4rem); overflow-y: auto;
        scrollbar-width: thin; scrollbar-color: transparent transparent; scrollbar-gutter: stable; overscroll-behavior-y: contain; }
    .manual-sidebar:hover, .manual-sidebar:focus-within, .manual-contents:hover, .manual-contents:focus-within { scrollbar-color: var(--scrollbar-thumb-hover, #b7b1ce) transparent; }
    .manual-sidebar::-webkit-scrollbar, .manual-contents::-webkit-scrollbar { width: 6px; }
    .manual-sidebar::-webkit-scrollbar-track, .manual-contents::-webkit-scrollbar-track { background: transparent; }
    .manual-sidebar::-webkit-scrollbar-thumb, .manual-contents::-webkit-scrollbar-thumb { background: var(--scrollbar-thumb-hover, #b7b1ce); border-radius: 999px; }
    .manual-main { min-width: 0; }

    /* Sidebar */
    .manual-sidebar { padding-right: .5rem; }
    .navigation-home { display: flex; align-items: center; gap: .6rem; width: 100%; padding: .55rem .75rem; font-size: 14.5px; font-weight: 600; border-radius: .55rem; margin-bottom: 1.5rem; color: var(--manual-muted); transition: background-color .15s, color .15s; }
    .navigation-home:hover { background: var(--bg-hover, #f4f6f9); color: var(--text-primary, inherit); }
    .navigation-home.active { color: var(--manual-tint-text); background: var(--manual-tint); }
    .manual-group + .manual-group { margin-top: 1.5rem; }
    .manual-group h3, .manual-contents h3 { display: flex; align-items: center; gap: .45rem; font-size: 11px; font-weight: 700; margin: 0 0 .5rem; padding: 0 .75rem; letter-spacing: .08em; text-transform: uppercase; color: var(--manual-muted); }
    .manual-contents h3 { padding: 0; margin-bottom: .85rem; }
    .manual-chapter + .manual-chapter { margin-top: 1px; }
    .manual-chapter a { display: block; padding: .42rem .75rem; font-size: 14px; line-height: 1.45; border-radius: .5rem; color: var(--text-primary, inherit); opacity: .82; transition: background-color .12s, opacity .12s; }
    .manual-chapter a:hover { background: var(--bg-hover, #f4f6f9); opacity: 1; }
    .manual-chapter a.active { background: var(--manual-tint); color: var(--manual-tint-text); font-weight: 600; opacity: 1; }
    .mobile-navigation { display: none; }

    /* Shared headings */
    .manual-reader .manual-eyebrow { display: flex; align-items: center; flex-wrap: wrap; gap: .45rem; color: var(--manual-muted); font-size: 13px; font-weight: 500; margin-bottom: .9rem; }
    .manual-reader .manual-eyebrow > span { opacity: .5; }
    .manual-reader .manual-eyebrow .crumb { padding: 0; color: var(--manual-accent); font-weight: 600; }
    .manual-eyebrow .crumb:hover { text-decoration: underline; text-underline-offset: 3px; }
    h1 { font-size: clamp(29.5px, 2.6vw, 40px); line-height: 1.2; font-weight: 750; letter-spacing: -.025em; margin: 0 0 1rem; }

    /* Article */
    .manual-article, .search-results { max-width: 800px; margin: 0 auto; }
    .article-title { display: flex; align-items: center; gap: 1rem; }
    .article-title h1 { margin: 0; }
    .manual-reader .title-icon { display: grid; place-items: center; flex: 0 0 52px; height: 52px; border-radius: .9rem; background: var(--manual-tint); color: var(--manual-tint-text); }
    .title-icon :global(svg) { display: block; }
    .chapter-description { color: var(--manual-muted); font-size: 17px; line-height: 1.75; margin: 1rem 0 0; }
    .article-meta { display: flex; align-items: center; flex-wrap: wrap; gap: .5rem; margin-top: 1.25rem; padding-bottom: 1.5rem; border-bottom: 1px solid var(--manual-border); }
    .manual-reader .article-meta span { display: inline-flex; align-items: center; gap: .4rem; padding: .3rem .65rem; font-size: 13px; font-weight: 500; color: var(--manual-muted); background: var(--manual-soft); border-radius: 999px; }
    .chapter-section { scroll-margin-top: calc(var(--manual-topbar-height) + 1.5rem); padding-top: 2.5rem; }
    .chapter-section + .chapter-section { border-top: 1px solid var(--manual-border); margin-top: 2.5rem; }
    .section-heading { display: flex; align-items: center; gap: .5rem; margin-bottom: 1.25rem; }
    .section-heading h2 { font-size: 23px; font-weight: 700; line-height: 1.35; letter-spacing: -.015em; margin: 0; }
    .section-anchor { display: grid; place-items: center; width: 30px; height: 30px; flex: 0 0 30px; border-radius: .45rem; color: var(--manual-muted); opacity: 0; transition: opacity .15s, background-color .15s, color .15s; }
    .section-heading:hover .section-anchor, .section-anchor:focus-visible { opacity: 1; }
    .section-anchor:hover { background: var(--manual-tint); color: var(--manual-tint-text); }
    @media (hover: none) { .section-anchor { opacity: .55; } }
    .guide-pagination { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; border-top: 1px solid var(--manual-border); margin-top: 3rem; padding-top: 2rem; }
    .guide-pagination a { display: block; padding: 1rem 1.15rem; border: 1px solid var(--manual-border); border-radius: var(--manual-radius); color: inherit; transition: border-color .15s, background-color .15s; }
    .guide-pagination a:hover { border-color: var(--manual-accent); background: var(--manual-tint); }
    .manual-reader .guide-pagination span { display: flex; align-items: center; gap: .35rem; font-size: 12.5px; color: var(--manual-muted); margin-bottom: .35rem; }
    .manual-reader .guide-pagination strong { display: block; font-size: 15px; font-weight: 650; color: var(--manual-accent); }
    .next-guide { grid-column: 2; text-align: right; }
    .next-guide span { justify-content: flex-end; }
    .back-to-top { position: fixed; right: clamp(1rem, 3vw, 2rem); bottom: clamp(1rem, 3vw, 2rem); z-index: 5; display: grid; place-items: center; width: 44px; height: 44px; border-radius: 999px;
        color: var(--manual-accent); background: var(--bg-surface, #fff); border: 1px solid var(--manual-border); box-shadow: 0 8px 24px -8px rgba(15, 23, 42, .3); transition: transform .15s; }
    .back-to-top:hover { transform: translateY(-2px); }

    /* Table of contents */
    .manual-contents { padding-left: .25rem; }
    .contents-list { border-left: 1px solid var(--manual-border); }
    .manual-contents a { display: block; margin-left: -1px; padding: .38rem 0 .38rem .9rem; border-left: 2px solid transparent; font-size: 13.5px; line-height: 1.45; color: var(--manual-muted); transition: color .12s, border-color .12s; }
    .manual-contents a:hover { color: var(--text-primary, inherit); border-left-color: var(--manual-border); }
    .manual-contents a.active { color: var(--manual-accent); font-weight: 600; border-left-color: var(--primary); }

    /* Search results */
    .results-count { color: var(--manual-muted); font-size: 14.5px; margin: -.25rem 0 1.25rem; }
    .search-result-list { display: grid; gap: .6rem; }
    .search-result { display: flex; align-items: flex-start; gap: 1rem; padding: 1.1rem 1.15rem; border: 1px solid var(--manual-border); border-radius: var(--manual-radius); color: inherit; transition: border-color .15s, background-color .15s; }
    .search-result:hover { border-color: var(--manual-accent); background: color-mix(in srgb, var(--manual-tint) 45%, transparent); }
    .search-result > div { flex: 1; min-width: 0; }
    .manual-reader .result-icon { display: grid; place-items: center; flex: 0 0 40px; height: 40px; border-radius: .65rem; background: var(--manual-tint); color: var(--manual-tint-text); }
    .manual-reader .result-page { font-size: 12px; font-weight: 600; color: var(--manual-accent); }
    .search-result h2 { font-size: 17px; margin: .2rem 0 .35rem; font-weight: 650; line-height: 1.4; }
    .search-result p { font-size: 14.5px; line-height: 1.6; color: var(--manual-muted); margin: 0; }
    .search-result mark { padding: 0 .1em; border-radius: .2em; color: inherit; background: color-mix(in srgb, var(--warning, #f5ce01) 35%, transparent); }
    .search-result > :global(svg) { flex-shrink: 0; align-self: center; color: var(--manual-muted); transition: transform .15s, color .15s; }
    .search-result:hover > :global(svg) { color: var(--manual-accent); transform: translateX(3px); }
    .text-action { display: inline-flex; align-items: center; gap: .4rem; margin-top: 1.5rem; padding: .3rem 0; font-weight: 600; font-size: 14.5px; color: var(--manual-accent); }
    .text-action:hover { text-decoration: underline; text-underline-offset: 3px; }

    /* Home */
    .manual-introduction { max-width: 1200px; }
    .manual-hero { position: relative; display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 2rem; align-items: center; overflow: hidden; padding: clamp(1.5rem, 3vw, 2.75rem);
        border: 1px solid var(--manual-border); border-radius: 1.25rem; background: radial-gradient(120% 140% at 0% 0%, var(--manual-tint) 0%, transparent 60%), var(--bg-surface, #fff); }
    .manual-hero .manual-eyebrow { color: var(--manual-accent); font-weight: 600; }
    .manual-hero h1 { font-size: clamp(32px, 3.2vw, 46.5px); margin-bottom: .75rem; }
    .manual-hero p { font-size: 17px; line-height: 1.7; color: var(--manual-muted); max-width: 560px; margin: 0 0 1.5rem; }
    .hero-topics { display: flex; flex-wrap: wrap; gap: .5rem; }
    .hero-topics button { display: inline-flex; align-items: center; gap: .45rem; padding: .45rem .6rem .45rem .8rem; font-size: 14px; font-weight: 600; border: 1px solid var(--manual-border);
        border-radius: 999px; background: var(--bg-surface, #fff); transition: border-color .15s, color .15s; }
    .hero-topics button:hover { border-color: var(--manual-accent); color: var(--manual-accent); }
    .hero-topics :global(svg) { color: var(--manual-accent); }
    .manual-reader .hero-topics span { min-width: 1.5rem; padding: .1rem .4rem; font-size: 12px; border-radius: 999px; color: var(--manual-muted); background: var(--manual-soft); }
    .hero-start { display: flex; flex-direction: column; align-items: flex-start; gap: .25rem; width: 270px; padding: 1.35rem; border: 1px solid var(--manual-border); border-radius: 1rem;
        background: var(--bg-surface, #fff); color: inherit; box-shadow: 0 12px 32px -18px rgba(15, 23, 42, .35); transition: border-color .15s, transform .15s; }
    .hero-start:hover { border-color: var(--manual-accent); transform: translateY(-2px); }
    .hero-start .topic-icon { margin-bottom: .6rem; }
    .manual-reader .start-label { font-size: 13px; color: var(--manual-muted); }
    .hero-start strong { font-size: 17px; font-weight: 700; line-height: 1.35; }
    .manual-reader .start-link { display: inline-flex; align-items: center; gap: .35rem; margin-top: .6rem; font-size: 14px; font-weight: 600; color: var(--manual-accent); }
    .topic-group { margin-top: 3rem; scroll-margin-top: calc(var(--manual-topbar-height) + 1.5rem); }
    .group-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 1rem; margin-bottom: 1rem; }
    .group-heading h2 { font-size: 21px; font-weight: 700; letter-spacing: -.01em; margin: 0; }
    .manual-reader .group-heading span { font-size: 13.5px; color: var(--manual-muted); }
    .topic-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 280px), 1fr)); gap: 1rem; }
    .topic-card { display: flex; flex-direction: column; align-items: flex-start; padding: 1.25rem; border: 1px solid var(--manual-border); border-radius: var(--manual-radius); color: inherit;
        background: var(--bg-surface, #fff); transition: border-color .15s, box-shadow .15s, transform .15s; }
    .topic-card:hover { border-color: color-mix(in srgb, var(--manual-accent) 55%, var(--manual-border)); box-shadow: 0 14px 30px -18px rgba(15, 23, 42, .35); transform: translateY(-2px); }
    .manual-reader .topic-icon { display: grid; place-items: center; flex-shrink: 0; width: 42px; height: 42px; border-radius: .7rem; background: var(--manual-tint); color: var(--manual-tint-text); }
    .topic-icon :global(svg) { display: block; margin: 0; }
    .topic-card h3 { font-size: 17px; font-weight: 700; line-height: 1.4; margin: 1rem 0 .3rem; }
    .topic-card p { display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; color: var(--manual-muted); font-size: 14px; line-height: 1.55; margin: 0 0 1rem; }
    .manual-reader .topic-footer { display: flex; align-items: center; justify-content: space-between; width: 100%; margin-top: auto; padding-top: .85rem; border-top: 1px solid var(--manual-border); font-size: 13px; font-weight: 500; color: var(--manual-muted); }
    .manual-reader .topic-footer :global(svg) { color: var(--manual-accent); transition: transform .15s; }
    .topic-card:hover .topic-footer :global(svg) { transform: translateX(3px); }
    .faq-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .5rem 1rem; }
    .faq-item { display: flex; align-items: center; gap: .8rem; padding: .9rem 1rem; border: 1px solid var(--manual-border); border-radius: .7rem; color: inherit; font-size: 15px; font-weight: 500; line-height: 1.45; transition: border-color .15s, background-color .15s; }
    .faq-item span { flex: 1; min-width: 0; }
    .faq-item :global(svg) { flex-shrink: 0; color: var(--manual-accent); }
    .faq-item > :global(svg:last-child) { color: var(--manual-muted); transition: transform .15s, color .15s; }
    .faq-item:hover { border-color: var(--manual-accent); background: color-mix(in srgb, var(--manual-tint) 45%, transparent); }
    .faq-item:hover > :global(svg:last-child) { color: var(--manual-accent); transform: translateX(3px); }

    /* States */
    .manual-reader .manual-state { display: flex; flex-direction: column; align-items: center; gap: .75rem; max-width: 460px; margin: 3rem auto; padding: 2.5rem 1.5rem; text-align: center; color: var(--manual-muted); line-height: 1.7;
        border: 1px dashed var(--manual-border); border-radius: 1rem; }
    .manual-state.compact { margin: 1rem 0; max-width: none; }
    .manual-state p { margin: 0; }
    .manual-state .text-action { margin-top: 0; }
    .manual-reader .state-icon { display: grid; place-items: center; width: 56px; height: 56px; border-radius: 999px; background: var(--manual-tint); color: var(--manual-tint-text); }
    .manual-skeleton { display: grid; gap: 1rem; max-width: 800px; margin: 0 auto; }
    .manual-skeleton span { display: block; height: 1rem; border-radius: .5rem; background: linear-gradient(90deg, var(--manual-soft) 25%, var(--bg-hover, #eef0f4) 50%, var(--manual-soft) 75%); background-size: 200% 100%; animation: manual-shimmer 1.4s ease-in-out infinite; }
    .manual-skeleton span:first-child { width: 40%; height: 2.4rem; margin-bottom: .5rem; }
    .manual-skeleton span:nth-child(3) { width: 85%; }
    .manual-skeleton span:last-child { width: 60%; }
    .manual-skeleton.compact span:first-child { width: 100%; height: 5rem; }
    @keyframes manual-shimmer { to { background-position: -200% 0; } }
    @media (prefers-reduced-motion: reduce) {
        .manual-skeleton span { animation: none; }
        .topic-card, .hero-start, .back-to-top, .reading-progress { transition: none; }
        .topic-card:hover, .hero-start:hover, .back-to-top:hover { transform: none; }
    }

    @media (max-width: 1199px) { .manual-hero { grid-template-columns: minmax(0, 1fr); } .hero-start { width: auto; } }
    @media (max-width: 1023px) {
        .manual-layout, .manual-layout.without-contents { grid-template-columns: 220px minmax(0, 1fr); gap: 2rem; }
        .manual-contents { display: none; }
        .faq-list { grid-template-columns: minmax(0, 1fr); }
    }
    @media (max-width: 767px) {
        .manual-topbar { flex-wrap: wrap; gap: .75rem; padding: .75rem 1rem; }
        .manual-search { order: 3; flex-basis: 100%; max-width: none; }
        .search-shortcut { display: none; }
        .back-to-app { margin-left: auto; padding: .5rem; } .back-to-app span { display: none; }
        .manual-layout, .manual-layout.without-contents { display: block; padding: 1rem 1rem 3rem; }
        .manual-sidebar { position: static; max-height: none; margin-bottom: 1.5rem; padding-right: 0; overflow: visible; }
        .mobile-navigation { display: flex; align-items: center; gap: .65rem; width: 100%; padding: .75rem 1rem; font-weight: 600; text-align: left; border: 1px solid var(--manual-border); border-radius: .75rem; background: var(--manual-soft); }
        .mobile-navigation span { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .mobile-navigation :global(.mobile-caret) { transition: transform .15s; }
        .mobile-navigation[aria-expanded="true"] :global(.mobile-caret) { transform: rotate(180deg); }
        #manual-navigation { display: none; margin-top: .5rem; padding: .75rem .5rem; border: 1px solid var(--manual-border); border-radius: .75rem; background: var(--bg-surface, #fff); }
        #manual-navigation.open { display: block; }
        .manual-hero { padding: 1.4rem; border-radius: 1rem; }
        .title-icon { flex-basis: 44px; height: 44px; }
        .topic-grid, .guide-pagination { grid-template-columns: minmax(0, 1fr); }
        .next-guide { grid-column: auto; }
        .section-heading h2 { font-size: 20px; }
    }
</style>
