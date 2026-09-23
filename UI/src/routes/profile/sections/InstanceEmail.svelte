<script>
    import {onMount} from 'svelte';
    import {toast} from 'svelte-sonner';
    import InstanceAlert from './InstanceAlert.svelte';
    import Switch from '../../../components/inputs/Switch.svelte';
    import DiagnosticResult from './DiagnosticResult.svelte';
    export let request;
    export let changes = false;
    export let disabled = false;
    export let onSaved = () => {};
    let config;
    let draft = {};
    let baseline = '';
    let password = '';
    let clearPassword = false;
    let loading = true;
    export let busy = false;
    let error = '';
    let recipient = '';
    let testResult;
    let confirmReset = false;
    const fields = ['host', 'port', 'security', 'username', 'from_email', 'sender_name'];
    $: changes = !!baseline && (JSON.stringify(draft) !== baseline || !!password || clearPassword);

    function adopt(value) {
        config = value;
        draft = Object.fromEntries(fields.map(key => [key, value[key]]));
        baseline = JSON.stringify(draft);
        password = '';
        clearPassword = false;
        confirmReset = false;
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
            toast.success('Impostazioni email salvate.');
            onSaved();
        } catch (e) { error = e.message; toast.error(e.message); }
        finally { busy = false; }
    }
    async function resetEnvironment() {
        if (!confirmReset || busy || disabled || changes) return;
        busy = true; error = '';
        try {
            adopt(await request('/admin/email', {method: 'DELETE', body: JSON.stringify({revision: config.revision})}));
            toast.success('Ripristinata la configurazione di ambiente. Nessuna variabile sul server è stata modificata.');
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

<section aria-labelledby="instance-email-title">
    <h2 id="instance-email-title">Email di sistema</h2>
    <p>Configura le email inviate dall’applicazione. Eventuali impostazioni SMTP specifiche dell’associazione rimangono separate.</p>
    {#if loading}<p role="status">Caricamento configurazione email…</p>{/if}
    {#if error}<InstanceAlert tone="danger" role="alert">{error}</InstanceAlert><button class="btn btn-light mb-4" disabled={busy || changes} on:click={load}>Ricarica configurazione</button>{/if}
    {#if config}
        <p class="source">Origine: <strong>{config.source === 'instance' ? 'Impostazioni dell’istanza' : 'Variabili di ambiente del server'}</strong>. Le modifiche salvate qui si applicano ai nuovi invii senza riavvio.</p>
        {#if config.credential_error}<InstanceAlert tone="warning" role="alert">La password salvata non può essere letta. Reinseriscila o rimuovila esplicitamente.</InstanceAlert>{/if}
        <form on:submit|preventDefault={save}>
            <fieldset disabled={disabled || busy || loading}>
                <legend class="sr-only">Configurazione SMTP e mittente</legend>
                <div class="email-grid">
                    <label>Server SMTP<input class="form-control" bind:value={draft.host} required maxlength="253" placeholder="smtp.example.org" autocomplete="off" /></label>
                    <label>Porta<input class="form-control" type="number" bind:value={draft.port} min="1" max="65535" required /></label>
                    <label>Sicurezza<select class="form-control" bind:value={draft.security}>{#if config.security === 'invalid'}<option value="invalid" disabled>Seleziona TLS o SSL (ambiente non valido)</option>{/if}<option value="ssl">SSL / TLS implicito</option><option value="tls">STARTTLS</option><option value="none">Nessuna (relay locale senza credenziali)</option></select></label>
                    <label>Nome utente<input class="form-control" bind:value={draft.username} maxlength="254" autocomplete="off" /></label>
                    <label>Email mittente<input class="form-control" type="email" bind:value={draft.from_email} required maxlength="254" /></label>
                    <label>Nome mittente<input class="form-control" bind:value={draft.sender_name} maxlength="100" /></label>
                </div>
                <details class="mt-4">
                    <summary>Password e opzioni avanzate</summary>
                    <p class="mt-3">Password: {config.password_configured ? '•••••••• · salvata' : 'non configurata'}. Lascia il campo vuoto per mantenerla.</p>
                    <label class="d-block">Nuova password SMTP<input class="form-control" type="password" bind:value={password} disabled={clearPassword} autocomplete="new-password" maxlength="4096" /></label>
                    <Switch id="email-clear-password" label="Rimuovi la password salvata" bind:checked={clearPassword} on:change={() => {if (clearPassword) password = '';}} />
                    <p>La password è conservata cifrata. Conserva la chiave dell’installazione insieme ai backup per poterla recuperare.</p>
                </details>
                <div class="actions mt-4">
                    <button class="btn btn-primary" type="submit" disabled={!changes}>{busy ? 'Operazione in corso…' : 'Salva email'}</button>
                    {#if changes}<button class="btn btn-light" type="button" on:click={cancel}>Annulla modifiche</button><span role="status">Modifiche non salvate</span>{/if}
                </div>
            </fieldset>
        </form>
        <section class="test-panel mt-6" aria-labelledby="email-testing">
            <h3 id="email-testing">Verifica la configurazione salvata</h3>
            <p>Il test di connessione verifica SMTP e autenticazione senza inviare email. Il messaggio di prova viene inviato solo premendo “Invia email di prova”.</p>
            {#if changes}<p>Salva o annulla le modifiche prima di eseguire un test.</p>{/if}
            <button class="btn btn-light-primary" disabled={disabled || busy || loading || changes} on:click={() => test('connect')}>Verifica connessione SMTP</button>
            <form class="test-form mt-4" on:submit|preventDefault={() => test('send')}>
                <label>Destinatario di prova<input class="form-control" type="email" bind:value={recipient} required disabled={disabled || busy || loading} /></label>
                <button class="btn btn-light-primary" disabled={disabled || busy || loading || changes || !recipient}>Invia email di prova</button>
            </form>
            {#if busy}<p role="status" class="mt-3">Operazione in corso…</p>{/if}
            {#if testResult}<div class="mt-4" aria-live="polite"><DiagnosticResult check={{...testResult, label: 'Ultimo test email'}} /></div>{/if}
        </section>
        {#if config.source === 'instance'}
            <details class="mt-6">
                <summary>Ripristina le impostazioni di ambiente</summary>
                <p class="mt-3">Rimuove le impostazioni email e la password salvate qui. I nuovi invii useranno le variabili già presenti sul server.</p>
                <Switch id="email-confirmReset" label="Confermo il ripristino della configurazione di ambiente" bind:checked={confirmReset} disabled={busy || disabled || changes} />
                <button class="btn btn-light" disabled={!confirmReset || busy || disabled || changes} on:click={resetEnvironment}>Ripristina email di ambiente</button>
            </details>
        {/if}
    {/if}
</section>

<style>
    .email-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; }
    label { min-width: 0; }
    .email-grid label, .test-form label { display: grid; gap: .5rem; }
    .actions, .test-form { display: flex; flex-wrap: wrap; gap: .75rem; align-items: end; }
    .test-form label { flex: 1 1 18rem; margin: 0; }
    .test-panel { padding: 1.25rem; border: 1px solid #dce1e7; border-radius: .65rem; }
    fieldset { min-width: 0; }
    @media (max-width: 575px) { .email-grid { grid-template-columns: 1fr; } .test-form button { width: 100%; } }
</style>
