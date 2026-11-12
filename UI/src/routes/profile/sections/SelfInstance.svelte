<script>
    import {onMount, onDestroy} from 'svelte';
    import {readProfileLocation, navigateProfile} from 'utils/profileNavigation.js';
    import {apiFetch, originalFetch} from 'utils/ApiMiddleware.js';
    import {getApiHost, saveRuntimeConfig, clearInstanceCache} from 'store/instanceStore.js';
    import {readStoredStatus, applicationReady} from './independentStatus.js';
    import {apiErrorMessage} from './backupPassword.js';
    import {createCompletionRefresh, mergeRunnerStatus, restartResult} from './updateStatus.js';
    import {v4 as uuidv4} from 'uuid';
    import {SquaresFour, Archive, ArrowsClockwise, GearSix, IdentificationCard, EnvelopeSimple, Plugs, Robot, LinkSimple, Power, Flask} from 'phosphor-svelte';
    import './selfInstance.css';
    import InstanceBanner from './InstanceBanner.svelte';
    import InstanceMenu from './InstanceMenu.svelte';
    import InstanceDialog from './InstanceDialog.svelte';
    import InstanceOverview from './InstanceOverview.svelte';
    import InstanceBackups from './InstanceBackups.svelte';
    import InstanceUpdates from './InstanceUpdates.svelte';
    import InstanceBranding from './InstanceBranding.svelte';
    import InstanceEmail from './InstanceEmail.svelte';
    import InstanceIntegrations from './InstanceIntegrations.svelte';
    import InstanceIntegration from './InstanceIntegration.svelte';
    import BakneyPairing from './BakneyPairing.svelte';
    import InstanceApiVersion from './InstanceApiVersion.svelte';

    export let changes = false;
    const endpoint = `${getApiHost()}/instance`;

    // Top-level tabs; settings has its own sub-sections. The URL keeps the
    // sub-section id (tab=email…) so earlier links keep working.
    const tabs = [['overview', 'Panoramica', SquaresFour], ['backups', 'Backup', Archive], ['updates', 'Aggiornamenti', ArrowsClockwise], ['settings', 'Impostazioni', GearSix]];
    const settingsSections = [['branding', 'Identità', IdentificationCard], ['email', 'Email', EnvelopeSimple], ['integrations', 'Integrazioni', Plugs], ['ai', 'Bot AI', Robot], ['pairing', 'Bakney', LinkSimple], ['api', 'API (beta)', Flask]];
    const settingsIds = settingsSections.map(([id]) => id);
    let tab = 'overview';
    let settingsSection = 'branding';
    let checksOpen = false;

    function applyLocation() {
        if (readProfileLocation().page !== 'self-instance') return;
        const requested = readProfileLocation().tab;
        if (settingsIds.includes(requested)) { tab = 'settings'; settingsSection = requested; }
        else if (requested === 'settings') tab = 'settings';
        else if (requested === 'diagnostics') { tab = 'overview'; checksOpen = true; }
        else tab = tabs.some(([id]) => id === requested) ? requested : 'overview';
    }

    function navigate(target) {
        if (settingsIds.includes(target)) { tab = 'settings'; settingsSection = target; }
        else tab = target;
        navigateProfile('self-instance', tab === 'settings' ? settingsSection : tab);
        window.scrollTo?.({top: 0, behavior: 'smooth'});
    }

    // Arrow keys move between tabs, following the WAI-ARIA tabs pattern.
    function onTabKeydown(event, list, current, select) {
        const index = list.findIndex(([id]) => id === current);
        const step = {ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1}[event.key];
        let next = step ? (index + step + list.length) % list.length : event.key === 'Home' ? 0 : event.key === 'End' ? list.length - 1 : null;
        if (next === null) return;
        event.preventDefault();
        select(list[next][0]);
        event.currentTarget.parentElement.querySelectorAll('[role="tab"]')[next]?.focus();
    }

    let info;
    let loading = true;
    let refreshingInfo = false;
    let error = '';
    let logoChanges = false;
    let logoBusy = false;
    let emailChanges = false;
    let emailBusy = false;
    let aiChanges = false;
    let aiBusy = false;
    let integrationChanges = false;
    let integrationBusy = false;
    let pairingBusy = false;
    let apiBusy = false;

    let diagnostics;
    let diagnosticError = '';
    let diagnosticBusy = false;
    let diagnosticLoading = false;

    let backups = null;
    let backupsLoading = false;
    let backupsError = '';

    let catalog;
    let releaseError = '';
    let checkingReleases = false;
    let releases = [];
    // The release catalog is fetched only for the tabs that show it.
    let releasesRequested = false;
    $: if (info && !releasesRequested && (tab === 'overview' || tab === 'updates')) { releasesRequested = true; loadReleases(); }
    let runner = {available: false, active: null, history: []};
    let reconnecting = false;
    let apiUnavailable = false;
    let reviewing = null;
    let reviewNotes = [];
    let requestId;
    let starting = false;
    let startError = '';
    let restartOpen = false;
    let restartRequestId = null;
    let restartStartedAt = 0;
    let restartTimedOut = false;
    let disposed = false;
    let timer;
    let simulation = null;
    let simulateFailure = false;
    let simulationTimer;
    const completionRefresh = createCompletionRefresh();

    $: settingsDirty = {branding: logoChanges, email: emailChanges, integrations: integrationChanges, ai: aiChanges, pairing: false, api: false};
    $: changes = aiChanges || integrationChanges || emailChanges || logoChanges;
    $: settingsBusy = logoBusy || emailBusy || aiBusy || integrationBusy || pairingBusy || apiBusy;
    $: active = simulation || runner.active || (restartRequestId ? {kind: 'restart', stage: 'queued'} : null);
    $: locked = apiUnavailable || !!active || starting;
    $: restartBlocked = info?.mode !== 'production' ? 'Disponibile solo in produzione.'
        : changes ? 'Salva o annulla prima le modifiche.'
        : active || starting || restartRequestId ? 'C’è già un’operazione in corso.'
        : apiUnavailable || !runner.available || runner.can_restart !== true ? 'Servizio di riavvio non disponibile.'
        : settingsBusy || diagnosticBusy || loading ? 'Attendi il salvataggio in corso.' : '';
    $: updateBlocked = active || starting ? 'C’è già un’operazione in corso.'
        : changes ? 'Salva o annulla prima le modifiche nelle impostazioni.'
        : apiUnavailable || !runner.available ? 'Servizio di aggiornamento non disponibile.'
        : runner.can_update === false ? runner.reason || 'Aggiornamento non disponibile.'
        : info?.mode !== 'production' ? 'Disponibile solo in produzione.'
        : catalog?.latest && !catalog.latest.artifacts_ready ? 'La versione è pubblicata ma non ancora pronta. Riprova più tardi.'
        : settingsBusy || diagnosticBusy || refreshingInfo || checkingReleases ? 'Attendi il completamento dell’operazione.'
        : releaseError ? 'Controlla di nuovo gli aggiornamenti.' : '';
    $: backupBlocked = changes ? 'Salva o annulla prima le modifiche nelle impostazioni.'
        : apiUnavailable || !runner.available ? 'Servizio di backup non disponibile.'
        : runner.can_update === false && runner.reason ? runner.reason : '';

    async function request(path, options = {}) {
        const result = await apiFetch(`${endpoint}${path}`, {method: 'GET', skipForbidden: true, ...options});
        if (result.error) throw Object.assign(new Error(apiErrorMessage(result.response)), {status: result.status});
        return result.response;
    }

    async function loadDiagnostics() {
        diagnosticLoading = true;
        try { diagnostics = await request('/admin/diagnostics'); diagnosticError = ''; }
        catch (e) { diagnosticError = e.message; }
        finally { diagnosticLoading = false; }
    }

    async function runDiagnostics() {
        if (diagnosticBusy || active || apiUnavailable) return;
        diagnosticBusy = true;
        diagnosticError = '';
        try { diagnostics = await request('/admin/diagnostics', {method: 'POST', body: '{}'}); }
        catch (e) { diagnosticError = e.message; }
        finally { diagnosticBusy = false; }
    }

    async function loadBackups(refresh = false) {
        if (backupsLoading) return;
        backupsLoading = true;
        try { backups = await request(`/admin/backups${refresh ? '?refresh=1' : ''}`); backupsError = ''; }
        catch (e) { backupsError = e.message; }
        finally { backupsLoading = false; }
    }

    function refreshAI(value) {
        info = {...info, config: {...info.config, features: {...info.config.features, aiEnabled: value.enabled}}};
        saveRuntimeConfig(info.config);
    }

    async function refreshIntegrations() {
        try { await loadInfo(); saveRuntimeConfig(info.config); }
        catch { error = 'Configurazione salvata. Ricarica la pagina per vedere i valori aggiornati.'; }
        await loadDiagnostics();
    }

    async function restartApplication() {
        if (restartBlocked) return;
        restartOpen = false;
        starting = true;
        error = '';
        restartRequestId = uuidv4();
        restartStartedAt = Date.now();
        restartTimedOut = false;
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 15000);
        try {
            const result = await apiFetch(`${endpoint}/admin/restarts`, {
                method: 'POST', skipForbidden: true, signal: controller.signal,
                body: JSON.stringify({request_id: restartRequestId}),
            });
            if (result.error && result.status >= 400 && result.status < 500) {
                restartRequestId = null;
                error = result.response?.error || 'Riavvio non autorizzato o già in corso.';
            } else if (!result.error) runner = {...runner, active: result.response.operation};
            // A network/5xx failure can follow acceptance. Resolve the same
            // request from the journal; never automatically start another one.
        } catch { /* Expected if the API stops before returning acceptance. */ }
        finally { clearTimeout(timeout); starting = false; }
        if (!disposed) await loadRunner();
    }

    async function checkRestart(status) {
        if (!restartRequestId) return;
        const result = restartResult(status, restartRequestId, restartStartedAt);
        if (result.phase === 'failed') {
            error = `${result.operation.error || 'Riavvio non riuscito.'} ${result.operation.recovery || 'Controlla i servizi sul server prima di riprovare.'}`;
            restartRequestId = null;
            restartTimedOut = false;
        } else if (result.phase === 'ready' && await applicationReady(originalFetch, getApiHost())) {
            if (disposed) return;
            clearInstanceCache();
            window.location.reload();
        } else if (result.phase === 'timeout' || Date.now() - restartStartedAt >= 10 * 60 * 1000) {
            restartTimedOut = true;
        }
    }

    function retryRestartStatus() {
        restartStartedAt = Date.now();
        restartTimedOut = false;
        loadRunner();
    }

    async function loadInfo() {
        refreshingInfo = true;
        try { info = await request('/admin'); }
        finally { refreshingInfo = false; }
    }

    async function loadReleases(refresh = false, page = 1) {
        checkingReleases = true;
        releaseError = '';
        try {
            const result = await request(`/admin/releases?page=${page}${refresh ? '&refresh=1' : ''}`);
            catalog = result;
            releases = page === 1 ? result.history : [...releases, ...result.history];
            return true;
        } catch (e) { releaseError = e.message; return false; }
        finally { checkingReleases = false; }
    }

    async function loadRunner() {
        let status;
        if (!restartRequestId && runner.active?.kind !== 'restart') {
            try {
                status = await request('/admin/updates');
                apiUnavailable = false;
            } catch {
                apiUnavailable = true;
            }
        }
        // The status socket is the same runner: ask it only when the API itself
        // did not answer (updates and restarts take the API down).
        if (!status) {
            const independent = await readStoredStatus(originalFetch);
            if (independent.kind === 'owner') status = independent.status;
            else if (independent.kind === 'denied') {
                // Do not keep displaying private history after access is revoked.
                runner = {available: false, active: null, history: []};
                info = undefined;
                error = 'Accesso al servizio di aggiornamento non autorizzato. Accedi di nuovo quando l’app torna disponibile.';
                restartRequestId = null;
                reconnecting = false;
                return;
            }
        }
        if (!status) status = {available: false};
        completionRefresh.observe(runner, status);
        const finished = runner.active && status.available && !status.active;
        runner = mergeRunnerStatus(runner, status);
        reconnecting = !status.available;
        if (restartRequestId) {
            await checkRestart(status);
            return;
        }
        if (!apiUnavailable && status.available && !runner.active) {
            try {
                if (!info) {
                    await loadInfo();
                    error = '';
                }
                await completionRefresh.run(() => loadInfo(), () => loadReleases(true));
                if (finished) await Promise.all([loadBackups(true), loadDiagnostics()]);
            } catch { apiUnavailable = true; }
        }
    }

    function reviewUpdate() {
        if (!catalog?.latest) return;
        // Generate before displaying confirmation; randomUUID alone is absent
        // on HTTP installations, while uuid also supports getRandomValues.
        requestId = uuidv4();
        reviewing = {...catalog.latest};
        reviewNotes = catalog.pending.map(release => ({...release}));
        startError = '';
        if (tab !== 'updates') navigate('updates');
    }

    async function startUpdate() {
        if (!reviewing || !requestId || starting || updateBlocked) return;
        starting = true;
        startError = '';
        try {
            const result = await request('/admin/updates', {
                method: 'POST',
                body: JSON.stringify({release_id: reviewing.id, tag: reviewing.tag, request_id: requestId}),
            });
            runner = {...runner, active: result.operation};
            reviewing = null;
        } catch (e) { startError = e.message; }
        finally { starting = false; }
    }

    function startOperation(operation) {
        if (operation) runner = {...runner, active: operation};
        loadRunner();
    }

    function startSimulation() {
        clearTimeout(simulationTimer);
        const steps = ['queued', 'checking', 'backup', 'downloading', 'migrating', 'restarting', 'health_check', 'completed'];
        let index = 0;
        function advance() {
            if (disposed) return;
            const stage = simulateFailure && index === 4 ? 'recovery_required' : steps[index];
            if (stage === 'completed' || stage === 'recovery_required') {
                const record = {id: `simulation-${Date.now()}`, stage, status: stage === 'completed' ? 'succeeded' : 'recovery_required', source_version: info?.running_version || 'sviluppo', target_version: 'simulazione', created_at: new Date().toISOString(), updated_at: new Date().toISOString(), simulated: true,
                    error: stage === 'recovery_required' ? 'Simulazione: errore durante l’aggiornamento del database.' : null};
                simulation = null;
                runner = {...runner, history: [record, ...(runner.history || [])]};
                return;
            }
            simulation = {id: 'simulation', stage, target_version: 'simulazione', simulated: true};
            index += 1;
            simulationTimer = setTimeout(advance, 900);
        }
        advance();
    }

    function stopSimulation() {
        clearTimeout(simulationTimer);
        simulation = null;
        runner = {...runner, history: (runner.history || []).filter(item => !item.simulated)};
    }

    onMount(() => {
        applyLocation();
        window.addEventListener('hashchange', applyLocation);
        async function initialize() {
            try { await loadInfo(); }
            catch (e) { error = e.message; }
            finally { loading = false; }
            if (disposed) return;
            await Promise.all([info ? loadDiagnostics() : Promise.resolve(), info ? loadBackups() : Promise.resolve(), loadRunner()]);
            async function poll() {
                if (disposed) return;
                await loadRunner();
                if (!disposed) timer = setTimeout(poll, restartRequestId || runner.active || reconnecting || apiUnavailable ? 4000 : 15000);
            }
            if (!disposed) timer = setTimeout(poll, 4000);
        }
        initialize();
        return () => window.removeEventListener('hashchange', applyLocation);
    });
    onDestroy(() => {
        disposed = true;
        clearTimeout(timer);
        clearTimeout(simulationTimer);
    });
