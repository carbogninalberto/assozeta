<script>
    import BasicModal from 'components/modals/BasicModal.svelte';
    import {createEventDispatcher, onMount} from 'svelte';
    import {apiFetch} from 'utils/ApiMiddleware.js';
    import {toast} from 'svelte-sonner';
    import {ArrowLeft, ArrowRight, Check, Users, Receipt, ListChecks, CheckCircle} from 'phosphor-svelte';
    import {blockPage, unblockPage} from 'store/loadingStore.js';

    import Step1SelectAssociates from './bulk-add-payment/Step1SelectAssociates.svelte';
    import Step2PaymentDetails from './bulk-add-payment/Step2PaymentDetails.svelte';
    import Step3Confirmation from './bulk-add-payment/Step3Confirmation.svelte';
    import Step4Results from './bulk-add-payment/Step4Results.svelte';

    const dispatch = createEventDispatcher();

    export let show = true;
    export let preSelectedAssociates = [];
    export let target = '#portal-elements';

    let currentStep = 1;
    let selectedAssociates = [];
    let paymentData = {
        amount: '',
        type: 'cash',
        subject: 0,
        expense: false,
        description: '',
        creation_date: moment().format('YYYY-MM-DD'),
        payment_date: null,
        paid: false,
        payment_category: null,
        custom_accounts: null,
        notes: '',
        meta_payment_categories: [],
    };
    let result = {
        created_count: 0,
        total_requested: 0,
        invalid_associate_ids: [],
    };

    const steps = [
        {number: 1, title: 'Seleziona Persone', icon: Users},
        {number: 2, title: 'Dettagli Pagamento', icon: Receipt},
        {number: 3, title: 'Conferma', icon: ListChecks},
        {number: 4, title: 'Risultato', icon: CheckCircle},
    ];

    // Track primitive values for reliable reactivity
    $: selectedCount = selectedAssociates ? selectedAssociates.length : 0;
    $: paymentAmount = parseFloat(String(paymentData.amount || '0').replace(',', '.'));
    $: paymentHasAccount = !!paymentData.custom_accounts;

    // Reactive statement to track if user can proceed to next step
    $: canProceed = (() => {
        switch (currentStep) {
            case 1:
                return selectedCount > 0;
            case 2:
                return paymentAmount > 0 && paymentHasAccount;
            case 3:
                return true;
            default:
                return false;
        }
    })();

    function handleAssociatesChange(e) {
        selectedAssociates = e.detail || [];
    }

    function handlePaymentDataChange(e) {
        paymentData = e.detail || {};
    }

    function nextStep() {
        if (currentStep < 3 && canProceed) {
            currentStep++;
        } else if (currentStep === 3) {
            submitBulkPayment();
        }
    }

    function prevStep() {
        if (currentStep > 1 && currentStep < 4) {
            currentStep--;
        }
    }

    async function submitBulkPayment() {
        blockPage({
            overlayColor: '#000000',
            state: 'primary',
            message: 'Creazione pagamenti in corso...',
        });

        try {
            // Prepare payload - deduplicate associate_ids in case same person has multiple subscriptions
            const uniqueAssociateIds = [...new Set(selectedAssociates.map(a => a.value))];
            const payload = {
                associate_ids: uniqueAssociateIds,
                amount: String(parseFloat(String(paymentData.amount).replace(',', '.')).toFixed(2)),
                type: paymentData.type,
                subject: paymentData.subject,
                expense: paymentData.expense,
                paid: paymentData.paid,
                creation_date: paymentData.creation_date,
            };

            // Add optional fields only if they have values
            if (paymentData.description) {
                payload.description = paymentData.description;
            }
            if (paymentData.payment_date) {
                payload.payment_date = paymentData.payment_date;
            }
            if (paymentData.payment_category) {
                payload.payment_category = paymentData.payment_category;
            }
            if (paymentData.custom_accounts) {
                payload.custom_accounts = paymentData.custom_accounts;
            }
            if (paymentData.notes) {
                payload.notes = paymentData.notes;
            }
            if (paymentData.meta_payment_categories && paymentData.meta_payment_categories.length > 0) {
                payload.meta_payment_categories = paymentData.meta_payment_categories.map(meta => ({
                    ...meta,
                    amount: parseFloat(String(meta.amount).replace(',', '.')),
                }));
            }
            if (paymentData.course) {
                payload.course = paymentData.course.value || paymentData.course;
            }

            const res = await apiFetch(__bakney.env.API.PAYMENT.BULK_ADD, {
                method: 'POST',
                body: JSON.stringify(payload),
            });

            unblockPage();

            if (res.status === 201 || res.status === 200) {
                result = {
                    created_count: res.response?.data?.created_count || 0,
                    total_requested: res.response?.data?.total_requested || selectedAssociates.length,
                    invalid_associate_ids: res.response?.data?.invalid_associate_ids || [],
                };

                if (result.created_count === result.total_requested) {
                    toast.success(`Creati ${result.created_count} pagamenti con successo.`);
                } else if (result.created_count > 0) {
                    toast.warning(`Creati ${result.created_count} di ${result.total_requested} pagamenti.`);
                } else {
                    toast.error('Nessun pagamento creato.');
                }

                currentStep = 4;
                dispatch('update');
            } else if (res.status === 400) {
                toast.error(res.response?.msg || 'Errore nei dati inviati.');
            } else if (res.status === 403) {
                toast.error('Non hai i permessi per eseguire questa operazione.');
            } else {
                toast.error('Si è verificato un errore. Riprova.');
            }
        } catch (error) {
            unblockPage();
            toast.error('Si è verificato un errore. Riprova.');
            console.error('Bulk payment error:', error);
        }
    }

    function handleClose() {
        show = false;
        dispatch('close');
    }

    function handleNavigate(event) {
        show = false;
        dispatch('close');
        if (event.detail) {
            location.href = '/#' + event.detail;
        }
    }

    onMount(() => {
        // If we have pre-selected associates, start from step 1 to let the component load them
        if (preSelectedAssociates && preSelectedAssociates.length > 0) {
            currentStep = 1;
        }
    });
