<script>
    // The instance runtime (off: Django, on: Go). One switch moves the whole
    // instance: API, background work and realtime delivery. The switch never
    // saves on its own: it asks for the password, then shows the server's
    // operation until the server reports how it ended.
    import {onMount, onDestroy} from 'svelte';
    import Switch from '../../../components/inputs/Switch.svelte';
    import InstanceDialog from './InstanceDialog.svelte';
    import {getApiHost} from 'store/instanceStore.js';
    import {createRuntimeStore, switchRunning} from 'store/runtimeSwitchStore.js';
    import {RUNTIME_STAGES, runtimeName, lastCompletedStage} from './operationLabels.js';
    import {formatDate} from './instanceFormat.js';

    // Why the owner cannot switch now (another operation, app unreachable).
    export let blockedReason = '';

    const runtime = createRuntimeStore({
        fetcher: (...args) => window.fetch(...args),
        readToken: () => { try { return JSON.parse(localStorage.getItem('sessionToken')); } catch { return null; } },
        endpoint: `${getApiHost()}/instance/admin/runtime`,
    });

    const phases = {
        submitting: ['info', 'Invio della richiesta…'], in_progress: ['info', 'In corso'], blocked: ['warn', 'Bloccato'],
        failed: ['bad', 'Non riuscito'], recovery_required: ['warn', 'Ripristino necessario'], completed: ['ok', 'Completato'],
    };
    const counts = [['staged', 'In attesa'], ['transferred', 'Trasferite'], ['blocked', 'Bloccate'], ['ambiguous', 'Esito incerto']];
    const actions = {retry: 'Riprova', recover: 'Ripristina', reconcile: 'Riconcilia'};

    let dialog = null; // {target}
    let password = '';
    let dialogError = '';
    let busy = false;
    let shown = false;

    $: ({phase, status, error, reachable, denied} = $runtime);
    $: op = status?.active || status?.operation || null;
    $: running = switchRunning(status);
    $: failed = ['failed', 'recovery_required'].includes(op?.status);
    $: progress = op?.progress;
    $: stage = failed ? op.failed_stage : progress?.stage || op?.stage;
    $: completedStage = failed ? lastCompletedStage(op) : null;
    $: dispositions = op?.dispositions || {};
    $: showCounts = !!op?.blockers?.count || counts.some(([key]) => dispositions[key] > 0);
    $: deadline = running && progress?.maintenance_deadline ? formatDate(progress.maintenance_deadline) : '';
    $: tone = phases[phase] || (running ? phases.in_progress : null);
    $: statusLabel = op?.status === 'queued' && phase === 'in_progress' ? 'In coda' : tone?.[1];
    $: effective = status?.effective_runtime === 'go';
    // The controller admits a new switch only after an operation that needs
    // recovery has been cleared on the server.
    $: recovering = status?.active?.status === 'recovery_required';
    $: reason = !status ? '' : !status.switch_available ? status.unavailable_reason || 'Cambio non disponibile.'
        : running ? 'C’è un cambio in corso.' : recovering ? 'Completa prima il ripristino indicato qui sotto.'
        : !reachable ? 'App non raggiungibile.' : blockedReason;
    $: locked = !status || !status.switch_available || !!status.active || phase === 'submitting' || !reachable || !!blockedReason;
    // A value reloaded from the server replaces the switch unless a confirmation is open.
    $: if (!dialog && !busy) shown = effective;

    function formatAge(seconds) {
        if (seconds < 120) return seconds === 1 ? '1 secondo' : `${seconds} secondi`;
        if (seconds < 7200) return `${Math.round(seconds / 60)} minuti`;
        if (seconds < 172800) return `${Math.round(seconds / 3600)} ore`;
        return `${Math.round(seconds / 86400)} giorni`;
    }

    function reasonLabel(reason) {
        return {ambiguous_effect: 'esito incerto', unsupported: 'non trasferibili'}[reason] || reason.replaceAll('_', ' ');
    }

    function toggle() {
        // Revert at once: nothing changes until the password is confirmed.
        shown = effective;
        if (locked) return;
        password = '';
        dialogError = '';
        dialog = {target: effective ? 'django' : 'go'};
    }

    function close() {
        if (busy) return;
        dialog = null;
        password = '';
        dialogError = '';
    }

    async function confirm() {
        if (!dialog || !password || busy) return;
        busy = true;
        dialogError = '';
        try {
            const result = await runtime.submit(dialog.target, password);
            password = '';
            if (result.done) dialog = null;
            else dialogError = result.error || 'Cambio non avviato.';
        } finally { busy = false; }
    }

    onMount(() => { runtime.refresh(); runtime.start(); });
    onDestroy(() => runtime.stop());
</script>

