<script>
	import { X } from 'lucide-svelte';
    import {apiFetch, replaceUID} from 'utils/ApiMiddleware';
    const scrollToTop = () => window.scrollTo({ top: 0, behavior: 'smooth' });
import {blockPage, unblockPage} from 'store/loadingStore.js';
    import Portal from 'svelte-portal';
    import {getDataFromForm} from 'utils/Functions';
    import Select from 'svelte-select';
    import {toast} from 'svelte-sonner';
    import DateInput from 'components/inputs/DateInput.svelte';
    import {hideModal} from 'shim/modal.js';

    export let id;
    export let row;
    export let datatableHandle;
    export let suppliers = [];

    let editForm;

    function initForm() {
        editForm?.destroy();
        editForm = FormValidation.formValidation(document.getElementById('form_edit_' + id), {
            fields: {
                invoice_identifier: {
                    validators: {
                        notEmpty: {
                            message: "L'indenfiticativo è obbligatorio.",
                        },
                    },
                },
                expire_date: {
                    validators: {
                        notEmpty: {
                            message: 'La data di scadenza è obbligatoria.',
                        },
                    },
                },
                paid: {
                    validators: {
                        notEmpty: {
                            message: 'Lo stato del pagamento è obbligatorio.',
                        },
                    },
                },
                payment_date: {
                    validators: {
                        notEmpty: {
                            message: 'Seleziona una data.',
                        },
                    },
                },
            },
            plugins: {
                trigger: new FormValidation.plugins.Trigger(),
                bootstrap: new FormValidation.plugins.Bootstrap(),
                // submitButton: new FormValidation.plugins.SubmitButton(),
            },
        });
    }

    async function update(data) {
        blockPage({message: 'Modifica in corso...'});

        let res;

        try {
            data.paid = JSON.parse(data.paid).value || false;
            delete data.payment;
            delete data.supplier;
            delete data.amount;

            const url = replaceUID(__bakney.env.API.INVOICE_SUPPLIERS.UPDATE, id);

            res = await apiFetch(url, {
                method: 'PATCH',
                body: JSON.stringify(data),
            });
        } finally {
            unblockPage();
        }

        if (res.status == 200) {
            toast.success('Modificato con successo.');
            datatableHandle?.reload();
        } else {
            swal.fire({
                text: 'Scusa, ho individuato degli errori, riprova.',
                icon: 'error',
                buttonsStyling: false,
                confirmButtonText: 'Ok, capito!',
                customClass: {
                    confirmButton: 'btn font-weight-bold btn-light-primary',
                },
            }).then(function () {
                scrollToTop();
            });
        }
    }

    function handleValidation(e) {
        if (!editForm) initForm();
        editForm?.validate().then(function (status) {
            if (status === 'Valid') {
                update(getDataFromForm(e));
                hideModal('editModal-' + id);
                datatableHandle.reload();
            } else {
                swal.fire({
                    text: 'Per favore, inserisci tutti i dati e riprova.',
                    icon: 'error',
                    buttonsStyling: false,
                    confirmButtonText: 'Ok, capito!',
                    customClass: {
                        confirmButton: 'btn font-weight-bold btn-light-primary',
                    },
                }).then(function () {
                    scrollToTop();
                });
            }
        });
    }

</script>

