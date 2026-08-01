<script>
	import { X } from 'lucide-svelte';
    import {apiFetch, replaceUID} from 'utils/ApiMiddleware';
    const scrollToTop = () => window.scrollTo({ top: 0, behavior: 'smooth' });
import {blockPage, unblockPage} from 'store/loadingStore.js';
    import Portal from 'svelte-portal';
    import {getDataFromForm} from 'utils/Functions';
    import {createEventDispatcher, onMount} from 'svelte';
    import {toast} from 'svelte-sonner';
    import {initSelectpicker} from 'shim/select.js';
    import {hideModal} from 'shim/modal.js';

    const dispatch = createEventDispatcher();

    export let id;
    export let row;
    export let datatableHandle;

    let accountForm;

    function initForm() {
        accountForm?.destroy();
        accountForm = FormValidation.formValidation(document.getElementById('account_form_edit_' + id), {
            fields: {
                name: {
                    validators: {
                        notEmpty: {
                            message: 'Il nome è obbligatorio.',
                        },
                    },
                },
                account_type: {
                    validators: {
                        notEmpty: {
                            message: 'Seleziona una tipologia di conto.',
                        },
                    },
                },
                initial_balance: {
                    validators: {
                        notEmpty: {
                            message: 'Seleziona un bilancio iniziale valido.',
                        },
                    },
                },
                account_code: {
                    validators: {
                        notEmpty: {
                            message: 'Seleziona un bilancio iniziale valido.',
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
        blockPage({message: 'Modifica conto...'});

        let res;

        try {
            data.initial_balance = parseFloat(data.initial_balance.replace(',', '.'));
            data.enabled = data.enabled == 1 ? true : false;

            const url = replaceUID(__bakney.env.API.BALANCE_SHEET_ACCOUNTS.UPDATE, row.custom_account_id);

            res = await apiFetch(url, {
                method: 'PATCH',
                body: JSON.stringify(data),
            });
        } finally {
            unblockPage();
        }

        if (res.status == 200) {
            datatableHandle.reload();
            toast.success('Conto modificato con successo.');
            initElements();
            initForm();
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
        if (!accountForm) initForm();
        accountForm?.validate().then(function (status) {
            if (status === 'Valid') {
                update(getDataFromForm(e));
                hideModal('editAccountModal-' + id);
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

    function initElements() {
        row.initial_balance = row.initial_balance.replace('.', ',');
        initSelectpicker(document.getElementById('account_type_' + id));
        initSelectpicker(document.getElementById('enabled_' + id));
        setTimeout(() => {
        }, 500);
    }

    onMount(() => {
        initElements();
    });
</script>

<!-- svelte-ignore missing-declaration -->
<Portal>
    <!-- Modal-->
    <form class="form" id="account_form_edit_{id}" on:submit|preventDefault={handleValidation}>
        <div
            class="modal fade"
            id="editAccountModal-{id}"
            tabindex="-1"
            role="dialog"
            aria-labelledby="staticBackdrop"
            aria-hidden="true">
            <div class="modal-dialog modal-dialog-centered modal-dialog-scrollable" role="document">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title" id="exampleModalLabel">Modifica conto economico</h5>
                        <button type="button" class="close" data-dismiss="modal" aria-label="Close">
                            <X size={16} aria-hidden="true" />
                        </button>
                    </div>
                    <div class="modal-body">
                        <div>
                            <div class="form-group">
                                <!-- svelte-ignore a11y-label-has-associated-control -->
                                <label>Nome<b>*</b></label>
                                <input
                                    value={row.name}
                                    name="name"
                                    type="text"
                                    class="form-control form-control-solid form-control-lg margin-t-2"
                                    placeholder="Nome"
                                    style="text-transform:capitalize" />
                                <!-- <span class="form-text text-muted">Per favore inserisci il nome.</span> -->
                            </div>
                            <div class="form-group">
                                <!-- svelte-ignore a11y-label-has-associated-control -->
                                <label>Tipologia<b>*</b></label>
                                <select
                                    value={row.account_type}
                                    name="account_type"
                                    class="form-control selectpicker form-control-solid form-control-lg"
                                    id="account_type_{id}">
                                    <option value={1}>Cassa</option>
                                    <option value={2}>Banca</option>
                                    <option value={3}>Altro</option>
                                </select>
                                <!-- <span class="form-text text-muted">Per favore inserisci il sesso.</span> -->
                            </div>
                            <div class="form-group">
                                <!-- svelte-ignore a11y-label-has-associated-control -->
                                <label>Stato<b>*</b></label>
                                <select
                                    value={row.enabled ? 1 : 0}
                                    name="enabled"
                                    class="form-control selectpicker form-control-solid form-control-lg"
                                    id="enabled_{id}">
                                    <option value={1}>Attivo</option>
                                    <option value={0}>Disattivato</option>
                                </select>
                            </div>
                            <div class="form-group">
                                <!-- svelte-ignore a11y-label-has-associated-control -->
                                <label>Saldo Iniziale<b>*</b></label>
                                <div class="input-group input-group-solid">
                                    <div class="input-group-prepend">
                                        <span class="input-group-text fs-1-1">€</span>
                                    </div>
                                    <input
                                        value={row.initial_balance}
                                        name="initial_balance"
                                        type="text"
                                        class="form-control fs-1-1"
                                        id="initial_balance_{id}"
                                        placeholder="0,00" />
                                </div>
                            </div>
                            <div class="form-group">
                                <!-- svelte-ignore a11y-label-has-associated-control -->
                                <label>Codice<b>*</b></label>
                                <input
                                    value={row.account_code}
                                    name="account_code"
                                    type="text"
                                    class="form-control form-control-solid form-control-lg margin-t-2"
                                    placeholder="Codice"
                                    style="text-transform:uppercase" />
                            </div>
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
