<script>
    import {onMount, onDestroy} from 'svelte';
    import RestoreDownloadButton from './RestoreDownloadButton.svelte';
    import RestoreProgressCard from './RestoreProgressCard.svelte';
    import NotificationService from 'utils/NotificationService.js';
    import {mergeRestoreProgress} from 'utils/restoreProgress.js';
    import InstanceAlert from './InstanceAlert.svelte';
    import InstanceAccordion from './InstanceAccordion.svelte';
    import Switch from '../../../components/inputs/Switch.svelte';
    import {apiFetch} from 'utils/ApiMiddleware.js';
    import {getApiHost, clearInstanceCache} from 'store/instanceStore.js';
    import {userData} from 'store/stores.js';
    import {UploadCloud, FileArchive, X, ShieldCheck, CheckCircle2} from 'lucide-svelte';

    const endpoint = `${getApiHost()}/instance/admin/data-restore`;
    const labels = {queued: 'In attesa del worker', backup: 'Creazione e verifica del backup di sicurezza',
        files: 'Preparazione degli allegati', restoring: 'Ripristino dei dati', completed: 'Ripristino completato',
        failed: 'Ripristino non riuscito', validated: 'Backup verificato', cancelled: 'Annullato', expired: 'Validazione scaduta'};
    let status;
    let liveProgress;
    let liveSource = false;
    let progressNow = Date.now();
    let selected;
    let relationReport;
    let reportOperationId;
    let reportLoading = false;
    const modelLabels = {MedicalCertificate: 'Certificati medici', Invoice: 'Fatture',
        SubscriptionTransfer: 'Trasferimenti iscrizioni', Associate: 'Anagrafiche', User: 'Utenti'};
    const fieldLabels = {user_id: 'Utente', selected_tutor_id: 'Tutore selezionato', recipient_id: 'Destinatario',
        family_id: 'Famiglia', document_id: 'Documento'};
    $: reportGroups = Object.values((relationReport?.missing_relations || []).reduce((groups, row) => {
        const key = `${row.model}.${row.field}`;
        if (!groups[key]) groups[key] = {model: row.model, field: row.field, rows: []};
        groups[key].rows.push(row);
        return groups;
    }, {}));
    let file;
    let fileInput;
    let dragOver = false;
    let busy = false;
    let downloading = '';
    let loading = true;
    let error = '';
    let connectionError = '';
    let confirmation = '';
    let allowMissing = false;
    let timer;
    let disposed = false;
    let backups = [];
    let backupCursor;
    let backupsInitialized = false;

    function mergeBackups(incoming) {
        const combined = [...incoming, ...backups];
        backups = combined.filter((op, index) => combined.findIndex(other => other.id === op.id) === index);
    }
    $: active = status?.active;
    $: uploadLimit = formatSize(status?.max_upload_bytes || 5 * 1024 ** 3);
    $: review = selected?.state === 'review';
    $: ready = review && confirmation === 'RIPRISTINA' && (!selected.preview.missing_media || allowMissing);

    async function request(url, options = {}) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), options.body instanceof FormData ? 3600000 : 30000);
        try {
            const result = await apiFetch(url, {method: 'GET', skipForbidden: true, ...options, signal: controller.signal});
            if (result.error) throw new Error(result.response?.error || 'Impossibile contattare il server. Riprova.');
            return result.response;
        } catch (e) {
            if (e.name === 'AbortError') throw new Error('Il server non ha risposto in tempo. Aggiorna lo stato prima di riprovare.');
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

    function formatSize(bytes) {
        const unit = bytes >= 1024 ** 3 ? 'GB' : bytes >= 1024 ** 2 ? 'MB' : 'KB';
        const divisor = unit === 'GB' ? 1024 ** 3 : unit === 'MB' ? 1024 ** 2 : 1024;
        return `${new Intl.NumberFormat('it-IT', {maximumFractionDigits: 2}).format(bytes / divisor)} ${unit}`;
    }
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
        if (count !== 1 || !chosen.name.toLowerCase().endsWith('.zip')) {
            error = 'Seleziona un solo file ZIP di backup.';
        } else if (chosen.size > status.max_upload_bytes) {
            error = `Il file supera il limite di ${uploadLimit}.`;
        } else {
            file = chosen;
        }
    }
    function dropFile(event) {
        event.preventDefault();
        dragOver = false;
        selectFile(event.dataTransfer.files);
    }

    async function upload() {
        if (!file || busy || active) return;
        if (file.size > status.max_upload_bytes) { error = 'Il file supera il limite indicato.'; return; }
        busy = true;
        error = '';
        try {
            const body = new FormData();
            body.append('file', file);
            selected = await request(endpoint, {method: 'POST', body});
            confirmation = '';
            allowMissing = false;
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
    async function showReport(op) {
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
    async function download(op, kind = 'backup') {
        if (busy) return;
        busy = true;
        error = '';
        downloading = `${op.id}:${kind}`;
        try {
            const response = await window.fetch(`${endpoint}/${op.id}/${kind}`);
            if (!response.ok) throw new Error('Download non riuscito. Riprova.');
            const url = URL.createObjectURL(await response.blob());
            const link = document.createElement('a');
            link.href = url;
            link.download = kind === 'backup' ? `backup-prima-del-ripristino-${op.id}.zip` : `relazioni-mancanti-${op.id}.json`;
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
    async function reload() {
        if (busy) return;
        busy = true;
        error = '';
        try {
            const profile = await request(`${getApiHost()}/profile/info`);
            if (!profile.user_data?.user_id) throw new Error('Impossibile aggiornare il profilo. Riprova.');
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

<section aria-labelledby="restore-title">
    <div class="restore-intro">
        <span class="intro-icon"><ShieldCheck size={28} aria-hidden="true" /></span>
        <div><span class="section-kicker">DATI DELL’ASSOCIAZIONE</span><h2 id="restore-title">Ripristina da backup Bakney</h2>
            <p>Porta qui anagrafiche, iscrizioni, pagamenti e documenti dal tuo export.</p></div>
        <span class="protected-badge"><ShieldCheck size={14} aria-hidden="true" /> Copia di sicurezza prima del ripristino</span>
    </div>
    <details class="restore-explainer"><summary>Cosa viene sostituito e cosa resta invariato</summary>
        <p>I dati associativi vengono sostituiti da quelli del backup. Restano invariati il tuo accesso come titolare,
            dominio, identità, logo, email, integrazioni e versione installata. Gli account esistenti mantengono le credenziali;
            i collegamenti dei collaboratori vengono ricostruiti dal backup. I collaboratori rimossi perdono l’accesso.</p>
    </details>
    {#if loading}
        <p role="status">Caricamento dello stato del ripristino…</p>
    {:else if !status?.available}
        <InstanceAlert tone="warning">{status?.reason || 'Ripristino non disponibile.'}</InstanceAlert>
    {:else}
        {#if !active && !review}
            <div class="form-group">
                <label for="bakney-backup" class="col-form-label font-weight-bolder">Backup ZIP Bakney</label>
                <input id="bakney-backup" type="file" accept=".zip,application/zip" class="d-none" bind:this={fileInput}
                    disabled={busy} on:change={event => selectFile(event.target.files)} />
                {#if file}
                    <div class="backup-file d-flex align-items-center p-3 bg-light rounded-lg">
                        <FileArchive size={28} class="text-primary mr-3 flex-shrink-0" aria-hidden="true" />
                        <div class="file-details flex-grow-1">
                            <p class="mb-0 font-weight-bolder">{file.name}</p>
                            <small class="text-muted">{formatSize(file.size)}</small>
                        </div>
                        <button type="button" class="btn btn-icon btn-light ml-3 flex-shrink-0" disabled={busy}
                            aria-label="Rimuovi backup selezionato" on:click={removeFile}><X size={18} aria-hidden="true" /></button>
                    </div>
                {:else}
                    <button type="button" class="dropzone dropzone-default backup-dropzone" class:drag-over={dragOver}
                        disabled={busy} aria-label="Seleziona backup ZIP" aria-describedby="backup-upload-help"
                        on:click={() => fileInput?.click()} on:dragover|preventDefault={() => { if (!busy) dragOver = true; }}
                        on:dragleave={() => dragOver = false} on:drop={dropFile}>
                        <UploadCloud size={36} class="text-primary mb-3" aria-hidden="true" />
                        <span class="dropzone-msg-title d-block">Trascina qui il backup ZIP</span>
                        <span class="dropzone-msg-desc d-block">oppure premi per selezionare il file</span>
                    </button>
                {/if}
                <small id="backup-upload-help" class="text-muted d-block mt-2">Formato ZIP · Dimensione massima {uploadLimit}</small>
            </div>
            <button class="btn btn-primary" disabled={busy || !file} on:click={upload}>
                {#if busy && !downloading}<span class="spinner-border spinner-border-sm mr-2" aria-hidden="true" />{/if}
                {busy && !downloading ? 'Caricamento e verifica…' : 'Carica e verifica backup'}
            </button>
            {#if busy && !downloading}<p class="text-muted mt-2" role="status">Caricamento e verifica del backup. Mantieni aperta questa pagina.</p>{/if}
            <p class="text-muted mt-2">Sono accettati export Bakney v1 completi. Gli archivi di backup del server non sono compatibili.</p>
        {/if}
        {#if selected}
            <div class="review-panel mt-4" class:restore-failed={selected.state === 'failed'} aria-live="polite">
                <div class="d-flex justify-content-between align-items-start">
                    <h3>{active ? 'Ripristino dei dati' : labels[selected.stage] || selected.stage}</h3>
                    {#if ['failed', 'completed', 'cancelled', 'expired'].includes(selected.state)}
                        <button class="btn btn-light btn-sm" disabled={busy} aria-label="Chiudi esito ripristino" on:click={() => action('dismiss')}><X size={18} /></button>
                    {/if}
                </div>
                {#if selected.state === 'failed'}
                    <InstanceAlert tone="danger" role="alert"><strong>{selected.error}</strong>
                        <p class="mb-0 mt-2">Nessun dato è stato sostituito. Per riprovare, carica e verifica nuovamente il backup ZIP,
                            poi conferma il ripristino. Questo esito non viene aggiornato da un nuovo avvio del worker.</p>
                    </InstanceAlert>
                {/if}
                {#if active}
                    <RestoreProgressCard operation={selected} progress={liveProgress} now={progressNow} live={liveSource} />
                {:else if selected.state === 'completed'}
                    <div class="completion-banner"><span><CheckCircle2 size={30} aria-hidden="true" /></span><div><strong>I tuoi dati sono pronti</strong><p>Ripristino completato e verificato. Puoi tornare alla tua associazione.</p></div></div>
                {/if}
                <dl class="summary-grid">
                    <div><dt>Associazione nel backup</dt><dd>{selected.preview.association}</dd></div>
                    <div><dt>Data export</dt><dd>{selected.preview.export_date ? new Date(selected.preview.export_date).toLocaleString('it-IT') : 'Non indicata'}</dd></div>
                    <div><dt>Contenuto</dt><dd>{selected.preview.records} record · {selected.preview.files} allegati inclusi</dd></div>
                </dl>
                {#if selected.preview.missing_relations}
                    <details class="report-section" open={review}>
                    <summary><span>Rapporto del backup</span><span class="report-count">{selected.preview.missing_relations} collegamenti da verificare</span></summary>
                    <InstanceAlert tone="warning">Il backup contiene {selected.preview.missing_relations} collegamenti opzionali
                        a dati non inclusi. Il ripristino conserva i record e gli allegati inclusi,
                        lasciando vuoti questi collegamenti. Gli identificativi originali restano nel rapporto.</InstanceAlert>
                    <button class="btn btn-light mb-3" disabled={busy || reportLoading} on:click={() => showReport(selected)}>
                        {reportLoading ? 'Caricamento rapporto…' : reportOperationId === selected.id && relationReport ? 'Nascondi rapporto' : 'Visualizza rapporto'}
                    </button>
                    <div class="report-download mb-3"><RestoreDownloadButton kind="missing-relations" disabled={busy}
                        loading={downloading === `${selected.id}:missing-relations`} on:click={() => download(selected, 'missing-relations')} /></div>
                    {#if reportOperationId === selected.id && relationReport}
                        <div class="relation-report mb-3" aria-label="Rapporto relazioni mancanti">
                            <p>I record elencati sono presenti nel backup; manca soltanto il dato a cui punta il collegamento.
                                Gli ID permettono di identificarli per una verifica successiva.</p>
                            {#each reportGroups as group}
                                <details class="border rounded p-3 mb-2">
                                    <summary>{modelLabels[group.model] || group.model} → {fieldLabels[group.field] || group.field}
                                        <strong> · {group.rows.length} record</strong></summary>
                                    <div class="relation-rows mt-3" role="region" aria-label="Identificativi originali" tabindex="0">
                                        <table class="table table-sm"><thead><tr><th>Record conservato (ID)</th><th>Collegamento assente (ID)</th></tr></thead>
                                            <tbody>{#each group.rows.slice(0, 100) as row}<tr><td><code>{row.record_id}</code></td><td><code>{row.target_id}</code></td></tr>{/each}</tbody>
                                        </table>
                                    </div>
                                    {#if group.rows.length > 100}<p>Mostrati i primi 100 record. Il download contiene il rapporto completo.</p>{/if}
                                </details>
                            {/each}
                        </div>
                    {/if}
                    </details>
                {/if}
                {#if review}
                    {#if selected.preview.legacy_invoices}
                        <InstanceAlert tone="warning">Le {selected.preview.legacy_invoices} fatture del servizio Bakney
                            saranno conservate nell’archivio documenti dell’associazione, con la data originale.
                            La fatturazione dell’istanza resta invariata.</InstanceAlert>
                    {/if}
                    <InstanceAlert tone="warning">I dati attuali saranno sostituiti. Prima di procedere verrà creato e verificato
                        un backup di sicurezza scaricabile. Durante il ripristino le modifiche e i processi in background saranno sospesi.</InstanceAlert>
                    {#if selected.preview.missing_media}
                        <Switch id="restore-missing-media" bind:checked={allowMissing} disabled={busy}
                            label={`Accetto che ${selected.preview.missing_media} allegati o firme non inclusi non siano recuperati`} />
                        <p class="text-muted">Eventuali link esterni alle firme possono dipendere dal servizio originale.</p>
                    {/if}
                    <label for="restore-confirmation">Scrivi RIPRISTINA per confermare la sostituzione</label>
                    <input id="restore-confirmation" class="form-control mb-3" autocomplete="off" bind:value={confirmation} disabled={busy} />
                    <button class="btn btn-danger mr-2" disabled={busy || !ready} on:click={() => action('start')}>Sostituisci i dati</button>
                    <button class="btn btn-light" disabled={busy} on:click={() => action('cancel')}>Annulla</button>
                {:else if active}
                    <details class="restore-explainer recovery-help"><summary>Il ripristino sembra fermo?</summary>
                        <p>Se il worker è stato riavviato o lo stato non avanza, puoi richiedere la ripresa.
                            Un processo ancora attivo non verrà eseguito due volte.</p>
                        <button class="btn btn-light" disabled={busy} on:click={() => action('resume')}>Riprendi</button>
                    </details>
                {:else if selected.state === 'completed'}
                    <p>I dati sono stati ripristinati. Le impostazioni dell’istanza e il tuo accesso sono stati conservati.</p>
                    <button class="btn btn-primary" disabled={busy} on:click={reload}>Ricarica l’applicazione</button>

                {/if}
            </div>
        {/if}
        {#if backups.length}
            <InstanceAccordion id="data-recovery-history" title="Backup di sicurezza" description="Copie dei dati precedenti, create prima di ogni ripristino." count={backups.length}>
            {#each backups as op}
                <div class="recovery-row">
                    <div class="recovery-caption"><strong>{new Date(op.created_at).toLocaleString('it-IT')}</strong><span>{labels[op.stage] || op.stage}</span></div>
                    <div class="recovery-downloads">
                        <RestoreDownloadButton disabled={busy} loading={downloading === `${op.id}:backup`} on:click={() => download(op)} />
                        {#if op.preview.missing_relations}
                            <RestoreDownloadButton kind="missing-relations" disabled={busy} loading={downloading === `${op.id}:missing-relations`}
                                on:click={() => download(op, 'missing-relations')} />
                        {/if}
                    </div>
                </div>
            {/each}
            {#if backupCursor}<button class="btn btn-light" disabled={busy} on:click={loadOlderBackups}>Carica backup precedenti</button>{/if}
            </InstanceAccordion>
        {/if}
    {/if}
    {#if error || connectionError}
        <InstanceAlert tone="danger" role="alert">{error || connectionError}
            <button class="btn btn-link" disabled={busy} on:click={refresh}>Aggiorna stato</button>
        </InstanceAlert>
    {/if}
</section>

<style>
    .recovery-row {display:flex;align-items:center;justify-content:space-between;gap:1rem;flex-wrap:wrap;padding:1rem 0;border-bottom:1px solid var(--border-color,#e6e8ef);}
    .recovery-row:last-child {border-bottom:0;}
    .recovery-caption {display:flex;flex-direction:column;gap:.25rem;font-size:.8rem;}
    .recovery-caption span {color:var(--text-secondary,#73798c);font-size:.74rem;}
    .recovery-downloads {display:flex;gap:.65rem;flex-wrap:wrap;}
    @media(max-width:575px) {.recovery-downloads {width:100%;} .report-download {width:100%;}}

    .restore-intro {display:flex;align-items:center;gap:1rem;flex-wrap:wrap;margin:1.5rem 0 1rem;}
    .intro-icon {display:grid;place-items:center;border-radius:16px;padding:1rem;color:var(--primary);background:color-mix(in srgb, var(--primary) 8%, white);}
    .restore-intro>div {flex:1;min-width:210px;}
    .restore-intro h2 {font-size:1.45rem;margin:.35rem 0;letter-spacing:-.025em;}
    .restore-intro p {margin:0;color:#73798c;font-size:.85rem;}
    .section-kicker {font-size:.65rem;letter-spacing:.12em;color:var(--primary);font-weight:700;}
    .protected-badge {display:flex;gap:.4rem;align-items:center;color:#56745f;font-size:.72rem;background:#f0f7f2;padding:.5rem .75rem;border-radius:99px;}
    .restore-explainer {color:#73798c;font-size:.8rem;margin:1rem 0 1.5rem;}
    .restore-explainer summary {cursor:pointer;font-weight:500;}
    .restore-explainer p {margin:.75rem 0;max-width:80ch;}
    details {min-width: 0;}
    .report-section {border:1px solid #ede7d6;border-radius:.8rem;padding:1rem;margin:1.3rem 0;background:#fffdfa;}
    .report-section>summary {cursor:pointer;font-weight:600;font-size:.9rem;}
    .report-count {display:inline-block;background:#fff0cd;color:#926315;border-radius:99px;padding:.25rem .6rem;font-size:.72rem;margin-left:.7rem;}
    .completion-banner {display:flex;align-items:center;gap:1rem;background:linear-gradient(100deg,#edf8f0, #fafdfb);border:1px solid #d9eddf;border-radius:1rem;padding:1.5rem;margin:1rem 0;}
    .completion-banner>span {color:#34845a;}
    .completion-banner strong {font-size:1.3rem;color:#255f40;}
    .completion-banner p {margin:.3rem 0 0;color:#5e796a;}

    .backup-dropzone { width: 100%; padding: 2rem 1rem; text-align: center; background: transparent; }
    .backup-dropzone:hover, .backup-dropzone.drag-over, .backup-dropzone:focus-visible { border-color: var(--primary); background: var(--bg-hover, #f8fafc); }
    .backup-dropzone:focus-visible { outline: 2px solid var(--primary); outline-offset: 3px; }
    .backup-file { border: 1px solid var(--border-color, #e4e6ef); }
    .file-details { min-width: 0; }
    section { min-width: 0; overflow-wrap: anywhere; }
    .review-panel { margin-bottom: 1rem; border: 1px solid var(--border-color, #e3e5ee); padding: 1.5rem; border-radius: 1rem; background: var(--bg-card, #fff); box-shadow:0 8px 28px #20223806; }
    .restore-failed {border-color: var(--danger, #c62828);}
    .relation-report {min-width:0; width:100%; max-height: 28rem; overflow-y: auto;}
    .relation-rows {min-width:0; width:100%; max-height: 16rem; overflow: auto;}
    .relation-rows table {table-layout:fixed; width:100%;}
    .relation-rows code {white-space: normal; overflow-wrap:anywhere; color: inherit;}
    .relation-report summary {cursor: pointer;}
    .review-panel h3 { font-size: 1.35rem; font-weight: 600; margin-bottom: 1.25rem; }
    .summary-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .75rem; margin-bottom: 1.25rem; }
    .summary-grid > div { background: var(--bg-hover, #f8fafc); border-radius: .5rem; padding: 1rem; }
    .summary-grid dt { color: var(--text-secondary, #536171); font-size: .9rem; font-weight: 400; }
    .summary-grid dd { font-size: 1.1rem; font-weight: 600; margin: .4rem 0 0; }
    @media (max-width: 575px) { .summary-grid { grid-template-columns: 1fr; } .review-panel { padding: 1rem; } }
</style>
