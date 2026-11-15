<script>
    // Go background tasks (beta): the owner moves the background email jobs to
    // the Go worker (worker-go). The switch never saves on its own: it asks for
    // the password in a dialog and the runner then rewrites the config and
    // restarts every service, exactly like "Riavvia applicazione".
    import Switch from '../../../components/inputs/Switch.svelte';
    import InstanceDialog from './InstanceDialog.svelte';

    // request(path, options) is the owner page helper rooted at /instance.
    export let request;
    export let enabled = false;
    export let mode;
    export let disabled = false;
    export let busy = false;
    export let onStarted = () => {};

    let dialog = null; // {wanted: boolean}
    let password = '';
    let dialogError = '';

    // The saved value from GET /admin: it stays as is until the page reloads
    // after the restart, which shows the new value.
    $: saved = !!enabled;
    $: available = mode === 'production';
    let shown = enabled;
    // A value reloaded from the server (another tab, a finished operation)
    // replaces the switch unless a confirmation is being asked.
    $: if (enabled !== shown && !dialog && !busy) shown = enabled;

    function toggle(event) {
        const wanted = event.target.checked;
        // Revert at once: nothing is saved until the password is confirmed.
        shown = saved;
        if (disabled || busy || !available) return;
        password = '';
        dialogError = '';
        dialog = {wanted};
    }

    function close() {
        if (busy) return;
        dialog = null;
        password = '';
        dialogError = '';
    }

    async function confirm() {
        if (!dialog || !password || busy) return;
        const wanted = dialog.wanted;
        busy = true;
        dialogError = '';
        try {
            const result = await request('/admin', {method: 'PUT', body: JSON.stringify({goTasksEnabled: wanted, password})});
            dialog = null;
            password = '';
            onStarted(result.operation, wanted);
        } catch (e) {
            dialogError = e.message;
            password = '';
        } finally { busy = false; }
    }
</script>

<section class="si-card" aria-labelledby="instance-go-tasks">
    <div class="si-card-head">
        <div>
            <h2 id="instance-go-tasks" class="si-title">Attività in background su Go <span class="si-pill info beta">Beta</span></h2>
            <p class="si-muted si-small">Gli invii email in background passano al servizio Go (worker-go). Cambiare l’impostazione riavvia l’applicazione per circa un minuto.</p>
            <p class="si-muted si-small">Se il servizio Go non è attivo, le attività restano in attesa finché non lo riattivi o spegni l’interruttore.</p>
        </div>
    </div>
    <Switch id="instance-go-tasks" name="instance-go-tasks" label="Attività in background su Go (beta)" bind:checked={shown} disabled={disabled || busy || !available} on:change={toggle} />
    {#if !available}<p class="si-hint mb-0">Disponibile solo in produzione.</p>{/if}
</section>

<InstanceDialog open={!!dialog} title={dialog?.wanted ? 'Attivare le attività in background su Go?' : 'Disattivare le attività in background su Go?'} locked={busy} on:close={close}>
    <p>L’app si riavvia e non sarà disponibile per circa un minuto.</p>
    {#if dialog && !dialog.wanted}
        <p>Le nuove attività tornano al servizio attuale. Il servizio Go resta attivo per completare quelle già in coda.</p>
    {/if}
    <form id="instance-go-tasks-form" on:submit|preventDefault={confirm}>
        <label class="si-field" for="instance-go-tasks-password">Conferma la tua password
            <input id="instance-go-tasks-password" class="form-control" type="password" autocomplete="current-password" bind:value={password} disabled={busy} />
        </label>
        {#if dialogError}<p class="text-danger mt-2 mb-0" role="alert">{dialogError}</p>{/if}
    </form>
    <svelte:fragment slot="footer">
        <button type="button" class="btn btn-light" disabled={busy} on:click={close}>Annulla</button>
        <button type="submit" form="instance-go-tasks-form" class="btn btn-primary si-btn" disabled={!password || busy}>{busy ? 'Verifica…' : dialog?.wanted ? 'Attiva e riavvia' : 'Disattiva e riavvia'}</button>
    </svelte:fragment>
</InstanceDialog>

<style>
    .beta { margin-left: .5rem; vertical-align: middle; font-size: .85rem; padding: .15rem .55rem; }
</style>
