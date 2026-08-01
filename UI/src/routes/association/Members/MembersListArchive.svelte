<script>
    import {sessionToken} from 'store/stores.js';
    import {replaceUID} from 'utils/ApiMiddleware.js';
    import {onDestroy, onMount} from 'svelte';
    import {scale} from 'svelte/transition';
    import * as easing from 'svelte/easing';
    import {capitalizeWords, waitForElementAndExecute} from 'utils/Functions.js';
    import ArchiveButton from 'components/buttons/ArchiveButton.svelte';
    import RepeatButton from 'components/buttons/RepeatButton.svelte';
    import {canPerformAction} from 'utils/Permissions.js';
    import NavigationTab from './shared/NavigationTab.svelte';
    import {toast} from 'svelte-sonner';
    import RenewalModal from './modals/RenewalModal.svelte';
    import BKNDatatable from 'components/tables/BKNDatatable.svelte';
    import { initTooltips, destroyTooltips } from 'shim/tooltip.js';
    import { initPopovers, destroyPopovers } from 'shim/popover.js';
    import {initSelectpicker} from 'shim/select.js';
	import { UiApp, UiUtil } from 'shim/ui.js';
    sessionToken.useLocalStorage();

    const statusTextDictionary = {
        1: '<span class="label label-light-info label-inline font-weight-bolder label-lg">Non Firmata</span>',
        2: '<span class="label label-light-warning label-inline font-weight-bolder label-lg">In Attesa</span>',
        3: '<span class="label label-light-danger label-inline font-weight-bolder label-lg">Rifiutata</span>',
        4: '<span class="label label-light-success label-inline font-weight-bolder label-lg">Accettata</span>',
        5: '<span class="label label-light-dark label-inline font-weight-bolder label-lg">Archiviata</span>',
    };

    onDestroy(() => {
        document.querySelectorAll('.popover').forEach(popover => popover.remove());
        document.querySelectorAll('.tooltip').forEach(popover => popover.remove());
    });

    let datatable;

    const columns = [
                {
                    field: 'associate',
                    title: 'Tesserato',
                    autoHide: false,
                    sortable: false,
                    template: function (row) {
                        let name = capitalizeWords(`${row.associate.first_name} ${row.associate.last_name}`);
                        if (name == ' ') name = '- -';
                        let content =
                            "<div class='font-weight-200 ml-2 mr-2'><div class='mb-2'>" +
                            row.associate?.born_date +
                            ' (' +
                            (row.associate?.is_minor ? 'Minore' : 'Adulto') +
                            ')' +
                            "</div><div class='mb-2'>" +
                            (row.associate?.sex == 'F'
                                ? 'Femmina'
                                : row.associate?.sex == 'M'
                                ? 'Maschio'
                                : 'Altro') +
                            "</div><div class='mb-0'> C.F. " +
                            row.associate?.tax_code?.toUpperCase() +
                            '</div></div>';
                        return UiUtil.isMobileDevice()
                            ? `<a class="navi-text font-weight-bolder text-hover-primary" href="javascript:openDetail('/members/list/detail/${row.subscription_id}/info')">` +
                                  name +
                                  '</span></a>'
                            : `<a class="navi-text font-weight-bolder text-hover-primary" href="javascript:openDetail('/members/list/detail/${row.subscription_id}/info')">` +
                                  '<span id="popover-sub-detail" style="cursor:pointer" ' +
                                  'data-toggle="popover" data-trigger="hover" title="' +
                                  row.associate.first_name?.charAt(0)?.toUpperCase() +
                                  row.associate.first_name?.slice(1) +
                                  (name == '- -' ? '-' : '') +
                                  ' ' +
                                  row.associate.last_name?.charAt(0)?.toUpperCase() +
                                  row.associate.last_name?.slice(1) +
                                  (name == '- -' ? '-' : '') +
                                  '" data-html="true" ' +
                                  'data-content="' +
                                  content +
                                  '">' +
                                  name +
                                  '</span></a>';
                    },
                },
                {
                    field: 'status_flag',
                    title: 'Stato',
                    width: 100,
                    autoHide: false,
                    sortCallback: function (data, sort, column) {
                        let statusArray = Object.values(data);

                        statusArray.sort(function (a, b) {
                            let timeA = parseInt(a['status_flag']);
                            let timeB = parseInt(b['status_flag']);
                            if (sort === 'asc') {
                                return timeA > timeB ? 1 : timeA < timeB ? -1 : 0;
                            } else {
                                return timeA < timeB ? 1 : timeA > timeB ? -1 : 0;
                            }
                        });
                        let newData = {};
                        for (let i = 0; i < statusArray.length; i++) {
                            newData[i] = statusArray[i];
                        }

                        return newData;
                    },
                    template: function (row) {
                        return statusTextDictionary[row.status_flag];
                    },
                },
                {
                    field: 'creation_date',
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
                    field: 'user',
                    title: 'Utente',
                    checked: true,
                    sortable: false,
                    width: 120,
                    minWidth: '100%',
                    responsive: {
                        visible: 'lg',
                        hidden: 'md',
                    },
                    template: function (row) {
                        return `<a class="navi-text font-weight-bold text-lowercase text-hover-primary" href="/#/search/profile/${row.user?.username}">${row.user?.username}</a>`;
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
                        waitForElementAndExecute(`#action-col-${row.subscription_id}`, () => {
                            if (document.querySelector(`#action-col-${row.subscription_id}`))
                                document.querySelector(`#action-col-${row.subscription_id}`).innerHTML = '';

                            if (!row?.current_year && !row?.next_years) {
                                let renewBtn = new RepeatButton({
                                    target: document.querySelector(`#action-col-${row.subscription_id}`),
                                    intro: true,
                                    props: {
                                        disabled: !row.renewal_available,
                                        hidden: false,
                                        popover_text: 'Rinnova Iscrizione',
                                    },
                                });
                                renewBtn.$on('open', data => {
                                    // window.renewSubscription(row.associate?.associate_id);

                                    let renewalModal = new RenewalModal({
                                        target: document.querySelector(`#action-col-${row.subscription_id}`),
                                        props: {
                                            id: row.subscription_id,
                                            show: true,
                                            formData: row,
                                        },
                                    });

                                    // listen for confirm event
                                    renewalModal.$on('confirm', data => {
                                        // reload datatable
                                        datatable.reload();
                                        // delete renewal modal
                                        renewalModal.$destroy();
                                    });
                                });
                            }

                            let archiveBtn = new ArchiveButton({
                                target: document.querySelector(`#action-col-${row.subscription_id}`),
                                intro: true,
                                props: {
                                    disabled: !canPerformAction('association.members.archive.update'),
                                    // hidden: !row.editable,
                                },
                            });

                            archiveBtn.$on('open', async data => {
                                let result = {isConfirmed: false};

                                result = await swal.fire({
                                    text: "Vuoi spostare l'atleta nel libro soci?",
                                    icon: 'question',
                                    buttonsStyling: true,
                                    showCancelButton: true,
                                    cancelButtonText: 'Annulla',
                                    confirmButtonText: 'Sposta nel libro soci',
                                    reverseButtons: true,
                                });
                                if (result.isConfirmed) {
                                    UiUtil.scrollTop();
                                    UiApp.blockPage({
                                        overlayColor: '#000000',
                                        type: 'v2',
                                        state: 'primary',
                                        message: 'Sposto in libro soci...',
                                    });
                                    const url = replaceUID(__bakney.env.API.SUBSCRIPTION.ARCHIVE, row.subscription_id);

                                    let response = await window.fetch(url, {
                                        method: 'POST',
                                        headers: {
                                            Accept: 'application/json',
                                            'Content-Type': 'application/json',
                                            Authorization: 'Bearer ' + $sessionToken,
                                        },
                                    });
                                    response.json();
                                    UiApp.unblockPage();
                                    if (response.status === 200) {
                                        toast.success('Iscrizione spostata nel libro soci.');
                                        datatable.reload();
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
                                        }).then(function () {
                                            UiUtil.scrollTop();
                                        });
                                    }
                                }
                            });
                        });

                        return `<div id="action-col-${row.subscription_id}" class="action-column pr-4"></div>`;
                    },
                },
            ];

    const loadFilters = function () {
        const statusEl = document.getElementById('bkn_datatable_search_status');
        statusEl?.addEventListener('change', function (e) {
            datatable.search(e.currentTarget.value.toLowerCase(), 'status_flag');
        });
        initSelectpicker(statusEl);
    };

    onMount(() => {
        initTooltips(document.body);
        initPopovers(document.body);
    });
</script>

<!--begin::Entry-->
<div  class="d-flex flex-column-fluid">
    <!--begin::Container-->
    <div class="container">
        <!--begin::Card-->
        <div class="card card-custom gutter-b">
            <NavigationTab />
            <div class="card-header flex-wrap border-0 p-0">
                <div class="card-title">
                    <h1 class="card-label font-size-h1 font-weight-bolder">
                        Archivio
                        <span class="d-block text-muted pt-2 font-size-sm">Soci e tesserati archiviati.</span>
                    </h1>
                </div>
                <div class="card-toolbar" />
            </div>
            <div class="card-body p-0">
                <BKNDatatable
                    bind:datatable
                    {columns}
                    url={__bakney.env.API.SUBSCRIPTION.LIST_ARCHIVED}
                    dataKey="data.subscriptions"
                    {loadFilters}
                />
            </div>
        </div>
        <!--end::Card-->
    </div>
    <!--end::Container-->
</div>

<!--end::Entry-->
