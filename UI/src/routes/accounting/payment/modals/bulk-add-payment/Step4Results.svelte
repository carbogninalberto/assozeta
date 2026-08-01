<script>
    import {createEventDispatcher} from 'svelte';
    import {CheckCircle, XCircle, WarningCircle, ArrowRight} from 'phosphor-svelte';
    import {slide} from 'svelte/transition';

    const dispatch = createEventDispatcher();

    export let result = {
        created_count: 0,
        total_requested: 0,
        invalid_associate_ids: [],
    };
    export let selectedAssociates = [];

    $: isFullSuccess = result.created_count === result.total_requested && result.invalid_associate_ids.length === 0;
    $: isPartialSuccess = result.created_count > 0 && result.invalid_associate_ids.length > 0;
    $: isFailure = result.created_count === 0;

    $: invalidAssociatesInfo = result.invalid_associate_ids.map(id => {
        const found = selectedAssociates.find(a => a.value === id);
        return found ? found.label : id;
    });

    function goToPayments() {
        dispatch('navigate', '/payment/list');
    }
</script>

<div class="step-content">
    <div class="text-center py-5">
        {#if isFullSuccess}
            <div class="mb-4" transition:slide={{duration: 300}}>
                <CheckCircle size={80} weight="duotone" class="text-success" />
            </div>
            <h4 class="font-weight-bolder text-success mb-3">Operazione completata con successo!</h4>
            <p class="text-dark-75 font-size-lg">
                Sono stati creati <strong>{result.created_count}</strong> pagamenti con successo.
            </p>
        {:else if isPartialSuccess}
            <div class="mb-4" transition:slide={{duration: 300}}>
                <WarningCircle size={80} weight="duotone" class="text-warning" />
            </div>
            <h4 class="font-weight-bolder text-warning mb-3">Operazione completata parzialmente</h4>
            <p class="text-dark-75 font-size-lg">
                Sono stati creati <strong>{result.created_count}</strong> di <strong>{result.total_requested}</strong> pagamenti.
            </p>
            <p class="text-muted">
                {result.invalid_associate_ids.length} {result.invalid_associate_ids.length === 1 ? 'persona non valida' : 'persone non valide'}.
            </p>
        {:else}
            <div class="mb-4" transition:slide={{duration: 300}}>
                <XCircle size={80} weight="duotone" class="text-danger" />
            </div>
            <h4 class="font-weight-bolder text-danger mb-3">Operazione non riuscita</h4>
            <p class="text-dark-75 font-size-lg">
                Non è stato possibile creare alcun pagamento.
            </p>
        {/if}

        {#if result.invalid_associate_ids.length > 0}
            <div class="card card-custom border mt-5 mx-auto" style="max-width: 500px;">
                <div class="card-header p-4">
                    <h6 class="font-weight-bolder mb-0 text-danger">
                        Persone non valide ({result.invalid_associate_ids.length})
                    </h6>
                </div>
                <div class="card-body p-4 pt-0">
                    <div class="max-h-200px overflow-auto text-left">
                        {#each invalidAssociatesInfo as name, idx}
                            <div class="py-2 {idx < invalidAssociatesInfo.length - 1 ? 'border-bottom' : ''}">
                                <span class="font-weight-bold text-danger">{name}</span>
                            </div>
                        {/each}
                    </div>
                </div>
            </div>
        {/if}

        <div class="mt-5">
            <button
                type="button"
                class="btn btn-sm btn-primary font-weight-bolder font-size-h6 px-8 py-4"
                on:click={goToPayments}>
                <ArrowRight size={18} weight="bold" class="mr-2" />
                Vai ai Pagamenti
            </button>
        </div>
    </div>
</div>

<style>
    .max-h-200px {
        max-height: 200px;
    }
</style>
