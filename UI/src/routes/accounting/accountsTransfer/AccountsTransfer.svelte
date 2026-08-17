<script>
	import { Plus as LucidePlus, X } from 'lucide-svelte';
    import Portal from 'svelte-portal';
    import {sessionToken} from 'store/stores.js';
    import {scale} from 'svelte/transition';
    import * as easing from 'svelte/easing';
    import {onMount, onDestroy} from 'svelte';
    import {replaceUID} from 'utils/ApiMiddleware.js';
    import {Plus} from 'phosphor-svelte';
    import {getDataFromForm, waitForElementAndExecute} from 'utils/Functions';
    import {apiFetch} from 'utils/ApiMiddleware';
    import DeleteButton from 'components/buttons/DeleteButton.svelte';
    import {canPerformAction} from 'utils/Permissions';
    import {toast} from 'svelte-sonner';
    const scrollToTop = () => window.scrollTo({ top: 0, behavior: 'smooth' });
import {blockPage, unblockPage} from 'store/loadingStore.js';
    import BKNDatatable from 'components/tables/BKNDatatable.svelte';
    import { initTooltips, destroyTooltips } from 'shim/tooltip.js';
    import { initPopovers, destroyPopovers } from 'shim/popover.js';
    import DateInput from 'components/inputs/DateInput.svelte';
    import {initSelectpicker, refreshSelectpicker} from 'shim/select.js';
    import {hideModal} from 'shim/modal.js';

    sessionToken.useLocalStorage();

    let datatable;
    let accountForm;
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

    let accounts = null;
    let customAccountFrom = '';
    let customAccountTo = '';

    const columns = [
        {
            field: 'amount',
            title: 'Importo',
            sortable: true,
            width: 120,
            autoHide: false,
            template: function (row) {
                return (
                    '<p class="text-dark-75 font-weight-bolder mb-0">' +
                    parseFloat(row.amount).toLocaleString('it-IT', {
                        style: 'currency',
                        currency: 'EUR',
                        minimumFractionDigits: 2,
                    }) +
                    '</p>'
                );
            },
        },
        {
            field: 'date',
            title: 'Data',
            width: 90,
            type: 'date',
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            sortCallback: function (data, sort, column) {
                let dataArray = Object.values(data);
                dataArray.sort(function (a, b) {
                    let timeA = new Date(a['date']).getTime();
                    let timeB = new Date(b['date']).getTime();
                    if (sort === 'asc') {
                        return parseFloat(timeA) > parseFloat(timeB)
                            ? 1
                            : parseFloat(timeA) < parseFloat(timeB)
                            ? -1
                            : 0;
                    } else {
                        return parseFloat(timeA) < parseFloat(timeB)
                            ? 1
                            : parseFloat(timeA) > parseFloat(timeB)
                            ? -1
                            : 0;
                    }
                });
                let newData = {};
                for (let i = 0; i < dataArray.length; i++) {
                    newData[i] = dataArray[i];
                }

                return newData;
            },
            template: function (row) {
                return moment(new Date(row.date)).format('DD/MM/YYYY');
            },
        },
        {
            field: 'custom_account_from.name',
            title: 'Dal conto',
            sortable: true,
            width: 150,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                return (
                    '<p class="text-dark-50 font-weight-bolder mb-0">' +
                    String(row.custom_account_from.name).toUpperCase() +
                    '</p>'
                );
            },
        },
        {
            field: 'custom_account_to.name',
            title: 'Al conto',
            sortable: true,
            width: 150,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                return (
                    '<p class="text-dark-50 font-weight-bolder mb-0">' +
                    String(row.custom_account_to.name).toUpperCase() +
                    '</p>'
                );
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
                waitForElementAndExecute(`#action-col-${row.custom_account_transfer_id}`, () => {
                    if (document.querySelector(`#action-col-${row.custom_account_transfer_id}`))
                        document.querySelector(`#action-col-${row.custom_account_transfer_id}`).innerHTML = '';
                    let deleteBtn = new DeleteButton({
                        target: document.querySelector(`#action-col-${row.custom_account_transfer_id}`),
                        intro: true,
                        props: {
                            disabled: !canPerformAction('bookeeping.management.accountstransfers.delete'),
                        },
                    });

                    deleteBtn.$on('open', data => {
                        swal.fire({
                            text: 'Vuoi eliminare il giroconto?',
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
                                            __bakney.env.API.BALANCE_SHEET_ACCOUNTS_TRANSFER.DELETE,
                                            row.custom_account_transfer_id
                                        ),
                                        {
                                            method: 'DELETE',
                                        }
                                    );

                                    if (!response.error) {
                                        datatable.reload();
                                        toast.success('Giroconto eliminato!');
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
                return `<div id="action-col-${row.custom_account_transfer_id}" class="action-column pr-4"></div>`;
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
            data.amount = parseFloat(data.amount.replace(',', '.'));

            const url = __bakney.env.API.BALANCE_SHEET_ACCOUNTS_TRANSFER.ADD;

            const res = await apiFetch(url, {
                method: 'POST',
                body: JSON.stringify(data),
            });

            if (res.status == 200) {
                datatable.reload();
                resetForm();

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

    async function fetchCustomAccounts() {
        const res = await apiFetch(__bakney.env.API.BALANCE_SHEET_ACCOUNTS.LIST, {
            method: 'GET',
        });
        if (!res.error) {
            accounts = res?.response?.data || [];
        } else {
            toast.error('Qualcosa è andato storto.');
        }
        return;
    }

    onMount(() => {
        fetchCustomAccounts();
        initTooltips(document.body);
        initPopovers(document.body);
        setTimeout(() => {
            initSelectpicker(document.getElementById('custom_account_to'));
            initSelectpicker(document.getElementById('custom_account_from'));
        }, 200);
    });

    onDestroy(() => {
        document.querySelectorAll('.popover').forEach(popover => popover.remove());
    });

    function resetForm() {
        document.getElementById('account_form').elements['amount'].value = '';
        document.getElementById('account_form').elements['date'].value = moment().format('YYYY-MM-DD');
        document.getElementById('account_form').elements['custom_account_from'].value = '';
        document.getElementById('account_form').elements['custom_account_to'].value = '';

        refreshSelectpicker(document.getElementById('custom_account_to'));
        refreshSelectpicker(document.getElementById('custom_account_from'));
    }

    function initForm() {
        accountForm?.destroy();
        accountForm = FormValidation.formValidation(document.getElementById('account_form'), {
            fields: {
                date: {
                    validators: {
                        notEmpty: {
                            message: 'La data non può essere vuota.',
                        },
                        // date validation
                        regexp: {
                            regexp: '^[0-9]{4}-[0-9]{2}-[0-9]{2}$',
                            flags: 'ig',
                            message: 'La data non è valida',
                        },
                        date: {
                            format: 'YYYY-MM-DD',
                            message: 'La data deve essere nel formato YYYY-MM-DD',
                        },
                    },
                },
                custom_account_from: {
                    validators: {
                        notEmpty: {
                            message: 'Seleziona un conto.',
                        },
                        callback: {
                            message: 'Deve essere un conto presente nella sezione conti.',
                            callback: function (input) {
                                return accounts.some(account => account.custom_account_id === input.value);
                            },
                        },
                    },
                },
                custom_account_to: {
                    validators: {
                        notEmpty: {
                            message: 'Seleziona un conto.',
                        },
                        callback: {
                            message: 'Deve essere un conto presente nella sezione conti.',
                            callback: function (input) {
                                return accounts.some(account => account.custom_account_id === input.value);
                            },
                        },
                    },
                },
                amount: {
                    validators: {
                        notEmpty: {
                            message: 'Seleziona un importo valido.',
                        },
                        regexp: {
                            regexp: '^[0-9]{1,9},[0-9]{2}$',
                            flags: 'ig',
                            message: "L'importo deve essere > 0.",
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
                hideModal('addAccountModal');
                create(getDataFromForm(e));
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
                        Giroconti
                        <span class="d-block text-muted pt-2 font-size-sm"
                            >Contiene una lista di transanzioni tra un conto ed un altro.</span>
                    </h3>
                </div>
                <div class="card-toolbar">
                    <!--begin::Button-->
                    {#if canPerformAction('bookeeping.management.accountstransfers.create')}
                        <!-- svelte-ignore a11y-click-events-have-key-events -->
                        <!-- svelte-ignore a11y-no-static-element-interactions -->
                        <span
                            data-toggle="modal"
                            data-target="#addAccountModal"
                            on:click={() => {
                                resetForm();
                            }}
                            class="btn btn-sm btn-primary font-weight-bolder m-2">
                            <LucidePlus size={18} weight="duotone" />
                            <span class="ml-0 ml-md-1"><span class="d-none d-md-inline-block">Giroconto</span></span>
                        </span>
                        <!--end::Button-->
                    {/if}
                </div>
            </div>
            <div class="card-body p-0">
                <BKNDatatable
                    bind:datatable
                    {columns}
                    url={__bakney.env.API.BALANCE_SHEET_ACCOUNTS_TRANSFER.LIST}
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
<Portal>
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
                        <h5 class="modal-title" id="exampleModalLabel">Nuovo giroconto</h5>
                        <button type="button" class="close" data-dismiss="modal" aria-label="Close">
                            <X size={16} aria-hidden="true" />
                        </button>
                    </div>
                    <div class="modal-body" style="overflow: visible;">
                        <div>
                            <div class="form-group" style="position:relative;z-index:10000">
                                <!-- svelte-ignore a11y-label-has-associated-control -->
                                <label>Data giroconto<b>*</b></label>
                                <DateInput id="banktransfer_date" name="date" placeholder="Seleziona Data" />
                            </div>
                            <div class="form-group">
                                <!-- svelte-ignore a11y-label-has-associated-control -->
                                <label>Dal conto<b>*</b></label>
                                {#if accounts}
                                    <select
                                        bind:value={customAccountFrom}
                                        on:change={() => {
                                            setTimeout(() => refreshSelectpicker(document.getElementById('custom_account_to')), 500);
                                        }}
                                        name="custom_account_from"
                                        class="form-control selectpicker form-control-solid form-control-lg"
                                        id="custom_account_from">
                                        <option value=""> Seleziona un conto </option>
                                        {#each Array.from(accounts || [])?.filter(x => x.custom_account_id != customAccountTo) as account}
                                            <option value={account.custom_account_id}>
                                                {account.name}
                                            </option>
                                        {/each}
                                    </select>
                                {/if}
                            </div>
                            <div class="form-group">
                                <!-- svelte-ignore a11y-label-has-associated-control -->
                                <label>Al conto<b>*</b></label>
                                {#if accounts}
                                    <select
                                        bind:value={customAccountTo}
                                        on:change={() => {
                                            setTimeout(
                                                () => refreshSelectpicker(document.getElementById('custom_account_from')),
                                                500
                                            );
                                        }}
                                        name="custom_account_to"
                                        class="form-control selectpicker form-control-solid form-control-lg"
                                        id="custom_account_to">
                                        <option value=""> Seleziona un conto </option>
                                        {#each Array.from(accounts || [])?.filter(x => x.custom_account_id != customAccountFrom) as account}
                                            <option value={account.custom_account_id}>
                                                {account.name}
                                            </option>
                                        {/each}
                                    </select>
                                {/if}
                            </div>
                            <div class="form-group">
                                <!-- svelte-ignore a11y-label-has-associated-control -->
                                <label>Importo<b>*</b></label>
                                <div class="input-group input-group-solid">
                                    <div class="input-group-prepend">
                                        <span class="input-group-text fs-1-1">€</span>
                                    </div>
                                    <input
                                        name="amount"
                                        type="text"
                                        inputmode="decimal"
                                        class="form-control fs-1-1"
                                        id="amount"
                                        placeholder="0,00" />
                                </div>
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
