<script>
    import {Robot} from 'phosphor-svelte';
    import {aiEnabled} from 'store/instanceStore.js';
    import {isAgentOpen, agentProcessing} from 'store/agentStore.js';
</script>

{#if $aiEnabled}
    <button type="button" class="btn btn-icon btn-clean btn-sm position-relative agent-toggle-btn"
        class:agent-toggle-active={$isAgentOpen} aria-label="Agente AI" aria-pressed={$isAgentOpen}
        on:click={() => ($isAgentOpen = !$isAgentOpen)} title="Agente AI">
        <Robot size={18} weight={$isAgentOpen ? 'fill' : 'duotone'} />
        {#if $agentProcessing}<span class="agent-processing-dot" />{/if}
    </button>
{/if}

<style>
    .agent-toggle-active {background: var(--primary-light, #eee9ff); color: var(--primary);}
    .agent-processing-dot {position: absolute; top: 2px; right: 2px; width: 7px; height: 7px; border-radius: 50%; background: var(--success); animation: agent-pulse 1.5s infinite;}
    @keyframes agent-pulse { 50% { opacity: .35; } }
    @media (prefers-reduced-motion: reduce) { .agent-processing-dot { animation: none; } }
    button:focus-visible {outline: 3px solid var(--primary); outline-offset: 2px;}
</style>
