<script>
    import {onMount} from 'svelte';
    import InstanceAlert from './InstanceAlert.svelte';
    import DiagnosticResult from './DiagnosticResult.svelte';
    import {getApiHost} from 'store/instanceStore.js';

    // request(path, options) is the owner page helper rooted at /instance.
    export let request;
    export let checks = [];
    export let active = false;

    const originLabels = {manual: 'Manuale', scheduled: 'Pianificato', 'pre-upgrade': 'Prima di un aggiornamento',
        'pre-restore': 'Prima di un ripristino', unknown: 'Non registrata'};
    const locationLabels = {local: 'Solo locale', both: 'Locale + remoto', remote: 'Solo remoto'};
    const segments = [['database', 'Database'], ['objects', 'Documenti e file'], ['backups', 'Backup'], ['other', 'Altro'], ['free', 'Libero']];

    let overview;
    let loading = false;
    let error = '';
    let downloading = null;
    let password = '';
    let downloadError = '';
    let requestingToken = false;
    let passwordInput;

    $: disk = overview?.disk;
    $: parts = disk ? diskParts(disk) : [];
    $: marker = disk && overview?.next_backup && disk.total > 0
        ? Math.min(100, (disk.used + overview.next_backup.estimated) / disk.total * 100) : null;

    function formatSize(bytes) {
        if (bytes === null || bytes === undefined) return 'Non disponibile';
        const unit = bytes >= 1024 ** 3 ? 'GB' : bytes >= 1024 ** 2 ? 'MB' : 'KB';
        const divisor = unit === 'GB' ? 1024 ** 3 : unit === 'MB' ? 1024 ** 2 : 1024;
        return `${new Intl.NumberFormat('it-IT', {maximumFractionDigits: unit === 'GB' ? 1 : 0}).format(bytes / divisor)} ${unit}`;
    }

    function formatDate(value) {
        return value ? new Date(value).toLocaleString('it-IT', {dateStyle: 'medium', timeStyle: 'short'}) : 'Data non disponibile';
    }

    // The data and the backups usually share the disk; anything else in use is "Altro".
    function diskParts(value) {
        const known = (value.database || 0) + (value.objects || 0) + (value.backups || 0);
        const sizes = {database: value.database || 0, objects: value.objects || 0, backups: value.backups || 0,
            other: Math.max(0, value.used - known), free: value.free};
        return segments.map(([key, label]) => ({key, label, bytes: sizes[key],
            width: value.total > 0 ? sizes[key] / value.total * 100 : 0,
            unknown: ['database', 'objects'].includes(key) && value[key] === null}));
    }

    async function load(refresh = false) {
        loading = true;
        try {
            overview = await request(`/admin/backups${refresh ? '?refresh=1' : ''}`);
            error = '';
        } catch (e) {
            error = e.message;
        } finally {
            loading = false;
        }
    }

    function askDownload(archive) {
        downloading = archive;
        password = '';
        downloadError = '';
        setTimeout(() => passwordInput?.focus());
    }

    async function confirmDownload() {
        if (!downloading || !password || requestingToken) return;
        requestingToken = true;
        downloadError = '';
        try {
            const result = await request('/admin/backups/download-token', {method: 'POST', body: JSON.stringify({name: downloading.name, password})});
            const link = document.createElement('a');
            link.href = `${getApiHost()}/instance/admin/backups/${encodeURIComponent(downloading.name)}/download?download_token=${encodeURIComponent(result.download_token)}`;
            link.download = downloading.name;
            link.rel = 'noopener noreferrer';
            document.body.appendChild(link);
            link.click();
            link.remove();
            downloading = null;
        } catch (e) {
            downloadError = e.message;
        } finally {
            password = '';
            requestingToken = false;
        }
    }

    onMount(() => load());
</script>