<!-- svelte-ignore missing-declaration -->
<Portal>
    <!-- Modal-->
    <form class="form" id="form_edit_{id}" on:submit|preventDefault={handleValidation}>
        <div
            class="modal fade"
            id="editModal-{id}"
            tabindex="-1"
            role="dialog"
            aria-labelledby="staticBackdrop"
            aria-hidden="true">
            <div class="modal-dialog modal-dialog-centered modal-lg" role="document">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title" id="exampleModalLabel">Modifica fattura</h5>
                        <button type="button" class="close" data-dismiss="modal" aria-label="Close">
                            <X size={16} aria-hidden="true" />
                        </button>
                    </div>
                    <div class="modal-body" style="overflow-y: visible;">
                        <div class="row p-0">
                            <div class="form-group col-12 col-md-6">
                                <!-- svelte-ignore a11y-label-has-associated-control -->
                                <label>Identificativo<b>*</b></label>
                                <input
                                    value={row.invoice_identifier}
                                    name="invoice_identifier"
                                    type="text"
                                    class="form-control form-control-solid form-control-lg margin-t-2"
                                    placeholder="Identificativo fattura" />
                                <!-- <span class="form-text text-muted">Per favore inserisci il nome.</span> -->
                            </div>

                            <div class="form-group col-12 col-md-6">
                                <!-- svelte-ignore a11y-label-has-associated-control -->
                                <label>Importo<b>*</b></label>
                                <div class="input-group input-group-solid">
                                    <div class="input-group-prepend">
                                        <span class="input-group-text fs-1-1">€</span>
                                    </div>
                                    <input
                                        disabled
                                        value={row.amount}
                                        name="amount"
                                        type="text"
                                        class="form-control fs-1-1"
                                        id="amount_{id}"
                                        placeholder="0,00" />
                                </div>
                            </div>
                        </div>

                        <div class="row p-0">
                            <div class="form-group col-12 col-md-4">
                                <!-- svelte-ignore a11y-label-has-associated-control -->
                                <label>Data pagamento<b>*</b></label>
                                <DateInput id="payment_date_{id}" name="payment_date" placeholder="Seleziona Data"
                                    bind:value={row.payment_date} />
                            </div>

                            <div class="form-group col-12 col-md-4">
                                <!-- svelte-ignore a11y-label-has-associated-control -->
                                <label>Data scadenza<b>*</b></label>
                                <DateInput id="expire_date_{id}" name="expire_date" placeholder="Seleziona Data"
                                    bind:value={row.expire_date} />
                            </div>
                            <div class="form-group col-12 col-md-4">
                                <!-- svelte-ignore a11y-label-has-associated-control -->
                                <label>Conto<b>*</b></label>
                                <div>
                                    <input
                                        disabled
                                        style="opacity: .5;pointer-events: none;"
                                        value={row.payment?.custom_account_name.toUpperCase()}
                                        class="form-control form-control-solid form-control-lg margin-t-2" />
                                </div>
                            </div>
                        </div>
                        <div class="row p-0">
                            <div class="form-group col-12 col-md-8">
                                <!-- svelte-ignore a11y-label-has-associated-control -->
                                <label>Fornitore<b>*</b></label>
                                <!-- class="form-control selectpicker form-control-solid form-control-lg" -->
                                <span style="opacity:.5;">
                                    <Select
                                        hideEmptyState={true}
                                        id="supplier"
                                        disabled={true}
                                        value={{
                                            value: row.supplier ? row.supplier?.supplier_id : '',
                                            label: row.supplier ? row.supplier?.name : 'Non definito',
                                        }}
                                        bind:items={suppliers}
                                        placeholder="Seleziona fornitore"
                                        name="supplier" />
                                </span>
                            </div>

                            <div class="form-group col-12 col-md-4">
                                <!-- svelte-ignore a11y-label-has-associated-control -->
                                <label>Pagata<b>*</b></label>
                                <Select
                                    hideEmptyState={true}
                                    items={[
                                        {value: false, label: 'Non pagata'},
                                        {value: true, label: 'Pagata'},
                                    ]}
                                    value={{value: row.paid, label: row.paid ? 'Pagata' : 'Non pagata'}}
                                    id="paid_{id}"
                                    placeholder="Seleziona stato"
                                    name="paid" />
                                <!-- <span class="form-text text-muted">Per favore inserisci il sesso.</span> -->
                            </div>
                        </div>
                        <div class="form-group">
                            {row?.payment?.custom_account_name}
                        </div>
                        <div class="form-group">
                            <!-- svelte-ignore a11y-label-has-associated-control -->
                            <label>Note</label>
                            <textarea
                                name="notes"
                                value={row.notes}
                                style="resize: none;"
                                rows="4"
                                class="form-control form-control-solid form-control-lg margin-t-2"
                                placeholder="Aggiungi delle note" />
                            <!-- <span class="form-text text-muted">Per favore inserisci il nome.</span> -->
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-light-primary font-weight-bold" data-dismiss="modal"
                            >Chiudi</button>
                        <button type="submit" class="btn btn-primary font-weight-bold">Salva</button>
                    </div>
                </div>
            </div>
        </div>
    </form>
</Portal>
