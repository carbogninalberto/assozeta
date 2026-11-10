<script>
    import {ArrowCounterClockwise, FloppyDisk, PaperPlaneTilt, Plugs} from 'phosphor-svelte';
    import {onMount} from 'svelte';
    import {toast} from 'svelte-sonner';
    import Switch from '../../../components/inputs/Switch.svelte';
    import DiagnosticResult from './DiagnosticResult.svelte';
    import InstanceMenu from './InstanceMenu.svelte';
    import InstanceDialog from './InstanceDialog.svelte';
    export let request;
    export let changes = false;
    export let disabled = false;
    export let onSaved = () => {};
    export let busy = false;
    let config;
    let draft = {};
    let baseline = '';
    let password = '';
    let clearPassword = false;
    let loading = true;
    let error = '';
    let recipient = '';
    let testResult;
    let resetOpen = false;
    const fields = ['host', 'port', 'security', 'username', 'from_email', 'sender_name'];
    $: changes = !!baseline && (JSON.stringify(draft) !== baseline || !!password || clearPassword);
    $: testBlocked = changes ? 'Salva prima le modifiche.' : '';

    function adopt(value) {
        config = value;
        draft = Object.fromEntries(fields.map(key => [key, value[key]]));
        baseline = JSON.stringify(draft);
        password = '';
        clearPassword = false;
        testResult = value.last_test;
    }
    async function load() {
        loading = true;
        error = '';
        try { adopt(await request('/admin/email')); }
        catch (e) { error = e.message; }
        finally { loading = false; }
    }
    function cancel() {
        draft = JSON.parse(baseline);
        password = '';
        clearPassword = false;
        error = '';
    }
    async function save() {
        if (busy || disabled || !changes) return;
        busy = true; error = '';
        try {
            const data = {...draft, revision: config.revision, clear_password: clearPassword};
            if (password) data.password = password;
            adopt(await request('/admin/email', {method: 'PUT', body: JSON.stringify(data)}));
            toast.success('Email salvata.');
            onSaved();
        } catch (e) { error = e.message; }
        finally { busy = false; }
    }
    async function resetEnvironment() {
        // Only the confirmation dialog can reset to the server values.
        if (!resetOpen || busy || disabled || changes) return;
        busy = true; error = '';
        try {
            adopt(await request('/admin/email', {method: 'DELETE', body: JSON.stringify({revision: config.revision})}));
            resetOpen = false;
            toast.success('Ripristinati i valori del server.');
            onSaved();
        } catch (e) { error = e.message; }
        finally { busy = false; }
    }
    async function test(action) {
        if (busy || disabled || changes) return;
        busy = true; error = ''; testResult = null;
        try {
            testResult = await request('/admin/email/test', {method: 'POST', body: JSON.stringify({action, revision: config.revision, ...(action === 'send' ? {recipient} : {})})});
            onSaved();
        } catch (e) { error = e.message; }
        finally { busy = false; }
    }
    onMount(load);
</script>

