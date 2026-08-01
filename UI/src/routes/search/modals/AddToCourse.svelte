<script>
	import { X } from 'lucide-svelte';
    import SmartSelect from 'components/formBuilder/preview-blocks/smart-select-input.svelte';
    import Switch from 'components/inputs/Switch.svelte';
    import {onMount, afterUpdate, onDestroy} from 'svelte';
    import {toast} from 'svelte-sonner';
    import {push} from 'svelte-spa-router';
    import {apiFetch, replaceUID} from 'utils/ApiMiddleware';

    export let id = '';
    export let course = '';
    export let full = false;
    export let callback = () => {
        console.log('reload');
    };
    export let multiple_quotes = [];
    export let one_fee_payment = false;
    let associatesSubscribed;
    let associatesUnsubscribed;
    let loading = false;
    let selectedQuote = null;
    let oneFeePayment = true;

    let valid = false;

    // Create controller when component initializes
    const controller = new AbortController();

    onMount(async () => {
        await fetchData();
    });

    afterUpdate(() => {
        valid = checkValidity();
    });

    onDestroy(() => {
        // cancel pending fetches
        controller.abort();
    });

    export function reset() {
        fetchData();
    }

    async function fetchData() {
        loading = true;
        const res = await apiFetch(`${__bakney.env.API.PROFILE.ASSOCIATES_COURSE}/${id}`, {signal: controller.signal});
        if (!res.error) {
            associatesUnsubscribed = res.response.associates_unsubscribed;
            full = associatesUnsubscribed?.length == 0;
        }
        loading = false;
    }

    async function subscribeAthletes() {
        let responses = [];
        for (const associate of associatesUnsubscribed) {
            if (!associate.selected) continue;

            const url = replaceUID(
                replaceUID(__bakney.env.API.COURSE.OVERVIEW_ADD, id),
                associate.subscription_id,
                '<sub_uid>'
            );

            let response = await apiFetch(url, {
                method: 'POST',
                body: JSON.stringify({
                    multiple_quote: selectedQuote,
                    one_fee_payment: oneFeePayment,
                }),
            });
            // spinner stop

            if (!response.error) {
                responses.push(true);
            } else {
                let modalText =
                    response.status == 403
                        ? 'Operazione non permessa.'
                        : 'Scusa, ho individuato degli errori, riprova.';
                toast.error(modalText);
            }
        }
        if (!responses.every(x => x)) {
            toast.error('Si sono verificati degli errori.');
        } else {
            toast.success('Atleti aggiunti!');
            await callback();
            // TODO: add a way to show the newly created payments
            push('/payment/list');
        }
    }

    function checkValidity() {
        let validQuote = true;
        if (multiple_quotes && multiple_quotes.length > 0) {
            validQuote = selectedQuote !== null;
        }
        console.log(validQuote, selectedQuote, selectedQuote !== null);
        let validAssociates = associatesUnsubscribed?.some(x => x.selected);
        return validQuote && validAssociates;
    }
</script>

<div
    class="modal fade"
    id="subscription-course-{id}"
    tabindex="-1"
    role="dialog"
    aria-labelledby="exampleModalLabel"
    aria-hidden="true">
    <div class="modal-dialog modal-dialog-centered modal-md" role="document">
        <div class="modal-content">
            <div class="modal-header">
                <h5 class="modal-title" id="exampleModalLabel">
                    Richiesta Iscrizione (<span class="font-weight-boldest">{course}</span>)
                </h5>
                <button type="button" class="close" data-dismiss="modal" aria-label="Close">
                    <X size={16} aria-hidden="true" />
                </button>
            </div>
            <div class="modal-body">
                <div class="scroll scroll-pull" data-scroll="true" data-height="400">
                    <p>
                        Seleziona l'atleta o gli atleti che vuoi iscrivere al corso <span
                            class="font-weight-boldest text-dark-75">{course}</span
                        >. Puoi iscrivere ad un corso solo gli atleti che sono già stati accettati come soci.
                    </p>
                    <div class="form-group row mb-0">
                        <div class="col-12 col-form-label">
                            <div class="checkbox-list">
                                {#if loading}
                                    <div class="d-flex justify-content-center">
                                        <div class="spinner spinner-primary spinner-lg mr-15">
                                            <span class="ml-12">Caricamento...</span>
                                        </div>
                                    </div>
                                {:else}
                                    {#each associatesUnsubscribed || [] as associate}
                                        <label
                                            class="checkbox checkbox-darker"
                                            class:selected-checkbox-container={associate.selected}>
                                            <input
                                                type="checkbox"
                                                name={associate.id}
                                                bind:checked={associate.selected} />
                                            <span />
                                            {associate.full_name}
                                        </label>
                                    {/each}
                                {/if}
                            </div>
                        </div>
                    </div>
                    {#if multiple_quotes && multiple_quotes.length > 0}
                        <div class="mt-6">
                            <h3 class="font-weight-boldest">Seleziona la quota</h3>
                        </div>
                        <SmartSelect
                            customClasses="mb-0 p-0"
                            editable={false}
                            on:change={e => {
                                selectedQuote = e.detail;
                            }}
                            props={{
                                options: multiple_quotes.map(quote => ({
                                    label: `${quote.title} - ${quote.amount}€`,
                                    value: quote.quote_id,
                                })),
                                label: 'Quota',
                                name: 'selectedQuote',
                                id: 'selectedQuote',
                                placeholder: 'Seleziona la quota',
                                required: true,
                            }} />
                    {:else if one_fee_payment}
                        <div class="form-group">
                            <Switch class="mb-0" label="Pagamento in un'unica soluzione" bind:checked={oneFeePayment} name="oneFeePayment" id="oneFeePayment" helpText="Pagherai il corso in un'unica soluzione se attivato." />
                            <p class="font-size-sm mt-1 text-primary mt-0 font-weight-bold text-center">
                                Il pagamento in un'unica soluzione si applicherà a tutti gli atleti selezionati.
                            </p>
                        </div>
                    {/if}
                </div>
            </div>
            <div class="modal-footer">
                <button type="button" class="btn btn-light-primary font-weight-bolder" data-dismiss="modal"
                    >Chiudi</button>
                <button
                    type="button"
                    class="btn btn-primary font-weight-bold"
                    disabled={!valid}
                    data-dismiss="modal"
                    on:click={() => {
                        subscribeAthletes();
                    }}>Iscrivi</button>
            </div>
        </div>
    </div>
</div>

<style>
    .checkbox-darker {
        font-size: 1rem;
        padding: 1rem;
        background: var(--bg-surface-secondary);
        border-radius: 0.5rem;
        word-break: break-word;
        -webkit-touch-callout: none;
        -webkit-user-select: none;
        -khtml-user-select: none;
        -moz-user-select: none;
        -ms-user-select: none;
        user-select: none;
        border: 1px solid #351dc205;
    }
    .checkbox-darker > span {
        background-color: var(--border-color-dark);
    }
    .checkbox-darker > input:checked ~ span {
        background-color: #351dc2;
    }

    .selected-checkbox-container {
        background-color: var(--border-color) !important;
        font-weight: 600;
    }
</style>
