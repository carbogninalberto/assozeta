<script>
    // The single global banner of the instance page. It appears only for states
    // that block or need the owner: an operation running, the app unreachable,
    // a recovery to perform, or an operation that just finished.
    import {CheckCircle, WarningCircle, XCircle, SpinnerGap} from 'phosphor-svelte';
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
            <span class="si-banner-icon"><XCircle size={20} weight="fill" /></span>
            <div class="si-banner-text"><p>{error}</p></div>
            <button type="button" class="si-banner-dismiss" on:click={onDismissError}>Chiudi</button>
        </div>
    {/if}
    {#if restarting}
        <div class="si-banner {restartTimedOut ? 'warn' : 'info'}" role={restartTimedOut ? 'alert' : 'status'}>
            <span class="si-banner-icon">{#if restartTimedOut}<WarningCircle size={20} weight="fill" />{:else}<span class="si-spin"><SpinnerGap size={20} weight="bold" /></span>{/if}</span>
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
            <span class="si-banner-icon"><span class="si-spin"><SpinnerGap size={20} weight="bold" /></span></span>
            <div class="si-banner-text">
                <strong>{active.simulated ? 'Simulazione · ' : ''}{operationTitle(active)}</strong>
                <p>{progress.label}{#if progress.total}{' · '}passo {progress.step} di {progress.total}{/if}</p>
                {#if progress.total}<div class="si-progress" role="progressbar" aria-label="Avanzamento" aria-valuemin="0" aria-valuemax={progress.total} aria-valuenow={progress.step}><span style={`width:${progress.step / progress.total * 100}%`}></span></div>{/if}
                {#if active.error}<p class="si-banner-detail">{active.error}</p>{/if}
            </div>
        </div>
    {:else if recovery}
        <div class="si-banner warn" role="alert">
            <span class="si-banner-icon"><WarningCircle size={20} weight="fill" /></span>
            <div class="si-banner-text">
                <strong>Ripristino necessario · {recovery.target_version}</strong>
                {#if recovery.recovery}<p>{recovery.recovery}</p>{/if}
            </div>
        </div>
    {:else if showOutcome}
        <div class="si-banner {outcome.ok ? 'ok' : 'bad'}" role="status">
            <span class="si-banner-icon">{#if outcome.ok}<CheckCircle size={20} weight="fill" />{:else}<XCircle size={20} weight="fill" />{/if}</span>
            <div class="si-banner-text">
                <strong>{outcome.title}</strong>
                {#if outcome.detail}<p>{outcome.detail}</p>{/if}
            </div>
            <button type="button" class="si-banner-dismiss" on:click={() => dismissed = outcome.id}>Chiudi</button>
        </div>
    {/if}
    {#if apiUnavailable || reconnecting}
        <div class="si-banner neutral" role="status">
            <span class="si-banner-icon"><span class="si-spin"><SpinnerGap size={20} weight="bold" /></span></span>
            <div class="si-banner-text">
                <strong>{apiUnavailable ? 'Applicazione non raggiungibile' : 'Servizio di aggiornamento non raggiungibile'}</strong>
                <p>Riprovo in automatico. Le impostazioni tornano modificabili appena possibile.</p>
            </div>
        </div>
    {/if}
</section>

<style>
    .si-banners { display: flex; flex-direction: column; gap: .75rem; }
    .si-banners:empty { display: none; }
    .si-banner { display: flex; align-items: flex-start; gap: .75rem; padding: .875rem 1rem; border-radius: .75rem; border: 1px solid transparent; overflow-wrap: anywhere; }
    .si-banner.info { background: var(--light-primary); border-color: var(--primary-border, transparent); }
    .si-banner.ok { background: var(--light-success); }
    .si-banner.warn { background: var(--light-warning); }
    .si-banner.bad { background: var(--light-danger); }
    .si-banner.neutral { background: var(--bg-hover); }
    .si-banner-icon { display: inline-flex; flex-shrink: 0; margin-top: .05rem; }
    .info .si-banner-icon { color: var(--primary-text, var(--primary)); }
    .ok .si-banner-icon { color: #12a37a; }
    .warn .si-banner-icon { color: #c27c00; }
    .bad .si-banner-icon { color: var(--danger, #d92d20); }
    .neutral .si-banner-icon { color: var(--text-secondary); }
    .si-banner-text { flex: 1; min-width: 0; }
    .si-banner-text p { margin: .15rem 0 0; }
    .si-banner-detail { color: var(--text-secondary); }
    .si-banner-dismiss { border: 0; background: none; padding: .1rem .25rem; font-weight: 600; color: var(--text-secondary); }
    .si-banner-dismiss:hover { color: var(--text-primary); }
    .si-progress { height: .375rem; margin-top: .6rem; border-radius: 999px; background: rgba(0, 0, 0, .08); overflow: hidden; }
    .si-progress span { display: block; height: 100%; background: var(--primary); border-radius: inherit; transition: width .4s ease; }
    .si-spin { display: inline-flex; animation: si-spin 1s linear infinite; }
    @keyframes si-spin { to { transform: rotate(360deg); } }
    @media (prefers-reduced-motion: reduce) { .si-spin { animation: none; } .si-progress span { transition: none; } }
</style>
