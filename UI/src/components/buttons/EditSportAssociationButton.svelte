<script>
	import { X } from 'lucide-svelte';
    import {scale} from 'svelte/transition';
    import {SlidersHorizontal} from 'phosphor-svelte';
    import {createEventDispatcher} from 'svelte';
    import Portal from 'svelte-portal';
    import * as easing from 'svelte/easing';
    import {apiFetch, replaceUID} from 'utils/ApiMiddleware';
    import {toast} from 'svelte-sonner';

    const dispatch = createEventDispatcher();

    export let disabled = false;
    export let hidden = false;
    export let popover_text = 'Modifica cliente';
    export let sport_association_id;
    export let data;

    let randomId = Math.random().toString(36).substring(7);

    $: {
        if (disabled) {
            popover_text = 'Non puoi Modificare il cliente';
        } else {
            popover_text = 'Modifica cliente';
        }
    }

    function open(event) {
        event.preventDefault();
        dispatch('open');
    }
</script>

<span data-toggle="modal" data-target="#upload-billing-invoice-{randomId}">
    <button
        {disabled}
        
        class="btn btn-xs btn-clean btn-icon text-primary m-0 mr-2 {hidden ? 'd-none' : ''}"
        data-toggle="tooltip"
        data-placement="bottom"
        title={popover_text}
        on:touchend={open}
        on:click={open}>
        <SlidersHorizontal size="24" weight="duotone" />
    </button>
</span>

<Portal>
    <div
        class="modal fade"
        id="upload-billing-invoice-{randomId}"
        style="padding:0!important"
        data-backdrop="static"
        tabindex="-1"
        role="dialog"
        aria-labelledby="staticBackdrop"
        aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="exampleModalLabel">Modifica Associazione Sportiva</h5>
                    <button type="button" class="close" data-dismiss="modal" aria-label="Close">
                        <X size={16} aria-hidden="true" />
                    </button>
                </div>
                <div class="modal-body">
                    <form>
                        <div class="form-group">
                            <label for="auto_renewal">Rinnovo automatico</label>
                            <select
                                class="form-control form-control-solid"
                                id="auto_renewal"
                                bind:value={data.billing_subscription.auto_renewal}>
                                <option value={true}>Sì</option>
                                <option value={false}>No</option>
                            </select>
                        </div>

                        <div class="form-group">
                            <label for="renewal_type">Tipo di rinnovo</label>
                            <select
                                class="form-control form-control-solid"
                                id="renewal_type"
                                bind:value={data.billing_subscription.renewal_type}>
                                <option value={1}>Mensile</option>
                                <option value={2}>Annuale</option>
                            </select>
                        </div>

                        <div class="form-group">
                            <label for="ends_on">Data di scadenza</label>
                            <input
                                type="text"
                                class="form-control form-control-solid"
                                id="ends_on"
                                bind:value={data.billing_subscription.ends_on} />
                        </div>

                        <div class="form-group">
                            <label for="billing_plan">Piano di fatturazione</label>
                            <input
                                type="text"
                                class="form-control form-control-solid"
                                id="billing_plan"
                                bind:value={data.billing_subscription.billing_plan} />
                        </div>
                        <div class="form-group">
                            <label for="notes">Note</label>
                            <textarea
                                class="form-control form-control-solid"
                                id="notes"
                                rows="3"
                                bind:value={data.notes}
                                placeholder="Inserisci note..."></textarea>
                        </div>
                    </form>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-light-primary font-weight-bold" data-dismiss="modal"
                        >Chiudi</button>
                    <button
                        type="button"
                        class="btn btn-primary font-weight-bold"
                        on:click|preventDefault={async () => {
                            let res = await apiFetch(
                                replaceUID(__bakney.env.API.SPORT_ASSOCIATIONS_ADMIN_UPDATE, sport_association_id),
                                {
                                    method: 'POST',
                                    body: JSON.stringify(data),
                                }
                            );
                            if (res.status == 200) {
                                toast.success('Modifiche salvate!');
                            } else {
                                toast.error('Qualcosa è andato storto.');
                            }
                            dispatch('saved');
                        }}>Salva</button>
                </div>
            </div>
        </div>
    </div>
</Portal>
