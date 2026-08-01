<script>
    import {sessionToken} from 'store/stores.js';
    import {onMount, onDestroy} from 'svelte';
    import {link} from 'svelte-spa-router';
    import {replaceUID} from 'utils/ApiMiddleware.js';
    import DeleteButton from 'components/buttons/DeleteButton.svelte';
    import {waitForElementAndExecute} from 'utils/Functions';
    const scrollToTop = () => window.scrollTo({ top: 0, behavior: 'smooth' });
import {PlusCircle} from 'phosphor-svelte';
    import {canPerformAction} from 'utils/Permissions';
    import {toast} from 'svelte-sonner';
    import BKNDatatable from 'components/tables/BKNDatatable.svelte';
    import { initTooltips, destroyTooltips } from 'shim/tooltip.js';
    import { initPopovers, destroyPopovers } from 'shim/popover.js';
    import {initSelectpicker} from 'shim/select.js';
	import { UiApp } from 'shim/ui.js';

    sessionToken.useLocalStorage();

    let carnets = [];
    let datatable;

    const statusTextDictionary = {
        false: '<span class="label label-light-info label-inline font-weight-bolder label-lg">privato</span>',
        true: '<span class="label label-light-success label-inline font-weight-bolder label-lg">pubblico</span>',
    };

    const mapFunction = function (raw) {
        var dataSet = raw;
        if (typeof raw.data !== 'undefined') {
            dataSet = raw.data;
        }
        carnets = dataSet;
        return dataSet;
    };

    window.deleteCarnet = id => {
        swal.fire({
            text: 'Vuoi eliminare definitivamente il carnet? Non influirà sui carnet già venduti.',
            icon: 'question',
            buttonsStyling: true,
            showCancelButton: true,
            cancelButtonText: 'Annulla',
            confirmButtonText: 'Elimina',
            reverseButtons: true,
        }).then(function (result) {
            if (result.isConfirmed) {
                UiApp.blockPage({
                    overlayColor: '#000000',
                    state: 'primary',
                    message: 'Eliminazione in corso...',
                });

                const url = replaceUID(__bakney.env.API.CARNET.DELETE, id);

                window
                    .fetch(url, {
                        method: 'POST',
                        headers: {
                            Accept: 'application/json',
                            'Content-Type': 'application/json',
                            Authorization: 'Bearer ' + $sessionToken,
                        },
                    })
                    .then(response => {
                        response.json();
                        // spinner stop
                        UiApp.unblockPage();
                        if (response.status == 200) {
                            toast.success('Corso Eliminato!');
                            datatable.reload();
                            initTooltips(document.body);
                        } else {
                            let modalText =
                                response.status == 403
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
                            }).then(function () {
                                scrollToTop();
                            });
                        }
                    });
            }
        });
    };

    const columns = [
        {
            field: 'title',
            title: 'Titolo',
            autoHide: false,
            sortable: false,
            template: function (row) {
                return (
                    '</div style="cursor:pointer"><a href=\'/#/course/carnet/list/detail/' +
                    row.carnet_id +
                    '\'><div class="d-flex justify-content-start align-items-center"><div class="mr-1">' +
                    '</div><span class="navi-text font-weight-bolder text-hover-primary" style="cursor: pointer"><b>' +
                    row.title.charAt(0).toUpperCase() +
                    row.title.slice(1) +
                    '</b></span></div></a>'
                );
            },
        },
        {
            field: 'description',
            title: 'Descrizione',
            sortable: false,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                let lenSubstring = 20;
                return (
                    '<span style="word-break: keep-all;">' +
                    row.description.substring(0, lenSubstring) +
                    (row.description.length > lenSubstring ? '[...]' : '') +
                    '<span>'
                );
            },
        },
        {
            field: 'public',
            title: 'Stato',
            width: 70,
            sortable: false,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                return statusTextDictionary[row.public];
            },
        },
        {
            field: 'fee',
            title: 'Costo',
            width: 70,
            sortable: false,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                // TODO: red or green color if positive or negative
                let amount = "<span style='font-weight:600'>€ " + row.fee.replace('.', ',') + '</span>';
                return amount;
            },
        },
        {
            field: 'creation_date',
            title: 'Data Creazione',
            type: 'date',
            width: 120,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            sortCallback: function (data, sort, column) {
                let dataArray = Object.values(data);
                dataArray.sort(function (a, b) {
                    let timeA = new Date(a['creation_date']).getTime();
                    let timeB = new Date(b['creation_date']).getTime();
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
                return moment(new Date(row.creation_date)).format('DD/MM/YYYY');
            },
        },
        {
            field: '',
            title: '',
            sortable: false,
            overflow: 'visible',
            textAlign: 'right',
            autoHide: false,
            width: 70,
            minWidth: '100%',
            template: function (row) {
                waitForElementAndExecute(`#action-col-${row.carnet_id}`, () => {
                    if (document.querySelector(`#action-col-${row.carnet_id}`))
                        document.querySelector(`#action-col-${row.carnet_id}`).innerHTML = '';
                    let deleteBtn = new DeleteButton({
                        target: document.querySelector(`#action-col-${row.carnet_id}`),
                        intro: true,
                        props: {
                            disabled: !canPerformAction('association.carnet.delete'),
                        },
                    });

                    deleteBtn.$on('open', data => {
                        swal.fire({
                            text: 'Vuoi eliminare il carnet?',
                            icon: 'warning',
                            buttonsStyling: true,
                            showCancelButton: true,
                            cancelButtonText: 'Annulla',
                            confirmButtonText: 'Elimina',
                            reverseButtons: true,
                            confirmButtonColor: '#d63030',
                        }).then(async function (result) {
                            if (result.isConfirmed) {
                                deleteCarnet(row.carnet_id);
                            }
                        });
                    });
                });
                return `<div id="action-col-${row.carnet_id}" class="action-column pr-4"></div>`;
            },
        },
    ];

    onMount(() => {
        initTooltips(document.body);
        initPopovers(document.body);
    });

    onDestroy(() => {
        document.querySelectorAll('.popover').forEach(popover => popover.remove());
    });
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
                        Carnet
                        <span class="d-block text-muted pt-2 font-size-sm"
                            >Contiene la lista completa dei carnet entrate che hai creato.</span>
                    </h3>
                </div>
                <div class="card-toolbar">
                    {#if canPerformAction('association.carnet.create')}
                        <a
                            href="/course/carnet/add"
                            use:link
                            class="btn btn-primary btn-sm m-2 d-flex align-items-center font-weight-boldest">
                            <PlusCircle size={18} weight="duotone" class="mr-1" />Carnet</a>
                    {/if}
                </div>
            </div>
            <div class="card-body p-0">
                <BKNDatatable
                    bind:datatable
                    {columns}
                    {mapFunction}
                    url={__bakney.env.API.CARNET.LIST}
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
                >
                    <div slot="search-header">
                        <div class="d-flex align-items-center">
                            <select class="form-control form-control-solid mb-0" id="bkn_datatable_search_status">
                                <option value="">Tutti gli stati</option>
                                <option value="1">in bozza</option>
                                <option value="2">pubblicato</option>
                            </select>
                        </div>
                    </div>
                </BKNDatatable>
            </div>
        </div>
        <!--end::Card-->
    </div>
    <!--end::Container-->
</div>
<!--end::Entry-->
