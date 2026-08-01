<script>
	import { X } from 'lucide-svelte';
    import Portal from 'svelte-portal';
    import {sessionToken} from 'store/stores.js';
    import {scale} from 'svelte/transition';
    import * as easing from 'svelte/easing';
    import {onMount, onDestroy} from 'svelte';
    import {replaceUID} from 'utils/ApiMiddleware.js';
    import {PlusCircle} from 'phosphor-svelte';
    import {getDataFromForm, waitForElementAndExecute} from 'utils/Functions';
    import {apiFetch} from 'utils/ApiMiddleware';
    import EditButton from 'components/buttons/EditButton.svelte';
    import DeleteButton from 'components/buttons/DeleteButton.svelte';
    import EditModal from './modals/EditModal.svelte';
    import {canPerformAction} from 'utils/Permissions';
    import {toast} from 'svelte-sonner';
    const scrollToTop = () => window.scrollTo({ top: 0, behavior: 'smooth' });
import {blockPage, unblockPage} from 'store/loadingStore.js';
    import BKNDatatable from 'components/tables/BKNDatatable.svelte';
    import { initTooltips, destroyTooltips } from 'shim/tooltip.js';
    import { initPopovers, destroyPopovers } from 'shim/popover.js';
    import {initSelectpicker} from 'shim/select.js';
    import {showModal, hideModal} from 'shim/modal.js';

    sessionToken.useLocalStorage();

    let accountForm;
    let datatable;
    let accountTypeMap = {
        1: {
            icon: 'font-weight-bolder text-dark-75 mr-2',
            text: 'Cassa',
        },
        2: {
            icon: 'font-weight-bolder text-dark-75 mr-2',
            text: 'Banca',
        },
        3: {
            icon: 'font-weight-bolder text-dark-75 mr-2',
            text: 'Altro',
        },
    };
    const enabledDictionary = {
        0: '<span class="label label-light-danger label-inline font-weight-bolder label-lg">Disabilitato</span>',
        1: '<span class="label label-light-success label-inline font-weight-bolder label-lg">Attivo</span>',
    };

    const columns = [
        {
            field: 'name',
            title: 'Nome',
            sortable: true,
            width: 150,
            autoHide: false,
            template: function (row) {
                return (
                    '<p class="text-dark-75 font-weight-bolder mb-0">' + String(row.name).toUpperCase() + '</p>'
                );
            },
        },
        {
            field: 'account_type',
            title: 'Tipo',
            sortable: true,
            responsive: {visible: 'xl'},
            width: 100,
            template: function (row) {
                return (
                    `<p class="text-dark-75 font-weight-bolder mb-0"><i class="${
                        accountTypeMap[row.account_type].icon
                    }"></i>` +
                    String(accountTypeMap[row.account_type].text).toUpperCase() +
                    '</p>'
                );
            },
        },
        {
            field: 'current_balance',
            title: 'Saldo attuale',
            sortable: true,
            width: 120,
            responsive: {visible: 'lg'},
            template: function (row) {
                return (
                    '<p class="font-weight-bolder mb-0 ' +
                    (parseFloat(row.current_balance) < 0 ? ' text-danger' : 'text-success') +
                    '">' +
                    parseFloat(row.current_balance).toLocaleString('it-IT', {
                        style: 'currency',
                        currency: 'EUR',
                        minimumFractionDigits: 2,
                    }) +
                    '</p>'
                );
            },
        },
        {
            field: 'initial_balance',
            title: 'Saldo iniziale',
            sortable: true,
            width: 120,
            responsive: {visible: 'lg'},
            template: function (row) {
                return (
                    '<p class="text-dark-75 font-weight-bolder mb-0">' +
                    parseFloat(row.initial_balance).toLocaleString('it-IT', {
                        style: 'currency',
                        currency: 'EUR',
                        minimumFractionDigits: 2,
                    }) +
                    '</p>'
                );
            },
        },
        {
            field: 'enabled',
            title: 'Stato',
            sortable: true,
            responsive: {visible: 'lg'},
            width: 90,
            template: function (row) {
                return enabledDictionary[row.enabled ? 1 : 0];
            },
        },
        {
            field: '',
            title: '',
            sortable: false,
            textAlign: 'right',
            autoHide: false,
            width: 100,
            minWidth: '100%',
            template: function (row) {
                waitForElementAndExecute(`#action-col-${row.custom_account_id}`, () => {
                    if (document.querySelector(`#action-col-${row.custom_account_id}`))
                        document.querySelector(`#action-col-${row.custom_account_id}`).innerHTML = '';
                    let editBtn = new EditButton({
                        target: document.querySelector(`#action-col-${row.custom_account_id}`),
                        intro: true,
                        props: {
                            disabled: !canPerformAction('bookeeping.management.accounts.update'),
                            // hidden: !row.editable,
                        },
                    });

                    document.querySelector(`#editAccountModal-${row.custom_account_id}`)?.remove();

                    let editModal = new EditModal({
                        target: document.querySelector(`#action-col-${row.custom_account_id}`),
                        intro: true,
                        props: {
                            id: row.custom_account_id,
                            row: row,
                            datatableHandle: {reload: () => datatable.reload()},
                        },
                    });

                    editBtn.$on('open', data => {
                        showModal(`editAccountModal-${row.custom_account_id}`);
                    });
                    let deleteBtn = new DeleteButton({
                        target: document.querySelector(`#action-col-${row.custom_account_id}`),
                        intro: true,
                        props: {
                            disabled:
                                !row.deletable || !canPerformAction('bookeeping.management.accounts.delete'),
                            // hidden: !row.editable,
                        },
                    });

                    deleteBtn.$on('open', data => {
                        swal.fire({
                            text: 'Vuoi eliminare il conto?',
                            icon: 'warning',
                            buttonsStyling: true,
                            showCancelButton: true,
                            cancelButtonText: 'Annulla',
                            confirmButtonText: 'Elimina',
                            reverseButtons: true,
                            confirmButtonColor: '#d63030',
                        }).then(async function (result) {
                            if (result.isConfirmed) {
                                blockPage({
                                    overlayColor: '#000000',
                                    state: 'primary',
                                    message: 'Eliminazione in corso...',
                                });

                                try {
                                    const response = await apiFetch(
                                        replaceUID(
                                            __bakney.env.API.BALANCE_SHEET_ACCOUNTS.DELETE,
                                            row.custom_account_id
                                        ),
                                        {
                                            method: 'DELETE',
                                        }
                                    );

                                    if (!response.error) {
                                        datatable.reload();
                                        toast.success('Conto eliminato!');
                                    } else {
                                        toast.error('Qualcosa è andato storto.');
                                    }
                                } finally {
                                    unblockPage();
                                }
                            }
                        });
                    });
                });
                return `<div id="action-col-${row.custom_account_id}" class="action-column pr-4"></div>`;
            },
        },
    ];

    async function create(data) {
        blockPage({
            overlayColor: '#000000',
            state: 'primary',
            message: 'Creazione conto...',
        });

        try {
            data.initial_balance = parseFloat(data.initial_balance.replace(',', '.'));

            const url = __bakney.env.API.BALANCE_SHEET_ACCOUNTS.ADD;

            const res = await apiFetch(url, {
                method: 'POST',
                body: JSON.stringify(data),
            });

            if (res.status == 200) {
                datatable.reload();
                document.getElementById('account_form').reset();

                toast.success('Conto creato con successo.');
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
        } finally {
            unblockPage();
        }
    }

    onMount(() => {
        initTooltips(document.body);
        initPopovers(document.body);
        initSelectpicker(document.getElementById('account_type'));
    });

    onDestroy(() => {
        document.querySelectorAll('.popover').forEach(popover => popover.remove());
    });

    function initForm() {
        accountForm?.destroy();
        accountForm = FormValidation.formValidation(document.getElementById('account_form'), {
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

    function handleValidation(e) {
        if (!accountForm) initForm();
        accountForm?.validate().then(function (status) {
            if (status === 'Valid') {
                create(getDataFromForm(e));
                hideModal('addAccountModal');
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

<!--begin::Entry-->
<div

    class="d-flex flex-column-fluid font-weight-bold text-dark-50">
    <!--begin::Container-->
    <div class="container">
        <!--begin::Card-->
        <div class="card card-custom gutter-b">
            <div class="card-header flex-wrap border-0 p-0">
                <div class="card-title">
                    <h3 class="card-label font-size-h2">
                        Conti finanziari
                        <span class="d-block text-muted pt-2 font-size-sm">Contiene la lista completa dei conti.</span>
                    </h3>
                </div>
                <div class="card-toolbar">
                    {#if canPerformAction('bookeeping.management.accounts.create')}
                        <!--begin::Button-->
                        <!-- svelte-ignore a11y-click-events-have-key-events -->
                        <span
                            data-toggle="modal"
                            data-target="#addAccountModal"
                            class="btn btn-sm btn-primary font-weight-bolder m-2 d-flex align-items-center">
                            <PlusCircle size={18} weight="duotone" />
                            <span class="ml-md-1 ml-0"><span class="d-none d-md-inline-block">Conto</span></span>
                        </span>
                        <!--end::Button-->
                    {/if}
                </div>
            </div>
            <div class="card-body p-0">
                <BKNDatatable
                    bind:datatable
                    {columns}
                    url={__bakney.env.API.BALANCE_SHEET_ACCOUNTS.LIST}
                    serverPaging={false}
                    serverFiltering={false}
                    serverSorting={false}
                    showDividerFilter={false}
                    loadFilters={() => {
                        const statusEl = document.getElementById('bkn_datatable_search_status');
                        statusEl?.addEventListener('change', function (e) {
                            datatable.search(e.currentTarget.value.toLowerCase(), 'status_flag');
                        });
                        initSelectpicker(statusEl);
                    }}
                />
            </div>
        </div>
        <!--end::Card-->
    </div>
    <!--end::Container-->
</div>
<!--end::Entry-->

<!-- svelte-ignore missing-declaration -->
<Portal target="#portal-elements">
    <!-- Modal-->
    <form class="form" id="account_form" on:submit|preventDefault={handleValidation}>
        <div
            class="modal fade"
            id="addAccountModal"
            tabindex="-1"
            role="dialog"
            aria-labelledby="staticBackdrop"
            aria-hidden="true">
            <div class="modal-dialog modal-dialog-centered modal-dialog-scrollable" role="document">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title" id="exampleModalLabel">Creazione di un conto economico</h5>
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
                                    name="account_type"
                                    class="form-control selectpicker form-control-solid form-control-lg"
                                    id="account_type">
                                    <option value={1}>Cassa</option>
                                    <option value={2}>Banca</option>
                                    <option value={3}>Altro</option>
                                </select>
                                <!-- <span class="form-text text-muted">Per favore inserisci il sesso.</span> -->
                            </div>
                            <div class="form-group">
                                <!-- svelte-ignore a11y-label-has-associated-control -->
                                <label>Saldo Iniziale<b>*</b></label>
                                <div class="input-group input-group-solid">
                                    <div class="input-group-prepend">
                                        <span class="input-group-text fs-1-1">€</span>
                                    </div>
                                    <input
                                        name="initial_balance"
                                        type="text"
                                        inputmode="decimal"
                                        class="form-control fs-1-1"
                                        id="initial_balance"
                                        placeholder="0,00" />
                                </div>
                            </div>
                            <div class="form-group">
                                <!-- svelte-ignore a11y-label-has-associated-control -->
                                <label>Codice<b>*</b></label>
                                <input
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
