<script>
	import { CheckCircle as LucideCheckCircle, Info, X } from 'lucide-svelte';
    import {
        sessionToken,
        userData,
        tablesSettings,
        associatesListFilter,
        associatesListFilterDatatable,
    } from 'store/stores.js';
    import {apiFetch, replaceUID} from 'utils/ApiMiddleware.js';
    import {onDestroy, onMount, tick} from 'svelte';
    import {scale} from 'svelte/transition';
    import * as easing from 'svelte/easing';
    import {SmartSelect} from 'components/formBuilder/preview-blocks';
    import {canPerformAction} from 'utils/Permissions.js';
    import {
        ShareNetwork,
        Printer,
        ArchiveBox,
        UserSwitch,
        CheckCircle,
        TrashSimple,
        Funnel,
        Warning,
        Wallet,
        PenNib,
        UserCirclePlus,
        Link,
        Calendar,
    } from 'phosphor-svelte';
    import {push} from 'svelte-spa-router';
    import {slide} from 'svelte/transition';
    import ApproveButton from 'components/buttons/ApproveButton.svelte';
    import {capitalizeWords, clearLocalStorageForPartialMatching, debounce, waitForElementAndExecute} from 'utils/Functions';
    import XCircle from 'components/buttons/XCircle.svelte';
    import MoreActionsButton from 'components/buttons/MoreActionsButton.svelte';
    import PaymentModal from './modals/PaymentModal.svelte';
    import RepeatButton from 'components/buttons/RepeatButton.svelte';
    import DeleteButton from 'components/buttons/DeleteButton.svelte';
    import MoveToArchiveButton from 'components/buttons/MoveToArchiveButton.svelte';
    import SignatureModal from './modals/SignatureModal.svelte';
    import DocumentPreviewModal from 'components/modals/DocumentPreviewModal.svelte';
    import DocumentButton from 'components/buttons/DocumentButton.svelte';
    import CurrentViewPrintingModal from 'components/modals/CurrentViewPrintingModal.svelte';
    import ShareModuleSubscriptionLink from 'components/modals/ShareModuleSubscriptionLink.svelte';
    import NavigationTab from './shared/NavigationTab.svelte';
    import {toast} from 'svelte-sonner';
    import ExpandPayments from 'components/buttons/ExpandPayments.svelte';
    import ExportData from './shared/ExportData.svelte';
    import RenewalModal from './modals/RenewalModal.svelte';
    import {MembersListTypeLabel} from 'utils/enumUtils';
    import DetailDrawer from './detail/DetailDrawer.svelte';
    import QueryFilter from 'components/filters/QueryFilter.svelte';
    import NotesButton from 'components/buttons/NotesButton.svelte';
    import ShareModulePresubscriptionLink from 'components/modals/ShareModulePresubscriptionLink.svelte';
    import SignaturePreview from 'components/buttons/SignaturePreview.svelte';
    import BKNDatatable from 'components/tables/BKNDatatable.svelte';
    import { initTooltips, destroyTooltips } from 'shim/tooltip.js';
    import { initPopovers, destroyPopovers } from 'shim/popover.js';
    import DateInput from 'components/inputs/DateInput.svelte';
    import DateRangePicker from 'components/inputs/DateRangePicker.svelte';
    import {createDropzone, getDropzone} from 'shim/dropzone.js';
    import {initSelectpicker} from 'shim/select.js';
	import { UiApp, UiUtil } from 'shim/ui.js';

    sessionToken.useLocalStorage();
    userData.useLocalStorage();
    tablesSettings.useLocalStorage();

    let subscriptionId = null;
    let certificate_expring_date = moment().format('DD/MM/YYYY');
    let isValid = false;
    let uploadedFile = false;
    let datatable;
    let datatableKey = 0;
    let ready = false;
    let selectedCounter = 0;
    let visibleMultiaction = false;
    let tags;
    let searchTagName = '';
    let aiSuggestion = false;
    let generalSearch = '';

    const statusTextDictionary = {
        1: '<span class="label label-light-info label-inline font-weight-bolder label-lg">non firmata</span>',
        2: '<span class="label label-light-warning label-inline font-weight-bolder label-lg">in attesa</span>',
        3: '<span class="label label-light-danger label-inline font-weight-bolder label-lg">rifiutata</span>',
        4: '<span class="label label-light-success label-inline font-weight-bolder label-lg">accettata</span>',
        5: '<span class="label label-light label-inline font-weight-bolder label-lg">ritirata/o</span>',
    };

    const statusCurrentYearDictionary = {
        0: '<span><span class="label label-warning label-dot mr-2"></span><span class="font-weight-bold text-warning">precedente</span></span>',
        1: '<span><span class="label label-success label-dot mr-2"></span><span class="font-weight-bold text-success">corrente</span></span>',
        3: '<span><span class="label label-info label-dot mr-2"></span><span class="font-weight-bold text-info">Preiscritto</span></span>',
    };

    const statusMedicalCertificateDictionary = {
        valid: '<span class="font-weight-bolder"><span class="label label-success label-dot mr-2"></span>[DAYS] giorni</span>',
        expiring:
            '<span class="font-weight-bolder"><span class="label label-warning label-dot mr-2"></span>[DAYS] giorni</span>',
        expired:
            '<span class="font-weight-bolder text-danger"><span class="label label-danger label-dot mr-2"></span>scaduto</span>',
        not_present:
            '<span class="font-weight-bolder text-danger"><span class="label label-danger label-dot mr-2"></span>scadenza mancante</span>',
    };

    let filters = [
        {
            name: 'Età ',
            value: '',
            active: false,
            type: 'age',
            key: 'age',
            data: {
                options: [],
                and: true,
            },
        },
    ];

    let columns = [
        {
            field: 'subscription_id',
            title: '#',
            sortable: false,
            autoHide: false,
            width: 20,
            selector: {
                class: '',
            },
            textAlign: 'center',
        },
        {
            field: 'associate',
            title: 'Socio',
            checked: true,
            autoHide: false,
            sortable: true,
            minWidth: '100%',
            width: 150,
            fireClick: true,
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
                return (
                    '<div style="line-height: 1.1">' +
                    (UiUtil.isMobileDevice()
                        ? `<span class="navi-text font-weight-bolder text-hover-primary text-primary">` +
                          name +
                          '</span>'
                        : `<span class="navi-text font-weight-bolder text-hover-primary text-primary">` +
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
                          '</span></span>') +
                    '<br><span class="font-size-xs text-dark-65">' +
                    MembersListTypeLabel[row.type] +
                    '</span></div>'
                );
            },
        },
        {
            field: 'status_flag',
            title: 'Stato',
            checked: true,
            width: 100,
            fireClick: true,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
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
            field: 'age',
            title: 'Età',
            checked: true,
            sortable: true,
            fireClick: true,
            width: 50,
            responsive: {
                visible: 'xxl',
                hidden: 'xl',
            },
            template: function (row) {
                return `<span class="font-weight-boldest">${row.age}</span>`;
            },
        },
        {
            field: 'current_year',
            title: 'Anno',
            checked: true,
            sortable: false,
            fireClick: true,
            width: 100,
            responsive: {
                visible: 'xxl',
                hidden: 'xl',
            },
            template: function (row) {
                let key = row?.current_year && !row?.next_years ? 1 : 0;
                if (row?.next_years) key = 3;
                return statusCurrentYearDictionary[key];
            },
        },
        {
            field: 'creation_date',
            title: 'Data',
            checked: true,
            fireClick: true,
            width: 80,
            type: 'date',
            responsive: {
                visible: 'xxxxl',
                hidden: 'xxxl',
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
            fireClick: true,
            width: 130,
            minWidth: '100%',
            responsive: {
                visible: 'xxxl',
                hidden: 'xxl',
            },
            template: function (row) {
                return `<span class="navi-text font-weight-bold text-lowercase text-hover-primary" >${row.user?.username}</span>`;
            },
        },

        {
            field: 'all_payments_paid',
            title: 'Pagamenti',
            sortable: false,
            checked: true,
            width: 120,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                waitForElementAndExecute(`#payments-col-${row.subscription_id}`, () => {
                    if (document.querySelector(`#payments-col-${row.subscription_id}`))
                        document.querySelector(`#payments-col-${row.subscription_id}`).innerHTML = '';

                    const paymentsCol = document.querySelector(`#payments-col-${row.subscription_id}`);
                    paymentsCol.innerHTML = ''; // Clear previous content

                    const paymentsBtn = new ExpandPayments({
                        target: paymentsCol,
                        intro: true,
                        props: {
                            disabled: false,
                            popover_text: 'Vedi pagamenti',
                            color: row.all_payments_paid ? 'success' : 'warning',
                            text: row.all_payments_paid
                                ? `Saldati`
                                : `${new Intl.NumberFormat('it-IT', {style: 'currency', currency: 'EUR'}).format(
                                      row.payments_info?.to_be_paid
                                  )}`,
                        },
                    });

                    paymentsBtn.$on('open', data => {
                        let paymentModal = new PaymentModal({
                            target: paymentsCol,
                            props: {
                                id: row.subscription_id,
                                associate_id: row.associate.associate_id,
                                subscription_id: row.subscription_id,
                                show: true,
                            },
                        });

                        // reload datatable on close
                        paymentModal.$on('close', data => {
                            setTimeout(() => {
                                datatable?.reload();
                            }, 400);
                        });
                    });
                });
                return `<div id="payments-col-${row.subscription_id}" class="action-column pr-4 d-flex align-items-center"></div>`;
            },
        },
        {
            field: 'documenti',
            title: 'Info',
            checked: true,
            sortable: false,
            overflow: 'visible',
            width: 120,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                let pdfLink =
                    __bakney.env.API.DOCUMENT.RETRIEVE +
                    '/' +
                    row.document +
                    '?download=false&token=' +
                    row.document_token;
                if (!row.document || !row.document_token) {
                    pdfLink = null;
                }

                waitForElementAndExecute(`#info-col-${row.subscription_id}`, () => {
                    if (document.querySelector(`#info-col-${row.subscription_id}`))
                        document.querySelector(`#info-col-${row.subscription_id}`).innerHTML = '';

                    let signaturePreview = new SignaturePreview({
                        target: document.querySelector(`#info-col-${row.subscription_id}`),
                        intro: true,
                        props: {
                            signature_present: row.signature_present,
                        },
                    });

                    let documentButton = new DocumentButton({
                        target: document.querySelector(`#info-col-${row.subscription_id}`),
                        intro: true,
                        props: {
                            disabled: !pdfLink,
                            popover_text: `Vedi modulo d'iscrizione di ${row.associate.first_name} ${row.associate.last_name}`,
                        },
                    });

                    documentButton.$on('open', data => {
                        let filePreview = new DocumentPreviewModal({
                            target: document.querySelector(`#info-col-${row.subscription_id}`),
                            intro: true,
                            props: {
                                pdfLink: pdfLink,
                                id: row.subscription_id,
                                title: `Modulo d'iscrizione di ${row.associate.first_name} ${row.associate.last_name}`,
                            },
                        });
                    });
                });

                return `<div id="info-col-${row.subscription_id}" class="info-column pr-4"></div>`;
            },
        },
        {
            field: 'Azioni',
            title: '',
            sortable: false,
            overflow: 'visible',
            textAlign: 'right',
            minWidth: '100%',
            autoHide: false,
            width: 140,
            template: function (row) {
                waitForElementAndExecute(`#action-col-${row.subscription_id}`, () => {
                    if (document.querySelector(`#action-col-${row.subscription_id}`))
                        document.querySelector(`#action-col-${row.subscription_id}`).innerHTML = '';

                    let disableApproval = row.status_flag && row.status_flag == 4;
                    let disableReject = row.status_flag && row.status_flag == 3;

                    if (row.notes) {
                        let notesButton = new NotesButton({
                            target: document.querySelector(`#action-col-${row.subscription_id}`),
                            intro: true,
                            props: {
                                notes: row.notes,
                            },
                        });
                    }

                    if (row?.current_year && !row?.next_years) {
                        let approveBtn = new ApproveButton({
                            target: document.querySelector(`#action-col-${row.subscription_id}`),
                            intro: true,
                            props: {
                                disabled: disableApproval || !canPerformAction('association.members.update'),
                                hidden: false,
                                popover_text: 'Approva Iscrizione',
                            },
                        });
                        approveBtn.$on('open', data => {
                            window.acceptSubscription(row.subscription_id);
                        });
                        let rejectBtn = new XCircle({
                            target: document.querySelector(`#action-col-${row.subscription_id}`),
                            intro: true,
                            props: {
                                disabled: disableReject || !canPerformAction('association.members.update'),
                                hidden: false,
                                popover_text: 'Rifiuta Iscrizione',
                            },
                        });
                        rejectBtn.$on('open', data => {
                            window.rejectSubscription(row.subscription_id);
                        });

                        let moreActionsButton = new MoreActionsButton({
                            target: document.querySelector(`#action-col-${row.subscription_id}`),
                            intro: true,
                            props: {
                                disabled: !canPerformAction('association.members.delete'),
                                hidden: false,
                                id: row.subscription_id,
                                actions: [
                                    {
                                        icon: PenNib,
                                        label: 'Firma',
                                        action: () => {
                                            // init SignatureModal
                                            let signatureModal = new SignatureModal({
                                                target: document.querySelector(`#action-col-${row.subscription_id}`),
                                                props: {
                                                    id: row.subscription_id,
                                                    show: true,
                                                },
                                            });

                                            // listen for close event
                                            signatureModal.$on('close', data => {
                                                // reload datatable
                                                datatable?.reload();
                                            });
                                        },
                                    },
                                    {
                                        icon: Wallet,
                                        label: 'Pagamenti',
                                        action: () => {
                                            // init PaymentModal
                                            let paymentModal = new PaymentModal({
                                                target: document.querySelector(`#action-col-${row.subscription_id}`),
                                                props: {
                                                    id: row.subscription_id,
                                                    associate_id: row.associate.associate_id,
                                                    subscription_id: row.subscription_id,
                                                    show: true,
                                                },
                                            });
                                        },
                                    },
                                    {
                                        icon: TrashSimple,
                                        label: 'Elimina',
                                        action: () => window.deleteSubscription(row.subscription_id),
                                        color: 'danger',
                                    },
                                ],
                            },
                        });
                    } else if (!row?.current_year && !row?.next_years) {
                        let renewBtn = new RepeatButton({
                            target: document.querySelector(`#action-col-${row.subscription_id}`),
                            intro: true,
                            props: {
                                disabled: !row.renewal_available,
                                hidden: false,
                                popover_text: !row.renewal_available
                                    ? 'Iscrizione già rinnovata'
                                    : 'Rinnova Iscrizione',
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
                                datatable?.reload();
                                // delete renewal modal
                                renewalModal.$destroy();
                            });
                        });

                        let archiveBtn = new MoveToArchiveButton({
                            target: document.querySelector(`#action-col-${row.subscription_id}`),
                            intro: true,
                            props: {
                                disabled: !canPerformAction('association.members.update'),
                                hidden: false,
                                popover_text: 'Archivia Iscrizione',
                            },
                        });
                        archiveBtn.$on('open', data => {
                            window.archiveSubscription(row.subscription_id);
                        });
                    } else {
                        let deleteBtn = new DeleteButton({
                            target: document.querySelector(`#action-col-${row.subscription_id}`),
                            intro: true,
                            props: {
                                disabled: false,
                            },
                        });

                        deleteBtn.$on('open', data => {
                            window.deleteSubscription(row.subscription_id);
                        });
                    }
                });
                return `<div id="action-col-${row.subscription_id}" class="action-column pr-4"></div>`;
            },
        },
    ];
    let visibleColumns = [...columns.filter(column => column.checked || column.title == '' || column.title == '#')];
    window.updateSubscription = id => {
        subscriptionId = id;
    };

    $: {
        isValid = !moment(certificate_expring_date, 'DD/MM/YYYY').isBefore(moment());
    }

    async function fetchTags() {
        apiFetch(__bakney.env.API.SUBSCRIPTION.TAGS.LIST, {
            method: 'GET',
        }).then(res => {
            if (res.status == 200) {
                tags = [...res.response?.tags];
            }
        });
    }

    async function createTag() {
        apiFetch(__bakney.env.API.SUBSCRIPTION.TAGS.ADD, {
            method: 'POST',
            body: JSON.stringify({
                tag_name: searchTagName,
            }),
        }).then(res => {
            if (res.status == 200) {
                fetchTags();
                toast.success('Tag creato con successo');
            } else {
                toast.error(res.response.msg, 'Errore');
            }
            searchTagName = '';
        });
    }

    async function deleteTag(tag_id) {
        // ask for confirmation before deleting with sweetalert
        swal.fire({
            text: 'Vuoi eliminare il tag?',
            icon: 'question',
            buttonsStyling: true,
            showCancelButton: true,
            cancelButtonText: 'Annulla',
            confirmButtonText: 'Elimina',
            reverseButtons: true,
        }).then(function (result) {
            if (result.isConfirmed) {
                apiFetch(replaceUID(__bakney.env.API.SUBSCRIPTION.TAGS.DELETE, tag_id), {
                    method: 'DELETE',
                }).then(res => {
                    visibleMultiaction = false;
                    if (res.status == 200) {
                        fetchTags();
                        toast.success('Tag eliminato con successo');
                        datatable?.reload();
                    } else {
                        toast.error(res.response.msg, 'Errore');
                    }
                });
            }
        });
    }

    let periodStartBook = '';
    let periodEndBook = '';

    function handleBookPeriodRangeChange(e) {
        periodStartBook = e.detail.start;
        periodEndBook = e.detail.end;
        $associatesListFilter.period_start = formatAssociatesPeriodDate(periodStartBook);
        $associatesListFilter.period_end = formatAssociatesPeriodDate(periodEndBook);
        applyAssociatesFilterWhenReady();
    }

    onMount(async () => {
        clearLocalStorageForPartialMatching('bkn_datatable_payments_');
        fetchTags();

        if ($tablesSettings['members-books']) {
            // loop through tableSettings and update columns
            Object.keys($tablesSettings['members-books']).forEach(key => {
                // find column idx by title
                let idx = columns.findIndex(column => column.title == $tablesSettings['members-books'][key].title);
                // if idx is found
                if (idx != -1) {
                    // update column checked
                    columns[idx].checked = $tablesSettings['members-books'][key].checked;
                }
            });
            visibleColumns = [...columns.filter(column => column.checked || column.title == '' || column.title == '#')];

            // add missing columns with default values
            let defaultColumns = [
                ...columns.filter(column => column.checked || column.title == '' || column.title == '#'),
            ];

            // merge with default if not present in visibleColumns
            visibleColumns = [
                ...visibleColumns,
                ...defaultColumns.filter(column => !visibleColumns.map(x => x.title).includes(column.title)),
            ];
        }
        ready = true;
        await tick();

        initTooltips(document.body);
        initPopovers(document.body);

        const dropzoneEl = document.querySelector('#bkn_dropzone');
        if (dropzoneEl) {
            createDropzone(dropzoneEl, {
                accept: 'image/*,application/pdf',
                multiple: false,
            });
        }
    });

    onDestroy(() => {
        clearLocalStorageForPartialMatching('bkn_datatable_payments_');
        document.querySelectorAll('.popover').forEach(popover => popover.remove());
        document.querySelectorAll('.tooltip').forEach(popover => popover.remove());
    });

    window.acceptSubscription = async (id, skipReload = false, skipToast = false) => {
        let result = {isConfirmed: false};
        if (!skipToast)
            result = await swal.fire({
                text: "Vuoi accettare l'iscrizione?",
                icon: 'question',
                buttonsStyling: true,
                showCancelButton: true,
                cancelButtonText: 'Annulla',
                confirmButtonText: 'Approva',
                reverseButtons: true,
            });
        else result = {isConfirmed: true};
        if (result.isConfirmed) {
            if (!skipToast)
                UiApp.blockPage({
                    overlayColor: '#000000',
                    state: 'primary',
                    message: 'Approvazione in corso...',
                });

            const url = replaceUID(__bakney.env.API.SUBSCRIPTION.APPROVE, id);

            let response = await window.fetch(url, {
                method: 'POST',
                headers: {
                    Accept: 'application/json',
                    'Content-Type': 'application/json',
                    Authorization: 'Bearer ' + $sessionToken,
                },
            });
            response.json();
            // spinner stop
            if (!skipToast) UiApp.unblockPage();
            if (response.status == 200) {
                if (!skipToast) toast.success('Iscrizione Approvata con Successo.');
                if (!skipReload) {
                    datatable?.reload();
                }
            } else {
                let modalText =
                    response.status == 403
                        ? 'Operazione non permessa.'
                        : 'Scusa, ho individuato degli errori, riprova.';
                if (!skipToast) toast.error(modalText);
            }
            return response;
        }
    };

    async function resubSelected() {
        swal.fire({
            text: `Vuoi reiscrivere ${selectedCounter} soci?`,
            icon: 'question',
            buttonsStyling: true,
            showCancelButton: true,
            cancelButtonText: 'Annulla',
            confirmButtonText: 'Reiscrivi selezionati',
            reverseButtons: true,
        }).then(async function (result) {
            if (result.isConfirmed) {
                let checkedNodes = datatable.getSelectedRecords();
                let records = datatable.dataSet;
                let count = checkedNodes.length;
                UiApp.blockPage({
                    overlayColor: '#000000',
                    state: 'primary',
                    message: 'Reiscrizione in corso...',
                });
                let results = [];
                if (count > 0) {
                    for (let i = 0; i < count; i++) {
                        let id = checkedNodes[i].dataset.row;
                        results.push(
                            new Promise(async (resolve, reject) => {
                                let r = await window.renewSubscription(records[id].associate.associate_id, true, true);
                                resolve(r.status == 200);
                            })
                        );
                    }
                }

                Promise.all(results).then(values => {
                    UiApp.unblockPage();
                    visibleMultiaction = false;

                    if (values.length > 0) {
                        if (values.length != selectedCounter) {
                            toast.warning(`${values.length} Associati Reiscritti su ${selectedCounter}.`);
                        } else if (values.every(r => r)) {
                            toast.success(`${values.length} Associati Reiscritti.`);
                        } else {
                            let valid = values.filter(r => r).length;
                            toast.warning(`${valid} Associati Reiscritti su ${selectedCounter}.`);
                        }
                    } else {
                        toast.warning(`Nessun associato reiscritto.`);
                    }
                    datatable?.reload();
                });
            }
        });
    }

    async function approveSelected() {
        swal.fire({
            text: `Vuoi accettare ${selectedCounter} soci?`,
            icon: 'question',
            buttonsStyling: true,
            showCancelButton: true,
            cancelButtonText: 'Annulla',
            confirmButtonText: 'Approva selezionati',
            reverseButtons: true,
        }).then(async function (result) {
            if (result.isConfirmed) {
                let checkedNodes = datatable.getSelectedRecords();
                let records = datatable.dataSet;
                let count = checkedNodes.length;
                UiApp.blockPage({
                    overlayColor: '#000000',
                    state: 'primary',
                    message: 'Approvazione in corso...',
                });
                let results = [];
                if (count > 0) {
                    for (let i = 0; i < count; i++) {
                        let id = checkedNodes[i].dataset.row;
                        results.push(
                            new Promise(async (resolve, reject) => {
                                let r = await window.acceptSubscription(records[id].subscription_id, true, true);
                                resolve(r.status == 200);
                            })
                        );
                    }
                }

                Promise.all(results).then(values => {
                    UiApp.unblockPage();
                    visibleMultiaction = false;

                    if (values.length > 0) {
                        if (values.length != selectedCounter) {
                            toast.warning(`${values.length} Associati Approvati su ${selectedCounter}.`);
                        } else if (values.every(r => r)) {
                            toast.success(`${values.length} Associati Approvati.`);
                        } else {
                            let valid = values.filter(r => r).length;
                            toast.warning(`${valid} Associati Approvati su ${selectedCounter}.`);
                        }
                    } else {
                        toast.warning(`Nessun associato approvato.`);
                    }
                    datatable?.reload();
                });
            }
        });
    }

    async function archiveSelected() {
        swal.fire({
            text: `Vuoi archiviare ${selectedCounter} soci?`,
            icon: 'question',
            buttonsStyling: true,
            showCancelButton: true,
            cancelButtonText: 'Annulla',
            confirmButtonText: 'Archivia selezionati',
            reverseButtons: true,
        }).then(async function (result) {
            if (result.isConfirmed) {
                let checkedNodes = datatable.getSelectedRecords();
                let records = datatable.dataSet;
                let count = checkedNodes.length;
                UiApp.blockPage({
                    overlayColor: '#000000',
                    state: 'primary',
                    message: 'Archiviazione in corso...',
                });
                let results = [];
                if (count > 0) {
                    for (let i = 0; i < count; i++) {
                        let id = checkedNodes[i].dataset.row;
                        results.push(
                            new Promise(async (resolve, reject) => {
                                let r = await window.archiveSubscription(records[id].subscription_id, true, true);
                                resolve(r.status == 200);
                            })
                        );
                    }
                }

                Promise.all(results).then(values => {
                    UiApp.unblockPage();
                    visibleMultiaction = false;

                    if (values.length > 0) {
                        if (values.length != selectedCounter) {
                            toast.warning(`${values.length} Associati Archiviati su ${selectedCounter}.`);
                        } else if (values.every(r => r)) {
                            toast.success(`${values.length} Associati Archiviati.`);
                        } else {
                            let valid = values.filter(r => r).length;
                            toast.warning(`${valid} Associati Archiviati su ${selectedCounter}.`);
                        }
                    } else {
                        toast.warning(`Nessun associato archiviato.`);
                    }
                    datatable?.reload();
                });
            }
        });
    }

    async function assignTag() {
        // get the selected tag
        let selectedTags = tags.filter(tag => tag.checked);
        let unassignedTags = tags.filter(tag => !tag.checked);

        // get selected subscriptions rows
        let checkedNodes = datatable.getSelectedRecords();
        let records = datatable.dataSet;
        let count = checkedNodes.length;
        UiApp.blockPage({
            overlayColor: '#000000',
            state: 'primary',
            message: 'Assegnazione tag in corso...',
        });
        let results = [];
        if (count > 0) {
            for (let i = 0; i < count; i++) {
                let id = checkedNodes[i].dataset.row;
                for (let j = 0; j < selectedTags.length; j++) {
                    results.push(
                        new Promise(async (resolve, reject) => {
                            let r = await apiFetch(
                                replaceUID(
                                    replaceUID(
                                        __bakney.env.API.SUBSCRIPTION.TAGS.ASSIGN,
                                        records[id].subscription_id,
                                        '<sub_uid>'
                                    ),
                                    selectedTags[j].tag_id
                                ),
                                {
                                    method: 'PATCH',
                                }
                            );
                            resolve(r.status == 200);
                        })
                    );
                }
                for (let j = 0; j < unassignedTags.length; j++) {
                    results.push(
                        new Promise(async (resolve, reject) => {
                            let r = await apiFetch(
                                replaceUID(
                                    replaceUID(
                                        __bakney.env.API.SUBSCRIPTION.TAGS.UNASSIGN,
                                        records[id].subscription_id,
                                        '<sub_uid>'
                                    ),
                                    unassignedTags[j].tag_id
                                ),
                                {
                                    method: 'PATCH',
                                }
                            );
                            resolve(r.status == 200);
                        })
                    );
                }
            }
        }

        Promise.all(results).then(values => {
            UiApp.unblockPage();
            visibleMultiaction = false;

            if (values.length > 0) {
                if (values.every(r => r)) {
                    toast.success('Tag assegnati con successo');
                } else {
                    toast.warning(`${values.length} Tag assegnati a ${selectedCounter} soci.`);
                }
            } else {
                toast.warning(`Nessun tag assegnato.`);
            }
            datatable?.reload();
        });
    }

    async function transferAccount() {
        let checkedNodes = datatable.getSelectedRecords();
        let records = datatable.dataSet;
        let count = checkedNodes.length;
        if (count != 1) return toast.warning(`Non hai selezionato nessun tesserato.`);
        let row = records[parseInt(checkedNodes[0].dataset.row)];

        // check if the user is the same of the subscription
        if (row.user.username != $userData.username) {
            return toast.warning(`Non puoi dare accesso ad un iscritto che già ha accesso.`);
        }

        let emailValue = '';
        // get the associate email
        if (row.associate?.email) emailValue = row.associate.email;
        else if (row.associate?.tutor?.email) emailValue = row.associate.tutor.email;
        //<div id="subscription-transfer-modal"></div>
        let htmlSelect = `<input value="${emailValue}" class="form-control form-control-solid mb-2" name="email" id="subscription-receipient" placeholder="inserisci l\'email dell\'utente o il suo username"><br><div class="mb-4"></div><div id="warning-message"></div>`;
        // searchData?.user?.sport_association?.subscription_fee_plans.forEach(plan => {
        //     htmlSelect += `<option value="${plan.id}">${plan.name} (${plan.subscription_fee}€)</option>`;
        // });
        let emailRegex = new RegExp('^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\\.[a-zA-Z0-9-.]+$', 'g');
        let alreadyChecked = false;
        let lastValue = '';
        let user = null;
        let intervalCheckEmail = null;
        let result = await swal.fire({
            title: "Dai accesso all'iscritto con un account " + __bakney.OEM_CONFIG?.name,
            icon: 'info',
            html: htmlSelect,
            buttonsStyling: true,
            showCancelButton: true,
            cancelButtonText: 'Annulla',
            confirmButtonText: `Dai accesso`,
            reverseButtons: true,
            preConfirm: async value => {
                value = document.getElementById('subscription-receipient').value || null;

                alreadyChecked = value == lastValue;
                if (alreadyChecked) return;

                lastValue = value;

                if (value != null) {
                    const res = await apiFetch(`${__bakney.env.API.OAUTH2.CHECK.EMAIL}?email=${value}&get_user`);
                    if (!res?.response?.valid && res?.response?.exception === 'email already taken.') {
                        user = res?.response?.user;
                        document.getElementById('warning-message').innerHTML = `
                            <div class="alert alert-custom alert-light-info fade show mb-5" role="alert">
                                <div class="alert-icon">
                                    <Info size={16} />
                                </div>
                                <div class="alert-text text-left">
                                    Verrà inviata una richiesta a <b>${res?.response?.user?.first_name} ${res?.response?.user?.last_name}>
                                </div>
                            </div>
                        `;
                        confirmButton.disabled = false;
                        return;

                        // check email is valid in regex
                    } else if (emailRegex.test(value)) {
                        document.getElementById('warning-message').innerHTML = `
                            <div class="alert alert-custom alert-light-warning fade show mb-5" role="alert">
                                <div class="alert-icon">
                                    <Info size={16} />
                                </div>
                                <div class="alert-text text-left">
                                    Verrà creato un account.
                                </div>
                            </div>
                        `;
                        confirmButton.disabled = false;
                        return;
                    }
                }
                document.getElementById('warning-message').innerHTML = `
                    <div class="alert alert-custom alert-light-info fade show mb-5" role="alert">
                        <div class="alert-icon">
                            <Info size={16} />
                        </div>
                        <div class="alert-text text-left">
                            Se non inserisci un'email valida associata ad un utente, questo dovrà registrarsi tramite il <b>link</b> che verrà generato.
                        </div>
                    </div>
                `;
            },
            didRender: async () => {
                let confirmButton = document.querySelector('.swal2-confirm');
                confirmButton.disabled = true;
                intervalCheckEmail = setInterval(async () => {
                    let value = document.getElementById('subscription-receipient').value || null;

                    alreadyChecked = value == lastValue;
                    if (alreadyChecked) return;

                    lastValue = value;

                    if (value != null) {
                        const res = await apiFetch(`${__bakney.env.API.OAUTH2.CHECK.EMAIL}?email=${value}&get_user`);
                        if (!res?.response?.valid && res?.response?.exception === 'email already taken.') {
                            user = res?.response?.user;
                            document.getElementById('warning-message').innerHTML = `
                            <div class="alert alert-custom alert-light-info fade show mb-5" role="alert">
                                <div class="alert-icon">
                                    <Info size={16} />
                                </div>
                                <div class="alert-text text-left">
                                    Verrà inviata una richiesta a <b>${res?.response?.user?.first_name} ${res?.response?.user?.last_name}</b>
                                </div>
                            </div>
                            `;
                            confirmButton.disabled = false;
                            return;
                        } else if (emailRegex.test(value)) {
                            document.getElementById('warning-message').innerHTML = `
                            <div class="alert alert-custom alert-light-warning fade show mb-5" role="alert">
                                <div class="alert-icon">
                                    <Info size={16} />
                                </div>
                                <div class="alert-text text-left">
                                    Verrà creato un account.
                                </div>
                            </div>
                        `;
                            confirmButton.disabled = false;
                            return;
                        }
                    }
                    return;
                }, 500);
            },
            willClose: () => {
                clearInterval(intervalCheckEmail);
            },
        });
        if (result.isConfirmed) {
            const res = await apiFetch(
                replaceUID(__bakney.env.API.SUBSCRIPTION.TRANSFER.TRANSFER, row.subscription_id),
                {
                    method: 'POST',
                    body: JSON.stringify({
                        recipient: user?.user_id,
                        email: document.getElementById('subscription-receipient').value,
                    }),
                }
            );
            if (res.status == 201) {
                toast.success('Invito di accesso creato con successo');
            } else if (res.status == 409) {
                toast.error('Questa iscrizione è già collegata a questo utente', 'Errore');
            } else {
                toast.error(res.response.msg, 'Errore');
            }
        } else {
            return;
        }
    }

    window.rejectSubscription = id => {
        swal.fire({
            text: "Vuoi rifiutare l'iscrizione?",
            icon: 'question',
            buttonsStyling: true,
            showCancelButton: true,
            cancelButtonText: 'Annulla',
            confirmButtonText: 'Rifiuta',
            reverseButtons: true,
        }).then(function (result) {
            if (result.isConfirmed) {
                UiApp.blockPage({
                    overlayColor: '#000000',
                    state: 'primary',
                    message: 'Azione in corso...',
                });

                const url = replaceUID(__bakney.env.API.SUBSCRIPTION.REJECT, id);

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
                            toast.success('Iscrizione Rifiutata.');
                            datatable?.reload();
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
                                UiUtil.scrollTop();
                            });
                        }
                    });
            }
        });
    };

    window.archiveSubscription = async (id, skipReload = false, skipToast = false) => {
        let result = {isConfirmed: false};
        if (!skipToast)
            result = await swal.fire({
                text: "Vuoi archiviare l'atleta selezionato?",
                icon: 'question',
                buttonsStyling: true,
                showCancelButton: true,
                cancelButtonText: 'Annulla',
                confirmButtonText: 'Archivia',
                reverseButtons: true,
            });
        else result = {isConfirmed: true};
        if (result.isConfirmed) {
            if (!skipToast)
                UiApp.blockPage({
                    overlayColor: '#000000',
                    state: 'primary',
                    message: 'Archiviazione in corso...',
                });

            const url = replaceUID(__bakney.env.API.SUBSCRIPTION.ARCHIVE, id);

            let response = await window.fetch(url, {
                method: 'POST',
                headers: {
                    Accept: 'application/json',
                    'Content-Type': 'application/json',
                    Authorization: 'Bearer ' + $sessionToken,
                },
            });
            response.json();
            // spinner stop
            if (!skipToast) UiApp.unblockPage();
            if (response.status === 200) {
                if (!skipToast) toast.success('Iscrizione archiviata.');
                if (!skipReload) {
                    datatable?.reload();
                }
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
            return response;
        }
    };

    window.renewSubscription = async (id, skipReload = false, skipToast = false) => {
        let result = {isConfirmed: false};
        if (!skipToast)
            result = await swal.fire({
                text: "Vuoi rinnovare l'iscrizione all'anno corrente?",
                icon: 'question',
                buttonsStyling: true,
                showCancelButton: true,
                cancelButtonText: 'Annulla',
                confirmButtonText: 'Rinnova',
                reverseButtons: true,
            });
        else result = {isConfirmed: true};
        if (result.isConfirmed) {
            if (!skipToast)
                UiApp.blockPage({
                    overlayColor: '#000000',
                    state: 'primary',
                    message: 'Reiscrizione in corso...',
                });

            const url = `${__bakney.env.API.SUBSCRIPTION.ADD}?m=only_associate`;

            // get info about the subscription
            let records = datatable.dataSet;

            // loop the records and find the associate
            let record = null;
            for (let i = 0; i < records.length; i++) {
                if (records[i].associate.associate_id == id) {
                    record = records[i];
                    break;
                }
            }

            let body = {
                associate: id,
            };

            // get plan id from meta
            let plan_id = record?.meta?.plan_id;

            // if plan id is not null, add it to the body
            if (plan_id) body.plan_id = plan_id;

            let response = await apiFetch(url, {
                method: 'POST',
                body: JSON.stringify(body),
            });
            // spinner stop
            if (!skipToast) UiApp.unblockPage();
            if (response.status === 200) {
                if (!skipToast) toast.success('Iscrizione Rinnovata.');
                if (!skipReload) {
                    datatable?.reload();
                }
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
            return response;
        }
    };

    window.deleteSubscription = id => {
        swal.fire({
            text: "Vuoi eliminare l'iscrizione?",
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

                const url = replaceUID(__bakney.env.API.SUBSCRIPTION.DELETE, id);

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
                            toast.success('Iscrizione Eliminata.');
                            datatable?.reload();
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
                                UiUtil.scrollTop();
                            });
                        }
                    });
            }
        });
    };

    function generateFilterObject() {
        let filterObj = {};
        filters.forEach(filter => {
            if (filter.active) {
                if (filter.type === 'date-range') {
                    filterObj[filter.key] = filter.data;
                } else if (filter.type === 'checkbox') {
                    filterObj[filter.key] = filter.data.options
                        .filter(option => option.checked)
                        .map(option => option.value);
                } else if (filter.type === 'tags') {
                    filterObj[filter.key] = filter.data.options
                        .filter(option => option.checked)
                        .map(option => option.tag_id);
                    filterObj['tags_and'] = filter.data.and == 1;
                } else if (filter.type === 'radio') {
                    filterObj[filter.key] = filter.value;
                } else if (filter.type === 'age') {
                    filterObj['from_age'] = filter.data.from_age;
                    filterObj['to_age'] = filter.data.to_age;
                }
            }
        });
        return filterObj;
    }

    function handleFilterApplied(event) {
        // remove tags from filterObj
        let filterObj = generateFilterObject();
        let from_age = filterObj?.from_age || '';
        let to_age = filterObj?.to_age || '';
        console.log(filterObj);
        $associatesListFilter = {...$associatesListFilter, ...filterObj, from_age: from_age, to_age: to_age};
        // apply search
        datatable?.setDataSourceParams($associatesListFilterDatatable);
    }

    function applyAssociatesFilterWhenReady() {
        if (
            $associatesListFilter.year === '3' &&
            (!$associatesListFilter.period_start || !$associatesListFilter.period_end)
        ) {
            return;
        }

        datatable?.setDataSourceParams($associatesListFilterDatatable);
    }

    function formatAssociatesPeriodDate(value) {
        if (!value) return '';

        const date = moment(value, ['YYYY-MM-DD', 'DD/MM/YYYY'], true);
        return date.isValid() ? date.format('DD/MM/YYYY') : value;
    }

    window.openDetail = function (url) {
        localStorage.setItem('bkn_datatable-1-meta_BACKUP', localStorage.getItem('bkn_datatable-1-meta'));
        push(url);
    };

    const updateMedicalCertificate = function () {
        //replace('/members/list');
        datatable?.reload();
        getDropzone('#bkn_dropzone')?.removeAllFiles();

        toast.success('Certificato Medico Aggiornato con Successo.');
    };

    const uploadMedicalCertificateUrl =
        __bakney.env.API.SUBSCRIPTION.MEDICAL_CERTIFICATE.UPLOAD.replace('<uid>', '${subscriptionId}');

    async function setCertificateExpiration() {
        await apiFetch(
            replaceUID(__bakney.env.API.SUBSCRIPTION.MEDICAL_CERTIFICATE.SET_CERTIFICATE_EXPIRATION, subscriptionId),
            {
                method: 'POST',
                body: JSON.stringify({
                    subscription_id: subscriptionId,
                    certificate_expiring_date: certificate_expring_date,
                }),
            }
        );
        aiSuggestion = false;
    }
