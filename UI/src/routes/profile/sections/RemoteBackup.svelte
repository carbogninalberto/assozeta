<script>
    // Copia remota: encrypted copies on S3-compatible storage, off by default.
    // Settings open in a drawer; the recovery key is set up in a 3-step dialog.
    import {onMount} from 'svelte';
    import {toast} from 'svelte-sonner';
    import {CheckCircle, Copy, DownloadSimple, FloppyDisk, PencilSimple, Plugs} from 'phosphor-svelte';
    import Switch from '../../../components/inputs/Switch.svelte';
    import InstanceDialog from './InstanceDialog.svelte';
    import InstanceMenu from './InstanceMenu.svelte';
    import {formatDate} from './instanceFormat.js';
    import {remoteStatus, keyFile} from './remoteBackup.js';
    import {remoteSettingsBody, remoteTestBody, remoteToggleBody, keyBody, keyConfirmBody, WRONG_PASSWORD} from './backupPassword.js';

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
    // Every settings change needs the owner password; it is cleared after each request.
    let password = '';
    let togglePrompt = null; // {enabled, target} while the switch waits for the password
    let toggleError = '';
    // "Prova connessione" from the menu tests the saved settings: it asks for the password first.
    let testPrompt = false;
    let testPassword = '';

    let wizard = false;
    let step = 1;
    let recovery = null; // {key_id, recovery_key}, in memory only while the dialog is open
    let tail = '';
    let wizardError = '';
    let working = false;
    // Typed once for the wizard, kept in memory until it closes or the key is confirmed.
    let keyPassword = '';
    let askKeyPassword = false;

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
        password = '';
        drawer = true;
    }

    function closeDrawer() {
        if (saving) return;
        drawer = false;
        password = '';
    }

    async function save() {
        if (saving || !password) return;
        saving = true;
        formError = '';
        const typed = password;
        try {
            config = await request('/admin/backups/remote', {method: 'PUT', body: JSON.stringify(remoteSettingsBody(draft, typed))});
            saving = false;
            drawer = false;
            toast.success('Impostazioni salvate.');
            onSaved();
            // Same owner, same minute: reuse the password for the first key.
            if (!config.key) openWizard(typed);
        } catch (e) { formError = e.message; }
        finally { saving = false; password = ''; draft.secret_key = ''; }
    }

    async function test(body = remoteTestBody(draft, password)) {
        if (testing || !body.password) return;
        testing = true;
        testResult = null;
        try { testResult = await request('/admin/backups/remote/test', {method: 'POST', body: JSON.stringify(body)}); }
        catch (e) { testResult = {ok: false, message: e.message}; }
        finally { testing = false; }
        if (!drawer) testResult.ok ? toast.success(testResult.message) : toast.error(testResult.message);
    }

    function openTestPrompt() {
        testPrompt = true;
        testPassword = '';
    }

    function closeTestPrompt() {
        if (testing) return;
        testPrompt = false;
        testPassword = '';
    }

    async function testSaved() {
        if (testing || !testPassword) return;
        const typed = testPassword;
        testPrompt = false;
        testPassword = '';
        await test(remoteTestBody(null, typed));
    }

    // The switch asks for the password first; cancelling puts it back.
    function toggle(event) {
        togglePrompt = {enabled: event.target.checked, target: event.target};
        password = '';
        toggleError = '';
    }

    function cancelToggle() {
        if (toggling || !togglePrompt) return;
        togglePrompt.target.checked = !togglePrompt.enabled;
        togglePrompt = null;
        password = '';
        toggleError = '';
    }

    async function confirmToggle() {
        if (toggling || !password || !togglePrompt) return;
        const {enabled} = togglePrompt;
        toggling = true;
        toggleError = '';
        try {
            config = await request('/admin/backups/remote', {method: 'PUT', body: JSON.stringify(remoteToggleBody(enabled, password))});
            togglePrompt = null;
            toast.success(enabled ? 'Copia remota attivata.' : 'Copia remota disattivata.');
            onSaved();
        } catch (e) { toggleError = e.message; }
        finally { password = ''; toggling = false; }
    }

    // With a password (right after saving the settings) the key is generated at once;
    // otherwise step 1 asks for it first.
    function openWizard(typed = '') {
        wizard = true;
        step = 1;
        tail = '';
        wizardError = '';
        recovery = null;
        askKeyPassword = false;
        keyPassword = typeof typed === 'string' ? typed : '';
        if (keyPassword) generateKey();
    }

    async function generateKey() {
        if (working || !keyPassword) return;
        working = true;
        wizardError = '';
        try { recovery = await request('/admin/backups/remote/key', {method: 'POST', body: JSON.stringify(keyBody(keyPassword))}); }
        catch (e) { wizardError = e.message; keyPassword = ''; }
        finally { working = false; }
    }

    function closeWizard() {
        if (working) return;
        const done = step === 3;
        wizard = false;
        recovery = null;
        tail = '';
        keyPassword = '';
        askKeyPassword = false;
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
        if (working || tail.trim().length !== 6 || !keyPassword) return;
        working = true;
        wizardError = '';
        try {
            config = await request('/admin/backups/remote/key/confirm', {method: 'POST', body: JSON.stringify(keyConfirmBody(recovery.key_id, tail, keyPassword))});
            step = 3;
            recovery = {key_id: recovery.key_id};
            keyPassword = '';
            askKeyPassword = false;
        } catch (e) {
            wizardError = e.message;
            // Let the owner type it again right here.
            if (e.message === WRONG_PASSWORD) { keyPassword = ''; askKeyPassword = true; }
        } finally { working = false; }
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
            <button type="button" class="btn btn-light btn-sm si-btn" disabled={disabled || !config && !loadError} on:click={openDrawer}><PencilSimple size={16} weight="duotone" />{config?.configured ? 'Modifica' : 'Configura'}</button>
            {#if config?.configured}
                <InstanceMenu label="Altre azioni copia remota" items={[
                    {label: testing ? 'Prova in corso…' : 'Prova connessione', disabled: testing, onSelect: openTestPrompt},
                    {label: hasKey ? 'Genera nuova chiave' : 'Genera chiave', hint: hasKey ? 'Le copie già fatte restano con la chiave attuale.' : '', disabled, onSelect: () => openWizard()},
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
            <p class="si-reason mb-0">{enableReason} <button type="button" class="si-link" {disabled} on:click={() => openWizard()}>Genera chiave</button></p>
        {/if}
        <dl class="facts">
            <div><dt>Bucket</dt><dd>{config.bucket}{config.prefix ? `/${config.prefix}` : ''}</dd></div>
            <div><dt>Chiave</dt><dd>{config.key?.id || '—'}</dd></div>
            <div><dt>Da conservare</dt><dd>{config.retention}</dd></div>
        </dl>
    {/if}
</section>

<InstanceDialog open={drawer} variant="drawer" title="Copia remota" locked={saving} on:close={closeDrawer}>
    <form id="remote-backup-form" class="si-stack" on:submit|preventDefault={save}>
        <label class="si-field">Endpoint<input class="form-control" type="url" bind:value={draft.endpoint} placeholder="https://… (vuoto per AWS S3)" autocomplete="off" aria-describedby="remote-backup-endpoint-hint" /></label>
        <p id="remote-backup-endpoint-hint" class="si-hint mb-0">Indirizzo https:// del servizio S3 compatibile.</p>
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
        <label class="si-field" for="remote-backup-password">Conferma la tua password
            <input id="remote-backup-password" class="form-control" type="password" autocomplete="current-password" bind:value={password} disabled={saving} />
        </label>
        {#if formError}<p class="text-danger mb-0" role="alert">{formError}</p>{/if}
    </form>
    <svelte:fragment slot="footer">
        <button type="button" class="btn btn-light mr-auto si-btn" disabled={testing || saving || !draft.bucket || !draft.access_key || !password} title={password ? '' : 'Conferma la tua password per provare la connessione.'} on:click={() => test()}><Plugs size={18} weight="duotone" />{testing ? 'Prova in corso…' : 'Prova connessione'}</button>
        <button type="button" class="btn btn-light" disabled={saving} on:click={closeDrawer}>Annulla</button>
        <button type="submit" form="remote-backup-form" class="btn btn-primary si-btn" disabled={saving || !password}><FloppyDisk size={18} weight="duotone" />{saving ? 'Salvataggio…' : 'Salva'}</button>
    </svelte:fragment>
</InstanceDialog>

<InstanceDialog open={wizard} title="Chiave di ripristino" locked={working} on:close={closeWizard}>
    <p class="si-muted si-small">Passo {step} di 3</p>
    {#if step === 1}
        <p class="note" role="note">Senza questa chiave le copie remote non si possono ripristinare. Viene mostrata solo ora.</p>
        {#if !recovery && !working}
            <form id="remote-key-password" on:submit|preventDefault={generateKey}>
                <label class="si-field" for="remote-key-password-input">Conferma la tua password
                    <input id="remote-key-password-input" class="form-control" type="password" autocomplete="current-password" bind:value={keyPassword} />
                </label>
            </form>
        {:else if recovery?.recovery_key}
            <label class="si-field" for="remote-recovery-key">Chiave {recovery.key_id}</label>
            <textarea id="remote-recovery-key" class="form-control key" rows="3" readonly on:focus={event => event.target.select()}>{recovery.recovery_key}</textarea>
            <div class="si-actions mt-3">
                <button type="button" class="btn btn-primary btn-sm si-btn" data-autofocus on:click={downloadKey}><DownloadSimple size={16} weight="duotone" />Scarica file</button>
                <button type="button" class="btn btn-light btn-sm si-btn" on:click={copyKey}><Copy size={16} weight="duotone" />Copia</button>
            </div>
        {:else if working}
            <span class="si-skeleton" style="height: 4.5rem"></span>
        {/if}
    {:else if step === 2}
        <form id="remote-key-confirm" on:submit|preventDefault={confirm}>
            <label class="si-field" for="remote-key-tail">Ultimi 6 caratteri della chiave</label>
            <input id="remote-key-tail" class="form-control tail" bind:value={tail} maxlength="6" autocomplete="off" spellcheck="false" />
            {#if askKeyPassword}
                <label class="si-field mt-3" for="remote-key-confirm-password">Conferma la tua password
                    <input id="remote-key-confirm-password" class="form-control" type="password" autocomplete="current-password" bind:value={keyPassword} disabled={working} />
                </label>
            {/if}
        </form>
    {:else}
        <p class="done"><CheckCircle size={22} weight="fill" /> Chiave {recovery?.key_id} attiva.</p>
        <p class="si-muted mb-0">{config?.enabled ? 'Le copie remote partono dopo ogni backup.' : 'Attiva la copia remota per iniziare.'}</p>
    {/if}
    {#if wizardError}<p class="text-danger mt-2 mb-0" role="alert">{wizardError}</p>{/if}
    <svelte:fragment slot="footer">
        {#if step === 1 && !recovery}
            <button type="button" class="btn btn-light" disabled={working} on:click={closeWizard}>Annulla</button>
            <button type="submit" form="remote-key-password" class="btn btn-primary" disabled={working || !keyPassword}>{working ? 'Generazione…' : 'Genera chiave'}</button>
        {:else if step === 1}
            <button type="button" class="btn btn-light" disabled={working} on:click={closeWizard}>Annulla</button>
            <button type="button" class="btn btn-primary" disabled={working || !recovery?.recovery_key} on:click={() => { step = 2; wizardError = ''; }}>Avanti</button>
        {:else if step === 2}
            <button type="button" class="btn btn-light" disabled={working} on:click={() => { step = 1; wizardError = ''; }}>Indietro</button>
            <button type="submit" form="remote-key-confirm" class="btn btn-primary" disabled={working || tail.trim().length !== 6 || !keyPassword}>{working ? 'Verifica…' : 'Conferma'}</button>
        {:else}
            <button type="button" class="btn btn-primary" data-autofocus on:click={closeWizard}>Fine</button>
        {/if}
    </svelte:fragment>
</InstanceDialog>

<InstanceDialog open={testPrompt} title="Prova connessione" locked={testing} on:close={closeTestPrompt}>
    <form id="remote-test-form" on:submit|preventDefault={testSaved}>
        <p class="si-muted si-small">Prova le impostazioni salvate: scrittura, lettura ed eliminazione di un file di prova.</p>
        <label class="si-field" for="remote-test-password">Conferma la tua password
            <input id="remote-test-password" class="form-control" type="password" autocomplete="current-password" bind:value={testPassword} disabled={testing} />
        </label>
    </form>
    <svelte:fragment slot="footer">
        <button type="button" class="btn btn-light" disabled={testing} on:click={closeTestPrompt}>Annulla</button>
        <button type="submit" form="remote-test-form" class="btn btn-primary" disabled={!testPassword || testing}>{testing ? 'Prova in corso…' : 'Prova'}</button>
    </svelte:fragment>
</InstanceDialog>

<InstanceDialog open={!!togglePrompt} title={togglePrompt?.enabled ? 'Attivare la copia remota?' : 'Disattivare la copia remota?'} locked={toggling} on:close={cancelToggle}>
    <form id="remote-toggle-form" on:submit|preventDefault={confirmToggle}>
        <label class="si-field" for="remote-toggle-password">Conferma la tua password
            <input id="remote-toggle-password" class="form-control" type="password" autocomplete="current-password" bind:value={password} disabled={toggling} />
        </label>
        {#if toggleError}<p class="text-danger mt-2 mb-0" role="alert">{toggleError}</p>{/if}
    </form>
    <svelte:fragment slot="footer">
        <button type="button" class="btn btn-light" disabled={toggling} on:click={cancelToggle}>Annulla</button>
        <button type="submit" form="remote-toggle-form" class="btn btn-primary" disabled={!password || toggling}>{toggling ? 'Salvataggio…' : togglePrompt?.enabled ? 'Attiva' : 'Disattiva'}</button>
    </svelte:fragment>
</InstanceDialog>

<style>
    .facts { display: flex; flex-wrap: wrap; gap: .5rem 2rem; margin: .5rem 0 0; }
    .facts div { min-width: 0; }
    .facts dt { font-size: .925rem; font-weight: 500; color: var(--text-secondary); }
    .facts dd { margin: 0; font-weight: 600; overflow-wrap: anywhere; }
    .pair { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 10rem), 1fr)); gap: 1rem; }
    .key { font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace); font-size: .925rem; resize: none; word-break: break-all; }
    .tail { max-width: 10rem; font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace); text-transform: uppercase; letter-spacing: .1em; }
    .note { padding: .625rem .875rem; border-radius: .5rem; background: var(--light-warning); }
    .done { display: flex; align-items: center; gap: .5rem; font-weight: 600; color: var(--success, #12a37a); }
    form :global(.form-group) { margin-bottom: 0; }
</style>
