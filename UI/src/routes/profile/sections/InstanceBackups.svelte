<script>
    // Backup: disk at a glance, the archive list with its actions, and the schedule.
    // Actions appear only when the runner reports them (overview.actions).
    import {toast} from 'svelte-sonner';
    import {getApiHost} from 'store/instanceStore.js';
    import {Archive, PushPin, WarningCircle, SpinnerGap, HardDrive, CloudCheck, Cloud, ArrowCounterClockwise, CloudArrowDown, DownloadSimple, PlusCircle, Trash, UploadSimple} from 'phosphor-svelte';
    import InstanceDialog from './InstanceDialog.svelte';
    import InstanceMenu from './InstanceMenu.svelte';
    import DiagnosticResult from './DiagnosticResult.svelte';
    import BackupSchedule from './BackupSchedule.svelte';
    import RemoteBackup from './RemoteBackup.svelte';
    import {locationLabel, recoveryKeyLine} from './remoteBackup.js';
    import DataTransfer from './DataTransfer.svelte';
    import {formatSize, formatDate, formatRelative} from './instanceFormat.js';
    import {RESTORE_WORD, uploadProblem, uploadArchive, sessionToken} from './backupUpload.js';
    import {deleteBody, keepBody} from './backupPassword.js';

    // request(path, options) is the owner page helper rooted at /instance.
    export let request;
    export let overview = null;
    export let loading = false;
    export let error = '';
    export let checks = [];
    // The running runner operation, if any; blocks every action.
    export let active = null;
    export let blockedReason = '';
    export let onReload = () => {};
    export let onOperation = () => {};

    const origins = {manual: 'Manuale', scheduled: 'Pianificato', 'pre-upgrade': 'Prima dell’aggiornamento', 'pre-restore': 'Prima del ripristino', uploaded: 'Caricato'};

    let dialog = null; // {kind: 'download'|'delete'|'unkeep'|'create'|'restore'|'upload'|'fetch', archive}
    let password = '';
    let recoveryKey = '';
    let confirmWord = '';
    let file = null;
    let fileInput;
    let progress = null; // 0..1 while uploading
    let upload = null;
    let dialogError = '';
    let working = false;
    let pending = {};

    $: actions = overview?.actions || {};
    $: archives = overview?.archives || [];
    $: disk = overview?.disk;
    $: data = disk ? (disk.database || 0) + (disk.objects || 0) : 0;
    $: parts = disk && disk.total > 0 ? [
        {key: 'data', label: 'Dati', bytes: data, unknown: disk.database === null || disk.objects === null},
        {key: 'backups', label: 'Backup', bytes: disk.backups || 0},
        {key: 'other', label: 'Sistema e altro', bytes: Math.max(0, disk.used - data - (disk.backups || 0))},
    ] : [];
    $: marker = disk?.total > 0 && overview?.next_backup ? Math.min(100, (disk.used + overview.next_backup.estimated) / disk.total * 100) : null;
    $: tooSmall = overview?.next_backup && !overview.next_backup.fits;
    $: running = active && ['backup', 'restore', 'fetch'].includes(active.kind) ? active : null;
    $: located = !!overview?.remote?.configured;
    const runningLabels = {backup: 'Backup in corso…', restore: 'Ripristino in corso…', fetch: 'Recupero dalla copia remota…'};
    $: busyReason = active ? 'Attendi la fine dell’operazione in corso.' : blockedReason;
    $: problems = checks.filter(check => ['failed', 'warning'].includes(check.status));

    function badge(archive) {
        if (archive.pending_upgrade) return ['warn', 'In uso dall’aggiornamento'];
        if (archive.last_upgrade) return ['ok', 'Ultimo aggiornamento riuscito'];
        return null;
    }

    function rowActions(archive) {
        if (archive.location === 'remote') {
            return [{label: 'Recupera sul server', hint: active ? busyReason : archive.key_id ? `Serve la chiave ${archive.key_id}.` : '', disabled: !!active || !!blockedReason, onSelect: () => openDialog('fetch', archive)}];
        }
        const locked = archive.pending_upgrade ? 'Serve all’aggiornamento in sospeso.' : '';
        return [
            {label: 'Scarica', disabled: !!active, hint: active ? busyReason : '', onSelect: () => openDialog('download', archive)},
            actions.keep && {label: archive.keep ? 'Non conservare più' : 'Conserva', hint: archive.keep ? 'Torna nella rotazione automatica.' : 'Mai eliminato in automatico.', disabled: !!active || pending[archive.name], onSelect: () => archive.keep ? openDialog('unkeep', archive) : keep(archive)},
            actions.restore && {label: 'Ripristina', disabled: !!active || !!locked || !!blockedReason, hint: locked || (active ? busyReason : blockedReason), onSelect: () => openDialog('restore', archive)},
            actions.delete && {label: 'Elimina', danger: true, disabled: !!active || !!locked, hint: locked || (active ? busyReason : ''), onSelect: () => openDialog('delete', archive)},
        ];
    }

    $: menuItems = [
        {label: loading ? 'Aggiornamento…' : 'Aggiorna elenco', disabled: loading, onSelect: () => onReload(true)},
        actions.upload && {label: 'Carica backup', disabled: !!active, hint: active ? busyReason : '', onSelect: () => openDialog('upload')},
    ];
    $: fileProblem = uploadProblem(file, disk?.free ?? null);
    $: restoreReady = !!password && confirmWord.trim() === RESTORE_WORD;

    function openDialog(kind, archive = null) {
        dialog = {kind, archive};
        password = '';
        confirmWord = '';
        file = null;
        progress = null;
        dialogError = '';
    }

    function closeDialog() {
        if (working) return;
        dialog = null;
        password = '';
        recoveryKey = '';
        confirmWord = '';
    }

    async function confirmRestore() {
        if (!restoreReady || working) return;
        working = true;
        dialogError = '';
        try {
            const result = await request(`/admin/backups/${encodeURIComponent(dialog.archive.name)}/restore`, {method: 'POST', body: JSON.stringify({password, confirm: RESTORE_WORD})});
            working = false;
            closeDialog();
            onOperation(result.operation);
        } catch (e) { dialogError = e.message; }
        finally { password = ''; working = false; }
    }

    async function startUpload() {
        if (!file || fileProblem || working) return;
        working = true;
        dialogError = '';
        progress = 0;
        upload = uploadArchive(`${getApiHost()}/instance/admin/backups/upload`, file, {token: sessionToken(), onProgress: value => progress = value});
        try {
            await upload.done;
            working = false;
            closeDialog();
            toast.success('Backup caricato.');
            onReload();
        } catch (e) {
            if (!e.aborted) dialogError = e.message;
            progress = null;
        } finally { upload = null; working = false; }
    }

    function cancelUpload() {
        if (upload) upload.abort();
        else closeDialog();
    }

    async function confirmFetch() {
        if (working || !recoveryKeyLine(recoveryKey)) return;
        working = true;
        dialogError = '';
        try {
            const result = await request('/admin/backups/remote/fetch', {method: 'POST', body: JSON.stringify({name: dialog.archive.name, recovery_key: recoveryKeyLine(recoveryKey)})});
            working = false;
            closeDialog();
            onOperation(result.operation);
        } catch (e) { dialogError = e.message; }
        finally { recoveryKey = ''; working = false; }
    }

    async function readKeyFile(event) {
        const file = event.target.files?.[0];
        event.target.value = '';
        if (file && file.size < 4096) recoveryKey = recoveryKeyLine(await file.text()) || '';
        if (!recoveryKey) dialogError = 'Il file non contiene una chiave di ripristino.';
        else dialogError = '';
    }

    async function confirmDownload() {
        if (!password || working) return;
        working = true;
        dialogError = '';
        try {
            const name = dialog.archive.name;
            const result = await request('/admin/backups/download-token', {method: 'POST', body: JSON.stringify({name, password})});
            const link = document.createElement('a');
            link.href = `${getApiHost()}/instance/admin/backups/${encodeURIComponent(name)}/download?download_token=${encodeURIComponent(result.download_token)}`;
            link.download = name;
            link.rel = 'noopener noreferrer';
            document.body.appendChild(link);
            link.click();
            link.remove();
            working = false;
            closeDialog();
            toast.success('Download avviato.');
        } catch (e) {
            dialogError = e.message;
        } finally {
            password = '';
            working = false;
        }
    }

    async function confirmDelete() {
        if (!password || working) return;
        working = true;
        dialogError = '';
        try {
            await request(`/admin/backups/${encodeURIComponent(dialog.archive.name)}`, {method: 'DELETE', body: JSON.stringify(deleteBody(dialog.archive, password))});
            working = false;
            closeDialog();
            toast.success('Backup eliminato.');
            onReload();
        } catch (e) { dialogError = e.message; }
        finally { password = ''; working = false; }
    }

    // Back into the rotation: needs the password, so it goes through a dialog.
    async function confirmUnkeep() {
        if (!password || working) return;
        working = true;
        dialogError = '';
        try {
            await request(`/admin/backups/${encodeURIComponent(dialog.archive.name)}`, {method: 'PATCH', body: JSON.stringify(keepBody(false, password))});
            working = false;
            closeDialog();
            toast.success('Il backup torna nella rotazione.');
            onReload();
        } catch (e) { dialogError = e.message; }
        finally { password = ''; working = false; }
    }

    async function confirmCreate() {
        if (working) return;
        working = true;
        dialogError = '';
        try {
            const result = await request('/admin/backups', {method: 'POST', body: '{}'});
            working = false;
            closeDialog();
            onOperation(result.operation);
        } catch (e) { dialogError = e.message; }
        finally { working = false; }
    }

    async function keep(archive) {
        pending = {...pending, [archive.name]: true};
        try {
            await request(`/admin/backups/${encodeURIComponent(archive.name)}`, {method: 'PATCH', body: JSON.stringify(keepBody(true))});
            toast.success('Backup conservato.');
            onReload();
        } catch (e) { toast.error(e.message); }
        finally { pending = {...pending, [archive.name]: false}; }
    }
