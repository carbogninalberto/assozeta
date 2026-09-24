<script>
    import {ShieldCheck, Files, Database, Link2, Check, LoaderCircle, Clock3, Radio} from 'lucide-svelte';
    export let operation;
    export let progress = null;
    export let now = Date.now();
    export let live = false;
    const steps = [
        {id: 'backup', label: 'Copia di sicurezza', description: 'Proteggiamo i dati attuali', icon: ShieldCheck},
        {id: 'files', label: 'Allegati', description: 'Trasferimento dei file inclusi', icon: Files},
        {id: 'restoring', label: 'Dati associativi', description: 'Anagrafiche, iscrizioni e pagamenti', icon: Database},
        {id: 'finalizing', label: 'Verifica finale', description: 'Collegamenti e conferma del ripristino', icon: Link2},
    ];
    const labels = {queued:'In attesa del worker', validating:'Verifica del backup caricato', backup:'Creazione della copia di sicurezza',
        files:'Trasferimento degli allegati', restoring:'Importazione dei dati', finalizing:'Verifica e salvataggio finale'};
    const models = {Associate:'Anagrafiche', User:'Utenti', Document:'Documenti', Subscription:'Iscrizioni',
        MedicalCertificate:'Certificati medici', Invoice:'Fatture', Payment:'Pagamenti', Course:'Corsi', Family:'Famiglie'};
    $: phase = progress?.phase || operation.stage;
    $: index = steps.findIndex(step => step.id === phase);
    $: stale = progress && now - Date.parse(progress.updated_at) > 20000;
    $: percent = progress?.percent == null || stale ? null : Math.max(0, Math.min(100, progress.percent));
    $: eta = !stale && progress?.eta_seconds != null ? duration(progress.eta_seconds) : null;
    function duration(seconds) { return seconds < 60 ? 'Meno di un minuto' : `Circa ${Math.ceil(seconds / 60)} min`; }
    function amount(value, unit) {
        if (unit === 'bytes') return `${(value / 1024 ** 2).toLocaleString('it-IT', {maximumFractionDigits: 1})} MB`;
        return new Intl.NumberFormat('it-IT').format(value);
    }
</script>

