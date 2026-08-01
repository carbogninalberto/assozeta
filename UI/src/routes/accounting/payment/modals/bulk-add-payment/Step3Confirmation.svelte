<script>
    import moment from 'moment';
    import {createEventDispatcher} from 'svelte';
    import {Warning, CaretDown, CaretUp, Users, CurrencyEur, Receipt} from 'phosphor-svelte';
    import {slide} from 'svelte/transition';

    const dispatch = createEventDispatcher();

    export let selectedAssociates = [];
    export let paymentData = {};

    let showAssociatesList = false;

    $: totalAmount = selectedAssociates.length * parseFloat(String(paymentData.amount || '0').replace(',', '.'));
    $: formattedAmount = parseFloat(String(paymentData.amount || '0').replace(',', '.')).toLocaleString('it-IT', {
        style: 'currency',
        currency: 'EUR',
        minimumFractionDigits: 2,
    });
    $: formattedTotalAmount = totalAmount.toLocaleString('it-IT', {
        style: 'currency',
        currency: 'EUR',
        minimumFractionDigits: 2,
    });

    const paymentTypeLabels = {
        default: 'Non specificato',
        cash: 'Contanti',
        transfer: 'Bonifico Bancario',
        online: 'Altro mezzo Online',
        'sepa-transfer': 'Bonifico SEPA',
        stripe: 'Stripe',
        pos: 'PoS',
    };

    const subjectLabels = {
        0: 'Altro',
        1: 'Iscrizione',
        2: 'Corso',
        3: 'Giroconto',
    };
</script>

<div class="step-content">
    <div class="mb-4">
        <h5 class="font-weight-bolder mb-4">Riepilogo operazione</h5>

        <!-- Summary Cards - Responsive Grid -->
        <div class="row">
            <div class="col-12 col-md-4 mb-3">
                <div class="card card-custom rounded-xl bg-light-primary h-100">
                    <div class="card-body p-4 text-center">
                        <div class="d-flex gap-2 justify-content-center align-items-center">
                        <Users size={26} weight="duotone" class="text-primary mb-0" />
                            <h6 class="text-primary font-weight-bolder font-size-h3 mb-0">Persone</h6>
                        </div>
                        <span class="font-size-h1 font-weight-boldest text-primary">
                            {selectedAssociates.length}
                        </span>
                    </div>
                </div>
            </div>
            <div class="col-12 col-md-4 mb-3">
                <div class="card card-custom rounded-xl bg-light-success h-100">
                    <div class="card-body p-4 text-center">
                        <div class="d-flex gap-2 justify-content-center align-items-center">
                            <CurrencyEur size={26} weight="duotone" class="text-success mb-0" />
                            <h6 class="text-success font-weight-bolder font-size-h3 mb-0">Importo per persona</h6>
                        </div>
                        <span class="font-size-h1 font-weight-boldest text-success">
                            {formattedAmount}
                        </span>
                    </div>
                </div>
            </div>
            <div class="col-12 col-md-4 mb-3">
                <div class="card card-custom rounded-xl bg-light-warning h-100">
                    <div class="card-body p-4 text-center">
                        <div class="d-flex gap-2 justify-content-center align-items-center">
                            <Receipt size={26} weight="duotone" class="text-warning mb-0" />
                            <h6 class="text-warning font-weight-bolder font-size-h3 mb-0">Totale complessivo</h6>
                        </div>
                        <span class="font-size-h1 font-weight-boldest text-warning">
                            {formattedTotalAmount}
                        </span>
                    </div>
                </div>
            </div>
        </div>

        <!-- Payment Details Card -->
        <div class="card card-custom border mt-4">
            <div class="card-body pt-4 px-2 pb-0">
                <h6 class="font-weight-bolder mb-3">Dettagli pagamento</h6>
                <div class="row">
                    <div class="col-12 col-md-6 mb-2">
                        <span class="text-muted">Descrizione:</span>
                        <span class="font-weight-bold ml-2">{paymentData.description || '-'}</span>
                    </div>
                    <div class="col-12 col-md-6 mb-2">
                        <span class="text-muted">Tipologia:</span>
                        <span class="font-weight-bold ml-2 {paymentData.expense ? 'text-danger' : 'text-success'}">
                            {paymentData.expense ? 'Uscita' : 'Entrata'}
                        </span>
                    </div>
                    <div class="col-12 col-md-6 mb-2">
                        <span class="text-muted">Metodo:</span>
                        <span class="font-weight-bold ml-2">{paymentTypeLabels[paymentData.type] || '-'}</span>
                    </div>
                    <div class="col-12 col-md-6 mb-2">
                        <span class="text-muted">Tipo quota:</span>
                        <span class="font-weight-bold ml-2">{subjectLabels[paymentData.subject] || '-'}</span>
                    </div>
                    <div class="col-12 col-md-6 mb-2">
                        <span class="text-muted">Data prevista:</span>
                        <span class="font-weight-bold ml-2">
                            {paymentData.creation_date ? moment(paymentData.creation_date).format('DD/MM/YYYY') : '-'}
                        </span>
                    </div>
                    <div class="col-12 col-md-6 mb-2">
                        <span class="text-muted">Stato:</span>
                        <span class="font-weight-bold ml-2">
                            {#if paymentData.paid}
                                <span class="label label-light-success label-inline">Pagato</span>
                            {:else}
                                <span class="label label-light-warning label-inline">In attesa</span>
                            {/if}
                        </span>
                    </div>
                </div>
            </div>
        </div>

        <!-- Selected Associates Card -->
        <div class="border border-secondary rounded-lg mt-4 mb-4 p-0">
            <div on:click={() => showAssociatesList = !showAssociatesList} class="px-3 py-2 cursor-pointer d-flex justify-content-between align-items-center w-100" style="height: fit-content;">
                <h6 class="font-weight-bolder mb-0">
                    Persone selezionate ({selectedAssociates.length})
                </h6>
                {#if showAssociatesList}
                    <CaretUp size={20} weight="bold" />
                {:else}
                    <CaretDown size={20} weight="bold" />
                {/if}
            </div>
            {#if showAssociatesList}
                <div transition:slide={{duration: 200}}>
                    <div class="max-h-200px overflow-auto">
                        {#each selectedAssociates as associate, idx}
                            <div class="py-2 border-top border-light-secondary px-3">
                                <span class="font-weight-bold">{associate.label}</span>
                            </div>
                        {/each}
                    </div>
                </div>
            {/if}
        </div>

        <!-- Warning Message -->
        <div class="mt-4 p-4 bg-light-primary rounded-lg d-flex flex-column flex-md-row align-items-start">
            <Warning size={24} weight="duotone" class="text-primary mr-0 mr-md-3 mb-2 mb-md-0 flex-shrink-0" />
            <div>
                <span class="font-weight-bolder text-primary d-block mb-1">Attenzione</span>
                <span class="text-primary">
                    Questa operazione creerà <strong>{selectedAssociates.length} pagamenti</strong> per un totale di <strong>{formattedTotalAmount}</strong>.
                    L'operazione non può essere annullata automaticamente.
                </span>
            </div>
        </div>
    </div>
</div>

<style>
    .max-h-200px {
        max-height: 200px;
    }
    .cursor-pointer {
        cursor: pointer;
    }
</style>