</script>

<section class="si-stack" aria-labelledby="instance-backups-heading">
    {#if problems.length}
        <div class="si-stack">{#each problems as check (check.id)}<DiagnosticResult {check} />{/each}</div>
    {/if}

    <div class="si-card">
        <div class="si-card-head">
            <div>
                <h2 id="instance-backups-heading" class="si-title">Backup</h2>
                <p class="si-muted si-small">Copie complete del server: dati, documenti, impostazioni e chiavi.</p>
            </div>
            <div class="si-actions">
                {#if actions.create}
                    <button type="button" class="btn btn-primary si-btn" disabled={!!active || !!blockedReason || tooSmall} title={busyReason || (tooSmall ? 'Spazio su disco insufficiente.' : '')} on:click={() => openDialog('create')}><PlusCircle size={18} weight="duotone" />Crea backup</button>
                {/if}
                <InstanceMenu label="Altre azioni backup" items={menuItems} />
            </div>
        </div>

        {#if error}<p class="text-danger" role="alert">{error} <button type="button" class="si-link" on:click={() => onReload()}>Riprova</button></p>{/if}
        {#if overview && !overview.available}<p class="si-muted mb-0" role="status">{overview.reason}</p>{/if}

        {#if !overview && loading}
            <span class="si-skeleton" style="height: .75rem"></span>
            <div class="mt-4 si-stack">{#each Array(3) as _}<span class="si-skeleton" style="height: 2.75rem"></span>{/each}</div>
        {/if}

        {#if overview?.available}
            {#if disk}
                <div class="disk">
                    <div class="disk-bar" role="img" aria-label={`Disco: ${formatSize(disk.used)} usati su ${formatSize(disk.total)}, ${formatSize(disk.free)} liberi`}>
                        {#each parts as part (part.key)}<span class="seg seg-{part.key}" style={`width:${part.bytes / disk.total * 100}%`}></span>{/each}
                        {#if marker !== null}<span class="marker" class:over={tooSmall} style={`left:${marker}%`} title={`Prossimo backup: circa ${formatSize(overview.next_backup.estimated)}`}></span>{/if}
                    </div>
                    <ul class="legend">
                        {#each parts as part (part.key)}<li><span class="dot seg-{part.key}" aria-hidden="true"></span>{part.label} <strong>{part.unknown ? '—' : formatSize(part.bytes)}</strong></li>{/each}
                        <li><span class="dot free" aria-hidden="true"></span>Libero <strong>{formatSize(disk.free)}</strong> <span class="si-muted">di {formatSize(disk.total)}</span></li>
                    </ul>
                    {#if tooSmall}
                        <p class="space-warning" role="alert"><WarningCircle size={18} weight="fill" /> Spazio insufficiente per il prossimo backup: servono {formatSize(overview.next_backup.required)}, liberi {formatSize(disk.free)}.</p>
                    {/if}
                </div>
            {/if}

            {#if running}
                <div class="running" role="status"><span class="spin"><SpinnerGap size={18} weight="bold" /></span>{runningLabels[running.kind]}</div>
            {/if}

            {#if archives.length}
                <table class="backups">
                    <thead><tr><th scope="col">Data</th><th scope="col">Origine</th><th scope="col">Versione</th><th scope="col" class="num">Dimensione</th>{#if located}<th scope="col" class="where"><span class="si-sr-only">Posizione</span></th>{/if}<th scope="col"><span class="si-sr-only">Azioni</span></th></tr></thead>
                    <tbody>
                        {#each archives as archive (archive.name)}
                            {@const tag = badge(archive)}
                            <tr class:remote-only={archive.location === 'remote'}>
                                <td><div class="when">
                                    <span class="date">{formatDate(archive.created_at)}{#if archive.keep}<span class="kept" title="Conservato: mai eliminato in automatico"><PushPin size={14} weight="fill" /><span class="si-sr-only">Conservato</span></span>{/if}</span>
                                    <span class="si-muted si-small">{formatRelative(archive.created_at)}</span>
                                    {#if tag}<span class="si-pill {tag[0]}">{tag[1]}</span>{/if}
                                </div></td>
                                <td data-label="Origine">{origins[archive.origin] || '—'}</td>
                                <td data-label="Versione">{archive.version || '—'}</td>
                                <td data-label="Dimensione" class="num">{formatSize(archive.size)}</td>
                                {#if located}
                                    <td data-label="Posizione" class="where">
                                        <span class="place place-{archive.location || 'local'}" title={`${locationLabel(archive)}${archive.key_id ? ` · chiave ${archive.key_id}` : ''}`}>
                                            {#if archive.location === 'both'}<CloudCheck size={18} weight="duotone" />{:else if archive.location === 'remote'}<Cloud size={18} weight="duotone" />{:else}<HardDrive size={18} weight="duotone" />{/if}
                                            <span class="si-sr-only">{locationLabel(archive)}</span>
                                        </span>
                                    </td>
                                {/if}
                                <td class="row-menu"><InstanceMenu size="sm" label={`Azioni backup del ${formatDate(archive.created_at)}`} items={rowActions(archive)} /></td>
                            </tr>
                        {/each}
                    </tbody>
                </table>
                {#if overview.retention?.automatic}<p class="si-hint mt-3 mb-0">Dei backup automatici restano gli ultimi {overview.retention.automatic}. Quelli manuali e conservati non vengono eliminati.</p>{/if}
            {:else if !running}
                <div class="empty">
                    <Archive size={40} weight="duotone" />
                    <p class="mb-0"><strong>Ancora nessun backup</strong></p>
                    {#if actions.create}<button type="button" class="btn btn-primary btn-sm si-btn" disabled={!!active || tooSmall} on:click={() => openDialog('create')}><PlusCircle size={16} weight="duotone" />Crea il primo backup</button>{/if}
                </div>
            {/if}
        {/if}
    </div>

    {#if overview?.available && overview.schedule}
        <BackupSchedule {request} schedule={overview.schedule} retention={overview.retention} disabled={!!active} onSaved={() => onReload()} />
    {/if}
    {#if overview?.available && overview.remote}
        <RemoteBackup {request} remote={overview.remote} disabled={!!active} onSaved={() => onReload()} />
    {/if}

    <!-- Bakney ZIP export/import, self-hosted only: this page exists only there. -->
    <DataTransfer />
</section>

<InstanceDialog open={dialog?.kind === 'download'} title="Scarica backup" locked={working} on:close={closeDialog}>
    {#if dialog?.archive}
        <form id="backup-download-form" on:submit|preventDefault={confirmDownload}>
            <p><strong>{formatDate(dialog.archive.created_at)}</strong> · {formatSize(dialog.archive.size)}</p>
            <p class="note" role="note">Il file non è cifrato e contiene password e chiavi del server. Conservalo al sicuro.</p>
            <label class="si-field" for="backup-download-password">Conferma la tua password
                <input id="backup-download-password" class="form-control" type="password" autocomplete="current-password" bind:value={password} disabled={working} />
            </label>
            {#if dialogError}<p class="text-danger mt-2 mb-0" role="alert">{dialogError}</p>{/if}
        </form>
    {/if}
    <svelte:fragment slot="footer">
        <button type="button" class="btn btn-light" disabled={working} on:click={closeDialog}>Annulla</button>
        <button type="submit" form="backup-download-form" class="btn btn-primary si-btn" disabled={!password || working}><DownloadSimple size={18} weight="duotone" />{working ? 'Verifica…' : 'Scarica backup'}</button>
    </svelte:fragment>
</InstanceDialog>

<InstanceDialog open={dialog?.kind === 'delete'} title="Eliminare il backup?" locked={working} on:close={closeDialog}>
    {#if dialog?.archive}
        <p>Il backup del <strong>{formatDate(dialog.archive.created_at)}</strong> ({formatSize(dialog.archive.size)}) viene eliminato dal server. Non si può annullare.</p>
        <form id="backup-delete-form" on:submit|preventDefault={confirmDelete}>
            {#if dialog.archive.last_upgrade}<p class="note" role="note">È il backup dell’ultimo aggiornamento riuscito: senza, non puoi tornare alla versione precedente.</p>{/if}
            <label class="si-field" for="backup-delete-password">Conferma la tua password
                <input id="backup-delete-password" class="form-control" type="password" autocomplete="current-password" bind:value={password} disabled={working} />
            </label>
            {#if dialogError}<p class="text-danger mt-2 mb-0" role="alert">{dialogError}</p>{/if}
        </form>
    {/if}
    <svelte:fragment slot="footer">
        <button type="button" class="btn btn-light" data-autofocus disabled={working} on:click={closeDialog}>Annulla</button>
        <button type="submit" form="backup-delete-form" class="btn btn-danger si-btn" disabled={!password || working}><Trash size={18} weight="duotone" />{working ? 'Eliminazione…' : 'Elimina'}</button>
    </svelte:fragment>
</InstanceDialog>

<InstanceDialog open={dialog?.kind === 'unkeep'} title="Rimettere il backup nella rotazione?" locked={working} on:close={closeDialog}>
    {#if dialog?.archive}
        <form id="backup-unkeep-form" on:submit|preventDefault={confirmUnkeep}>
            <p>Il backup del <strong>{formatDate(dialog.archive.created_at)}</strong> potrà essere eliminato in automatico come gli altri.</p>
            <label class="si-field" for="backup-unkeep-password">Conferma la tua password
                <input id="backup-unkeep-password" class="form-control" type="password" autocomplete="current-password" bind:value={password} disabled={working} />
            </label>
            {#if dialogError}<p class="text-danger mt-2 mb-0" role="alert">{dialogError}</p>{/if}
        </form>
    {/if}
    <svelte:fragment slot="footer">
        <button type="button" class="btn btn-light" disabled={working} on:click={closeDialog}>Annulla</button>
        <button type="submit" form="backup-unkeep-form" class="btn btn-primary si-btn" disabled={!password || working}>{working ? 'Verifica…' : 'Non conservare più'}</button>
    </svelte:fragment>
</InstanceDialog>

<InstanceDialog open={dialog?.kind === 'create'} title="Creare un backup ora?" locked={working} on:close={closeDialog}>
    <p>L’app resta offline per qualche minuto mentre i dati vengono copiati.</p>
    {#if overview?.next_backup}<p class="si-muted">Dimensione stimata: circa {formatSize(overview.next_backup.estimated)}.</p>{/if}
    {#if dialogError}<p class="text-danger mb-0" role="alert">{dialogError}</p>{/if}
    <svelte:fragment slot="footer">
        <button type="button" class="btn btn-light" disabled={working} on:click={closeDialog}>Annulla</button>
        <button type="button" class="btn btn-primary si-btn" data-autofocus disabled={working} on:click={confirmCreate}><PlusCircle size={18} weight="duotone" />{working ? 'Avvio…' : 'Crea backup'}</button>
    </svelte:fragment>
</InstanceDialog>

<InstanceDialog open={dialog?.kind === 'restore'} title="Ripristinare questo backup?" locked={working} on:close={closeDialog}>
    {#if dialog?.archive}
        <form id="backup-restore-form" class="si-stack" on:submit|preventDefault={confirmRestore}>
            <p class="mb-0"><strong>{formatDate(dialog.archive.created_at)}</strong> {[dialog.archive.version && `v${dialog.archive.version}`, formatSize(dialog.archive.size)].filter(Boolean).map(part => `· ${part}`).join(' ')}</p>
            <p class="mb-0">I dati attuali vengono sostituiti. Prima viene creato un backup di sicurezza.</p>
            <label class="si-field" for="backup-restore-password">Conferma la tua password
                <input id="backup-restore-password" class="form-control" type="password" autocomplete="current-password" bind:value={password} disabled={working} />
            </label>
            <label class="si-field" for="backup-restore-word">Scrivi {RESTORE_WORD}
                <input id="backup-restore-word" class="form-control" type="text" autocomplete="off" autocapitalize="characters" spellcheck="false" bind:value={confirmWord} disabled={working} />
            </label>
            {#if dialogError}<p class="text-danger mb-0" role="alert">{dialogError}</p>{/if}
        </form>
    {/if}
    <svelte:fragment slot="footer">
        <button type="button" class="btn btn-light" data-autofocus disabled={working} on:click={closeDialog}>Annulla</button>
        <button type="submit" form="backup-restore-form" class="btn btn-danger si-btn" disabled={!restoreReady || working}><ArrowCounterClockwise size={18} weight="duotone" />{working ? 'Avvio…' : 'Ripristina'}</button>
    </svelte:fragment>
</InstanceDialog>

<InstanceDialog open={dialog?.kind === 'upload'} title="Carica backup" locked={working} on:close={closeDialog}>
    <form id="backup-upload-form" class="si-stack" on:submit|preventDefault={startUpload}>
        <label class="si-field" for="backup-upload-file">File di backup (.tar.gz)
            <input id="backup-upload-file" class="form-control" type="file" accept=".gz,application/gzip" bind:this={fileInput} disabled={working} on:change={() => { file = fileInput.files?.[0] || null; dialogError = ''; }} />
        </label>
        {#if file}
            <p class="mb-0 si-muted">Dimensione {formatSize(file.size)}</p>
        {/if}
        {#if progress !== null}
            <div class="upload-bar" role="progressbar" aria-label="Caricamento" aria-valuemin="0" aria-valuemax="100" aria-valuenow={Math.round(progress * 100)}>
                <span style={`width:${progress * 100}%`}></span>
            </div>
            <p class="si-muted si-small mb-0" aria-live="polite">{progress < 1 ? `Caricamento ${Math.round(progress * 100)}%` : 'Verifica del file…'}</p>
        {/if}
        {#if fileProblem}<p class="text-danger mb-0" role="alert">{fileProblem}</p>{/if}
        {#if dialogError}<p class="text-danger mb-0" role="alert">{dialogError}</p>{/if}
    </form>
    <svelte:fragment slot="footer">
        <button type="button" class="btn btn-light" on:click={cancelUpload}>Annulla</button>
        <button type="submit" form="backup-upload-form" class="btn btn-primary si-btn" disabled={!file || !!fileProblem || working}><UploadSimple size={18} weight="duotone" />{working ? 'Caricamento…' : 'Carica'}</button>
    </svelte:fragment>
</InstanceDialog>

<InstanceDialog open={dialog?.kind === 'fetch'} title="Recupera dalla copia remota" locked={working} on:close={closeDialog}>
    {#if dialog?.archive}
        <form id="backup-fetch-form" on:submit|preventDefault={confirmFetch}>
            <p><strong>{formatDate(dialog.archive.created_at)}</strong> · {formatSize(dialog.archive.size)}{#if dialog.archive.key_id}{` · chiave ${dialog.archive.key_id}`}{/if}</p>
            <label class="si-field" for="backup-fetch-key">Chiave di ripristino</label>
            <input id="backup-fetch-key" class="form-control" type="password" autocomplete="off" spellcheck="false" bind:value={recoveryKey} disabled={working} placeholder="AGE-SECRET-KEY-1…" />
            <label class="si-link key-file mt-2">Carica file chiave<input type="file" accept=".txt,text/plain" class="si-sr-only" on:change={readKeyFile} disabled={working} /></label>
            {#if dialogError}<p class="text-danger mt-2 mb-0" role="alert">{dialogError}</p>{/if}
        </form>
    {/if}
    <svelte:fragment slot="footer">
        <button type="button" class="btn btn-light" disabled={working} on:click={closeDialog}>Annulla</button>
        <button type="submit" form="backup-fetch-form" class="btn btn-primary si-btn" disabled={!recoveryKeyLine(recoveryKey) || working}><CloudArrowDown size={18} weight="duotone" />{working ? 'Verifica…' : 'Recupera'}</button>
    </svelte:fragment>
</InstanceDialog>

<style>
    .disk { margin-bottom: 1.25rem; }
    .disk-bar { position: relative; display: flex; height: .625rem; border-radius: 999px; background: var(--bg-active); }
    .seg { height: 100%; }
    .seg:first-child { border-radius: 999px 0 0 999px; }
    .seg-data { background: var(--primary); }
    .seg-backups { background: #d08a2e; }
    .seg-other { background: var(--text-muted); opacity: .45; }
    .marker { position: absolute; top: -.3rem; bottom: -.3rem; width: 2px; margin-left: -1px; background: var(--text-primary); border-radius: 2px; }
    .marker.over { background: var(--danger, #d92d20); }
    .legend { display: flex; flex-wrap: wrap; gap: .25rem 1.25rem; list-style: none; margin: .625rem 0 0; padding: 0; font-size: 1rem; color: var(--text-secondary); }
    .legend strong { color: var(--text-primary); font-weight: 600; }
    .dot { display: inline-block; width: .55rem; height: .55rem; margin-right: .4rem; border-radius: 50%; vertical-align: .05rem; }
    .dot.free { background: var(--bg-active); box-shadow: inset 0 0 0 1px var(--border-color-dark, var(--border-color)); }
    .space-warning { display: flex; align-items: center; gap: .4rem; margin: .75rem 0 0; font-size: 1rem; font-weight: 500; color: var(--danger, #d92d20); }
    .running { display: flex; align-items: center; gap: .5rem; padding: .75rem 1rem; margin-bottom: .75rem; border-radius: .625rem; background: var(--light-primary); font-weight: 500; }
    .spin { display: inline-flex; animation: spin 1s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .backups { width: 100%; border-collapse: collapse; }
    .backups th { padding: 0 .75rem .5rem 0; font-size: .925rem; font-weight: 600; color: var(--text-secondary); text-align: left; white-space: nowrap; border-bottom: 1px solid var(--border-color); }
    .backups td { padding: .75rem .75rem .75rem 0; border-bottom: 1px solid var(--border-color); vertical-align: middle; }
    .backups tr:last-child td { border-bottom: 0; }
    .backups .num { text-align: right; white-space: nowrap; }
    .when { display: flex; flex-wrap: wrap; align-items: center; gap: .15rem .6rem; }
    .date { display: inline-flex; align-items: center; gap: .35rem; font-weight: 600; }
    .kept { display: inline-flex; color: var(--primary-text, var(--primary)); }
    .row-menu { width: 1%; padding-right: 0 !important; text-align: right; }
    .where { width: 1%; text-align: center; }
    .place { display: inline-flex; vertical-align: middle; color: var(--text-secondary); }
    .place-both { color: var(--primary-text, var(--primary)); }
    .remote-only .date { color: var(--text-secondary); }
    .key-file { display: inline-block; cursor: pointer; font-size: 1rem; }
    .key-file:focus-within { outline: 2px solid var(--primary); outline-offset: 2px; }
    .empty { display: flex; flex-direction: column; align-items: center; gap: .75rem; padding: 2rem 1rem; color: var(--text-secondary); text-align: center; }
    .upload-bar { height: .5rem; border-radius: 999px; background: var(--bg-active); overflow: hidden; }
    .upload-bar span { display: block; height: 100%; background: var(--primary); transition: width .2s ease-out; }
    .note { padding: .625rem .875rem; border-radius: .5rem; background: var(--light-warning); }
    @media (prefers-reduced-motion: reduce) { .spin { animation: none; } }
    @media (max-width: 767px) {
        .backups thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
        .backups, .backups tbody, .backups tr, .backups td { display: block; }
        .backups tr { position: relative; padding: .75rem 2.75rem .75rem 0; border-bottom: 1px solid var(--border-color); }
        .backups tr:last-child { border-bottom: 0; }
        .backups td { padding: 0; border: 0; }
        .backups td[data-label] { display: inline; font-size: 1rem; color: var(--text-secondary); }
        .backups td[data-label] + td[data-label]::before { content: ' · '; }
        .backups .num { text-align: left; }
        .row-menu { position: absolute; top: .75rem; right: 0; width: auto; }
        .when { margin-bottom: .25rem; }
    }
</style>