<div class="restore-progress-card">
    <div class="progress-heading">
        <div class="activity-icon"><LoaderCircle size={26} aria-hidden="true" /></div>
        <div class="progress-heading-copy"><span class="eyebrow">RIPRISTINO IN CORSO</span><h4>{labels[phase] || 'Elaborazione del backup'}</h4></div>
        <span class="live-badge"><Radio size={13} aria-hidden="true" /> {stale ? 'In attesa di aggiornamenti' : live ? 'Aggiornamento live' : 'Stato sincronizzato'}</span>
    </div>
    <div class="progress-stats">
        <div><span class="metric-label">Avanzamento della fase</span><strong>{percent == null ? 'In elaborazione' : `${percent}%`}</strong>
            <small>{progress?.total ? `${amount(progress.completed || 0, progress.unit)} / ${amount(progress.total, progress.unit)}${progress.unit === 'records' ? ' record preparati' : ''}` : 'Il conteggio apparirà appena disponibile'}</small></div>
        <div><span class="metric-label"><Clock3 size={14} aria-hidden="true" /> Tempo rimanente nella fase</span><strong class="eta">{eta || (stale ? 'Stima in aggiornamento' : 'Calcolo della stima…')}</strong>
            <small>Indicativo, dipende dai dati e dalla velocità del server</small></div>
    </div>
    <div class="phase-track" role="progressbar" aria-label="Avanzamento della fase di ripristino" aria-valuemin="0" aria-valuemax="100" aria-valuenow={percent ?? undefined} aria-valuetext={percent == null ? 'In elaborazione' : `${percent}%`}>
        <div class:indeterminate={percent == null} style:width={percent == null ? '30%' : `${percent}%`}></div>
    </div>
    {#if progress?.detail}<p class="current-detail">{models[progress.detail] || progress.detail}</p>{/if}
    <ol class="restore-steps">
        {#each steps as step, i}
            <li class:step-current={i === index} class:step-done={index > i} aria-current={i === index ? 'step' : undefined}>
                <span class="step-icon"><svelte:component this={index > i ? Check : step.icon} size={18} aria-hidden="true" /></span>
                <div><strong>{step.label}</strong><small>{step.description}</small></div>
            </li>
        {/each}
    </ol>
    <p class="progress-footnote">Puoi lasciare questa pagina e tornare più tardi. Il ripristino è completato soltanto dopo la verifica finale.</p>
</div>

<style>
    .restore-progress-card {background: linear-gradient(120deg, color-mix(in srgb, var(--primary, #3524cd) 7%, white), #fff 65%); border: 1px solid color-mix(in srgb, var(--primary, #3524cd) 18%, white); border-radius: 1rem; padding: 1.6rem; margin: .6rem 0 1.5rem; color: var(--text-primary, #202238);}
    .progress-heading {display:flex; align-items:center; gap:1rem; flex-wrap:wrap;}
    .progress-heading-copy {flex:1; min-width:180px;}
    .activity-icon {display:grid;place-items:center;width:52px;height:52px;border-radius:16px;background:var(--primary, #3524cd);color:white;}
    .activity-icon :global(svg) {animation:rotate 2s linear infinite;}
    .eyebrow {font-size:.68rem;letter-spacing:.12em;font-weight:700;color:var(--primary, #3524cd);}
    h4 {font-size:1.22rem; margin:.3rem 0 0;font-weight:650;}
    .live-badge {display:flex;align-items:center;gap:.4rem;color:#476151;background:#edf6ef;border:1px solid #dcebdd;border-radius:99px;padding:.35rem .7rem;font-size:.75rem;}
    .progress-stats {display:grid;grid-template-columns:1fr 1fr;gap:1.5rem;margin:1.8rem 0 1rem;}
    .progress-stats strong {display:block;font-size:2.1rem;letter-spacing:-.04em;line-height:1.3;}
    .progress-stats strong.eta {font-size:1.3rem;letter-spacing:-.02em;margin:.5rem 0;}
    .metric-label {display:flex;align-items:center;gap:.4rem;font-size:.78rem;color:#656b80;}
    small {display:block;color:#73798c;font-size:.76rem;margin-top:.3rem;}
    .phase-track {height:8px;background:#e9e8f2;border-radius:99px;overflow:hidden;}
    .phase-track>div {height:100%;background:var(--primary, #3524cd);border-radius:99px;transition:width .5s;}
    .indeterminate {animation:drift 2s ease-in-out infinite;}
    .current-detail {font-size:.8rem;color:#646b82;margin:.6rem 0;}
    .restore-steps {display:grid;grid-template-columns:repeat(4,1fr);gap:1rem;list-style:none;padding:1.5rem 0;margin:0;}
    li {display:flex;gap:.65rem;align-items:flex-start;color:#81869a;}
    li strong {font-size:.79rem;font-weight:600;}
    .step-icon {display:grid;place-items:center;border:1px solid #e1e3ec;border-radius:10px;padding:.55rem;background:white;}
    .step-current {color:var(--primary, #3524cd);}
    .step-current .step-icon {background:var(--primary, #3524cd);color:white;border-color:transparent;}
    .step-done .step-icon {background:#eaf6ef;color:#287a4a;border-color:#d9ecdf;}
    .progress-footnote {border-top:1px solid #e6e7ef;padding-top:1rem;margin:0;color:#73798c;font-size:.78rem;}
    @keyframes rotate {to {transform:rotate(360deg);}}
    @keyframes drift {0%,100% {transform:translateX(0);} 50% {transform:translateX(230%);}}
    @media(max-width:700px) {.restore-steps {grid-template-columns:1fr 1fr;} .progress-stats {grid-template-columns:1fr;} .restore-progress-card {padding:1.1rem;} .live-badge {font-size:.68rem;}}
    @media(prefers-reduced-motion:reduce) {.activity-icon :global(svg), .indeterminate {animation:none;} .phase-track>div {transition:none;}}
</style>