</script>

<div class="si-page card card-custom">
    <div class="card-body instance-body">
        <header class="page-head">
            <div class="page-title">
                <div class="page-name">
                    <h1>Istanza</h1>
                    {#if info?.config?.oem?.name}<p class="si-muted mb-0 instance-name">{info.config.oem.name}</p>{/if}
                </div>
                {#if info && info.mode !== 'production'}<span class="si-pill warn">Sviluppo</span>{/if}
            </div>
            {#if info}
                <InstanceMenu label="Azioni istanza" items={[{label: 'Riavvia applicazione', hint: restartBlocked || 'Tutti i servizi, circa un minuto.', disabled: !!restartBlocked, onSelect: () => restartOpen = true}]} />
            {/if}
        </header>

        <InstanceBanner {runner} {simulation} {reconnecting} {apiUnavailable} restarting={!!restartRequestId} {restartTimedOut}
            onRetryRestart={retryRestartStatus} {error} onDismissError={() => error = ''} />

        {#if loading}
            <div class="si-stack mt-6">
                <span class="si-skeleton" style="height: 2.5rem; width: 24rem; max-width: 100%"></span>
                <div class="skeleton-tiles">{#each Array(3) as _}<span class="si-skeleton" style="height: 6rem"></span>{/each}</div>
            </div>
        {/if}

        {#if info}
            <div class="tabs" role="tablist" aria-label="Sezioni istanza">
                {#each tabs as [id, title, icon]}
                    <button type="button" role="tab" id={`instance-tab-${id}`} aria-controls={`instance-panel-${id}`} aria-selected={tab === id} tabindex={tab === id ? 0 : -1}
                        on:click={() => navigate(id)} on:keydown={event => onTabKeydown(event, tabs, tab, navigate)}>
                        <svelte:component this={icon} size={20} weight={tab === id ? 'duotone' : 'regular'} aria-hidden="true" />{title}{#if id === 'settings' && changes}<span class="dirty-dot" aria-label="modifiche non salvate"></span>{/if}
                    </button>
                {/each}
            </div>

            <div id="instance-panel-overview" role="tabpanel" aria-labelledby="instance-tab-overview" hidden={tab !== 'overview'}>
                <InstanceOverview {info} {diagnostics} {diagnosticLoading} {diagnosticBusy} {diagnosticError} {catalog} {backups} {navigate}
                    runBlockedReason={active ? 'C’è un’operazione in corso.' : apiUnavailable ? 'App non raggiungibile.' : ''}
                    onRunDiagnostics={runDiagnostics} onReviewUpdate={updateBlocked ? null : reviewUpdate} bind:checksOpen />
            </div>

            <div id="instance-panel-backups" role="tabpanel" aria-labelledby="instance-tab-backups" hidden={tab !== 'backups'}>
                {#if tab === 'backups'}
                    <InstanceBackups {request} overview={backups} loading={backupsLoading} error={backupsError} {active} blockedReason={backupBlocked}
                        checks={(diagnostics?.checks || []).filter(check => ['backups', 'updater'].includes(check.id))}
                        onReload={refresh => loadBackups(refresh)} onOperation={startOperation} />
                {/if}
            </div>

            <div id="instance-panel-updates" role="tabpanel" aria-labelledby="instance-tab-updates" hidden={tab !== 'updates'}>
                <InstanceUpdates {info} {catalog} {releases} {releaseError} {checkingReleases} history={runner.history || []}
                    busy={!!active || starting} updateBlockedReason={updateBlocked} checkBlocked={apiUnavailable || !!active}
                    {reviewing} {reviewNotes} {starting} {startError} bind:simulateFailure simulationRunning={!!simulation}
                    onCheck={() => loadReleases(true)} onLoadMore={() => loadReleases(false, catalog.next_page)}
                    onReview={reviewUpdate} onStart={startUpdate} onCancelReview={() => reviewing = null}
                    onSimulate={startSimulation} onStopSimulation={stopSimulation} />
            </div>

            <div id="instance-panel-settings" role="tabpanel" aria-labelledby="instance-tab-settings" hidden={tab !== 'settings'}>
                <div class="settings">
                    <div class="settings-nav" role="tablist" aria-label="Impostazioni" aria-orientation="vertical">
                        {#each settingsSections as [id, title, icon]}
                            <button type="button" role="tab" id={`instance-settings-${id}`} aria-controls={`instance-settings-panel-${id}`} aria-selected={settingsSection === id} tabindex={settingsSection === id ? 0 : -1}
                                on:click={() => navigate(id)} on:keydown={event => onTabKeydown(event, settingsSections, settingsSection, navigate)}>
                                <svelte:component this={icon} size={22} weight="duotone" aria-hidden="true" /><span>{title}</span>{#if settingsDirty[id]}<span class="dirty-dot" aria-label="modifiche non salvate"></span>{/if}
                            </button>
                        {/each}
                    </div>
                    <div class="settings-body">
                        <!-- All forms stay mounted so drafts survive switching sections. -->
                        <div id="instance-settings-panel-branding" role="tabpanel" aria-labelledby="instance-settings-branding" hidden={settingsSection !== 'branding'}>
                            <InstanceBranding {request} oem={info.config.oem} bind:changes={logoChanges} bind:busy={logoBusy} disabled={locked} onSaved={oem => info = {...info, config: {...info.config, oem: {...info.config.oem, ...oem}}}} />
                        </div>
                        <div id="instance-settings-panel-email" role="tabpanel" aria-labelledby="instance-settings-email" hidden={settingsSection !== 'email'}>
                            <InstanceEmail {request} bind:changes={emailChanges} bind:busy={emailBusy} disabled={locked} onSaved={loadDiagnostics} />
                        </div>
                        <div id="instance-settings-panel-integrations" role="tabpanel" aria-labelledby="instance-settings-integrations" hidden={settingsSection !== 'integrations'}>
                            <InstanceIntegrations {request} checks={diagnostics?.integrations || []} bind:changes={integrationChanges} bind:busy={integrationBusy} disabled={locked} onSaved={refreshIntegrations} />
                        </div>
                        <div id="instance-settings-panel-ai" role="tabpanel" aria-labelledby="instance-settings-ai" hidden={settingsSection !== 'ai'}>
                            <InstanceIntegration provider="ai" title="Bot AI" {request} bind:changes={aiChanges} bind:busy={aiBusy} disabled={locked} onSaved={refreshAI} />
                        </div>
                        <div id="instance-settings-panel-pairing" role="tabpanel" aria-labelledby="instance-settings-pairing" hidden={settingsSection !== 'pairing'}>
                            {#if settingsSection === 'pairing'}<BakneyPairing {request} bind:busy={pairingBusy} disabled={locked} />{/if}
                        </div>
                        <div id="instance-settings-panel-api" role="tabpanel" aria-labelledby="instance-settings-api" hidden={settingsSection !== 'api'}>
                            <InstanceApiVersion {request} config={info.config} bind:busy={apiBusy} disabled={locked} onSaved={config => info = {...info, config}} />
                        </div>
                    </div>
                </div>
            </div>
        {/if}
    </div>
</div>

<InstanceDialog bind:open={restartOpen} title="Riavviare l’applicazione?">
    <p>L’app non sarà disponibile per circa un minuto. Dati e sessione restano; le modifiche non salvate si perdono.</p>
    <svelte:fragment slot="footer">
        <button type="button" class="btn btn-light" data-autofocus on:click={() => restartOpen = false}>Annulla</button>
        <button type="button" class="btn btn-primary si-btn" disabled={!!restartBlocked} on:click={restartApplication}><Power size={18} weight="duotone" />Riavvia</button>
    </svelte:fragment>
</InstanceDialog>

<style>
    .instance-body { display: flex; flex-direction: column; gap: 1.75rem; min-width: 0; }
    [hidden] { display: none !important; }
    .page-head { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
    .page-title { display: flex; flex-wrap: wrap; align-items: center; gap: .75rem; min-width: 0; }
    .page-title h1 { font-size: 2rem; font-weight: 700; margin: 0; }
    .page-name { min-width: 0; }
    .instance-name { overflow-wrap: anywhere; }
    .skeleton-tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 14rem), 1fr)); gap: 1rem; }

    .tabs { display: flex; gap: 2rem; border-bottom: 1px solid var(--border-color); overflow-x: auto; scrollbar-width: none; contain: inline-size; }
    .tabs::-webkit-scrollbar { display: none; }
    /* The highlight is the tab's own bottom border, laid over the divider and as wide as the label. */
    .tabs [role='tab'] { display: inline-flex; align-items: center; gap: .5rem; flex-shrink: 0; margin: 0 0 -1px; padding: .85rem 0; border: 0; border-bottom: 2px solid transparent; border-radius: 0; background: none; color: var(--text-secondary); font-size: 1.15rem; font-weight: 600; white-space: nowrap; }
    .tabs [role='tab']:hover { color: var(--text-primary); }
    .tabs [role='tab'][aria-selected='true'] { color: var(--primary-text, var(--primary)); border-bottom-color: var(--primary); }
    .tabs [role='tab']:focus-visible, .settings-nav [role='tab']:focus-visible { outline: 2px solid var(--primary); outline-offset: -2px; border-radius: .4rem; }
    .dirty-dot { display: inline-block; width: .5rem; height: .5rem; margin-left: .1rem; border-radius: 50%; background: var(--si-warning); }

    .settings { display: grid; grid-template-columns: 15rem minmax(0, 1fr); gap: 2rem; align-items: start; }
    .settings-nav { display: flex; flex-direction: column; gap: .35rem; position: sticky; top: 1rem; }
    .settings-nav [role='tab'] { display: flex; align-items: center; gap: .75rem; padding: .85rem 1rem; border: 0; border-radius: .65rem; background: none; color: var(--text-secondary); font-size: 1.15rem; font-weight: 600; text-align: left; }
    .settings-nav [role='tab'] :global(svg) { flex-shrink: 0; }
    .settings-nav [role='tab']:hover { background: var(--bg-hover); color: var(--text-primary); }
    .settings-nav [role='tab'][aria-selected='true'] { background: var(--light-primary); color: var(--primary-text, var(--primary)); }
    .settings-body { min-width: 0; }

    @media (max-width: 767px) {
        .settings { grid-template-columns: minmax(0, 1fr); gap: 1rem; }
        .settings-nav { position: static; flex-direction: row; overflow-x: auto; scrollbar-width: none; contain: inline-size; }
        .settings-nav::-webkit-scrollbar { display: none; }
        .settings-nav [role='tab'] { flex-shrink: 0; white-space: nowrap; }
        .page-title h1 { font-size: 1.75rem; }
        .tabs { gap: 1.25rem; }
        .tabs [role='tab'] { padding: .75rem 0; font-size: 1.08rem; }
        .settings-nav [role='tab'] { padding: .65rem .85rem; }
    }
</style>