</script>

<BasicModal
    id="bulk-add-payment-modal"
    bind:show
    title="Pagamento Multiplo"
    showTitle={true}
    {target}
    showActionButton={false}
    showCancelButton={false}
    showFooter={false}
    modalSize="xl"
    scrollable={true}
    hideOnClickOutside={false}
    bodyClass="py-2 px-0"
    on:cancel={handleClose}
    on:close={handleClose}>

    <!-- Stepper Header -->
    {#if currentStep < 4}
        <div class="wizard-nav px-5 mb-4">
            <div class="d-flex justify-content-center align-items-center">
                {#each steps.slice(0, 3) as step, idx}
                    <div
                        class="d-flex justify-content-between align-items-center"
                        class:text-primary={currentStep >= step.number}>
                        <div class="d-flex flex-column font-weight-boldest align-items-center">
                            <svelte:component
                                this={step.icon}
                                size={24}
                                weight={currentStep >= step.number ? 'duotone' : 'light'}
                                class="mx-auto mb-1" />
                            <span class="d-none d-md-block font-size-sm">{step.title}</span>
                        </div>
                    </div>
                    {#if idx < 2}
                        <div class:text-primary={currentStep > step.number} class="my-auto">
                            <ArrowRight size={12} weight="bold" class="mx-4 mx-md-8" />
                        </div>
                    {/if}
                {/each}
            </div>
        </div>
        <hr class="m-0 mb-4" />
    {/if}

    <!-- Step Content -->
    <div class="px-5" style="min-height: 15rem;">
        {#if currentStep === 1}
            <Step1SelectAssociates
                {preSelectedAssociates}
                on:change={handleAssociatesChange} />
        {:else if currentStep === 2}
            <Step2PaymentDetails
                on:change={handlePaymentDataChange} />
        {:else if currentStep === 3}
            <Step3Confirmation
                {selectedAssociates}
                {paymentData} />
        {:else if currentStep === 4}
            <Step4Results
                {result}
                {selectedAssociates}
                on:navigate={handleNavigate} />
        {/if}
    </div>

    <!-- Footer Navigation -->
    {#if currentStep < 4}
        <div class="modal-footer d-flex flex-column flex-md-row justify-content-between mt-4 px-5 pb-0">
            <div class="order-2 order-md-1 mt-2 mt-md-0 w-100 w-md-auto">
                {#if currentStep > 1}
                    <button
                        type="button"
                        class="btn btn-sm btn-light-primary font-weight-bolder font-size-h6 px-8 py-4 w-100 w-md-auto"
                        on:click={prevStep}>
                        <ArrowLeft size={16} weight="bold" class="mr-1" />
                        Indietro
                    </button>
                {:else}
                    <button
                        type="button"
                        class="btn btn-sm btn-light font-weight-bolder font-size-h6 px-8 py-4 w-100 w-md-auto"
                        on:click={handleClose}>
                        Annulla
                    </button>
                {/if}
            </div>
            <div class="order-1 order-md-2 w-100 w-md-auto">
                {#if currentStep < 3}
                    <button
                        type="button"
                        class="btn btn-sm btn-primary font-weight-bolder font-size-h6 px-8 py-4 w-100 w-md-auto"
                        disabled={!canProceed}
                        on:click={nextStep}>
                        Avanti
                        <ArrowRight size={16} weight="bold" class="ml-1" />
                    </button>
                {:else}
                    <button
                        type="button"
                        class="btn btn-sm btn-primary font-weight-bolder font-size-h6 px-8 py-4 w-100 w-md-auto"
                        disabled={!canProceed}
                        on:click={nextStep}>
                        Crea Pagamenti
                    </button>
                {/if}
            </div>
        </div>
    {/if}
</BasicModal>

<style>
    .w-md-auto {
        width: auto;
    }
    @media (max-width: 767.98px) {
        .w-md-auto {
            width: 100%;
        }
    }
</style>
