<script>
    import {onMount, onDestroy} from 'svelte';
    import {toast} from 'svelte-sonner';
    import {apiFetch, originalFetch} from 'utils/ApiMiddleware.js';
    import {readStoredStatus} from './independentStatus.js';
    import InstanceUpdateStatus from './InstanceUpdateStatus.svelte';
    import {getApiHost, saveRuntimeConfig} from 'store/instanceStore.js';
    import InstanceBranding from './InstanceBranding.svelte';
    import InstanceEmail from './InstanceEmail.svelte';
    import InstanceIntegrations from './InstanceIntegrations.svelte';
    import DiagnosticResult from './DiagnosticResult.svelte';
    import ReleaseNotes from './release-notes/ReleaseNotes.svelte';
    import {createCompletionRefresh, mergeRunnerStatus} from './updateStatus.js';
    import {v4 as uuidv4} from 'uuid';

    export let changes = false;
    const endpoint = `${getApiHost()}/instance`;
    let info;
    let draft = {};
    let saved = '';
    let logoChanges = false;
    let emailChanges = false;
    let emailBusy = false;
    let integrationChanges = false;
    let integrationBusy = false;
    let section = 'overview';
    const sections = [['overview', 'Panoramica'], ['branding', 'Identità e logo'], ['email', 'Email'], ['integrations', 'Integrazioni'], ['updates', 'Aggiornamenti e backup'], ['diagnostics', 'Diagnostica']];
    let diagnostics;
    let diagnosticError = '';
    let diagnosticBusy = false;
    let diagnosticLoading = false;
    const healthLabels = {passed: 'Controlli essenziali superati', failed: 'Servizi da ripristinare', warning: 'Verifiche che richiedono attenzione', not_checked: 'Salute non ancora verificata'};
    $: issues = diagnostics?.checks?.filter(check => ['failed', 'warning', 'not_configured'].includes(check.status)) || [];

    async function loadDiagnostics() {
        diagnosticLoading = true;
        try { diagnostics = await request('/admin/diagnostics'); diagnosticError = ''; }
        catch (e) { diagnosticError = e.message; }
        finally { diagnosticLoading = false; }
    }
    async function refreshIntegrations() {
        try { await loadInfo(false); saveRuntimeConfig(info.config); }
        catch (e) { error = 'Configurazione salvata. Ricarica la pagina per aggiornare i valori pubblici.'; }
        await loadDiagnostics();
    }
    async function runDiagnostics() {
        if (diagnosticBusy || active || apiUnavailable) return;
        diagnosticBusy = true;
        diagnosticError = '';
        try { diagnostics = await request('/admin/diagnostics', {method: 'POST', body: '{}'}); }
        catch (e) { diagnosticError = e.message; }
        finally { diagnosticBusy = false; }
    }
    let loading = true;
    let refreshingInfo = false;
    let error = '';
    let saving = false;
    let catalog;
    let releaseError = '';
    let checkingReleases = false;
    let history = [];
    let runner = {available: false, active: null, history: []};
    let reconnecting = false;
    let apiUnavailable = false;
    let reviewing = null;
    let reviewNotes = [];
    let requestId;
    let starting = false;
    let disposed = false;
    let timer;
    let simulation = null;
    let simulateFailure = false;
    let simulationTimer;
    const completionRefresh = createCompletionRefresh();

    $: changes = integrationChanges || emailChanges || logoChanges || (!!saved && JSON.stringify(draft) !== saved);
    $: active = simulation || runner.active;
    $: updateReady = !apiUnavailable && !diagnosticBusy && !emailBusy && !integrationBusy && !changes && !active && !starting && !refreshingInfo && !checkingReleases && !releaseError && runner.available && runner.can_update !== false &&
        info?.mode === 'production' && catalog?.relation === 'behind' && catalog?.latest?.artifacts_ready;

    async function request(path, options = {}) {
        const result = await apiFetch(`${endpoint}${path}`, {method: 'GET', skipForbidden: true, ...options});
        if (result.error) {
            const detail = result.response?.error || result.response?.detail || Object.values(result.response || {}).flat().filter(value => typeof value === 'string').join(' ');
            throw new Error(typeof detail === 'string' ? detail : 'Operazione non riuscita. Riprova.');
        }
        return result.response;
    }

    async function loadInfo(resetDraft = false) {
        refreshingInfo = true;
        try {
            info = await request('/admin');
            if (resetDraft) {
                const {name, abbreviation, primaryColor, supportEmail} = info.config.oem;
                draft = {name, abbreviation, primaryColor, supportEmail};
                saved = JSON.stringify(draft);
            }
        } finally { refreshingInfo = false; }
    }

    async function saveBranding() {
        if (saving || active || apiUnavailable) return;
        saving = true;
        error = '';
        try {
            const submitted = {...draft};
            const result = await request('/admin', {method: 'PUT', body: JSON.stringify({oem: submitted})});
            saveRuntimeConfig(result.config);
            saved = JSON.stringify(submitted);
            toast.success('Impostazioni dell’istanza aggiornate.');
        } catch (e) { error = e.message; }
        finally { saving = false; }
    }

    async function loadReleases(refresh = false, page = 1) {
        checkingReleases = true;
        releaseError = '';
        try {
            const result = await request(`/admin/releases?page=${page}${refresh ? '&refresh=1' : ''}`);
            catalog = result;
            history = page === 1 ? result.history : [...history, ...result.history];
            return true;
        } catch (e) { releaseError = e.message; return false; }
        finally { checkingReleases = false; }
    }

    async function loadRunner() {
        let status;
        try {
            status = await request('/admin/updates');
            apiUnavailable = false;
        } catch {
            apiUnavailable = true;
        }
        if (!status?.available) {
            const independent = await readStoredStatus(originalFetch);
            if (independent.kind === 'owner') status = independent.status;
            else if (independent.kind === 'denied') {
                // Do not keep displaying private history after access is revoked.
                runner = {available: false, active: null, history: []};
                info = undefined;
                error = 'Accesso al servizio di aggiornamento non autorizzato. Verifica la sessione quando l’applicazione torna disponibile.';
                reconnecting = false;
                return;
            }
        }
        if (!status) status = {available: false};
        completionRefresh.observe(runner, status);
        runner = mergeRunnerStatus(runner, status);
        reconnecting = !status.available;
        if (!apiUnavailable && status.available && !runner.active) {
            try {
                if (!info) {
                    await loadInfo(true);
                    await loadReleases();
                    error = '';
                }
                await completionRefresh.run(() => loadInfo(), () => loadReleases(true));
            } catch { apiUnavailable = true; }
        }
    }

    function reviewUpdate() {
        // Generate before displaying confirmation; randomUUID alone is absent
        // on HTTP installations, while uuid also supports getRandomValues.
        requestId = uuidv4();
        reviewing = {...catalog.latest};
        reviewNotes = catalog.pending.map(release => ({...release}));
        error = '';
    }

    async function startUpdate() {
        if (!reviewing || !requestId || starting || apiUnavailable || emailBusy || integrationBusy || diagnosticBusy || changes) return;
        starting = true;
        error = '';
        try {
            const result = await request('/admin/updates', {
                method: 'POST',
                body: JSON.stringify({release_id: reviewing.id, tag: reviewing.tag, request_id: requestId}),
            });
            runner = {...runner, active: result.operation};
            reviewing = null;
        } catch (e) { error = e.message; }
        finally { starting = false; }
    }

    function startSimulation() {
        clearTimeout(simulationTimer);
        const steps = ['queued', 'checking', 'backup', 'downloading', 'migrating', 'restarting', 'health_check', 'completed'];
        let index = 0;
        function advance() {
            if (disposed) return;
            const stage = simulateFailure && index === 4 ? 'recovery_required' : steps[index];
            simulation = {id: 'simulation', stage, target_version: 'simulazione', simulated: true};
            if (stage === 'completed' || stage === 'recovery_required') return;
            index += 1;
            simulationTimer = setTimeout(advance, 900);
        }
        advance();
    }

    onMount(() => {
        async function initialize() {
            try { await loadInfo(true); }
            catch (e) { error = e.message; }
            finally { loading = false; }
            if (disposed) return;
            await Promise.all([info ? loadReleases() : Promise.resolve(), info ? loadDiagnostics() : Promise.resolve(), loadRunner()]);
            async function poll() {
                if (disposed) return;
                await loadRunner();
                if (!disposed) timer = setTimeout(poll, runner.active || reconnecting || apiUnavailable ? 4000 : 15000);
            }
            if (!disposed) timer = setTimeout(poll, 4000);
        }
        initialize();
    });
    onDestroy(() => {
        disposed = true;
        clearTimeout(timer);
        clearTimeout(simulationTimer);
    });
