<script>
    // Copia remota: encrypted copies on S3-compatible storage, off by default.
    // Settings open in a drawer; the recovery key is set up in a 3-step dialog.
    import {onMount} from 'svelte';
    import {toast} from 'svelte-sonner';
    import {CheckCircle} from 'phosphor-svelte';
    import Switch from '../../../components/inputs/Switch.svelte';
    import InstanceDialog from './InstanceDialog.svelte';
    import InstanceMenu from './InstanceMenu.svelte';
    import {formatDate} from './instanceFormat.js';
    import {remoteStatus, keyFile} from './remoteBackup.js';

    export let request;
    // Summary from the backup overview: configured, enabled, key_id, last_upload, last_failure, status, error.
    export let remote;
    export let disabled = false;
    export let onSaved = () => {};

    const blank = {endpoint: '', region: '', bucket: '', prefix: '', access_key: '', secret_key: '', path_style: false, retention: 3};
    let config = null;
    let loadError = '';
    let drawer = false;
    let draft = {...blank};
    let saving = false;
    let formError = '';
    let testing = false;
    let testResult = null;
    let toggling = false;

    let wizard = false;
    let step = 1;
    let recovery = null; // {key_id, recovery_key}, in memory only while the dialog is open
    let tail = '';
    let wizardError = '';
    let working = false;

    $: status = remoteStatus(remote);
    $: hasKey = !!config?.key;
    $: enableReason = !config?.configured ? 'Configura prima lo storage.' : !hasKey ? 'Genera prima la chiave.' : '';

    onMount(load);

    async function load() {
        try { config = await request('/admin/backups/remote'); loadError = ''; }
        catch (e) { loadError = e.message; }
    }

    function openDrawer() {
        draft = config ? {...blank, ...Object.fromEntries(Object.keys(blank).map(key => [key, config[key] ?? blank[key]])), secret_key: ''} : {...blank};
        formError = '';
        testResult = null;
        drawer = true;
    }

    function settings() {
        const value = {...draft, retention: Number(draft.retention)};
        if (!value.secret_key) delete value.secret_key;
        return value;
    }

    async function save() {
        if (saving) return;
        saving = true;
        formError = '';
        try {
            config = await request('/admin/backups/remote', {method: 'PUT', body: JSON.stringify(settings())});
            saving = false;
            drawer = false;
            toast.success('Impostazioni salvate.');
            onSaved();
            if (!config.key) openWizard();
        } catch (e) { formError = e.message; }
        finally { saving = false; draft.secret_key = ''; }
    }

    async function test(body = settings()) {
        if (testing) return;
        testing = true;
        testResult = null;
        try { testResult = await request('/admin/backups/remote/test', {method: 'POST', body: JSON.stringify(body)}); }
        catch (e) { testResult = {ok: false, message: e.message}; }
        finally { testing = false; }
        if (!drawer) testResult.ok ? toast.success(testResult.message) : toast.error(testResult.message);
    }

    async function toggle(event) {
        const enabled = event.target.checked;
        toggling = true;
        try {
            config = await request('/admin/backups/remote', {method: 'PUT', body: JSON.stringify({enabled})});
            toast.success(enabled ? 'Copia remota attivata.' : 'Copia remota disattivata.');
            onSaved();
        } catch (e) {
            event.target.checked = !enabled;
            toast.error(e.message);
        } finally { toggling = false; }
    }

    async function openWizard() {
        wizard = true;
        step = 1;
        tail = '';
        wizardError = '';
        recovery = null;
        working = true;
        try { recovery = await request('/admin/backups/remote/key', {method: 'POST', body: '{}'}); }
        catch (e) { wizardError = e.message; }
        finally { working = false; }
    }

    function closeWizard() {
        if (working) return;
        const done = step === 3;
        wizard = false;
        recovery = null;
        tail = '';
        if (done) onSaved();
    }

    function downloadKey() {
        const url = URL.createObjectURL(new Blob([keyFile(recovery.recovery_key, recovery.key_id)], {type: 'text/plain'}));
        const link = document.createElement('a');
        link.href = url;
        link.download = `assozeta-chiave-${recovery.key_id}.txt`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    async function copyKey() {
        try { await navigator.clipboard.writeText(recovery.recovery_key); toast.success('Chiave copiata.'); }
        catch { toast.error('Copia non riuscita: seleziona il testo.'); }
    }

    async function confirm() {
        if (working || tail.trim().length !== 6) return;
        working = true;
        wizardError = '';
        try {
            config = await request('/admin/backups/remote/key/confirm', {method: 'POST', body: JSON.stringify({key_id: recovery.key_id, tail: tail.trim()})});
            step = 3;
            recovery = {key_id: recovery.key_id};
        } catch (e) { wizardError = e.message; }
        finally { working = false; }
    }
</script>

<section class="si-card" aria-labelledby="remote-backup-title">
    <div class="si-card-head">
        <div>
            <h3 id="remote-backup-title" class="si-subtitle">Copia remota</h3>
            <p class="si-muted si-small" title={remote?.last_upload ? formatDate(remote.last_upload.at) : ''}>{status.text}</p>
        </div>
        <div class="si-actions">
            {#if status.pill}<span class="si-pill {status.tone}">{status.pill}</span>{/if}
            <button type="button" class="btn btn-light btn-sm" disabled={disabled || !config && !loadError} on:click={openDrawer}>{config?.configured ? 'Modifica' : 'Configura'}</button>
            {#if config?.configured}
                <InstanceMenu label="Altre azioni copia remota" items={[
                    {label: testing ? 'Prova in corso…' : 'Prova connessione', disabled: testing, onSelect: () => test({})},
                    {label: hasKey ? 'Genera nuova chiave' : 'Genera chiave', hint: hasKey ? 'Le copie già fatte restano con la chiave attuale.' : '', disabled, onSelect: openWizard},
                ]} />
            {/if}
        </div>
    </div>

    {#if loadError}<p class="text-danger si-small" role="alert">{loadError} <button type="button" class="si-link" on:click={load}>Riprova</button></p>{/if}
    {#if remote?.enabled && remote.last_failure}
        <p class="text-danger si-small" role="alert">Copia non riuscita: {remote.last_failure.error}</p>
    {:else if remote?.enabled && remote.status === 'error' && remote.error}
        <p class="text-danger si-small" role="alert">{remote.error}</p>
    {/if}

    {#if config?.configured}
        <Switch id="remote-backup-enabled" label="Attiva copia remota" checked={config.enabled} disabled={disabled || toggling || !!enableReason} on:change={toggle} />
        {#if enableReason && !config.enabled}
            <p class="si-reason mb-0">{enableReason} <button type="button" class="si-link" {disabled} on:click={openWizard}>Genera chiave</button></p>
        {/if}
        <dl class="facts">
            <div><dt>Bucket</dt><dd>{config.bucket}{config.prefix ? `/${config.prefix}` : ''}</dd></div>
            <div><dt>Chiave</dt><dd>{config.key?.id || '—'}</dd></div>
            <div><dt>Da conservare</dt><dd>{config.retention}</dd></div>
        </dl>
    {/if}
</section>

<InstanceDialog open={drawer} variant="drawer" title="Copia remota" locked={saving} on:close={() => { drawer = false; }}>
    <form id="remote-backup-form" class="si-stack" on:submit|preventDefault={save}>
        <label class="si-field">Endpoint<input class="form-control" type="url" bind:value={draft.endpoint} placeholder="Vuoto per AWS S3" autocomplete="off" /></label>
        <div class="pair">
            <label class="si-field">Regione<input class="form-control" bind:value={draft.region} placeholder="eu-south-1" autocomplete="off" /></label>
            <label class="si-field">Bucket<input class="form-control" bind:value={draft.bucket} required autocomplete="off" /></label>
        </div>
        <label class="si-field">Prefisso<input class="form-control" bind:value={draft.prefix} placeholder="assozeta" autocomplete="off" /></label>
        <label class="si-field">Access key<input class="form-control" bind:value={draft.access_key} required autocomplete="off" /></label>
        <label class="si-field">Secret key<input class="form-control" type="password" bind:value={draft.secret_key} required={!config?.secret_configured}
            placeholder={config?.secret_configured ? 'Salvata' : ''} autocomplete="new-password" /></label>
        <Switch id="remote-backup-path-style" label="Indirizzi path-style" bind:checked={draft.path_style} />
        <label class="si-field">Copie da conservare<input class="form-control" type="number" min="1" max="30" step="1" bind:value={draft.retention} required /></label>
        {#if testResult}<p class="si-small mb-0 {testResult.ok ? 'text-success' : 'text-danger'}" role="status">{testResult.message}</p>{/if}
        {#if formError}<p class="text-danger mb-0" role="alert">{formError}</p>{/if}
    </form>
    <svelte:fragment slot="footer">
        <button type="button" class="btn btn-light mr-auto" disabled={testing || saving || !draft.bucket || !draft.access_key} on:click={() => test()}>{testing ? 'Prova in corso…' : 'Prova connessione'}</button>
        <button type="button" class="btn btn-light" disabled={saving} on:click={() => { drawer = false; }}>Annulla</button>
        <button type="submit" form="remote-backup-form" class="btn btn-primary" disabled={saving}>{saving ? 'Salvataggio…' : 'Salva'}</button>
    </svelte:fragment>
</InstanceDialog>

<InstanceDialog open={wizard} title="Chiave di ripristino" locked={working} on:close={closeWizard}>
    <p class="si-muted si-small">Passo {step} di 3</p>
    {#if step === 1}
        <p class="note" role="note">Senza questa chiave le copie remote non si possono ripristinare. Viene mostrata solo ora.</p>
        {#if recovery?.recovery_key}
            <label class="si-field" for="remote-recovery-key">Chiave {recovery.key_id}</label>
            <textarea id="remote-recovery-key" class="form-control key" rows="3" readonly on:focus={event => event.target.select()}>{recovery.recovery_key}</textarea>
            <div class="si-actions mt-3">
                <button type="button" class="btn btn-primary btn-sm" data-autofocus on:click={downloadKey}>Scarica file</button>
                <button type="button" class="btn btn-light btn-sm" on:click={copyKey}>Copia</button>
            </div>
        {:else if working}
            <span class="si-skeleton" style="height: 4.5rem"></span>
        {/if}
    {:else if step === 2}
        <form id="remote-key-confirm" on:submit|preventDefault={confirm}>
            <label class="si-field" for="remote-key-tail">Ultimi 6 caratteri della chiave</label>
            <input id="remote-key-tail" class="form-control tail" bind:value={tail} maxlength="6" autocomplete="off" spellcheck="false" />
        </form>
    {:else}
        <p class="done"><CheckCircle size={22} weight="fill" /> Chiave {recovery?.key_id} attiva.</p>
        <p class="si-muted mb-0">{config?.enabled ? 'Le copie remote partono dopo ogni backup.' : 'Attiva la copia remota per iniziare.'}</p>
    {/if}
    {#if wizardError}<p class="text-danger mt-2 mb-0" role="alert">{wizardError}</p>{/if}
    <svelte:fragment slot="footer">
        {#if step === 1}
            <button type="button" class="btn btn-light" disabled={working} on:click={closeWizard}>Annulla</button>
            <button type="button" class="btn btn-primary" disabled={working || !recovery?.recovery_key} on:click={() => { step = 2; wizardError = ''; }}>Avanti</button>
        {:else if step === 2}
            <button type="button" class="btn btn-light" disabled={working} on:click={() => { step = 1; wizardError = ''; }}>Indietro</button>
            <button type="submit" form="remote-key-confirm" class="btn btn-primary" disabled={working || tail.trim().length !== 6}>{working ? 'Verifica…' : 'Conferma'}</button>
        {:else}
            <button type="button" class="btn btn-primary" data-autofocus on:click={closeWizard}>Fine</button>
        {/if}
    </svelte:fragment>
</InstanceDialog>

<style>
    .facts { display: flex; flex-wrap: wrap; gap: .5rem 2rem; margin: .5rem 0 0; }
    .facts div { min-width: 0; }
    .facts dt { font-size: .8125rem; font-weight: 500; color: var(--text-secondary); }
    .facts dd { margin: 0; font-weight: 600; overflow-wrap: anywhere; }
    .pair { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 10rem), 1fr)); gap: 1rem; }
    .key { font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace); font-size: .8125rem; resize: none; word-break: break-all; }
    .tail { max-width: 10rem; font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace); text-transform: uppercase; letter-spacing: .1em; }
    .note { padding: .625rem .875rem; border-radius: .5rem; background: var(--light-warning); }
    .done { display: flex; align-items: center; gap: .5rem; font-weight: 600; color: var(--success, #12a37a); }
    form :global(.form-group) { margin-bottom: 0; }
</style>