<section aria-labelledby="instance-backups-heading" class="backup-page">
    <div class="backups-heading">
        <h2 id="instance-backups-heading">Backup e ripristino</h2>
        <button class="btn btn-light" disabled={loading} on:click={() => load(true)}>{loading ? 'Aggiornamento…' : 'Aggiorna'}</button>
    </div>
    <p>Ogni backup contiene l’intero server: dati, documenti, utenti, impostazioni e chiavi segrete. Per spostare i dati di un’associazione usa invece l’esportazione in Gestione Dati.</p>

    {#if error}<InstanceAlert tone="warning" role="alert">{error}</InstanceAlert>{/if}
    {#if loading && !overview}<p role="status">Caricamento dei backup…</p>{/if}
    {#if overview && !overview.available}<InstanceAlert tone="warning">{overview.reason}</InstanceAlert>{/if}

    {#if overview?.available}
        <h3 class="mt-6">Spazio su disco</h3>
        <div class="disk-bar" role="img" aria-label={`Disco del server: ${formatSize(disk.used)} usati su ${formatSize(disk.total)}, ${formatSize(disk.free)} liberi`}>
            {#each parts as part (part.key)}
                {#if part.width > 0}<span class="segment seg-{part.key}" style={`width:${part.width}%`}></span>{/if}
            {/each}
            {#if marker !== null}<span class="marker" class:overflow={!overview.next_backup.fits} style={`left:${marker}%`}></span>{/if}
        </div>
        <ul class="disk-legend">
            {#each parts as part (part.key)}
                <li><span class="swatch seg-{part.key}" aria-hidden="true"></span>{part.label}: <strong>{part.unknown ? 'Non disponibile' : formatSize(part.bytes)}</strong></li>
            {/each}
            {#if marker !== null}<li><span class="swatch marker-swatch" aria-hidden="true"></span>Dopo il prossimo backup: circa <strong>{formatSize(overview.next_backup.estimated)}</strong> in più</li>{/if}
        </ul>
        {#if overview.next_backup && !overview.next_backup.fits}
            <InstanceAlert tone="danger" role="alert">
                Il prossimo backup potrebbe non entrare nel disco: servono circa {formatSize(overview.next_backup.required)} liberi, disponibili {formatSize(disk.free)}. Libera spazio sul server, per esempio copiando altrove ed eliminando i backup manuali meno recenti.
            </InstanceAlert>
        {:else if !overview.next_backup}
            <p class="text-muted">Non è stato possibile stimare la dimensione del prossimo backup.</p>
        {/if}

        <h3 class="mt-6">Backup sul server</h3>
        <p>
            {#if overview.retention?.automatic}
                La rotazione conserva gli ultimi {overview.retention.automatic} backup automatici.
            {:else if overview.retention?.automatic === 0}
                La rotazione automatica è disattivata.
            {/if}
            I backup manuali, quelli conservati e quello dell’ultimo aggiornamento riuscito non vengono mai eliminati automaticamente.
        </p>
        {#if overview.archives.length}
            <div class="table-responsive">
                <table class="table backups-table">
                    <thead><tr><th scope="col">Data</th><th scope="col">Dimensione</th><th scope="col">Versione</th><th scope="col">Origine</th><th scope="col">Posizione</th><th scope="col"><span class="sr-only">Azioni</span></th></tr></thead>
                    <tbody>
                        {#each overview.archives as archive (archive.name)}
                            <tr>
                                <td>
                                    {formatDate(archive.created_at)}
                                    <div class="badges">
                                        {#if archive.keep}<span class="label label-inline label-light-primary">Conservato</span>{/if}
                                        {#if archive.last_upgrade}<span class="label label-inline label-light-success">Ultimo aggiornamento riuscito</span>{/if}
                                        {#if archive.pending_upgrade}<span class="label label-inline label-light-warning">Aggiornamento in sospeso</span>{/if}
                                    </div>
                                </td>
                                <td>{formatSize(archive.size)}</td>
                                <td>{archive.version || '—'}</td>
                                <td>{originLabels[archive.origin] || originLabels.unknown}</td>
                                <td>{locationLabels[archive.location] || archive.location}</td>
                                <td class="actions"><button class="btn btn-sm btn-light-primary" disabled={active || requestingToken} on:click={() => askDownload(archive)} aria-label={`Scarica il backup del ${formatDate(archive.created_at)}`}>Scarica</button></td>
                            </tr>
                        {/each}
                    </tbody>
                </table>
            </div>
        {:else}
            <p class="text-muted">Nessun backup nella cartella standard del server.</p>
        {/if}

        {#if downloading}
            <form class="download-panel" on:submit|preventDefault={confirmDownload} aria-labelledby="backup-download-heading">
                <h4 id="backup-download-heading">Scarica il backup del {formatDate(downloading.created_at)}</h4>
                <InstanceAlert tone="warning" role="note">
                    Il file non è cifrato e contiene tutti i dati e le chiavi segrete del server ({formatSize(downloading.size)}). Conservalo in un luogo protetto e non condividerlo. Il download viene registrato nel registro attività.
                </InstanceAlert>
                <label for="backup-download-password">Conferma la tua password</label>
                <input id="backup-download-password" class="form-control" type="password" autocomplete="current-password" bind:this={passwordInput} bind:value={password} disabled={requestingToken} />
                {#if downloadError}<p class="text-danger mt-2" role="alert">{downloadError}</p>{/if}
                <div class="download-actions">
                    <button class="btn btn-primary" type="submit" disabled={!password || requestingToken}>{requestingToken ? 'Verifica…' : 'Scarica backup'}</button>
                    <button class="btn btn-light" type="button" disabled={requestingToken} on:click={() => downloading = null}>Annulla</button>
                </div>
            </form>
        {/if}
    {/if}

    <h3 class="mt-8">Stato e recupero</h3>
    {#each checks as check (check.id)}<div class="mb-3"><DiagnosticResult {check} /></div>{/each}
    <p>Un backup viene creato prima di ogni aggiornamento. La presenza di un archivio non dimostra che possa essere ripristinato.</p>
    <p>Dalla cartella selfhost dell’installazione, <code>./bin/assozeta backup</code> crea un backup coerente; l’applicazione viene fermata temporaneamente.</p>
    <p>Conserva una copia protetta fuori dal server. Prova il ripristino su un’installazione isolata seguendo selfhost/UPDATES.md prima di un aggiornamento importante.</p>
    <p>Se un aggiornamento richiede recupero, segui le istruzioni mostrate nello stato operazione. Il comando <code>./bin/assozeta recover-upgrade PERCORSO_BACKUP</code> ripristina versione, configurazione e dati; richiede l’accesso al server.</p>
</section>

<style>
    .backups-heading { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: .75rem; }
    .backups-heading h2 { margin: 0; }
    h3 { font-size: 1.1rem; font-weight: 600; }
    .text-muted { color: #536171 !important; }
    .disk-bar { position: relative; display: flex; height: 1.25rem; margin: .75rem 0; border-radius: .5rem; overflow: visible; background: #eef1f5; border: 1px solid #dce1e7; }
    .segment { height: 100%; }
    .segment:first-child { border-radius: .5rem 0 0 .5rem; }
    .segment:last-of-type { border-radius: 0 .5rem .5rem 0; }
    .seg-database { background: #3524cd; }
    .seg-objects { background: #2d8a9e; }
    .seg-backups { background: #b5651d; }
    .seg-other { background: #8a93a6; }
    .seg-free { background: #eef1f5; }
    .marker { position: absolute; top: -.35rem; bottom: -.35rem; width: 3px; margin-left: -1.5px; background: #202238; border-radius: 2px; }
    .marker.overflow { background: #c62828; }
    .disk-legend { display: flex; flex-wrap: wrap; gap: .5rem 1.25rem; list-style: none; padding: 0; margin: 0 0 1rem; font-size: .9rem; }
    .swatch { display: inline-block; width: .8rem; height: .8rem; margin-right: .4rem; border-radius: .2rem; border: 1px solid #dce1e7; vertical-align: -.05rem; }
    .marker-swatch { width: 3px; background: #202238; border: 0; }
    .backups-table td { vertical-align: middle; }
    .badges { display: flex; flex-wrap: wrap; gap: .35rem; margin-top: .35rem; }
    .badges .label { height: auto; min-height: 1.5rem; padding: .2rem .55rem; line-height: 1.3; white-space: normal; text-align: left; }
    .actions { text-align: right; white-space: nowrap; }
    .download-panel { margin-top: 1rem; padding: 1.25rem; border: 1px solid var(--primary); border-radius: .75rem; max-width: 36rem; }
    .download-panel h4 { font-size: 1rem; font-weight: 600; }
    .download-actions { display: flex; flex-wrap: wrap; gap: .75rem; margin-top: 1rem; }
    .sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
    @media (max-width: 575px) { .download-panel { padding: 1rem; } }
</style>
