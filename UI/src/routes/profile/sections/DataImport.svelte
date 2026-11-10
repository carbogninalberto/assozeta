<script>
    // Import of a Bakney ZIP export, step by step: file → summary → confirm →
    // progress → result. Same endpoints and safety steps as before: the server
    // validates the file, takes a safety copy, then replaces the data.
    import {onMount, onDestroy} from 'svelte';
    import {UploadSimple, FileZip, X, CheckCircle, WarningCircle, Info, ArrowCounterClockwise, DownloadSimple} from 'phosphor-svelte';
    import RestoreProgressCard from './RestoreProgressCard.svelte';
    import NotificationService from 'utils/NotificationService.js';
    import {mergeRestoreProgress} from 'utils/restoreProgress.js';
    import Switch from '../../../components/inputs/Switch.svelte';
    import {apiFetch} from 'utils/ApiMiddleware.js';
    import {getApiHost, clearInstanceCache} from 'store/instanceStore.js';
    import {userData} from 'store/stores.js';
    import {formatDate, formatSize} from './instanceFormat.js';

    // True while a request that must not be interrupted is running.
    export let busy = false;

    const endpoint = `${getApiHost()}/instance/admin/data-restore`;
    const labels = {queued: 'In attesa del worker', backup: 'Copia di sicurezza in corso',
        files: 'Preparazione degli allegati', restoring: 'Ripristino dei dati', completed: 'Ripristino completato',
        failed: 'Ripristino non riuscito', validated: 'Backup verificato', cancelled: 'Importazione annullata', expired: 'Verifica scaduta'};
    const steps = ['File', 'Riepilogo', 'Conferma', 'Ripristino'];
    const modelLabels = {MedicalCertificate: 'Certificati medici', Invoice: 'Fatture',
        SubscriptionTransfer: 'Trasferimenti iscrizioni', Associate: 'Anagrafiche', User: 'Utenti'};
    const fieldLabels = {user_id: 'Utente', selected_tutor_id: 'Tutore selezionato', recipient_id: 'Destinatario',
        family_id: 'Famiglia', document_id: 'Documento'};

    let status;
    let liveProgress;
    let liveSource = false;
    let progressNow = Date.now();
    let selected;
    let relationReport;
    let reportOperationId;
    let reportLoading = false;
    let file;
    let fileInput;
    let dragOver = false;
    let downloading = '';
    let loading = true;
    let error = '';
    let connectionError = '';
    let confirmation = '';
    let allowMissing = false;
    let confirming = false;
    let timer;
    let disposed = false;
    let backups = [];
    let backupCursor;
    let backupsInitialized = false;
    // Safety copies stay short until asked: the newest three first.
    let showAllCopies = false;

    $: reportGroups = Object.values((relationReport?.missing_relations || []).reduce((groups, row) => {
        const key = `${row.model}.${row.field}`;
        if (!groups[key]) groups[key] = {model: row.model, field: row.field, rows: []};
        groups[key].rows.push(row);
        return groups;
    }, {}));
    $: tutorConflicts = (relationReport?.tutor_reassignments || []).filter(row => 'original_tax_code' in row);
    $: active = status?.active;
    $: uploadLimit = formatSize(status?.max_upload_bytes || 5 * 1024 ** 3);
    $: review = selected?.state === 'review';
    $: if (!review) confirming = false;
    $: ready = review && confirmation === 'RIPRISTINA' && (!selected.preview.missing_media || allowMissing);
    $: terminal = ['failed', 'completed', 'cancelled', 'expired'].includes(selected?.state);
    $: step = active ? 'progress' : review ? (confirming ? 'confirm' : 'review') : terminal ? 'result' : 'file';
    $: stepIndex = {file: 0, review: 1, confirm: 2, progress: 3, result: 3}[step];
    $: preview = selected?.preview || {};
    $: hasReport = !!(preview.missing_relations || preview.reassigned_tutors || preview.tutor_tax_code_conflicts);
    $: notes = [
        preview.skipped_records?.BillingPlan || preview.skipped_records?.PreviewAndCustomFeatures
            ? ['info', `Non importati: ${preview.skipped_records.BillingPlan || 0} piani e ${preview.skipped_records.PreviewAndCustomFeatures || 0} abilitazioni dei vecchi abbonamenti software.`] : null,
        preview.missing_relations ? ['warn', `${preview.missing_relations} collegamenti opzionali puntano a dati non inclusi e restano vuoti.`] : null,
        preview.reassigned_tutors ? ['info', `${preview.reassigned_tutors} tutori assegnati a questa associazione.`] : null,
        preview.tutor_tax_code_conflicts ? ['warn', preview.tutor_tax_code_conflicts === 1
            ? '1 tutore ha un codice fiscale già in uso: il campo resta vuoto.'
            : `${preview.tutor_tax_code_conflicts} tutori hanno un codice fiscale già in uso: il campo resta vuoto.`] : null,
        preview.legacy_invoices ? ['info', `${preview.legacy_invoices} fatture del servizio Bakney vanno nell’archivio documenti.`] : null,
    ].filter(Boolean);

    function mergeBackups(incoming) {
        const combined = [...incoming, ...backups];
        backups = combined.filter((op, index) => combined.findIndex(other => other.id === op.id) === index);
    }

    async function request(url, options = {}) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), options.body instanceof FormData ? 3600000 : 30000);
        try {
            const result = await apiFetch(url, {method: 'GET', skipForbidden: true, ...options, signal: controller.signal});
            if (result.error) throw new Error(result.response?.error || 'Impossibile contattare il server.');
            return result.response;
        } catch (e) {
            if (e.name === 'AbortError') throw new Error('Il server non ha risposto in tempo.');
            throw e;
        } finally { clearTimeout(timeout); }
    }

    async function refresh() {
        clearTimeout(timer);
        try {
            const next = await request(endpoint);
            if (disposed) return;
            status = next;
            const overlapsKnownBackups = (next.backups || []).some(op => backups.some(known => known.id === op.id));
            // After a long disconnection a whole new page may have appeared.
            // Start from its boundary so that intervening backups stay reachable.
            if (!backupsInitialized || (!overlapsKnownBackups && next.backups_next)) {
                backupCursor = next.backups_next;
                backupsInitialized = true;
            }
            mergeBackups(next.backups || []);
            const visible = next.history.filter(op => !op.preview.dismissed);
            selected = next.active || visible.find(op => op.id === selected?.id) || visible[0];
            liveProgress = mergeRestoreProgress(liveProgress, selected?.progress, selected);
            if (liveProgress?.operation_id !== selected?.id) { liveProgress = undefined; liveSource = false; }
            progressNow = Date.now();
            connectionError = '';
        } catch (e) { if (!disposed) connectionError = e.message; }
        finally {
            loading = false;
            if (!disposed) timer = setTimeout(refresh, 4000);
        }
    }

    onMount(() => {
        refresh();
        const unsubscribe = NotificationService.subscribeRestoreProgress(event => {
            const merged = mergeRestoreProgress(liveProgress, event, selected);
            if (merged !== liveProgress) {
                liveProgress = merged;
                liveSource = true;
                progressNow = Date.now();
                if (['completed', 'failed'].includes(event.phase)) refresh();
            }
        });
        return unsubscribe;
    });
    onDestroy(() => { disposed = true; clearTimeout(timer); });

    function removeFile() {
        if (busy) return;
        file = undefined;
        error = '';
        if (fileInput) fileInput.value = '';
    }

    function selectFile(files) {
        if (busy || !files?.length) return;
        const chosen = files[0];
        const count = files.length;
        removeFile();
        if (count !== 1 || !chosen.name.toLowerCase().endsWith('.zip')) error = 'Seleziona un solo file ZIP.';
        else if (chosen.size > status.max_upload_bytes) error = `Il file supera il limite di ${uploadLimit}.`;
        else file = chosen;
    }

    function dropFile(event) {
        event.preventDefault();
        dragOver = false;
        selectFile(event.dataTransfer.files);
    }

    async function upload() {
        if (!file || busy || active) return;
        if (file.size > status.max_upload_bytes) { error = `Il file supera il limite di ${uploadLimit}.`; return; }
        busy = true;
        error = '';
        try {
            const body = new FormData();
            body.append('file', file);
            selected = await request(endpoint, {method: 'POST', body});
            confirmation = '';
            allowMissing = false;
            file = undefined;
            await refresh();
        } catch (e) { error = e.message; }
        finally { busy = false; }
    }

    async function action(name) {
        if (busy || !selected) return;
        busy = true;
        error = '';
        try {
            selected = await request(`${endpoint}/${selected.id}/${name}`, {
                method: 'POST', body: JSON.stringify({confirmation, allow_missing_media: allowMissing}),
            });
            await refresh();
        } catch (e) { error = e.message; }
        finally { busy = false; }
    }

    async function toggleReport(op) {
        if (reportOperationId === op.id && relationReport) {
            relationReport = undefined;
            return;
        }
        reportLoading = true;
        error = '';
        try {
            relationReport = await request(`${endpoint}/${op.id}/missing-relations`);
            reportOperationId = op.id;
        } catch (e) { error = e.message; }
        finally { reportLoading = false; }
    }

    function downloadBackup(op) {
        error = '';
        if (!op.download_token) { error = 'Link di download scaduto. Riprova tra qualche secondo.'; return; }
        const link = document.createElement('a');
        link.href = `${endpoint}/${op.id}/download?download_token=${encodeURIComponent(op.download_token)}`;
        link.download = `backup-prima-del-ripristino-${op.id}.zip`;
        link.rel = 'noopener noreferrer';
        document.body.appendChild(link);
        link.click();
        link.remove();
    }

    async function downloadReport(op) {
        if (busy) return;
        busy = true;
        error = '';
        downloading = op.id;
        try {
            const response = await window.fetch(`${endpoint}/${op.id}/missing-relations`);
            if (!response.ok) throw new Error('Download non riuscito.');
            const url = URL.createObjectURL(await response.blob());
            const link = document.createElement('a');
            link.href = url;
            link.download = `rapporto-backup-${op.id}.json`;
            link.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (e) { error = e.message; }
        finally { busy = false; downloading = ''; }
    }

    async function loadOlderBackups() {
        busy = true;
        error = '';
        try {
            const page = await request(`${endpoint}/backups?before=${encodeURIComponent(backupCursor)}`);
            const known = new Set(backups.map(op => op.id));
            backups = [...backups, ...page.backups.filter(op => !known.has(op.id))];
            backupCursor = page.backups_next;
        } catch (e) { error = e.message; }
        finally { busy = false; }
    }

    function showMoreCopies() {
        showAllCopies = true;
        if (backupCursor) loadOlderBackups();
    }

    async function reload() {
        if (busy) return;
        busy = true;
        error = '';
        try {
            const profile = await request(`${getApiHost()}/profile/info`);
            if (!profile.user_data?.user_id) throw new Error('Impossibile aggiornare il profilo.');
            userData.set(profile.user_data);
            localStorage.setItem('userData', JSON.stringify(profile.user_data));
            // Retain authentication while replacing cached association data.
            localStorage.removeItem('selectedGroup');
            clearInstanceCache();
            window.location.reload();
        } catch (e) { error = e.message; }
        finally { busy = false; }
    }
</script>

<div class="data-import">
    {#if connectionError}
        <p class="text-danger si-small" role="alert">{connectionError}
            <button type="button" class="si-link ml-1" on:click={refresh}>Riprova</button></p>
    {/if}

    {#if loading}
        <div class="si-stack" role="status" aria-label="Caricamento">
            <span class="si-skeleton" style="height: 1.5rem"></span>
            <span class="si-skeleton" style="height: 8rem"></span>
        </div>
    {:else if !status?.available}
        <p class="si-muted" role="status">{status?.reason || 'Importazione non disponibile.'}</p>
    {:else}
        <ol class="steps" aria-label="Passaggi">
            {#each steps as label, i}
                <li class:done={i < stepIndex || (step === 'result' && selected?.state === 'completed')} class:current={i === stepIndex}
                    aria-current={i === stepIndex ? 'step' : undefined}><span class="num" aria-hidden="true">{i + 1}</span>{label}</li>
            {/each}
        </ol>

        {#if step === 'file'}
            <h3 class="si-subtitle">Scegli il file</h3>
            <p class="si-muted si-small" id="backup-upload-help">Solo export ZIP di Bakney, non i backup del server. Massimo {uploadLimit}.</p>
            <input id="bakney-backup" type="file" accept=".zip,application/zip" class="d-none" bind:this={fileInput}
                disabled={busy} on:change={event => selectFile(event.target.files)} />
            {#if file}
                <div class="picked">
                    <span class="si-icon" aria-hidden="true"><FileZip size={24} weight="duotone" /></span>
                    <div class="si-row-main"><strong>{file.name}</strong><p class="si-muted si-small">{formatSize(file.size)}</p></div>
                    <button type="button" class="icon-button" disabled={busy} aria-label="Rimuovi backup selezionato" on:click={removeFile}><X size={18} /></button>
                </div>
            {:else}
                <button type="button" class="drop" class:over={dragOver} disabled={busy} aria-label="Seleziona backup ZIP" aria-describedby="backup-upload-help"
                    on:click={() => fileInput?.click()} on:dragover|preventDefault={() => { if (!busy) dragOver = true; }}
                    on:dragleave={() => dragOver = false} on:drop={dropFile}>
                    <UploadSimple size={28} />
                    <span><strong>Trascina qui il file ZIP</strong> o selezionalo</span>
                </button>
            {/if}
            {#if error}<p class="text-danger si-small mt-3 mb-0" role="alert">{error}</p>{/if}
            <div class="step-actions">
                <button type="button" class="btn btn-primary si-btn" disabled={busy || !file} on:click={upload}><UploadSimple size={18} weight="duotone" />{busy ? 'Verifica in corso…' : 'Carica e verifica'}</button>
            </div>
            {#if busy}<p class="si-hint mb-0" role="status">Tieni aperta questa finestra fino alla fine della verifica.</p>{/if}

        {:else if step === 'review'}
            <h3 class="si-subtitle">{labels[selected.stage] || 'Riepilogo'}</h3>
            <dl class="summary">
                <div><dt>Associazione</dt><dd>{preview.association}</dd></div>
                <div><dt>Data export</dt><dd>{preview.export_date ? formatDate(preview.export_date) : 'Non indicata'}</dd></div>
                <div><dt>Contenuto</dt><dd>{preview.records} record · {preview.files} allegati</dd></div>
            </dl>
            {#if notes.length}
                <ul class="si-rows notes" aria-label="Da sapere">
                    {#each notes as [tone, text]}
                        <li><span class="si-icon {tone === 'warn' ? 'warn' : 'neutral'}" aria-hidden="true"><svelte:component this={tone === 'warn' ? WarningCircle : Info} size={18} weight="fill" /></span><span class="si-row-main si-small">{text}</span></li>
                    {/each}
                </ul>
            {/if}
            {#if hasReport}
                <div class="report-actions">
                    <button type="button" class="btn btn-light btn-sm" disabled={busy || reportLoading} on:click={() => toggleReport(selected)}>
                        {reportLoading ? 'Caricamento…' : reportOperationId === selected.id && relationReport ? 'Nascondi rapporto' : 'Mostra rapporto'}</button>
                    <button type="button" class="btn btn-light btn-sm si-btn" aria-label="Scarica rapporto del backup" disabled={busy} on:click={() => downloadReport(selected)}><DownloadSimple size={16} weight="duotone" />
                        {downloading === selected.id ? 'Download…' : 'Scarica rapporto'}</button>
                </div>
                {#if reportOperationId === selected.id && relationReport}
                    <!-- Scrollable region: focusable so keyboard users can scroll it. -->
                    <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
                    <div class="report" role="region" aria-label="Rapporto del backup" tabindex="0">
                        {#each reportGroups as group}
                            <h4>{modelLabels[group.model] || group.model} → {fieldLabels[group.field] || group.field} <span class="si-muted">· {group.rows.length}</span></h4>
                            <table>
                                <thead><tr><th scope="col">Record conservato</th><th scope="col">Collegamento assente</th></tr></thead>
                                <tbody>{#each group.rows.slice(0, 100) as row}<tr><td><code>{row.record_id}</code></td><td><code>{row.target_id}</code></td></tr>{/each}</tbody>
                            </table>
                            {#if group.rows.length > 100}<p class="si-hint">Primi 100: il file scaricato li contiene tutti.</p>{/if}
                        {/each}
                        {#if tutorConflicts.length}
                            <h4>Codici fiscali da verificare</h4>
                            <ul class="si-rows">
                                {#each tutorConflicts.slice(0, 100) as tutor}<li><span class="si-row-main">{tutor.name || 'Tutore'}</span><code>{tutor.original_tax_code}</code></li>{/each}
                            </ul>
                            {#if tutorConflicts.length > 100}<p class="si-hint">Primi 100: il file scaricato li contiene tutti.</p>{/if}
                        {/if}
                    </div>
                {/if}
            {/if}
            {#if error}<p class="text-danger si-small mt-3 mb-0" role="alert">{error}</p>{/if}
            <div class="step-actions">
                <button type="button" class="btn btn-light" disabled={busy} on:click={() => action('cancel')}>Annulla importazione</button>
                <button type="button" class="btn btn-primary" disabled={busy} on:click={() => { confirming = true; error = ''; }}>Continua</button>
            </div>

        {:else if step === 'confirm'}
            <h3 class="si-subtitle">Sostituire i dati attuali?</h3>
            <p class="si-small">Prima viene creata una copia di sicurezza scaricabile. Le copie dei ripristini precedenti vengono eliminate.</p>
            {#if preview.missing_media}
                <Switch id="restore-missing-media" bind:checked={allowMissing} disabled={busy}
                    label={`Accetto che ${preview.missing_media} allegati o firme non inclusi non siano recuperati`} />
            {/if}
            <label class="si-field" for="restore-confirmation">Scrivi RIPRISTINA per confermare
                <input id="restore-confirmation" class="form-control" autocomplete="off" spellcheck="false" bind:value={confirmation} disabled={busy} />
            </label>
            {#if error}<p class="text-danger si-small mt-3 mb-0" role="alert">{error}</p>{/if}
            <div class="step-actions">
                <button type="button" class="btn btn-light" disabled={busy} on:click={() => confirming = false}>Indietro</button>
                <button type="button" class="btn btn-danger si-btn" disabled={busy || !ready} on:click={() => action('start')}><ArrowCounterClockwise size={18} weight="duotone" />Sostituisci i dati</button>
            </div>

        {:else if step === 'progress'}
            <RestoreProgressCard operation={selected} progress={liveProgress} now={progressNow} live={liveSource} />
            <p class="si-hint mt-3 mb-0">Puoi chiudere questa finestra: il ripristino continua.
                Non avanza? <button type="button" class="si-link" disabled={busy} on:click={() => action('resume')}>Riprendi</button></p>
            {#if error}<p class="text-danger si-small mt-3 mb-0" role="alert">{error}</p>{/if}

        {:else if step === 'result'}
            <div class="result {selected.state}">
                <span class="si-icon {selected.state === 'completed' ? 'ok' : selected.state === 'failed' ? 'bad' : 'neutral'}" aria-hidden="true">
                    <svelte:component this={selected.state === 'completed' ? CheckCircle : WarningCircle} size={28} weight="fill" /></span>
                <div class="si-row-main">
                    <h3 class="si-subtitle">{labels[selected.stage] || labels[selected.state]}</h3>
                    {#if selected.state === 'completed'}
                        <p class="si-small">Accesso e impostazioni dell’istanza sono stati conservati.</p>
                    {:else if selected.state === 'failed'}
                        <p class="text-danger si-small" role="alert">{selected.error} Nessun dato è stato sostituito.</p>
                    {:else}
                        <p class="si-small si-muted">Nessun dato è stato sostituito.</p>
                    {/if}
                </div>
            </div>
            {#if error}<p class="text-danger si-small mt-3 mb-0" role="alert">{error}</p>{/if}
            <div class="step-actions">
                {#if selected.state === 'completed'}
                    <button type="button" class="btn btn-light" aria-label="Chiudi esito ripristino" disabled={busy} on:click={() => action('dismiss')}>Chiudi</button>
                    <button type="button" class="btn btn-primary" disabled={busy} on:click={reload}>Ricarica l’applicazione</button>
                {:else}
                    <button type="button" class="btn btn-primary" disabled={busy} on:click={() => action('dismiss')}>Riprova</button>
                {/if}
            </div>
        {/if}

        {#if backups.length}
            <section class="copies" aria-labelledby="data-import-copies">
                <h3 id="data-import-copies" class="si-subtitle">Copie di sicurezza</h3>
                <ul class="si-rows">
                    {#each showAllCopies ? backups : backups.slice(0, 3) as op (op.id)}
                        <li>
                            <div class="si-row-main"><strong class="si-small">{formatDate(op.created_at)}</strong><p class="si-muted si-small">Dati prima del ripristino</p></div>
                            {#if op.preview.missing_relations || op.preview.reassigned_tutors || op.preview.tutor_tax_code_conflicts}
                                <button type="button" class="btn btn-light btn-sm si-btn" aria-label="Scarica rapporto del backup" disabled={busy} on:click={() => downloadReport(op)}><DownloadSimple size={16} weight="duotone" />Rapporto</button>
                            {/if}
                            <button type="button" class="btn btn-light btn-sm si-btn" aria-label="Scarica backup di sicurezza" disabled={busy} on:click={() => downloadBackup(op)}><DownloadSimple size={16} weight="duotone" />Scarica</button>
                        </li>
                    {/each}
                </ul>
                {#if backupCursor || (!showAllCopies && backups.length > 3)}
                    <button type="button" class="si-link si-small" disabled={busy} on:click={showMoreCopies}>Mostra altri</button>
                {/if}
            </section>
        {/if}
    {/if}
</div>

<style>
    .data-import { display: grid; gap: .75rem; min-width: 0; }
    .data-import h3, .data-import p { margin: 0; }
    .steps { display: flex; flex-wrap: wrap; gap: .25rem .875rem; margin: 0 0 .5rem; padding: 0; list-style: none; font-size: .925rem; color: var(--text-muted); }
    .steps li { display: inline-flex; align-items: center; gap: .35rem; }
    .num { display: inline-grid; place-items: center; width: 1.25rem; height: 1.25rem; border-radius: 50%; border: 1px solid currentColor; font-size: .925rem; font-weight: 600; }
    .steps .done { color: var(--text-secondary); }
    .steps .done .num { background: var(--light-success); border-color: transparent; color: #0f6b4f; }
    .steps .current { color: var(--primary-text, var(--primary)); font-weight: 600; }
    .steps .current .num { background: var(--primary); border-color: var(--primary); color: var(--white, #fff); }
    .drop { display: flex; flex-direction: column; align-items: center; gap: .5rem; width: 100%; padding: 1.75rem 1rem; border: 1.5px dashed var(--border-color-dark, var(--border-color)); border-radius: .75rem; background: none; color: var(--text-secondary); text-align: center; }
    .drop :global(svg) { color: var(--primary); }
    .drop strong { color: var(--text-primary); font-weight: 600; }
    .drop:hover:not(:disabled), .drop.over { border-color: var(--primary); background: var(--bg-hover); }
    .drop:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
    .picked { display: flex; align-items: center; gap: .75rem; padding: .75rem .875rem; border: 1px solid var(--border-color); border-radius: .625rem; }
    .picked .si-icon { color: var(--primary); margin: 0; }
    .icon-button { display: inline-flex; padding: .4rem; border: 0; border-radius: .5rem; background: none; color: var(--text-secondary); }
    .icon-button:hover:not(:disabled) { background: var(--bg-hover); color: var(--text-primary); }
    .step-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: .5rem; margin-top: .25rem; }
    .summary { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .5rem; margin: 0; }
    .summary > div { padding: .75rem; border-radius: .5rem; background: var(--bg-hover); }
    .summary dt { font-size: .925rem; font-weight: 400; color: var(--text-secondary); }
    .summary dd { margin: .2rem 0 0; font-weight: 600; }
    .notes > li { padding: .5rem 0; }
    .report-actions { display: flex; flex-wrap: wrap; gap: .5rem; }
    .report { max-height: 22rem; overflow: auto; padding: .75rem; border: 1px solid var(--border-color); border-radius: .625rem; }
    .report:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
    .report h4 { font-size: 1rem; font-weight: 600; margin: .5rem 0; }
    .report h4:first-child { margin-top: 0; }
    .report table { width: 100%; table-layout: fixed; border-collapse: collapse; font-size: .925rem; margin-bottom: .5rem; }
    .report th { text-align: left; font-weight: 600; color: var(--text-secondary); padding: .25rem .5rem .25rem 0; border-bottom: 1px solid var(--border-color); }
    .report td { padding: .25rem .5rem .25rem 0; border-bottom: 1px solid var(--border-color); }
    .report code { color: inherit; white-space: normal; overflow-wrap: anywhere; }
    .report .si-rows > li { padding: .4rem 0; font-size: 1rem; }
    .result { display: flex; gap: .75rem; padding: 1rem; border-radius: .625rem; background: var(--bg-hover); }
    .result.completed { background: var(--light-success); }
    .result .si-icon { margin: 0; }
    .result p { margin-top: .25rem !important; }
    .copies { display: grid; gap: .25rem; margin-top: 1rem; padding-top: 1rem; border-top: 1px solid var(--border-color); }
    .copies .si-rows > li { align-items: center; padding: .5rem 0; }
    @media (max-width: 575px) {
        .summary { grid-template-columns: 1fr; }
        .step-actions { flex-direction: column-reverse; }
        .step-actions :global(.btn) { width: 100%; }
    }
</style>
