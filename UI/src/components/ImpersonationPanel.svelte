<script>
    import {slide} from 'svelte/transition';
    import {UserSwitch, CaretDown, SignOut, ShieldCheck} from 'phosphor-svelte';
    import {role, isMobileSidebarOpen} from 'store/stores.js';
    import {readImpersonation, stopImpersonation} from 'utils/impersonation.js';
    import BasicModal from './modals/BasicModal.svelte';
    import Picker from '../routes/tools/SportAssociationsManager.svelte';
    const context = readImpersonation();
    const legacySession = localStorage.getItem('switched_superuser') === 'true';
    const labels = {association: 'Associazione sportiva', athlete: 'Utente', collaborator: 'Collaboratore'};
    let expanded = false;
    let showPicker = false;
    let error = '';
    let exiting = false;
    function expandPanel() { expanded = true; isMobileSidebarOpen.set(true); }
    async function exitImpersonation() {
        if (exiting) return;
        exiting = true;
        try { await stopImpersonation(); }
        catch (failure) { error = failure.message; exiting = false; }
    }
</script>

<svelte:window on:open-impersonation-panel={expandPanel} />

{#if context || legacySession || $role === 'administrator'}
    <section class="identity-panel" aria-label="Identità amministrativa">
        <button type="button" class="identity-summary" aria-expanded={expanded} aria-controls="impersonation-details" on:click={() => expanded = !expanded}>
            <span class="identity-icon"><svelte:component this={context || legacySession ? UserSwitch : ShieldCheck} size={21} weight="duotone" /></span>
            <span class="identity-title">
                <small>{context || legacySession ? 'Stai impersonando' : 'Amministrazione'}</small>
                <strong title={context?.target?.username}>{context?.target?.username || (legacySession ? 'Sessione precedente' : 'Gestisci accessi')}</strong>
            </span>
            <span class:expanded><CaretDown size={16} /></span>
        </button>
        {#if expanded}
            <div id="impersonation-details" class="identity-details" transition:slide={{duration: 150}}>
                {#if context}
                    <span class="badge badge-light-primary mb-2">{labels[context.target.role]}</span>
                    <p class="mb-1 font-weight-bold">{context.target.association?.name || 'Account personale'}</p>
                    <p class="text-muted mb-3 identity-email">{context.target.email}</p>
                    <p class="text-muted mb-3">Stai usando i permessi di questo account.</p>
                {:else}
                    <p class="text-muted mb-3">Seleziona un account per lavorare con i suoi permessi.</p>
                {/if}
                <button type="button" class="btn btn-light-primary btn-sm w-100 mb-2" disabled={exiting} on:click={() => showPicker = true}>
                    <UserSwitch size={17} class="mr-1" /> {context || legacySession ? 'Cambia utente' : 'Impersona un utente'}
                </button>
                {#if context || legacySession}
                    <button type="button" class="btn btn-light btn-sm w-100" disabled={exiting} on:click={exitImpersonation}>
                        <SignOut size={17} class="mr-1" /> {exiting ? 'Uscita in corso…' : 'Torna all’amministrazione'}
                    </button>
                {/if}
                {#if error}<p class="text-danger mt-3 mb-0" role="alert">{error}</p>{/if}
            </div>
        {/if}
    </section>
    <BasicModal bind:show={showPicker} id="admin-impersonation-modal" showTitle title="Impersona utenti" modalSize="lg" showFooter={false}>
        {#if showPicker}<Picker embedded />{/if}
    </BasicModal>
{/if}

<style>
    .identity-panel { flex-shrink: 0; width: 200px; max-width: calc(100% - 2rem); margin: 0 auto .75rem; border: 1px solid var(--border-color, #e4e6ef); border-radius: 1rem; background: var(--bg-surface-secondary); overflow: hidden; }
    .identity-summary { width: 100%; display: flex; align-items: center; gap: .65rem; padding: .85rem; border: 0; background: transparent; color: var(--text-primary, #3f4254); text-align: left; }
    .identity-summary:hover { background: var(--bg-surface); }
    .identity-summary:focus-visible { outline: 2px solid var(--primary); outline-offset: -3px; border-radius: 1rem; }
    .identity-icon { color: var(--primary); display: flex; }
    .identity-title { min-width: 0; flex: 1; }
    .identity-title small, .identity-title strong { display: block; }
    .identity-title small { color: var(--text-secondary, #7e8299); margin-bottom: .15rem; }
    .identity-title strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: .9rem; }
    .expanded { transform: rotate(180deg); }
    .identity-details { padding: 0 .85rem .85rem; font-size: .85rem; }
    .identity-email { overflow-wrap: anywhere; }
</style>