<section class="si-card" aria-labelledby="instance-runtime-heading">
    <div class="si-card-head">
        <div>
            <h2 id="instance-runtime-heading" class="si-title">Runtime dell’istanza</h2>
            <p class="si-muted si-small">Django è il runtime predefinito, Go la nuova implementazione. Il cambio vale per tutta l’istanza: API, attività in background e notifiche in tempo reale. Durante il cambio l’app resta in manutenzione per alcuni minuti.</p>
        </div>
    </div>
    <Switch id="instance-runtime-go" name="instance-runtime-go" label="Usa Go per l’istanza" bind:checked={shown} disabled={locked || busy} on:change={toggle} />
    {#if reason}<p class="si-hint">{reason}</p>{/if}
    {#if status}
        <dl class="details">
            <div><dt>Runtime effettivo</dt><dd data-testid="runtime-effective">{runtimeName(status.effective_runtime)}</dd></div>
            <div><dt>Runtime previsto</dt><dd data-testid="runtime-intended">{runtimeName(status.intended_runtime || status.effective_runtime)}</dd></div>
        </dl>
    {:else if denied}
        <p class="text-danger" role="alert">Accesso non autorizzato. Accedi di nuovo come proprietario dell’istanza.</p>
    {:else}
        <span class="si-skeleton" style="width: 60%"></span>
    {/if}
    <p class="si-hint mb-0">Le attività in background seguono il runtime effettivo.</p>
    {#if error}<p class="text-danger mt-3 mb-0" role="alert">{error}</p>{/if}

    {#if op}
        <div class="operation">
            <h3 class="si-subtitle">{running ? 'Cambio in corso' : 'Ultimo cambio'} · {runtimeName(op.previous_runtime)} → {runtimeName(op.requested_runtime)}</h3>
            <p class="live" role="status">
                {#if tone}<span class="si-pill {tone[0]}" data-testid="runtime-status">{statusLabel}</span>{/if}
                {#if stage && op.status !== 'succeeded'}
                    <span><span class="si-sr-only">{failed ? 'Fase non riuscita:' : 'Fase:'}</span> <span data-testid="runtime-stage">{RUNTIME_STAGES[stage] || stage}</span></span>
                    {#if progress?.total}<span class="si-muted" data-testid="runtime-progress">passo {Math.min(progress.index, progress.total)} di {progress.total}</span>{/if}
                {/if}
            </p>
            <dl class="details">
                <div><dt>Operazione</dt><dd class="mono" data-testid="runtime-operation">{op.id}</dd></div>
                {#if failed}<div><dt>Ultima fase completata</dt><dd data-testid="runtime-last-completed">{completedStage ? RUNTIME_STAGES[completedStage] : 'Nessuna'}</dd></div>{/if}
                {#if deadline}<div><dt>Manutenzione prevista fino a</dt><dd>{deadline}</dd></div>{/if}
            </dl>
            {#if showCounts}
                <dl class="details counts">
                    {#each counts as [key, label]}
                        <div><dt>{label}</dt><dd data-testid={`runtime-${key}`}>{dispositions[key] ?? 0}</dd></div>
                    {/each}
                    {#if op.blockers?.oldest_age_seconds != null}
                        <div><dt>Blocco più vecchio</dt><dd data-testid="runtime-oldest">{formatAge(op.blockers.oldest_age_seconds)}</dd></div>
                    {/if}
                </dl>
                {#if Object.keys(op.blockers?.by_reason || {}).length}
                    <p class="si-hint">Bloccate: {Object.entries(op.blockers.by_reason).map(([key, count]) => `${count} ${reasonLabel(key)}`).join(', ')}.</p>
                {/if}
            {/if}
            {#if failed && op.error}<p class="text-danger mb-2">{op.error}</p>{/if}
            {#if actions[op.recovery_action]}
                <p class="mb-0" data-testid="runtime-recovery">
                    <strong>{actions[op.recovery_action]}</strong>:
                    {#if op.recovery_action === 'reconcile'}{op.blockers?.action || 'Verifica le attività bloccate prima di riprovare.'}
                    {:else if op.recovery_action === 'recover'}sul server esegui <code>{op.recovery || './bin/assozeta runtime-recover'}</code>. L’interruttore torna disponibile quando il ripristino è concluso.
                    {:else}{runtimeName(status.effective_runtime)} è ancora attivo. Usa l’interruttore per richiedere di nuovo il cambio.{/if}
                </p>
            {/if}
        </div>
    {/if}
</section>

<InstanceDialog open={!!dialog} title={dialog?.target === 'go' ? 'Passare a Go?' : 'Tornare a Django?'} locked={busy} on:close={close}>
    <p>L’app va in manutenzione per alcuni minuti, poi riprende su {runtimeName(dialog?.target)}. Puoi chiudere questa pagina: il cambio continua sul server.</p>
    <form id="instance-runtime-form" on:submit|preventDefault={confirm}>
        <label class="si-field" for="instance-runtime-password">Conferma la tua password
            <input id="instance-runtime-password" class="form-control" type="password" autocomplete="current-password" bind:value={password} disabled={busy} />
        </label>
        {#if dialogError}<p class="text-danger mt-2 mb-0" role="alert">{dialogError}</p>{/if}
    </form>
    <svelte:fragment slot="footer">
        <button type="button" class="btn btn-light" disabled={busy} on:click={close}>Annulla</button>
        <button type="submit" form="instance-runtime-form" class="btn btn-primary si-btn" disabled={!password || busy}>{busy ? 'Verifica…' : 'Conferma cambio'}</button>
    </svelte:fragment>
</InstanceDialog>

<style>
    .details { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 12rem), 1fr)); gap: .75rem 1.5rem; margin: 0 0 1rem; }
    .counts { grid-template-columns: repeat(auto-fit, minmax(min(100%, 8rem), 1fr)); }
    dt { font-size: .925rem; font-weight: 500; color: var(--text-secondary); }
    dd { margin: .15rem 0 0; font-weight: 600; overflow-wrap: anywhere; }
    .mono { font-family: var(--font-family-monospace, monospace); font-weight: 500; }
    .operation { margin-top: 1.5rem; padding-top: 1.5rem; border-top: 1px solid var(--border-color); }
    .operation h3 { margin-bottom: .75rem; }
    .live { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem .75rem; margin: 0 0 1rem; }
    .live:empty { display: none; }
    code { overflow-wrap: anywhere; }
</style>