</script>

<div class="card card-custom">
    <div class="card-header py-3">
        <div class="card-title flex-column align-items-start">
            <h1 class="font-size-h1 font-weight-bolder">Self Instance</h1>
            <p class="text-muted mb-0">Configurazione, salute e manutenzione della tua installazione.</p>
        </div>
    </div>
    <div class="card-body">
        {#if error}<p class="alert alert-danger" role="alert">{error}</p>{/if}
        {#if loading}<p role="status">Caricamento dell’istanza…</p>{/if}
        {#if !info}<InstanceUpdateStatus {runner} {simulation} {reconnecting} {apiUnavailable} />{/if}
        {#if info}
            <nav class="instance-nav" aria-label="Sezioni Self Instance">
                {#each sections as [id, label]}
                    <button type="button" class:current={section === id} aria-current={section === id ? 'page' : undefined} on:click={() => section = id}>{label}{((id === 'branding' && (logoChanges || JSON.stringify(draft) !== saved)) || (id === 'email' && emailChanges) || (id === 'integrations' && integrationChanges)) ? ' •' : ''}</button>
                {/each}
            </nav>
            {#if section !== 'updates'}<InstanceUpdateStatus {runner} {simulation} {reconnecting} {apiUnavailable} showHistory={false} />{/if}
            {#if changes}<p class="unsaved" role="status">Modifiche non salvate. Salvale o annullale nella relativa sezione.</p>{/if}
            <div hidden={section !== 'overview'}>
                <section aria-labelledby="instance-overview">
                    <h2 id="instance-overview">Panoramica</h2>
                    <div class="overview-grid">
                        <div class="overview-tile"><span>Istanza</span><strong>{info.config.oem.name}</strong><span>{info.config.oem.supportEmail || 'Email di supporto non impostata'}</span></div>
                        <div class="overview-tile"><span>Versione installata</span><strong>{info.running_version || 'Non riconosciuta'}</strong><button class="text-action" on:click={() => section = 'updates'}>Visualizza release e backup</button></div>
                        <div class="overview-tile"><span>Servizi essenziali</span><strong>{healthLabels[diagnostics?.overall] || healthLabels.not_checked}</strong><span>{diagnostics?.checked_at ? `Ultima verifica: ${new Date(diagnostics.checked_at).toLocaleString('it-IT')}` : 'Nessuna diagnostica eseguita'}</span></div>
                    </div>
                    <p class="mt-4">I risultati descrivono l’ultima verifica e non costituiscono monitoraggio continuo. Le integrazioni facoltative sono valutate separatamente.</p>
                    <button class="btn btn-primary" on:click={() => section = 'diagnostics'}>Apri diagnostica</button>
                    {#if diagnosticLoading}<p role="status" class="mt-4">Caricamento risultati…</p>{/if}
                    {#if diagnosticError}<p class="alert alert-warning mt-4" role="alert">Impossibile caricare i risultati. Apri Diagnostica per riprovare.</p>{/if}
                    {#if issues.length}
                        <h3 class="mt-6">Richiedono attenzione</h3>
                        <ul class="issue-list">{#each issues as check}<li><span>{check.label}{check.core ? '' : ' (facoltativo)'}</span><button class="text-action" on:click={() => section = check.section}>Visualizza dettagli</button></li>{/each}</ul>
                    {/if}
                </section>
            </div>
            <div hidden={section !== 'email'}><InstanceEmail {request} bind:changes={emailChanges} bind:busy={emailBusy} disabled={apiUnavailable || !!active || starting} onSaved={loadDiagnostics} /></div>
            <div hidden={section !== 'integrations'}><InstanceIntegrations {request} checks={diagnostics?.integrations || []} bind:changes={integrationChanges} bind:busy={integrationBusy} disabled={apiUnavailable || !!active || starting} onSaved={refreshIntegrations} /></div>
            <div hidden={section !== 'diagnostics'}>
                <section aria-labelledby="instance-diagnostics">
                    <h2 id="instance-diagnostics">Diagnostica</h2>
                    <p>Verifiche in lettura dei servizi, con un limite di tempo per ogni controllo. Non invia email, non esegue pagamenti e non avvia aggiornamenti o backup.</p>
                    <div class="instance-actions mb-4">
                        <button class="btn btn-primary" disabled={diagnosticBusy || apiUnavailable || !!active || starting} on:click={runDiagnostics}>{diagnosticBusy ? 'Diagnostica in corso…' : 'Esegui diagnostica'}</button>
                        <button class="btn btn-light" disabled={diagnosticBusy || diagnosticLoading || apiUnavailable} on:click={loadDiagnostics}>Ricarica risultati salvati</button>
                    </div>
                    {#if diagnosticBusy}<p role="status">Verifica in corso. L’operazione può richiedere circa 30 secondi; i risultati precedenti restano visibili.</p>{/if}
                    {#if diagnosticError}<p class="alert alert-danger" role="alert">{diagnosticError}</p>{/if}
                    <p class="text-muted">Ogni risultato distingue configurazione, connessione e prova funzionale. “Verificato” vale solo per la verifica descritta.</p>
                    <div class="checks-grid" aria-live="polite">{#each diagnostics?.checks || [] as check}<DiagnosticResult {check} />{/each}</div>
                </section>
            </div>
            <div hidden={section !== 'branding'}>
            <section class="mb-8" aria-labelledby="instance-identity">
                <h2 id="instance-identity">Identità dell’istanza</h2>
                <div class="brand-preview mb-4" aria-label="Anteprima identità">
                    <span class="brand-swatch" style:background-color={draft.primaryColor} aria-hidden="true"></span>
                    <div><strong>{draft.name || 'Nome istanza'}</strong><p class="mb-0">{draft.abbreviation} · {draft.supportEmail || 'Email di supporto'}</p></div>
                </div>
                <form on:submit|preventDefault={saveBranding} class="identity-form">
                    <label>Nome<input class="form-control" bind:value={draft.name} required maxlength="255" disabled={apiUnavailable || saving || !!active} /></label>
                    <label>Abbreviazione<input class="form-control" bind:value={draft.abbreviation} maxlength="50" disabled={apiUnavailable || saving || !!active} /></label>
                    <label>Email di supporto<input class="form-control" type="email" bind:value={draft.supportEmail} disabled={apiUnavailable || saving || !!active} /></label>
                    <label>Colore principale<input class="form-control" type="color" bind:value={draft.primaryColor} disabled={apiUnavailable || saving || !!active} /></label>
                    <div class="instance-actions">
                        <button type="submit" class="btn btn-primary" disabled={apiUnavailable || saving || !!active || JSON.stringify(draft) === saved}>
                            {saving ? 'Salvataggio…' : 'Salva impostazioni'}
                        </button>
                        {#if JSON.stringify(draft) !== saved}
                            <button type="button" class="btn btn-light" disabled={saving} on:click={() => draft = JSON.parse(saved)}>Annulla</button>
                        {/if}
                    </div>
                </form>
            </section>
            <InstanceBranding bind:changes={logoChanges} disabled={apiUnavailable || !!active || starting} />
            </div>
            <div hidden={section !== 'updates'}>
            {#if section === 'updates'}<InstanceUpdateStatus {runner} {simulation} {reconnecting} {apiUnavailable} />{/if}
            <section class="mb-8" aria-labelledby="instance-version">
                <h2 id="instance-version">Versione e aggiornamenti</h2>
                <dl class="version-grid">
                    <dt>Versione in esecuzione</dt><dd>{info.running_version || 'Non riconosciuta'}</dd>
                    <dt>Versione configurata</dt><dd>{info.configured_version || 'Non specificata'}</dd>
                    <dt>Ultima release stabile</dt><dd>{catalog?.latest?.tag || 'Non disponibile'}</dd>
                </dl>
                {#if catalog?.relation === 'current'}<p>La tua installazione è aggiornata.</p>{/if}
                {#if catalog?.relation === 'ahead'}<p>La versione installata è successiva all’ultima release stabile.</p>{/if}
                {#if catalog?.relation === 'unknown'}<p>La versione in esecuzione non è una release stabile riconosciuta.</p>{/if}
                {#if !runner.available || runner.can_update === false && !active}<p class="text-muted">{runner.reason || 'Verifica del servizio di aggiornamento…'}</p>{/if}
                {#if catalog?.latest && !catalog.latest.artifacts_ready}<p>La release è pubblicata, ma i file per l’aggiornamento non sono ancora pronti.</p>{/if}
                {#if changes}<p>Salva o annulla le modifiche prima di aggiornare l’istanza.</p>{/if}
                <div class="instance-actions">
                    <button class="btn btn-light-primary" disabled={apiUnavailable || checkingReleases || !!active} on:click={() => loadReleases(true)}>
                        {checkingReleases ? 'Verifica in corso…' : 'Controlla aggiornamenti'}
                    </button>
                    {#if catalog?.relation === 'behind'}
                        <button class="btn btn-primary" disabled={!updateReady} on:click={reviewUpdate}>Esamina aggiornamento a {catalog.latest.tag}</button>
                    {/if}
                </div>
                {#if releaseError}<p class="text-danger mt-4" role="alert">{releaseError}</p>{/if}
                {#if info.mode !== 'production'}
                    <details class="mt-4">
                        <summary>Prova il flusso di aggiornamento in sviluppo</summary>
                        <label class="d-block mt-4"><input type="checkbox" bind:checked={simulateFailure} /> Simula un errore durante le migrazioni</label>
                        <button class="btn btn-light" on:click={startSimulation}>Avvia simulazione</button>
                        {#if simulation}
                            <button class="btn btn-light" on:click={() => { clearTimeout(simulationTimer); simulation = null; }}>Chiudi simulazione</button>
                        {/if}
                    </details>
                {/if}
            </section>

            <section class="mb-8" aria-labelledby="instance-backups">
                <h2 id="instance-backups">Backup e ripristino</h2>
                {#each (diagnostics?.checks || []).filter(check => ['backups', 'updater'].includes(check.id)) as check}<div class="mb-3"><DiagnosticResult {check} /></div>{/each}
                <p>Un backup viene creato prima di ogni aggiornamento. La presenza di un archivio non dimostra che possa essere ripristinato.</p>
                <details><summary>Operazioni sul server e recupero</summary>
                    <p class="mt-3">Dalla cartella selfhost dell’installazione:</p>
                    <p><code>./bin/assozeta backup</code> crea un backup coerente; l’applicazione viene fermata temporaneamente.</p>
                    <p>Conserva una copia protetta fuori dal server. Prova il ripristino su un’installazione isolata seguendo selfhost/UPDATES.md prima di un aggiornamento importante.</p>
                    <p>Se un aggiornamento richiede recupero, segui le istruzioni mostrate nello stato operazione. Il comando <code>./bin/assozeta recover-upgrade PERCORSO_BACKUP</code> ripristina versione, configurazione e dati; richiede l’accesso al server.</p>
                </details>
            </section>
            {#if reviewing}
                <section class="review-panel mb-8" aria-labelledby="update-review">
                    <h2 id="update-review">Aggiorna a {reviewing.tag}</h2>
                    <p>Verrà creato un backup prima dell’aggiornamento. L’applicazione sarà temporaneamente non disponibile durante le migrazioni e il riavvio.</p>
                    <p>La versione selezionata rimane {reviewing.tag}, anche se viene pubblicata una nuova release.</p>
                    {#each reviewNotes as release (release.id)}<ReleaseNotes {release} expanded />{/each}
                    <div class="instance-actions mt-4">
                        <button class="btn btn-primary" disabled={apiUnavailable || starting || emailBusy || integrationBusy || diagnosticBusy || changes || !!active} on:click={startUpdate}>
                            {starting ? 'Avvio in corso…' : `Conferma aggiornamento a ${reviewing.tag}`}
                        </button>
                        <button class="btn btn-light" disabled={starting} on:click={() => reviewing = null}>Annulla</button>
                    </div>
                </section>
            {/if}

            <section class="mb-8" aria-labelledby="release-history">
                <h2 id="release-history">Note di rilascio</h2>
                {#if catalog?.pending?.length > 0}
                    <p>Tutte le modifiche dalla versione installata all’ultima release stabile.</p>
                    {#each catalog.pending as release (release.id)}<ReleaseNotes {release} />{/each}
                {:else if catalog?.latest}
                    <ReleaseNotes release={catalog.latest} expanded />
                {/if}
                <details class="mt-4">
                    <summary>Cronologia completa delle release</summary>
                    {#each history as release (release.id)}<ReleaseNotes {release} />{/each}
                    {#if catalog?.next_page}
                        <button class="btn btn-light mt-4" disabled={checkingReleases} on:click={() => loadReleases(false, catalog.next_page)}>Carica release precedenti</button>
                    {/if}
                </details>
            </section>
            </div>
        {/if}
    </div>
</div>

<style>
    .text-muted { color: #536171 !important; }
    [hidden] { display: none !important; }
    .instance-nav { display: flex; flex-wrap: wrap; gap: .5rem; padding-bottom: 1.5rem; margin-bottom: 1.5rem; border-bottom: 1px solid #dce1e7; }
    .instance-nav button { background: #f2f4f7; color: #263747; border: 1px solid transparent; border-radius: .5rem; padding: .75rem 1rem; }
    .instance-nav button.current { background: #e8eefc; border-color: #335caa; color: #234582; font-weight: 700; }
    .instance-nav button:focus-visible, .text-action:focus-visible { outline: 3px solid #335caa; outline-offset: 2px; }
    .overview-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 15rem), 1fr)); gap: 1rem; }
    .overview-tile { display: flex; flex-direction: column; gap: .75rem; padding: 1.25rem; border: 1px solid #dce1e7; border-radius: .65rem; overflow-wrap: anywhere; }
    .overview-tile strong { font-size: 1.2rem; }
    .text-action { background: none; border: 0; padding: 0; color: #234582; text-align: left; text-decoration: underline; }
    .checks-grid { display: grid; gap: 1rem; }
    .issue-list { list-style: none; padding: 0; }
    .issue-list li { display: flex; flex-wrap: wrap; justify-content: space-between; gap: .75rem; padding: 1rem 0; border-bottom: 1px solid #dce1e7; }
    .unsaved { padding: .75rem 1rem; border-left: 3px solid #936000; background: #fff8e6; }
    .brand-preview { display: flex; align-items: center; gap: 1rem; border: 1px solid #dce1e7; border-radius: .65rem; padding: 1rem; overflow-wrap: anywhere; }
    .brand-preview > div { min-width: 0; }
    .brand-swatch { width: 2.5rem; height: 2.5rem; flex-shrink: 0; border: 1px solid #dce1e7; border-radius: .5rem; }
    .identity-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; }
    .identity-form label { display: grid; gap: 0.5rem; }
    .instance-actions { display: flex; flex-wrap: wrap; gap: 0.75rem; grid-column: 1 / -1; }
    .version-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem 1rem; overflow-wrap: anywhere; }
    .review-panel { border: 1px solid var(--primary); padding: 1.5rem; border-radius: 0.75rem; }
    @media (max-width: 575px) { .identity-form { grid-template-columns: 1fr; } .version-grid { grid-template-columns: 1fr; } }
</style>
