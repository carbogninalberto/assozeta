<script>
	import { X } from 'lucide-svelte';
    import ArchiveButton from 'components/buttons/ArchiveButton.svelte';
    import {permissions, sessionToken, tablesSettings} from 'store/stores.js';
    import {scale} from 'svelte/transition';
    import * as easing from 'svelte/easing';
    import {onDestroy, onMount} from 'svelte';
    import {replaceUID, apiFetch, updateTableSettings} from 'utils/ApiMiddleware.js';
    import {SlidersHorizontal, Export, FileCsv, FileXls} from 'phosphor-svelte';
    import {canPerformAction, isFreePlan} from 'utils/Permissions.js';
    import {waitForElementAndExecute} from 'utils/Functions.js';
    import Tabs from 'components/Tabs.svelte';
    import {toast} from 'svelte-sonner';
    const scrollToTop = () => window.scrollTo({ top: 0, behavior: 'smooth' });
import {blockPage, unblockPage} from 'store/loadingStore.js';
    import BKNDatatable from 'components/tables/BKNDatatable.svelte';
    import {initSelectpicker} from 'shim/select.js';
    import DateInput from 'components/inputs/DateInput.svelte';
    import DateRangePicker from 'components/inputs/DateRangePicker.svelte';
    import { initTooltips, destroyTooltips } from 'shim/tooltip.js';
    import { initPopovers } from 'shim/popover.js';
	import { UiApp } from 'shim/ui.js';

    sessionToken.useLocalStorage();
    permissions.useLocalStorage();
    tablesSettings.useLocalStorage();

    let categories = [];
    let selectedCounter = 0;
    let datatable;
    let paymentDateRangeStart = moment().startOf('month').format('DD/MM/YYYY');
    let paymentDateRangeEnd = moment().endOf('month').format('DD/MM/YYYY');

    function handleDateRangeChangeArchive(e) {
        paymentDateRangeStart = e.detail.start;
        paymentDateRangeEnd = e.detail.end;
        if (datatable) {
            datatable.search(formatPaymentRangeArchive(paymentDateRangeStart, paymentDateRangeEnd).toLowerCase(), 'payment_range');
        }
    }

    function formatPaymentRangeArchive(start, end) {
        if (!start && !end) return '';
        const s = start ? moment(start, 'DD/MM/YYYY').format('YYYY/MM/DD') : '';
        const e = end ? moment(end, 'DD/MM/YYYY').format('YYYY/MM/DD') : '';
        return s && e ? `${s} al ${e}` : s || e;
    }

    const statusTextDictionary = {
        false: '<span class="label label-light-warning label-inline font-weight-bolder label-lg">In attesa</span>',
        true: '<span class="label label-light-success label-inline font-weight-bolder label-lg">Pagato</span>',
    };

    const typeTextDictionary = {
        default: '<span>-</span>',
        cash: '<span>Contanti</span>',
        stripe: '<span>stripe</span>',
        transfer: '<span>Bonifico</span>',
        online: '<span>Online</span>',
        'sepa-transfer': '<span>Bonifico SEPA</span>',
        pos: '<span>PoS</span>',
    };

    const subjectDictionary = {
        0: 'Altro',
        1: 'Iscrizione',
        2: 'Corso',
    };

    const accountType = {
        1: 'Cassa',
        2: 'Banca',
        3: 'Altro',
    };

    const paymentTypeDictionary = {
        default: 'Non selezionato',
        cash: 'Contanti',
        transfer: 'Bonifico Manuale',
        online: 'Online',
        'sepa-transfer': 'Bonifico SEPA',
    };

    const groupBy = item => item.group;
    let datatableKey = 0;
    let ready = false;

    let columns = [
        {
            field: 'user',
            title: 'Informazioni',
            checked: true,
            width: 130,
            minWidth: '100%',
            autoHide: false,
            template: function (row) {
                let content = '';
                if (!row.subscription_id) {
                    if (!row.associate) {
                        if (!row.associate && !row.supplier) {
                            content = `<b>${row.description}</b>`;
                        } else if (row.supplier) {
                            content = `<b>${(
                                row.supplier.name +
                                ' (' +
                                row.supplier.tax_code +
                                ')'
                            ).toUpperCase()}</b>`;
                        } else {
                            content = `<b>${(row.user.first_name + ' ' + row.user.last_name).toUpperCase()}</b>`;
                        }
                    } else {
                        content = `<b>${(row.associate.first_name + ' ' + row.associate.last_name).toUpperCase()}<br>
                                <span class="font-size-xs text-dark-65">${row.description || ''}</span></b>`;
                    }
                } else {
                    content = `<a href="/#/members/list/detail/${row.subscription_id}/info"><b>${(
                        row.associate.first_name +
                        ' ' +
                        row.associate.last_name
                    ).toUpperCase()}</b></a>`;
                }

                return `<div class="font-size-sm" style="line-height:1.2;">${content}</div>`;
            },
        },
        {
            field: 'paid',
            title: 'Stato',
            checked: true,
            width: 70,
            sortable: true,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                return statusTextDictionary[row.paid];
            },
        },
        {
            field: 'type',
            title: 'Metodo',
            checked: true,
            width: 70,
            minWidth: '100%',
            sortable: true,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                return `<span class="font-size-sm font-weight-bold" style="line-height:1.2">${
                    typeTextDictionary[row.type]
                }</span>`;
            },
        },
        {
            field: 'expense',
            title: 'Tipo',
            checked: true,
            width: 50,
            sortable: true,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                return `<span class="font-size-sm font-weight-bold">${row.expense ? 'Uscita' : 'Entrata'}</span>`;
            },
        },
        {
            field: 'amount',
            title: 'Importo',
            checked: true,
            width: 90,
            sortable: true,
            textAlign: 'right',
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                // TODO: red or green color if positive or negative
                let amount = !row.expense
                    ? "<span style='font-weight:700;color:#2eb132'>" +
                      parseFloat(row.amount).toLocaleString('it-IT', {
                          style: 'currency',
                          currency: 'EUR',
                          minimumFractionDigits: 2,
                      }) +
                      '</span>'
                    : "<span style='font-weight:700;color:#ec0000'>" +
                      parseFloat(row.amount).toLocaleString('it-IT', {
                          style: 'currency',
                          currency: 'EUR',
                          minimumFractionDigits: 2,
                      }) +
                      '</span>';
                return amount;
            },
        },
        {
            field: 'payment_date',
            title: 'Pagato',
            checked: true,
            width: 80,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            sortable: true,
            template: function (row) {
                return row.payment_date ? moment(new Date(row.payment_date)).format('DD/MM/YYYY') : '-';
            },
        },
        // {
        //     field: 'type',
        //     title: 'Modalità Pagamento',
        //     sortable: false,
        //     template: function (row) {
        //         return paymentTypeDictionary[row.type];
        //     },
        // },

        {
            field: 'custom_account_type',
            title: 'Conto',
            checked: true,
            width: 70,
            sortable: false,
            minWidth: '100%',
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                return `<div class="font-size-sm d-flex flex-column flex-wrap" style="line-height: 1.3">
                    <span class="font-weight-bolder">${accountType[row.custom_account_type]}</span>
                    <span class="font-size-xs text-dark-65">${row.custom_account_name}</span>
                    </div>`;
            },
        },
        {
            field: 'owner',
            title: 'Intestato a',
            checked: true,
            width: 130,
            sortable: false,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                // check if associate than show associate name
                if (row.associate && row.associate.first_name && row.associate.last_name)
                    return (row.associate.first_name + ' ' + row.associate.last_name).toUpperCase();
                if (row.supplier && row.supplier.name) return row.supplier.name.toUpperCase();
                if (row.instructor && row.instructor.first_name && row.instructor.last_name)
                    return (row.instructor.first_name + ' ' + row.instructor.last_name).toUpperCase();
                return '-';
            },
        },
        {
            field: 'subject',
            title: 'Attività',
            checked: true,
            // width: 190,
            sortable: false,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                let categoryDescription = null;
                if (row.subject == 0 && row.payment_category) {
                    categoryDescription =
                        categories?.find(category => category.payment_category_id == row.payment_category)?.name || '';
                }
                // check when subscription
                if (row.subject == 1) {
                    // $userData?.sport_association?.subscription_fee_plans?.forEach(p => {
                    //     if (p.subscription_fee == row.amount) categoryDescription = '(' + (p.name || '') + ')';
                    // });

                    categoryDescription = `(${row.meta?.subscription_data?.name || 'quota singola'})`;
                }
                // check if course
                if (row.subject == 2) {
                    if (row.course && row.course.title)
                        categoryDescription = `<a data-toggle="tooltip" title="${
                            row.course.title
                        }" href="/#/course/overview/${row.course.course_id}">${row.course.title?.slice(0, 40)}${
                            row.course.title.length > 40 ? '...' : ''
                        }</a>`;
                    else categoryDescription = row.description ?? '-';
                }
                return (
                    '<div class="font-size-sm d-flex flex-column flex-wrap" style="line-height:1.2;"><span class="font-size-md font-weight-boldest">' +
                    subjectDictionary[row.subject] +
                    (categoryDescription
                        ? ` <br><span class="font-weight-bold" style="font-size:10px">${categoryDescription}</span>`
                        : '') +
                    '</span></div>'
                );
            },
        },
        {
            field: 'notes',
            title: 'Note',
            checked: false,
            sortable: false,
            width: 150,
            minWidth: '100%',
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                return (
                    '<div class="font-size-xs">' +
                    (row.notes
                        ? `<div style="cursor:pointer" data-toggle="popover" data-trigger="hover" data-html="false" data-content="${row.notes?.replace(
                              /"/g,
                              '&quot;'
                          )}">` +
                          row.notes.slice(0, 60) +
                          '...</div>'
                        : '-') +
                    '</div>'
                );
            },
        },
        {
            field: '',
            title: '',
            sortable: false,
            textAlign: 'right',
            autoHide: false,
            minWidth: '100%',
            template: function (row) {
                waitForElementAndExecute(`#action-col-${row.payment_id}`, () => {
                    if (document.querySelector(`#action-col-${row.payment_id}`))
                        document.querySelector(`#action-col-${row.payment_id}`).innerHTML = '';

                    let archiveBtn = new ArchiveButton({
                        target: document.querySelector(`#action-col-${row.payment_id}`),
                        intro: true,
                        props: {
                            disabled: !canPerformAction('bookeeping.payments.archive.update'),
                            // hidden: !row.editable,
                        },
                    });

                    archiveBtn.$on('open', async data => {
                        let result = {isConfirmed: false};

                        result = await swal.fire({
                            text: 'Vuoi spostare il pagamento nella sezione principale?',
                            icon: 'question',
                            buttonsStyling: true,
                            showCancelButton: true,
                            cancelButtonText: 'Annulla',
                            confirmButtonText: 'Sposta',
                            reverseButtons: true,
                        });
                        if (result.isConfirmed) {
                            scrollToTop();
                            blockPage({
                                overlayColor: '#000000',
                                type: 'v2',
                                state: 'primary',
                                message: "Rimozione dall'archivio...",
                            });
                            try {
                                const url = replaceUID(__bakney.env.API.PAYMENT.ARCHIVE, row.payment_id);

                                let response = await apiFetch(url, {
                                    method: 'POST',
                                });
                                if (response.status === 200) {
                                    toast.success("Rimosso dall'archivio");
                                    datatableKey++;
                                } else {
                                    let modalText =
                                        response.status === 403
                                            ? 'Operazione non permessa.'
                                            : 'Scusa, ho individuato degli errori, riprova.';
                                    swal.fire({
                                        text: modalText,
                                        icon: 'error',
                                        buttonsStyling: false,
                                        confirmButtonText: 'Ok!',
                                        customClass: {
                                            confirmButton: 'btn font-weight-bold btn-light-primary',
                                        },
                                    });
                                }
                            } finally {
                                unblockPage();
                            }
                        }
                    });
                });

                return `<div id="action-col-${row.payment_id}" class="action-column pr-4"></div>`;
            },
        },
    ];
    let visibleColumns = [...columns.filter(column => column.checked || column.title == '' || column.title == '#')];

    function exportCSV() {
        // show loading
        blockPage({
            overlayColor: '#000000',
            state: 'primary',
            message: 'Esportazione in corso...',
        });
        apiFetch(`${__bakney.env.API.PAYMENT.EXPORT}?m=csv&archived=True`)
            .then(res => {
                window.tryDownloadCSV(res);
            })
            .finally(() => {
                unblockPage();
            });
    }

    function exportXLSX() {
        // show loading
        blockPage({
            overlayColor: '#000000',
            state: 'primary',
            message: 'Esportazione in corso...',
        });
        // for older office .xsl the mime is "application/vnd.ms-excel"
        apiFetch(`${__bakney.env.API.PAYMENT.EXPORT}?m=xlsx&archived=True`)
            .then(res => {
                window.tryDownloadFile(res, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
            })
            .finally(() => {
                unblockPage();
            });
    }

    onDestroy(() => {
        document.querySelectorAll('.popover').forEach(popover => popover.remove());
        document.querySelectorAll('.tooltip').forEach(popover => popover.remove());
    });


    onMount(async () => {
        UiApp.block('#payment-card', {
            overlayColor: '#000000',
            state: 'primary',
            message: 'Attendere prego...',
        });

        // update columns based on tablesSettings, title and checked
        // check if payment columns are present in tablesSettings
        if ($tablesSettings['payments']) {
            // loop through tableSettings and update columns
            Object.keys($tablesSettings['payments']).forEach(key => {
                // find column idx by title
                let idx = columns.findIndex(column => column.title == $tablesSettings['payments'][key].title);
                // if idx is found
                if (idx != -1) {
                    // update column checked
                    columns[idx].checked = $tablesSettings['payments'][key].checked;
                } else {
                }
            });
            visibleColumns = [...columns.filter(column => column.checked || column.title == '' || column.title == '#')];
        }

        ready = true;

        UiApp.unblock('#payment-card');
    });
</script>

<!--begin::Entry-->
<div  class="d-flex flex-column-fluid">
    <!--begin::Container-->
    <div class="container">
        <!--begin::Card-->
        <div id="payment-card" class="card card-custom gutter-b">
            <Tabs
                navigationPages={[
                    {
                        title: 'Pagamenti',
                        url: '/payment/list',
                        icon: 'book',
                    },
                    {
                        title: 'Archivio',
                        url: '/payment/archive',
                        icon: 'archive',
                    },
                ]} />
            <div class="card-header flex-wrap border-0 px-0 p-0">
                <div class="card-title">
                    <h3 class="card-label font-size-h2">
                        Archivio Pagamenti
                        <span class="d-block text-muted pt-2 font-size-sm">Archivio Storico dei pagamenti.</span>
                    </h3>
                </div>
                <div class="card-toolbar">
                    <!-- svelte-ignore a11y-click-events-have-key-events -->
                    <div
                        class="dropdown dropdown-inline m-2"
                        on:click={() => {
                            if (isFreePlan()) location.href = '/#/subscription/upgrade';
                        }}>
                        <button
                            disabled={!canPerformAction('bookeeping.payments.archive.read')}
                            type="button"
                            class="btn btn-light-primary font-weight-bolder dropdown-toggle"
                            data-toggle="dropdown"
                            aria-haspopup="true"
                            aria-expanded="false">
                            <span class="navi-icon">
                                <Export size={18} weight="duotone" />
                            </span>
                            <span class="d-none d-md-inline-block">Esporta tutto</span></button>
                        <div class="dropdown-menu dropdown-menu-sm dropdown-menu-right">
                            <!--begin::Navigation-->
                            <ul class="navi flex-column navi-hover py-2">
                                <li
                                    class="navi-header font-weight-bolder text-uppercase font-size-sm text-primary pb-2">
                                    Scegli un formato:
                                </li>
                                <li class="navi-item">
                                    <!-- svelte-ignore a11y-missing-attribute -->
                                    <a class="navi-link" on:click={exportCSV} style="cursor: pointer;">
                                        <span class="navi-icon">
                                            <FileCsv size={18} weight="duotone" />
                                        </span>
                                        <span class="navi-text"><b>CSV</b> (dall'inizio)</span>
                                    </a>
                                </li>
                                <li class="navi-item">
                                    <!-- svelte-ignore a11y-missing-attribute -->
                                    <a class="navi-link" on:click={exportXLSX} style="cursor: pointer;">
                                        <span class="navi-icon">
                                            <FileXls size={18} weight="duotone" />
                                        </span>
                                        <span class="navi-text"><b>Excel</b> (dall'inizio)</span>
                                    </a>
                                </li>
                            </ul>
                            <!--end::Navigation-->
                        </div>
                    </div>
                </div>
            </div>
            <div class="card-body px-0 pt-0">
                <!--begin::Search Form-->
                <!--begin: Datatable-->
                {#if ready}
                {#key datatableKey}
                <BKNDatatable
                    bind:datatable
                    bind:selectedCounter
                    columns={visibleColumns}
                    url={__bakney.env.API.PAYMENT.LIST}
                    params={{'query[archived]': 'True'}}
                    pageSizeSelect={[10, 20, 30, 50, 100]}
                    spinnerConfig={{message: ''}}
                    showDividerFilter={false}
                    loadFilters={() => {
                        const statusEl = document.getElementById('bkn_datatable_search_status');
                        statusEl?.addEventListener('change', function (e) {
                            datatable.search(e.currentTarget.value.toLowerCase(), 'paid');
                        });
                        initSelectpicker(statusEl);

                        const expenseEl = document.getElementById('bkn_datatable_search_expense');
                        expenseEl?.addEventListener('change', function (e) {
                            datatable.search(e.currentTarget.value.toLowerCase(), 'expense');
                        });
                        initSelectpicker(expenseEl);

                        // payment date range is handled by DateRangePicker component

                        initTooltips(document.body);
                        initPopovers(document.body);
                    }}>
                    <div slot="filter-bar">
                        <div class="col-12 pb-2">
                            <div class="row align-items-center justify-content-left pl-4">
                                <div class="my-1 my-md-0 mx-2">
                                    <div class="d-flex align-items-center">
                                        <select
                                            class="form-control form-control-solid"
                                            id="bkn_datatable_search_status"
                                            style="max-width: 13rem;width: 13rem">
                                            <option value="">Filtra stato</option>
                                            <option value="true">Pagato</option>
                                            <option value="false">In attesa</option>
                                        </select>
                                    </div>
                                </div>
                                <div class="my-1 my-md-0 mx-2">
                                    <div class="d-flex align-items-center">
                                        <select
                                            style="max-width: 13rem;width: 13rem"
                                            class="form-control form-control-solid"
                                            id="bkn_datatable_search_expense">
                                            <option value="">Filtra entrata</option>
                                            <option value="true">Uscite</option>
                                            <option value="false">Entrate</option>
                                        </select>
                                    </div>
                                </div>
                                <div class="mx-2 my-1 my-md-0">
                                    <div class="d-flex align-items-center">
                                <DateRangePicker
                                    id="payment-date-range-archive"
                                    format="DD/MM/YYYY"
                                    sizeClass=""
                                    startPlaceholder="Dal"
                                    endPlaceholder="Al"
                                    bind:startValue={paymentDateRangeStart}
                                    bind:endValue={paymentDateRangeEnd}
                                    on:change={handleDateRangeChangeArchive}
                                />
                                    </div>
                                </div>
                                <div class="ml-auto my-1 my-md-0">
                                    <div class="dropdown dropleft m-0">
                                        <button
                                            type="button"
                                            class="btn btn-sm btn-light-primary btn-icon m-0"
                                            data-toggle="dropdown"
                                            aria-haspopup="true"
                                            aria-expanded="false">
                                            <SlidersHorizontal
                                                size={18}
                                                weight="bold"
                                                data-toggle="tooltip"
                                                data-placement="bottom"
                                                title="Mostra / nascondi colonne tabella" />
                                        </button>
                                        <div class="dropdown-menu">
                                            <form class="p-0">
                                                {#each Array.from(columns || []) as col, idx}
                                                    {#if col.title != '' && col.title != '#'}
                                                        <!-- svelte-ignore a11y-click-events-have-key-events -->
                                                        <span
                                                            class="dropdown-item"
                                                            on:click|preventDefault={() => {
                                                                columns[idx].checked = !columns[idx].checked;
                                                                visibleColumns = [
                                                                    ...columns.filter(
                                                                        x => x.checked || x.title == '' || x.title == '#'
                                                                    ),
                                                                ];
                                                                datatableKey++;

                                                                updateTableSettings(
                                                                    columns.map(x => {
                                                                        return {
                                                                            title: x.title,
                                                                            checked: x.checked,
                                                                        };
                                                                    }),
                                                                    'payments'
                                                                );
                                                            }}>
                                                            <label class="checkbox">
                                                                <input
                                                                    type="checkbox"
                                                                    name="filter1"
                                                                    bind:checked={columns[idx].checked} />
                                                                <span />
                                                                <content class="ml-4"> {col.title} </content>
                                                            </label>
                                                        </span>
                                                    {/if}
                                                {/each}
                                            </form>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </BKNDatatable>
                {/key}
                {/if}
                <!--end: Datatable-->
            </div>
        </div>
        <!--end::Card-->
        <!--begin::Modal-->
        <div
            class="modal fade"
            id="bkn_datatable_fetch_modal"
            tabindex="-1"
            role="dialog"
            aria-labelledby="exampleModalLabel"
            aria-hidden="true">
            <div class="modal-dialog" role="document">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title" id="exampleModalLabel">Selected Records</h5>
                        <button type="button" class="close" data-dismiss="modal" aria-label="Close">
                            <X size={16} aria-hidden="true" />
                        </button>
                    </div>
                    <div class="modal-body">
                        <div class="scroll" data-scroll="true" data-height="200">
                            <ul id="bkn_datatable_fetch_display" />
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-light-primary font-weight-bold" data-dismiss="modal"
                            >Close</button>
                    </div>
                </div>
            </div>
        </div>
        <!--end::Modal-->
    </div>
    <!--end::Container-->
</div>
<!--end::Entry-->

<svelte:head>
    <style>
        .svelte-select {
            font-size: 13px !important;
            padding-left: 1rem !important;
            border: 0 !important;
            background: var(--bg-surface-secondary) !important;
            font-size: 13px !important;
            color: var(--text-primary) !important;
        }
        .svelte-select input:focus {
            border: 0 !important;
            outline: 0 !important;
        }
        .svelte-select input {
            font-size: 13px !important;
            color: var(--text-primary) !important;
        }
        .svelte-select .selected-item {
            font-size: 13px !important;
        }
    </style>
</svelte:head>
