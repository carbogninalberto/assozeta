<script>
    import {CheckCircle, WarningCircle, XCircle, Circle} from 'phosphor-svelte';
    import {formatRelative, formatDate} from './instanceFormat.js';
    export let check;
    export let compact = false;
    const states = {
        passed: ['ok', CheckCircle, 'Verificato'], warning: ['warn', WarningCircle, 'Da controllare'], failed: ['bad', XCircle, 'Non riuscito'],
        not_configured: ['neutral', Circle, 'Non configurato'], not_applicable: ['neutral', Circle, 'Non applicabile'], not_checked: ['neutral', Circle, 'Non verificato'],
    };
    $: [tone, icon, label] = states[check?.status] || states.not_checked;
</script>

<div class="diagnostic-result {tone}" class:compact>
    <span class="si-icon {tone}" aria-hidden="true"><svelte:component this={icon} size={20} weight="fill" /></span>
    <div class="result-main">
        <div class="result-heading">
            <strong>{check?.label || 'Risultato'}</strong>
            <span class="result-state" class:verified={check?.status === 'passed'}>{label}</span>
        </div>
        {#if check?.message}<p>{check.message}</p>{/if}
        <p class="result-meta">
            {#if check?.core === false}{'Facoltativo · '}{/if}
            {#if check?.checked_at}<time datetime={check.checked_at} title={formatDate(check.checked_at)}>{formatRelative(check.checked_at)}</time>{:else}Mai verificato{/if}
        </p>
    </div>
</div>

<style>
    .diagnostic-result { display: flex; gap: .75rem; padding: .875rem 1rem; border: 1px solid var(--border-color); border-radius: .625rem; background: var(--bg-card); min-width: 0; overflow-wrap: anywhere; }
    .diagnostic-result.compact { padding: .75rem 0; border: 0; border-radius: 0; background: none; }
    .result-main { flex: 1; min-width: 0; }
    .result-heading { display: flex; flex-wrap: wrap; justify-content: space-between; gap: .25rem 1rem; }
    .result-state { font-size: .925rem; font-weight: 600; color: var(--text-secondary); }
    .ok .result-state { color: #0f8a65; }
    .warn .result-state { color: #a86b00; }
    .bad .result-state { color: var(--danger, #d92d20); }
    p { margin: .2rem 0 0; }
    .result-meta { font-size: .925rem; color: var(--text-secondary); }
</style>