</script>

<!-- Modal-->
<div
    class="modal fade"
    id="uploadMedicalCertificateModal"
    style="padding:0!important"
    data-backdrop="static"
    tabindex="-1"
    role="dialog"
    aria-labelledby="staticBackdrop"
    aria-hidden="true">
    <div class="modal-dialog" role="document">
        <div class="modal-content">
            <div class="modal-header">
                <h5 class="modal-title" id="exampleModalLabel">Certificato Medico</h5>
                <button type="button" class="close" data-dismiss="modal" aria-label="Close">
                    <X size={16} aria-hidden="true" />
                </button>
            </div>
            <div class="modal-body">
                <div class="dropzone dropzone-default" id="bkn_dropzone">
                    <div class="dropzone-msg dz-message needsclick">
                        <h3 class="dropzone-msg-title">Trascina o premi per caricare il Certificato Medico.</h3>
                        <span class="dropzone-msg-desc"
                            >Sono supportati file <b>pdf</b> e <b>immagini</b> di grandezza inferiore a
                            <b>5MB</b>.</span>
                    </div>
                </div>
                <div class="col-xl-12 ml-0 pl-0 pt-8">
                    <div class="form-group">
                        <!-- svelte-ignore a11y-label-has-associated-control -->
                        <label>Data di scadenza<b>*</b></label>
                        <!-- svelte-ignore a11y-click-events-have-key-events -->
                        <div on:click={() => (aiSuggestion = false)}>
                            <DateInput id="expiring_certificate" name="bornDateAssociate"
                                format="L" placeholder="Seleziona Data"
                                bind:value={certificate_expring_date} />
                        </div>
                    </div>
                    {#if aiSuggestion}
                        <!-- alert -->
                        <div
                            class="d-flex align-items-center text-bold text-warning bg-light-warning p-4 mb-4"
                            style="border-radius: 0.35rem;">
                            <Warning size={18} weight="duotone" class="mr-2" />
                            La data di scadenza è stata suggerita automaticamente dal sistema.
                        </div>
                    {/if}
                    <div class="col-12 d-flex justify-content-start align-items-center">
                        <small class="text-muted font-size-sm lh-xs">
                            Verrà inviata una mail all'utente e all'associazione per notificare la scadenza del
                            certificato.
                        </small>
                    </div>
                </div>
            </div>
            <div class="modal-footer">
                <button type="button" class="btn btn-light-primary font-weight-bold" data-dismiss="modal"
                    >Chiudi</button>
                <button
                    disabled={!isValid}
                    type="button"
                    class="btn btn-primary font-weight-bold"
                    on:click|preventDefault={async () => {
                        await setCertificateExpiration();
                        updateMedicalCertificate();
                    }}>Salva</button>
            </div>
        </div>
    </div>
</div>

<!--begin::Entry-->
<div  class="d-flex flex-column-fluid">
    <!--begin::Container-->
    <div class="container">
        <!--begin::Card-->
        <div class="card card-custom gutter-b">
            <div class="d-block d-md-flex justify-content-between">
                <NavigationTab />
                <ExportData />
            </div>
            <div class="card-header flex-wrap border-0 p-0">
                <div class="card-title">
                    <h1 class="card-label font-size-h1 font-weight-bolder">
                        Libro Soci
                        <span class="d-block text-muted pt-2 font-size-sm font-weight-bolder"
                            >Libro Soci di {$userData.sport_association.denomination}.</span>
                    </h1>
                </div>
                <div class="card-toolbar">
                    <!--begin::Button-->
                    <!-- svelte-ignore a11y-missing-attribute -->
                    <!-- svelte-ignore a11y-click-events-have-key-events -->
                    <!-- svelte-ignore a11y-no-static-element-interactions -->
                    <a
                        class="btn btn-light-warning font-weight-boldest m-2 d-flex align-items-center"
                        on:click={() => {
                            let modal = new ShareModulePresubscriptionLink({
                                target: document.body,
                                props: {
                                    show: true,
                                },
                            });
                        }}>
                        <Link size="19" weight="duotone" class="mr-0 mr-md-2" />
                        Condividi link pre-iscrizioni
                    </a>
                    <!--begin::Button-->
                    <!-- svelte-ignore a11y-missing-attribute -->
                    <!-- svelte-ignore a11y-click-events-have-key-events -->
                    <!-- svelte-ignore a11y-no-static-element-interactions -->
                    <a
                        class="btn btn-light-primary font-weight-boldest m-2 d-flex align-items-center"
                        on:click={() => {
                            let modal = new ShareModuleSubscriptionLink({
                                target: document.body,
                                props: {
                                    show: true,
                                },
                            });
                        }}>
                        <ShareNetwork size="19" weight="duotone" class="mr-0 mr-md-2" />
                        Condividi link iscrizioni
                    </a>
                    <!--end::Button-->
                    <!--begin::Button-->
                    {#if canPerformAction('association.members.create')}
                        <a
                            href="/#/members/add"
                            class="btn btn-primary font-weight-boldest d-flex align-items-center m-2">
                            <UserCirclePlus size="19" weight="duotone" class="mr-0 mr-md-2" />
                            Aggiungi
                        </a>
                    {/if}
                    <!--end::Button-->
                </div>
            </div>
            <div class="card-body p-0">
                <!--begin: Datatable-->
                {#if ready}
                {#key datatableKey}
                <BKNDatatable
                    bind:datatable
                    bind:selectedCounter
                    bind:visibleMultiaction
                    columns={visibleColumns}
                    url={__bakney.env.API.SUBSCRIPTION.LIST}
                    dataKey="data.subscriptions"
                    params={$associatesListFilterDatatable || {}}
                    showDividerFilter={false}
                    clicked={function (td, obj) {
                        let basicDrawer = new DetailDrawer({
                            target: document.querySelector('#drawer-elements'),
                            intro: true,
                            props: {
                                subscriptionId: obj.subscription_id,
                                title: `${obj.associate?.first_name || ''} ${obj.associate?.last_name || ''}`,
                            },
                        });
                        basicDrawer.$on('close', () => {
                            datatable?.reload();
                        });
                    }}
                    loadFilters={() => {
                        const searchQueryEl = document.getElementById('bkn_datatable_search_query');
                        searchQueryEl?.addEventListener('keyup', debounce(function (e) {
                            $associatesListFilter.generalSearch = e.currentTarget.value;
                            datatable?.setDataSourceParams($associatesListFilterDatatable);
                        }, 300));

                        const statusEl = document.getElementById('bkn_datatable_search_status');
                        statusEl?.addEventListener('change', function (e) {
                            datatable.search(e.currentTarget.value.toLowerCase(), 'status_flag');
                        });
                        initSelectpicker(statusEl);

                        const currentYearEl = document.getElementById('bkn_datatable_show_current');
                        currentYearEl?.addEventListener('change', function (e) {
                            datatable.search(e.currentTarget.value.toLowerCase(), 'current_year');
                        });
                        initSelectpicker(currentYearEl);
                        initTooltips(document.body);
                        initPopovers(document.body);
                    }}>
                    <div slot="search-header" class="d-flex flex-wrap align-items-center">
                        <div class="col-12 col-md-auto my-2 px-0 mx-0 mx-md-1">
                                        <SmartSelect
                                            customClasses={'m-0 p-0 filter-select'}
                                            selectClasses={$associatesListFilter.status != ''
                                                ? 'query-filter-select border border-secondary border-2 bg-light'
                                                : 'query-filter-select border border-secondary border-dashed bg-white'}
                                            editable={false}
                                            active={false}
                                            on:change={() => {
                                                datatable?.setDataSourceParams($associatesListFilterDatatable);
                                            }}
                                            bind:value={$associatesListFilter.status}
                                            props={{
                                                id: 'search_status',
                                                name: 'status',
                                                label: null,
                                                placeholder: 'Filtra stato',
                                                required: true,
                                                clearable: false,
                                                showChevron: true,
                                                options: [
                                                    {label: 'Filtra stato', value: ''},
                                                    {label: 'Non firmata', value: '1'},
                                                    {label: 'In attesa', value: '2'},
                                                    {label: 'Rifiutata', value: '3'},
                                                    {label: 'Accettata', value: '4'},
                                                    {label: 'Ritirata', value: '5'},
                                                ],
                                                value: $associatesListFilter.status,
                                            }} />
                                    </div>
                                    <div class="col-12 col-md-auto my-2 mx-md-1 px-0">
                                        <SmartSelect
                                            customClasses={'m-0 p-0 filter-select'}
                                            selectClasses={$associatesListFilter.year != '-'
                                                ? 'query-filter-select border border-secondary border-2 bg-light'
                                                : 'query-filter-select border border-secondary border-dashed bg-white'}
                                            editable={false}
                                            active={false}
                                            bind:value={$associatesListFilter.year}
                                            on:change={() => {
                                                // update the store with current pickers values
                                                if ($associatesListFilter.year === '3') {
                                                    $associatesListFilter.period_start =
                                                        periodStartBook ? formatAssociatesPeriodDate(periodStartBook) : '';
                                                    $associatesListFilter.period_end =
                                                        periodEndBook ? formatAssociatesPeriodDate(periodEndBook) : '';
                                                } else {
                                                    $associatesListFilter.period_start = '';
                                                    $associatesListFilter.period_end = '';
                                                }
                                                applyAssociatesFilterWhenReady();
                                            }}
                                            props={{
                                                id: 'show_current',
                                                name: 'show_current',
                                                label: null,
                                                placeholder: 'Filtra Anno',
                                                required: true,
                                                clearable: false,
                                                showChevron: true,
                                                options: [
                                                    {label: 'Filtra Anno', value: '-'},
                                                    {label: 'Anno corrente', value: '1'},
                                                    {label: 'Anni precedenti', value: '0'},
                                                    {label: 'Pre-iscrizioni', value: '2'},
                                                    {label: 'Personalizzato', value: '3'},
                                                ],
                                                value: $associatesListFilter.year,
                                            }} />
                                    </div>
                                    <div
                                        style="max-width: 25rem;width: 25rem"
                                        class="{$associatesListFilter.year === '3'
                                            ? 'd-flex'
                                            : 'd-none'} col-12 col-md-auto my-2 mx-md-1 px-0">
                                        <DateRangePicker
                                            id="period_date_range_book"
                                            format="L"
                                            sizeClass=""
                                            startPlaceholder="Data inizio"
                                            endPlaceholder="Data fine"
                                            bind:startValue={periodStartBook}
                                            bind:endValue={periodEndBook}
                                            on:change={handleBookPeriodRangeChange}
                                        />
                                    </div>
                                    <div
                                        class="col-12 col-md-auto p-0 text-right text-md-left p-md-auto m-0 mx-md-1 my-2 my-md-0">
                                        <div class="dropdown dropdown-inline m-0">
                                            <button
                                                type="button"
                                                class="btn btn-light {Object.values($associatesListFilter.filter).some(
                                                    x => x
                                                )
                                                    ? 'border-secondary border-2 bg-light'
                                                    : 'border-secondary border-dashed bg-white'} btn-icon m-0"
                                                data-toggle="dropdown"
                                                aria-haspopup="true"
                                                aria-expanded="false">
                                                <Funnel size="18" weight="duotone" class="text-secondary" />
                                            </button>
                                            <!-- svelte-ignore a11y-click-events-have-key-events -->
                                            <!-- svelte-ignore a11y-no-static-element-interactions -->
                                            <div
                                                class="dropdown-menu rounded-xl py-0"
                                                on:click|preventDefault={() => {
                                                    datatable?.setDataSourceParams($associatesListFilterDatatable);
                                                }}>
                                                <!-- svelte-ignore a11y-click-events-have-key-events -->
                                                <!-- svelte-ignore a11y-no-static-element-interactions -->
                                                <form class="p-0">
                                                    <span
                                                        class="dropdown-item"
                                                        on:click|preventDefault={() => {
                                                            let filterHideAssociateMembers = document.querySelector(
                                                                'input[name="filter_hide_associate_and_members"]'
                                                            );
                                                            filterHideAssociateMembers.checked =
                                                                !filterHideAssociateMembers.checked;
                                                            $associatesListFilter.filter.hide_associate_and_members =
                                                                filterHideAssociateMembers.checked;
                                                        }}>
                                                        <!-- svelte-ignore missing-declaration -->
                                                        <label class="checkbox">
                                                            <input
                                                                type="checkbox"
                                                                bind:checked={$associatesListFilter.filter
                                                                    .hide_associate_and_members}
                                                                name="filter_hide_associate_and_members" />
                                                            <span />
                                                            <content class="ml-4"> Nascondi Soci Tesserati </content>
                                                        </label>
                                                    </span>
                                                    <span
                                                        class="dropdown-item"
                                                        on:click|preventDefault={() => {
                                                            let filter2 =
                                                                document.querySelector('input[name="filter2"]');
                                                            filter2.checked = !filter2.checked;
                                                            $associatesListFilter.filter.expired_certificate =
                                                                filter2.checked;
                                                        }}>
                                                        <!-- svelte-ignore missing-declaration -->
                                                        <label class="checkbox">
                                                            <input
                                                                type="checkbox"
                                                                bind:checked={$associatesListFilter.filter
                                                                    .expired_certificate}
                                                                name="filter2" />
                                                            <span />
                                                            <content class="ml-4"> Certificato scaduto </content>
                                                        </label>
                                                    </span>
                                                    <!-- svelte-ignore a11y-click-events-have-key-events -->
                                                    <span
                                                        class="dropdown-item"
                                                        on:click|preventDefault={() => {
                                                            let filter3 =
                                                                document.querySelector('input[name="filter3"]');
                                                            filter3.checked = !filter3.checked;
                                                            $associatesListFilter.filter.subscription_not_paid =
                                                                filter3.checked;
                                                        }}>
                                                        <label class="checkbox">
                                                            <input
                                                                type="checkbox"
                                                                bind:checked={$associatesListFilter.filter
                                                                    .subscription_not_paid}
                                                                name="filter3" />
                                                            <span />
                                                            <content class="ml-4"> Iscrizione non pagata </content>
                                                        </label>
                                                    </span>
                                                    <!-- svelte-ignore a11y-click-events-have-key-events -->
                                                    <span
                                                        class="dropdown-item"
                                                        on:click|preventDefault={() => {
                                                            let filter4 =
                                                                document.querySelector('input[name="filter4"]');
                                                            filter4.checked = !filter4.checked;
                                                            // if active disable
                                                            $associatesListFilter.filter.sort_lastname_desc =
                                                                $associatesListFilter.filter.sort_lastname_desc
                                                                    ? !filter4.checked
                                                                    : false;
                                                            $associatesListFilter.filter.sort_lastname_asc =
                                                                filter4.checked;
                                                            let q = datatable.getDataSourceQuery();
                                                            // delete the desc flag
                                                            delete q.sort_lastname_desc_flag;
                                                            datatable.setDataSourceQuery(q);
                                                            // datatable.setDataSourceParam('sort_lastname_desc_flag', 0);
                                                        }}>
                                                        <label class="checkbox">
                                                            <input
                                                                type="checkbox"
                                                                bind:checked={$associatesListFilter.filter
                                                                    .sort_lastname_asc}
                                                                name="filter4" />
                                                            <span />
                                                            <content class="ml-4"> Ordina per cognome A-Z </content>
                                                        </label>
                                                    </span>
                                                    <!-- svelte-ignore a11y-click-events-have-key-events -->
                                                    <span
                                                        class="dropdown-item"
                                                        on:click|preventDefault={() => {
                                                            let filter5 =
                                                                document.querySelector('input[name="filter5"]');
                                                            filter5.checked = !filter5.checked;
                                                            // if active disable
                                                            $associatesListFilter.filter.sort_lastname_asc =
                                                                $associatesListFilter.filter.sort_lastname_asc
                                                                    ? !filter5.checked
                                                                    : false;
                                                            $associatesListFilter.filter.sort_lastname_desc =
                                                                filter5.checked;
                                                            let q = datatable.getDataSourceQuery();
                                                            // delete the desc flag
                                                            delete q.sort_lastname_asc_flag;
                                                            datatable.setDataSourceQuery(q);
                                                        }}>
                                                        <label class="checkbox">
                                                            <input
                                                                type="checkbox"
                                                                bind:checked={$associatesListFilter.filter
                                                                    .sort_lastname_desc}
                                                                name="filter5" />
                                                            <span />
                                                            <content class="ml-4"> Ordina per cognome Z-A </content>
                                                        </label>
                                                    </span>
                                                </form>
                                            </div>
                                        </div>
                                    </div>

                                    <div
                                        class="col-12 col-md-auto p-0 text-right text-md-left p-md-auto m-0 mx-md-1 my-2 my-md-0">
                                        <QueryFilter {filters} on:filter-applied={handleFilterApplied} />
                                    </div>
                    </div>
                    <div slot="search-actions">
                        <div class="my-2 mr-1">
                            <button
                                class="btn btn-sm btn-light-primary font-weight-bolder m-0"
                                data-toggle="tooltip"
                                data-placement="bottom"
                                title="Esporta ricerca corrente in Excel o PDF"
                                on:click={() => {
                                    let query = $associatesListFilterDatatable;
                                    let queryString = Object.keys(query)
                                        .map(key => {
                                            return `${key}` + '=' + query[key];
                                        })
                                        .join('&');
                                    if (!Object.keys(query).includes('current_year')) {
                                        queryString += '&current_year=1';
                                    }
                                    let sort = datatable.getDataSourceParam('sort');
                                    if (sort) {
                                        queryString += '&sort[field]=' + sort.field + '&sort[sort]=' + sort.sort;
                                    }
                                    queryString += '&type=associates';
                                    let endpointWithQueryString = __bakney.env.API.SUBSCRIPTION.LIST + '?' + queryString;
                                    let printingModal = new CurrentViewPrintingModal({
                                        target: document.querySelector(`#portal-elements`),
                                        intro: true,
                                        props: {
                                            title: `Stampa vista corrente`,
                                            endpointWithQueryString: endpointWithQueryString,
                                        },
                                    });
                                }}>
                                <Printer size={14} weight="duotone" class="mr-1" />
                                Vista corrente
                            </button>
                        </div>
                    </div>
                    <!--begin: Selected Rows Group Action Form-->
                    <div slot="multiactions">
                        {#if visibleMultiaction}
                            <div in:slide={{duration: 100}} class="mt-10 mb-5" id="bkn_datatable_group_action_form">
                                <div class="d-flex align-items-center">
                                    <div class="font-weight-boldest mr-3 ml-2" style="font-size:1.1rem;">
                                        {selectedCounter} selezionati
                                    </div>
                                    <button
                                        disabled={!canPerformAction('association.members.update')}
                                        on:click={approveSelected}
                                        class="btn btn-sm btn-primary font-weight-bolder m-0 ml-2 p-2 d-flex align-items-center"
                                        type="button"
                                        data-toggle="tooltip"
                                        data-placement="top"
                                        title="Approva le iscrizioni selezionate"
                                        id="bkn_datatable_approve_selected">
                                        <LucideCheckCircle size="17" weight="duotone" class="mr-1" />
                                        <span class="d-none d-md-block">Approva</span></button>
                                    <button
                                        disabled={!canPerformAction('association.members.update')}
                                        on:click={archiveSelected}
                                        class="btn btn-sm btn-light-primary font-weight-bolder m-0 ml-2 p-2 d-flex align-items-center"
                                        type="button"
                                        data-toggle="tooltip"
                                        data-placement="top"
                                        title="Archivia le iscrizioni selezionate anche se nell'anno corrente"
                                        id="bkn_datatable_archive_selected">
                                        <ArchiveBox size="17" weight="duotone" class="mr-1" />
                                        <span class="d-none d-md-block">Archivia</span></button>
                                    <button
                                        disabled={selectedCounter != 1 || !canPerformAction('association.members.update')}
                                        on:click={transferAccount}
                                        class="btn btn-sm btn-light-primary font-weight-bolder m-0 ml-2 p-2 d-flex align-items-center"
                                        type="button"
                                        data-toggle="tooltip"
                                        data-placement="top"
                                        title="Dai accesso all'iscritto con un account {__bakney.OEM_CONFIG?.name}"
                                        id="bkn_datatable_transfer_selected">
                                        <UserSwitch size="17" weight="duotone" class="mr-1" />
                                        <span class="d-none d-md-block">Dai accesso</span></button>
                                </div>
                            </div>
                        {/if}
                    </div>
                    <!--end: Selected Rows Group Action Form-->
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

<style>
</style>
