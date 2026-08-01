<script>
    import {createEventDispatcher} from 'svelte';
    import {Check, SignIn} from 'phosphor-svelte';

    export let result = null;

    const dispatch = createEventDispatcher();

    function handleComplete() {
        dispatch('complete');
    }
</script>

<div class="step-complete text-center">
    <div class="success-animation mb-4">
        <div class="success-icon">
            <Check size={48} weight="bold" />
        </div>
    </div>

    <h2 class="font-weight-bolder mb-3">Configurazione Completata!</h2>
    <p class="text-muted font-size-sm mb-4">
        La tua istanza è stata configurata con successo.<br />
        Ora puoi accedere con le credenziali che hai impostato.
    </p>

    {#if result}
        <div class="result-card bg-light rounded-lg p-4 mb-4 text-left">
            <h6 class="font-weight-bolder mb-3">Riepilogo</h6>

            {#if result.association_id}
                <div class="d-flex justify-content-between mb-2">
                    <span class="text-muted font-size-sm">ID Associazione:</span>
                    <span class="font-weight-bolder font-size-sm text-truncate ml-2" style="max-width: 200px;">
                        {result.association_id}
                    </span>
                </div>
            {/if}

            <div class="d-flex justify-content-between">
                <span class="text-muted font-size-sm">Stato:</span>
                <span class="badge badge-success">Attivo</span>
            </div>
        </div>
    {/if}

    <div class="tips bg-light rounded-lg p-4 mb-4 text-left">
        <h6 class="font-weight-bolder mb-3">Prossimi Passi</h6>
        <ul class="mb-0 pl-3 font-size-sm">
            <li class="mb-2">Accedi con le credenziali che hai creato</li>
            <li class="mb-2">Completa il profilo della tua associazione</li>
            <li class="mb-2">Inizia ad aggiungere i tuoi soci</li>
            <li>Esplora le funzionalità del gestionale</li>
        </ul>
    </div>

    <button
        type="button"
        class="btn btn-primary btn-lg font-weight-bolder"
        on:click={handleComplete}
    >
        <SignIn size={18} weight="duotone" class="mr-2" />
        Vai al Login
    </button>
</div>

<style>
    .success-animation {
        animation: scaleIn 0.5s ease-out;
    }

    @keyframes scaleIn {
        from {
            transform: scale(0);
            opacity: 0;
        }
        to {
            transform: scale(1);
            opacity: 1;
        }
    }

    .success-icon {
        width: 80px;
        height: 80px;
        border-radius: 50%;
        background: linear-gradient(135deg, #50cd89 0%, #3dc27e 100%);
        display: flex;
        align-items: center;
        justify-content: center;
        color: white;
        margin: 0 auto;
        box-shadow: 0 0 30px rgba(80, 205, 137, 0.3);
    }

    .result-card,
    .tips {
        border-left: 4px solid #50cd89;
    }
</style>