<section class="si-stack" aria-labelledby="instance-email-title">
    <form class="si-card" on:submit|preventDefault={save}>
        <div class="si-card-head">
            <div>
                <h2 id="instance-email-title" class="si-title">Email di sistema</h2>
                <p class="si-muted si-small">Notifiche, ricevute e recupero password.</p>
            </div>
            {#if config}
                <div class="si-actions">
                    <span class="si-pill" class:info={config.source === 'instance'}>{config.source === 'instance' ? 'Personalizzata' : 'Valori del server'}</span>
                    {#if config.source === 'instance'}
                        <InstanceMenu label="Altre azioni email" items={[{label: 'Ripristina valori del server', hint: changes ? 'Salva o annulla prima le modifiche.' : '', disabled: changes || disabled, onSelect: () => resetOpen = true}]} />
                    {/if}
                </div>
            {/if}
        </div>
        {#if loading && !config}
            <div class="si-grid-2">{#each Array(6) as _}<span class="si-skeleton" style="height: 2.5rem"></span>{/each}</div>
        {/if}
        {#if error}<p class="text-danger" role="alert">{error}{#if !config} <button type="button" class="si-link" on:click={load}>Riprova</button>{/if}</p>{/if}
        {#if config}
            {#if config.credential_error}<p class="text-danger si-small" role="alert">La password salvata non è leggibile. Inseriscila di nuovo.</p>{/if}
            <fieldset disabled={disabled || busy || loading}>
                <legend class="si-sr-only">Server e mittente</legend>
                <div class="si-grid-2">
                    <label>Server SMTP<input class="form-control" bind:value={draft.host} required maxlength="253" placeholder="smtp.example.org" autocomplete="off" /></label>
                    <label>Porta<input class="form-control" type="number" bind:value={draft.port} min="1" max="65535" required /></label>
                    <label>Sicurezza<select class="form-control" bind:value={draft.security}>{#if config.security === 'invalid'}<option value="invalid" disabled>Scegli TLS o SSL</option>{/if}<option value="ssl">SSL/TLS</option><option value="tls">STARTTLS</option><option value="none">Nessuna</option></select></label>
                    <label>Nome utente<input class="form-control" bind:value={draft.username} maxlength="254" autocomplete="off" /></label>
                    <label>Password<input class="form-control" type="password" bind:value={password} disabled={clearPassword} autocomplete="new-password" maxlength="4096" placeholder={config.password_configured ? 'Salvata (lascia vuoto)' : 'Non impostata'} /></label>
                    <div class="si-field password-clear">
                        {#if config.password_configured}<Switch id="email-clear-password" label="Rimuovi la password salvata" bind:checked={clearPassword} on:change={() => {if (clearPassword) password = '';}} />{/if}
                    </div>
                    <label>Email mittente<input class="form-control" type="email" bind:value={draft.from_email} required maxlength="254" /></label>
                    <label>Nome mittente<input class="form-control" bind:value={draft.sender_name} maxlength="100" /></label>
                </div>
                <div class="si-savebar" class:dirty={changes}>
                    <button class="btn btn-primary si-btn" type="submit" disabled={!changes}><FloppyDisk size={18} weight="duotone" />{busy ? 'Salvataggio…' : 'Salva email'}</button>
                    {#if changes}<button class="btn btn-light" type="button" on:click={cancel}>Annulla</button><span class="si-dirty" role="status">Modifiche non salvate</span>{/if}
                </div>
            </fieldset>
        {/if}
    </form>

    {#if config}
        <div class="si-card" aria-labelledby="email-testing">
            <div class="si-card-head"><h3 id="email-testing" class="si-subtitle">Prova</h3>{#if testBlocked}<span class="si-reason">{testBlocked}</span>{/if}</div>
            <button type="button" class="btn btn-light-primary si-btn" disabled={disabled || busy || loading || changes} on:click={() => test('connect')}><Plugs size={18} weight="duotone" />Verifica connessione</button>
            <form class="test-form mt-3" on:submit|preventDefault={() => test('send')}>
                <label class="si-sr-only" for="email-test-recipient">Destinatario di prova</label>
                <input id="email-test-recipient" class="form-control" type="email" bind:value={recipient} required placeholder="Destinatario di prova" disabled={disabled || busy || loading} />
                <button class="btn btn-light-primary si-btn" disabled={disabled || busy || loading || changes || !recipient}><PaperPlaneTilt size={18} weight="duotone" />Invia email di prova</button>
            </form>
            {#if testResult}<div class="mt-3" aria-live="polite"><DiagnosticResult check={{...testResult, label: 'Ultima prova'}} /></div>{/if}
        </div>
    {/if}
</section>

<InstanceDialog bind:open={resetOpen} title="Ripristinare i valori del server?" locked={busy}>
    <p>Le impostazioni email salvate qui vengono rimosse. Si torna ai valori del file .env.</p>
    <svelte:fragment slot="footer">
        <button type="button" class="btn btn-light" data-autofocus disabled={busy} on:click={() => resetOpen = false}>Annulla</button>
        <button type="button" class="btn btn-danger si-btn" disabled={busy} on:click={resetEnvironment}><ArrowCounterClockwise size={18} weight="duotone" />{busy ? 'Ripristino…' : 'Ripristina'}</button>
    </svelte:fragment>
</InstanceDialog>

<style>
    fieldset { min-width: 0; }
    .password-clear { align-content: end; }
    .password-clear :global(.form-group) { margin-bottom: 0; }
    .test-form { display: flex; flex-wrap: wrap; gap: .5rem; }
    .test-form input { flex: 1 1 14rem; width: auto; }
    @media (max-width: 575px) { .test-form > :not(.si-sr-only) { width: 100%; } }
</style>
