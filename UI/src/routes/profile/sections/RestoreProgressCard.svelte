<script>
    // Live progress of a Bakney ZIP import: current phase, percent, estimate.
    import {Check} from 'phosphor-svelte';
    export let operation;
    export let progress = null;
    export let now = Date.now();
    export let live = false;
    const steps = [
        {id: 'backup', label: 'Copia di sicurezza'},
        {id: 'files', label: 'Allegati'},
        {id: 'restoring', label: 'Dati'},
        {id: 'finalizing', label: 'Verifica finale'},
    ];
    const labels = {queued: 'In attesa del worker', validating: 'Verifica del file', backup: 'Creazione della copia di sicurezza',
        files: 'Trasferimento degli allegati', restoring: 'Importazione dei dati', finalizing: 'Verifica finale'};
    const models = {Associate: 'Anagrafiche', User: 'Utenti', Document: 'Documenti', Subscription: 'Iscrizioni',
        MedicalCertificate: 'Certificati medici', Invoice: 'Fatture', Payment: 'Pagamenti', Course: 'Corsi', Family: 'Famiglie'};
    $: phase = progress?.phase || operation.stage;
    $: index = steps.findIndex(step => step.id === phase);
    $: stale = progress && now - Date.parse(progress.updated_at) > 20000;
    $: percent = progress?.percent == null || stale ? null : Math.max(0, Math.min(100, progress.percent));
    $: eta = !stale && progress?.eta_seconds != null ? duration(progress.eta_seconds) : null;
    $: counted = progress?.total ? `${amount(progress.completed || 0, progress.unit)} di ${amount(progress.total, progress.unit)}` : '';
    $: detail = progress?.detail ? models[progress.detail] || progress.detail : '';
    function duration(seconds) { return seconds < 60 ? 'Meno di un minuto' : `Circa ${Math.ceil(seconds / 60)} min`; }
    function amount(value, unit) {
        if (unit === 'bytes') return `${(value / 1024 ** 2).toLocaleString('it-IT', {maximumFractionDigits: 1})} MB`;
        return `${new Intl.NumberFormat('it-IT').format(value)}${unit === 'records' ? ' record' : ''}`;
    }
</script>

<div class="restore-progress">
    <div class="head">
        <h3 class="si-subtitle">{labels[phase] || 'Ripristino in corso'}</h3>
        <span class="si-muted si-small">{percent == null ? 'In corso' : `${percent}%`}</span>
    </div>
    <div class="track" role="progressbar" aria-label="Avanzamento della fase di ripristino" aria-valuemin="0" aria-valuemax="100"
        aria-valuenow={percent ?? undefined} aria-valuetext={percent == null ? 'In corso' : `${percent}%`}>
        <span class:indeterminate={percent == null} style:width={percent == null ? '30%' : `${percent}%`}></span>
    </div>
    <p class="si-muted si-small meta">
        {#if eta}<span>{eta}</span>{:else if stale}<span>In attesa di aggiornamenti</span>{/if}
        {#if counted}<span>{counted}</span>{/if}
        {#if detail}<span>{detail}</span>{/if}
        {#if !eta && !stale && !counted && !detail}<span>{live ? 'Aggiornamento live' : 'Stato sincronizzato'}</span>{/if}
    </p>
    <ol class="phases">
        {#each steps as step, i}
            <li class:current={i === index} class:done={index > i} aria-current={i === index ? 'step' : undefined}>
                <span class="dot" aria-hidden="true">{#if index > i}<Check size={12} weight="bold" />{/if}</span>{step.label}
            </li>
        {/each}
    </ol>
</div>

<style>
    .restore-progress { display: grid; gap: .625rem; }
    .head { display: flex; align-items: baseline; justify-content: space-between; gap: .75rem; }
    .track { height: .5rem; border-radius: 999px; background: var(--bg-active); overflow: hidden; }
    .track span { display: block; height: 100%; border-radius: inherit; background: var(--primary); transition: width .5s; }
    .indeterminate { animation: drift 2s ease-in-out infinite; }
    .meta { display: flex; flex-wrap: wrap; gap: .25rem .75rem; margin: 0; }
    .meta span + span::before { content: '·'; margin-right: .75rem; }
    .phases { display: flex; flex-wrap: wrap; gap: .375rem 1rem; list-style: none; margin: .25rem 0 0; padding: 0; font-size: .875rem; color: var(--text-muted); }
    .phases li { display: inline-flex; align-items: center; gap: .4rem; }
    .dot { display: inline-grid; place-items: center; width: 1rem; height: 1rem; border-radius: 50%; border: 1.5px solid currentColor; }
    .current { color: var(--primary-text, var(--primary)); font-weight: 600; }
    .current .dot { background: var(--primary); border-color: var(--primary); }
    .done { color: var(--text-secondary); }
    .done .dot { background: var(--light-success); border-color: transparent; color: #0f6b4f; }
    @keyframes drift { 0%, 100% { transform: translateX(0); } 50% { transform: translateX(230%); } }
    @media (prefers-reduced-motion: reduce) { .indeterminate { animation: none; } .track span { transition: none; } }
</style>
