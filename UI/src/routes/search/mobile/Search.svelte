<script>
	import { X } from 'lucide-svelte';
    import {push} from 'svelte-spa-router';
    import {Binoculars} from 'phosphor-svelte';
    import {apiFetch} from 'utils/ApiMiddleware.js';

    let searchResult = {sport_associations_match: []};
    let error = false;
    let searching = false;
    let query = '';
    let inputEl;
    let timeout;

    function handleInput() {
        if (timeout) clearTimeout(timeout);

        if (query.length < 2) {
            searchResult = {sport_associations_match: []};
            error = false;
            return;
        }

        timeout = setTimeout(doSearch, 300);
    }

    async function doSearch() {
        searching = true;
        error = false;
        const res = await apiFetch(`${__bakney.env.API.SEARCH.ALL}?q=${encodeURIComponent(query)}`);
        searching = false;

        if (!res.error) {
            searchResult = res.response.data;
        } else {
            error = true;
            searchResult = {sport_associations_match: []};
        }
    }

    function handleClear() {
        query = '';
        searchResult = {sport_associations_match: []};
        error = false;
        inputEl?.focus();
    }
</script>

<div class="search-page mt-16 mt-md-0">
    <div class="search-input-wrapper">
        <div class="input-group">
            <div class="input-group-prepend">
                <span class="input-group-text">
                    <span class="svg-icon svg-icon-lg">
                        <svg
                            xmlns="http://www.w3.org/2000/svg"
                            xmlns:xlink="http://www.w3.org/1999/xlink"
                            width="24px"
                            height="24px"
                            viewBox="0 0 24 24"
                            version="1.1">
                            <g stroke="none" stroke-width="1" fill="none" fill-rule="evenodd">
                                <rect x="0" y="0" width="24" height="24" />
                                <path
                                    d="M14.2928932,16.7071068 C13.9023689,16.3165825 13.9023689,15.6834175 14.2928932,15.2928932 C14.6834175,14.9023689 15.3165825,14.9023689 15.7071068,15.2928932 L19.7071068,19.2928932 C20.0976311,19.6834175 20.0976311,20.3165825 19.7071068,20.7071068 C19.3165825,21.0976311 18.6834175,21.0976311 18.2928932,20.7071068 L14.2928932,16.7071068 Z"
                                    fill="#000000"
                                    fill-rule="nonzero"
                                    opacity="0.3" />
                                <path
                                    d="M11,16 C13.7614237,16 16,13.7614237 16,11 C16,8.23857625 13.7614237,6 11,6 C8.23857625,6 6,8.23857625 6,11 C6,13.7614237 8.23857625,16 11,16 Z M11,18 C7.13400675,18 4,14.8659932 4,11 C4,7.13400675 7.13400675,4 11,4 C14.8659932,4 18,7.13400675 18,11 C18,14.8659932 14.8659932,18 11,18 Z"
                                    fill="#000000"
                                    fill-rule="nonzero" />
                            </g>
                        </svg>
                    </span>
                </span>
            </div>
            <input
                bind:this={inputEl}
                class="form-control"
                type="text"
                autocomplete="off"
                bind:value={query}
                on:input={handleInput}
                placeholder="Cerca un'Associazione Sportiva..." />
            {#if query}
                <div class="input-group-append">
                    <!-- svelte-ignore a11y-click-events-have-key-events -->
                    <span class="input-group-text" style="cursor: pointer;" on:click={handleClear}>
                        <X size={16} class="icon-sm text-muted" />
                    </span>
                </div>
            {/if}
        </div>
    </div>

    <div class="search-results">
        {#if searching}
            <div class="d-flex justify-content-center py-8">
                <span class="spinner spinner-sm spinner-primary" />
            </div>
        {:else if error}
            <div class="state-empty">
                <Binoculars size={64} weight="duotone" class="text-muted mb-4" />
                <span class="text-dark-50 font-weight-bold font-size-sm"
                    >Errore di connessione. Riprova più tardi!</span>
            </div>
        {:else if query.length > 0 && query.length < 2}
            <div class="state-empty">
                <Binoculars size={64} weight="duotone" class="text-muted mb-4" />
                <span class="text-dark-50 font-weight-bold font-size-sm"
                    >Inserisci almeno 2 caratteri per cercare.</span>
            </div>
        {:else if searchResult.sport_associations_match.length > 0}
            <div class="card-body p-0">
                {#each searchResult.sport_associations_match as sport_association}
                    <!-- svelte-ignore a11y-click-events-have-key-events -->
                    <!-- svelte-ignore a11y-no-static-element-interactions -->
                    <div
                        class="d-flex align-items-center result-item"
                        on:click|preventDefault={() => push(`/search/profile/${sport_association.user.username}`)}>
                        {#if sport_association.user.avatar_image != null}
                            <div class="symbol symbol-30 flex-shrink-0 mr-3">
                                <img alt={sport_association.user.username} src={sport_association.user.avatar_image} />
                            </div>
                        {:else}
                            <div class="symbol symbol-30 symbol-light-warning flex-shrink-0 mr-3">
                                <span class="symbol-label font-weight-bolder" style="font-size: 0.8rem;">
                                    {sport_association.denomination.substring(0, 2).toUpperCase()}
                                </span>
                            </div>
                        {/if}
                        <div class="d-flex flex-column flex-grow-1 min-w-0">
                            <span class="text-dark mb-0 font-weight-bolder font-size-sm text-truncate">
                                {sport_association.denomination}
                            </span>
                            <span class="text-muted font-size-xs" style="max-width: 100%;word-break: break-all;">
                                Associazione Sportiva{sport_association.address_city
                                    ? ` a ${sport_association.address_city}`
                                    : ''}
                            </span>
                        </div>
                    </div>
                {/each}
                <span class="font-weight-bold text-muted">
                    {searchResult.sport_associations_match.length}
                    {searchResult.sport_associations_match.length > 1 ? ' risultati trovati' : ' risultato trovato'}.
                </span>
            </div>
        {:else if query.length >= 2}
            <div class="state-empty">
                <Binoculars size={64} weight="duotone" class="text-muted mb-4" />
                <span class="text-dark-50 font-weight-bold font-size-sm">Nessuna associazione trovata.</span>
            </div>
        {:else}
            <div class="state-initial">
                <Binoculars size={64} weight="duotone" class="text-muted mb-4" />
                <span class="text-dark-50 font-weight-bold font-size-sm">Cerca un'associazione a cui iscriverti.</span>
            </div>
        {/if}
    </div>
</div>

<style>
    .search-page {
        padding: 0 1rem;
    }

    .search-input-wrapper {
        width: 100%;
        position: sticky;
        top: calc(55px + env(safe-area-inset-top, 0px));
        z-index: 1;
        background: var(--bg-body);
        padding-bottom: 0.5rem;
    }

    @media (min-width: 768px) {
        .search-input-wrapper {
            top: calc(65px + env(safe-area-inset-top, 0px));
        }
    }

    .search-input-wrapper .input-group {
        border: 1px solid var(--border-color);
        border-radius: 0.42rem;
        overflow: hidden;
    }

    .search-input-wrapper .input-group:focus-within {
        border-color: var(--border-color);
    }

    .search-input-wrapper .form-control {
        height: 3.1rem;
        font-size: 1rem;
        font-weight: 400;
        color: var(--text-primary);
        background: var(--bg-surface);
        border: none !important;
        box-shadow: none !important;
        outline: none !important;
    }

    .search-input-wrapper .form-control:focus {
        outline: none !important;
        box-shadow: none !important;
        border: none !important;
    }

    .search-input-wrapper .form-control::placeholder {
        color: var(--text-muted);
    }

    .search-input-wrapper .input-group-text {
        background: transparent;
        border: none;
    }

    .search-results {
        margin-top: 0.25rem;
        padding: 0 0.5rem;
    }

    .state-initial,
    .state-empty {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        text-align: center;
        padding: 4rem 1rem;
    }

    .result-item {
        cursor: pointer;
        border-radius: 0.42rem;
        padding: 0.5rem 0.5rem;
        margin-bottom: 0.25rem;
    }

    .result-item:hover {
        background: var(--bg-surface-secondary);
    }
</style>
