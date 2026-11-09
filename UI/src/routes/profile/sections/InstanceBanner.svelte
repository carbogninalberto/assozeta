<script>
    // The single global banner of the instance page. It appears only for states
    // that block or need the owner: an operation running, the app unreachable,
    // a recovery to perform, or an operation that just finished.
    import {CheckCircle, WarningCircle, XCircle, SpinnerGap, X} from 'phosphor-svelte';
    import {operationTitle, operationProgress, recentOutcome} from './operationLabels.js';

    export let runner = {active: null, history: []};
    export let simulation = null;
    export let reconnecting = false;
    export let apiUnavailable = false;
    export let restarting = false;
    export let restartTimedOut = false;
    export let onRetryRestart = () => {};
    export let error = '';
    export let onDismissError = () => {};

    let dismissed = null;

    $: active = simulation || runner.active;
    $: progress = active ? operationProgress(active) : null;
    $: recovery = !active && (runner.history || []).find(operation => operation.stage === 'recovery_required');
    $: outcome = !active && !recovery ? recentOutcome(runner.history) : null;
    $: showOutcome = outcome && dismissed !== outcome.id;
</script>

<section class="si-banners" aria-label="Stato operazioni" aria-live="polite">
    {#if error}
        <div class="si-banner bad" role="alert">
            <span class="si-banner-icon"><XCircle size={26} weight="duotone" /></span>
            <div class="si-banner-text"><p>{error}</p></div>
            <button type="button" class="si-banner-dismiss" aria-label="Chiudi" on:click={onDismissError}><X size={18} weight="bold" /></button>
        </div>
    {/if}
    {#if restarting}
        <div class="si-banner {restartTimedOut ? 'warn' : 'info'}" role={restartTimedOut ? 'alert' : 'status'}>
            <span class="si-banner-icon">{#if restartTimedOut}<WarningCircle size={26} weight="duotone" />{:else}<span class="si-spin"><SpinnerGap size={24} weight="bold" /></span>{/if}</span>
            <div class="si-banner-text">
                {#if restartTimedOut}
                    <strong>Riavvio non confermato</strong>
                    <p>Dopo 10 minuti i servizi non rispondono. Controlla il server.</p>
                {:else}
                    <strong>Riavvio in corso</strong>
                    <p>La pagina si ricarica da sola quando è pronta.</p>
                {/if}
            </div>
            {#if restartTimedOut}<button type="button" class="btn btn-sm btn-light" on:click={onRetryRestart}>Verifica di nuovo</button>{/if}
        </div>
    {:else if active}
        <div class="si-banner info" role="status">
            <span class="si-banner-icon"><span class="si-spin"><SpinnerGap size={24} weight="bold" /></span></span>
            <div class="si-banner-text">
                <strong>{active.simulated ? 'Simulazione · ' : ''}{operationTitle(active)}</strong>
                <p>{progress.label}{#if progress.total}{' · '}passo {progress.step} di {progress.total}{/if}</p>
                {#if progress.total}<div class="si-progress" role="progressbar" aria-label="Avanzamento" aria-valuemin="0" aria-valuemax={progress.total} aria-valuenow={progress.step}><span style={`width:${progress.step / progress.total * 100}%`}></span></div>{/if}
                {#if active.error}<p class="si-banner-detail">{active.error}</p>{/if}
            </div>
        </div>
    {:else if recovery}
        <div class="si-banner warn" role="alert">
            <span class="si-banner-icon"><WarningCircle size={26} weight="duotone" /></span>
            <div class="si-banner-text">
                <strong>Ripristino necessario · {recovery.target_version}</strong>
                {#if recovery.recovery}<p>{recovery.recovery}</p>{/if}
            </div>
        </div>
    {:else if showOutcome}
        <div class="si-banner {outcome.ok ? 'ok' : 'bad'}" role="status">
            <span class="si-banner-icon">{#if outcome.ok}<CheckCircle size={26} weight="duotone" />{:else}<XCircle size={26} weight="duotone" />{/if}</span>
            <div class="si-banner-text">
                <strong>{outcome.title}</strong>
                {#if outcome.detail}<p>{outcome.detail}</p>{/if}
            </div>
            <button type="button" class="si-banner-dismiss" aria-label="Chiudi" on:click={() => dismissed = outcome.id}><X size={18} weight="bold" /></button>
        </div>
    {/if}
    {#if apiUnavailable || reconnecting}
        <div class="si-banner neutral" role="status">
            <span class="si-banner-icon"><span class="si-spin"><SpinnerGap size={24} weight="bold" /></span></span>
            <div class="si-banner-text">
                <strong>{apiUnavailable ? 'Applicazione non raggiungibile' : 'Servizio di aggiornamento non raggiungibile'}</strong>
                <p>Riprovo in automatico. Le impostazioni tornano modificabili appena possibile.</p>
            </div>
        </div>
    {/if}
</section>

<style>
    /* Same shape and colours as the app's alert-custom alerts. */
    .si-banners { display: flex; flex-direction: column; gap: 1rem; }
    .si-banners:empty { display: none; }
    .si-banner { display: flex; align-items: center; gap: 1rem; padding: 1.25rem 1.5rem; border-radius: .65rem; overflow-wrap: anywhere; font-size: 1.08rem; }
    .si-banner.info { background: var(--light-primary); }
    .si-banner.ok { background: var(--si-success-bg); }
    .si-banner.warn { background: var(--si-warning-bg); }
    .si-banner.bad { background: var(--si-danger-bg); }
    .si-banner.neutral { background: var(--bg-hover); }
    .si-banner-icon { display: inline-flex; flex-shrink: 0; }
    .info .si-banner-icon { color: var(--primary-text, var(--primary)); }
    .ok .si-banner-icon { color: var(--si-success); }
    .warn .si-banner-icon { color: var(--si-warning); }
    .bad .si-banner-icon { color: var(--si-danger); }
    .neutral .si-banner-icon { color: var(--text-secondary); }
    .si-banner-text { flex: 1; min-width: 0; }
    .si-banner-text strong { display: block; font-size: 1.15rem; font-weight: 600; }
    .si-banner-text p { margin: .25rem 0 0; }
    .si-banner-text p:first-child { margin-top: 0; }
    .si-banner-detail { color: var(--text-secondary); }
    .si-banner-dismiss { display: inline-flex; align-self: center; padding: .4rem; border: 0; border-radius: .42rem; background: none; color: var(--text-secondary); }
    .si-banner-dismiss:hover { background: rgba(0, 0, 0, .06); color: var(--text-primary); }
    .si-progress { height: .45rem; margin-top: .75rem; border-radius: 999px; background: rgba(0, 0, 0, .08); overflow: hidden; }
    .si-progress span { display: block; height: 100%; background: var(--primary); border-radius: inherit; transition: width .4s ease; }
    .si-spin { display: inline-flex; animation: si-spin 1s linear infinite; }
    @keyframes si-spin { to { transform: rotate(360deg); } }
    @media (prefers-reduced-motion: reduce) { .si-spin { animation: none; } .si-progress span { transition: none; } }
    @media (max-width: 575px) { .si-banner { padding: 1rem 1.25rem; } }
</style>
