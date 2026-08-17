<script>
	import { CheckCircle as LucideCheckCircle, X as LucideX } from 'lucide-svelte';
    import moment from 'moment';
    import {permissions, sessionToken, tablesSettings, userData} from 'store/stores.js';
    import {scale} from 'svelte/transition';
    import * as easing from 'svelte/easing';
    import {onDestroy, onMount} from 'svelte';
    import {replaceUID, apiFetch, updateTableSettings} from 'utils/ApiMiddleware.js';
    import {
        Article,
        TrashSimple,
        SlidersHorizontal,
        PaperPlaneTilt,
        FileArrowDown,
        Export,
        FileCsv,
        FileXls,
        ArchiveBox,
        XCircle,
        X,
        Coins,
        PlusCircle,
        CheckCircle,
        UsersThree,
    } from 'phosphor-svelte';
    import EditButton from 'components/buttons/EditButton.svelte';
    import DeleteButton from 'components/buttons/DeleteButton.svelte';
    import ApproveButton from 'components/buttons/ApproveButton.svelte';
    import {canPerformAction, isFreePlan} from 'utils/Permissions.js';
    import {waitForElementAndExecute} from 'utils/Functions.js';
    import Tabs from 'components/Tabs.svelte';
    import DocumentButton from 'components/buttons/DocumentButton.svelte';
    import InvoicePreviewModal from 'components/modals/InvoicePreviewModal.svelte';
    import DocumentPreviewModal from 'components/modals/DocumentPreviewModal.svelte';
    import SubscriptionFeesPlanModal from 'components/modals/SubscriptionFeesPlanModal.svelte';
    import XCircleBtn from 'components/buttons/XCircle.svelte';
    import {toast} from 'svelte-sonner';
    import AddEditModal from './modals/AddEditModal.svelte';
    import BulkAddPaymentModal from './modals/BulkAddPaymentModal.svelte';
    import PaymentDrawer from './PaymentDrawer.svelte';
    import {SmartSelect} from 'components/formBuilder/preview-blocks/index.js';
    const scrollToTop = () => window.scrollTo({ top: 0, behavior: 'smooth' });
import {blockPage, unblockPage} from 'store/loadingStore.js';
    import {initSelectpicker, refreshSelectpicker} from 'shim/select.js';
    import DateInput from 'components/inputs/DateInput.svelte';
    import DateRangePicker from 'components/inputs/DateRangePicker.svelte';
    import BKNDatatable from 'components/tables/BKNDatatable.svelte';
    import { initTooltips, destroyTooltips } from 'shim/tooltip.js';
    import { initPopovers } from 'shim/popover.js';
	import { UiApp } from 'shim/ui.js';
    import { showCollapse, hideCollapse } from 'shim/collapse.js';

    sessionToken.useLocalStorage();
    permissions.useLocalStorage();
    tablesSettings.useLocalStorage();

    export let params;

    let stats = {
        stripe_charges: {
            application_fee_amount: 0,
            stripe_fee: 0,
            total: 0,
        },
        monthly_income: 0,
        monthly_expenses: 0,
        monthly_to_pay: 0,
        monthly_to_cash_in: 0,
        current_income: 0,
        current_expenses: 0,
        accounts: {
            cash: 0,
            bank: 0,
            other: 0,
        },
        total_transactions: 0,
        net_balance: 0,
    };
    let selectedCounter = 0;
    let datatable;
    let accounts = [];
    let dateRangeStart = moment().startOf('month').format('DD/MM/YYYY');
    let dateRangeEnd = moment().endOf('month').format('DD/MM/YYYY');

    function handleDateRangeChange(e) {
        dateRangeStart = e.detail.start;
        dateRangeEnd = e.detail.end;
        if (datatable) {
            UiApp.blockPage({
                overlayColor: '#000000',
                state: 'primary',
                message: 'Attendere prego...',
            });
            const dateRange = formatPaymentRange(dateRangeStart, dateRangeEnd);
            datatable.search(dateRange.toLowerCase(), 'payment_range');
            UiApp.unblockPage();
        }
    }

    function formatPaymentRange(start, end) {
        if (!start && !end) return '';
        const s = start ? moment(start, 'DD/MM/YYYY').format('YYYY/MM/DD') : '';
        const e = end ? moment(end, 'DD/MM/YYYY').format('YYYY/MM/DD') : '';
        return s && e ? `${s} al ${e}` : s || e;
    }    let paymentTypesMap = [
        {
            key: 'default',
            value: 'non definito',
        },
        {
            key: 'cash',
            value: 'contanti',
        },
        {
            key: 'transfer',
            value: 'Bonifico Bancario',
        },
        {
            key: 'online',
            value: 'Altro mezzo Online',
        },
        {
            key: 'sepa-transfer',
            value: 'Bonifico SEPA',
        },
        {
            key: 'stripe',
            value: 'Stripe',
        },
        {
            key: 'pos',
            value: 'PoS',
        },
    ];
    let paymentSubjectsMap = [
        {
            key: 0,
            value: 'Altro',
        },
        {
            key: 1,
            value: 'Iscrizione',
        },
        {
            key: 2,
            value: 'Corso',
        },
        {
            key: 3,
            value: 'Giroconto',
        },
    ];

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

    let datatableKey = 0;
    let ready = false;

    $: {
        let el = document.getElementById('bkn_datatable_selected_records');
        if (el) el.innerHTML = selectedCounter;
        if (selectedCounter > 0) {
            showCollapse('bkn_datatable_group_action_form');
        } else {
            hideCollapse('bkn_datatable_group_action_form');
        }
    }

    let categories = [];

    let columns = [
        {
            field: 'payment_id',
            title: '#',
            sortable: false,
            width: 20,
            autoHide: false,
            selector: {
                class: '',
            },
            textAlign: 'center',
        },
        {
            field: 'user',
            title: 'Informazioni',
            checked: true,
            width: 130,
            fireClick: true,
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
                            if (row.description)
                                content += `<br><span class="font-size-xs text-dark">${row.description || ''}</span>`;
                        } else {
                            content = `<b>${(row.user.first_name + ' ' + row.user.last_name).toUpperCase()}</b>`;
                            if (row.description)
                                content += `<br><span class="font-size-xs text-dark">${row.description || ''}</span>`;
                        }
                    } else {
                        content = `<b class="text-primary">${(
                            row.associate.first_name +
                            ' ' +
                            row.associate.last_name
                        ).toUpperCase()}</b>`;
                        if (row.description)
                            content += `<br><span class="font-size-xs text-dark">${row.description || ''}</span>`;
                    }
                } else {
                    content = `<b class="text-primary">${(
                        row.associate.first_name +
                        ' ' +
                        row.associate.last_name
                    ).toUpperCase()}</b>`;
                    if (row.description)
                        content += `<br><span class="font-size-xs text-dark">${row.description || ''}</span>`;
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
            fireClick: true,
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
            fireClick: true,
            sortable: true,
            minWidth: '100%',
            responsive: {
                visible: 'xxl',
                hidden: 'xl',
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
            fireClick: true,
            sortable: true,
            responsive: {
                visible: 'xxl',
                hidden: 'xl',
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
            fireClick: true,
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
            width: 85,
            fireClick: true,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            sortable: true,
            template: function (row) {
                return row.payment_date ? moment(new Date(row.payment_date)).format('DD/MM/YYYY') : '-';
            },
        },
        {
            field: 'creation_date',
            title: 'Data',
            checked: true,
            fireClick: true,
            width: 85,
            responsive: {
                visible: 'xxl',
                hidden: 'xl',
            },
            sortable: true,
            template: function (row) {
                return row.creation_date ? moment(new Date(row.creation_date)).format('DD/MM/YYYY') : '-';
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
            fireClick: true,
            sortable: false,
            responsive: {
                visible: 'xxl',
                hidden: 'xl',
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
            fireClick: true,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                let name = '-';
                // check if associate than show associate name
                if (row.associate && row.associate.first_name && row.associate.last_name)
                    name = (row.associate.first_name + ' ' + row.associate.last_name).toUpperCase();
                else if (row.supplier && row.supplier.name) name = row.supplier.name.toUpperCase();
                else if (row.instructor && row.instructor.first_name && row.instructor.last_name)
                    name = (row.instructor.first_name + ' ' + row.instructor.last_name).toUpperCase();
                return `<span class="font-size-sm font-weight-boldest">${name}</span>`;
            },
        },
        {
            field: 'subject',
            title: 'Attività',
            checked: true,
            width: 170,
            sortable: false,
            fireClick: true,
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
            fireClick: true,
            minWidth: '100%',
            responsive: {
                visible: 'xxl',
                hidden: 'xl',
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
            overflow: 'visible',
            textAlign: 'right',
            autoHide: false,
            width: 140,
            minWidth: '100%',
            template: function (row) {
                let pdfLink =
                    __bakney.env.API.DOCUMENT.RETRIEVE +
                    '/' +
                    row.invoice?.document_pdf +
                    '?download=false&token=' +
                    row.invoice?.document_token;
                let pdfLinkInstructor =
                    __bakney.env.API.DOCUMENT.RETRIEVE +
                    '/' +
                    row.instructor_document +
                    '?download=false&token=' +
                    row.instructor_document_token;
                waitForElementAndExecute(`#action-col-${row.payment_id}`, () => {
                    if (document.querySelector(`#action-col-${row.payment_id}`))
                        document.querySelector(`#action-col-${row.payment_id}`).innerHTML = '';

                    if (row.invoice && row.invoice.document_pdf) {
                        let documentButton = new DocumentButton({
                            target: document.querySelector(`#action-col-${row.payment_id}`),
                            intro: true,
                            props: {
                                disabled: false,
                                popover_text: `Scarica Ricevuta n.${row.invoice.number}`,
                            },
                        });

                        documentButton.$on('open', data => {
                            // dispatch onboarding-checklist-event
                            document.dispatchEvent(
                                new CustomEvent('onboarding-checklist-event', {detail: {key: 'download_invoice'}})
                            );
                            let filePreview = new InvoicePreviewModal({
                                target: document.querySelector(`#action-col-${row.payment_id}`),
                                intro: true,
                                props: {
                                    pdfLink: pdfLink,
                                    row: row.invoice,
                                    id: row.invoice.invoice_id,
                                    title: `Ricevuta n.${row.invoice.number}`,
                                },
                            });
                        });
                    }
                    if (row.instructor_document) {
                        let documentButton = new DocumentButton({
                            target: document.querySelector(`#action-col-${row.payment_id}`),
                            intro: true,
                            props: {
                                disabled: false,
                                popover_text: `Scarica Compenso`,
                            },
                        });

                        documentButton.$on('open', data => {
                            let filePreview = new DocumentPreviewModal({
                                target: document.querySelector(`#action-col-${row.payment_id}`),
                                intro: true,
                                props: {
                                    pdfLink: pdfLinkInstructor,
                                    id: row.instructor_id,
                                    title: `Compenso di ${row.instructor.first_name} ${row.instructor.last_name}`,
                                },
                            });
                        });
                    }
                    if (row.paid) {
                        let rejectBtn = new XCircleBtn({
                            target: document.querySelector(`#action-col-${row.payment_id}`),
                            intro: true,
                            props: {
                                disabled: !canPerformAction('bookeeping.payments.update'),
                                hidden: false,
                                popover_text: 'Annulla pagamento',
                            },
                        });
                        rejectBtn.$on('open', async data => {
                            swal.fire({
                                title: 'Vuoi annullare il pagamento?',
                                text:
                                    'Il pagamento verrà segnato come non pagato. ' +
                                    (row.invoice ? "sarà segnata come annullata l'eventuale ricevuta associata." : ''),
                                icon: 'warning',
                                buttonsStyling: true,
                                showCancelButton: true,
                                cancelButtonText: 'Annulla',
                                confirmButtonText: 'Conferma',
                                reverseButtons: true,
                                confirmButtonColor: '#d63030',
                            }).then(async function (result) {
                                if (result.isConfirmed) {
                                    blockPage({
                                        overlayColor: '#000000',
                                        state: 'primary',
                                        message: 'Annullamento in corso...',
                                    });

                                    try {
                                        const response = await apiFetch(
                                            replaceUID(__bakney.env.API.PAYMENT.CANCEL, row.payment_id),
                                            {
                                                method: 'POST',
                                            }
                                        );

                                        if (!response.error) {
                                            if (response.response.data.payment) {
                                                // add payment_category_name to data
                                                response.response.data.payment.payment_category_name =
                                                    categories?.find(
                                                        category => category.payment_category_id == row.payment_category
                                                    )?.name || '';
                                                let drawer = new PaymentDrawer({
                                                    target: document.body,
                                                    props: {
                                                        data: response.response.data.payment,
                                                        title: 'Dettagli Pagamento',
                                                    },
                                                });

                                                drawer.$on('close', () => {
                                                    datatable.reload();
                                                });
                                            } else {
                                                datatable.reload();
                                            }
                                            toast.success('Pagamento annullato!');
                                        } else {
                                            toast.error('Qualcosa è andato storto.');
                                        }
                                    } finally {
                                        unblockPage();
                                    }
                                }
                            });
                        });
                    } else {
                        let approveBtn = new ApproveButton({
                            target: document.querySelector(`#action-col-${row.payment_id}`),
                            intro: true,
                            props: {
                                disabled: row.paid || !canPerformAction('bookeeping.payments.update'),
                                popover_text: 'Segna come pagato',
                            },
                        });
                        approveBtn.$on('open', data => {
                            markAsPaid(row.payment_id, row.payment_date, row.expense);
                        });
                    }

                    let editBtn = new EditButton({
                        target: document.querySelector(`#action-col-${row.payment_id}`),
                        intro: true,
                        props: {
                            disabled: !row.sport_association || !canPerformAction('bookeeping.payments.update'),
                            hidden: !row.sport_association,
                        },
                    });

                    editBtn.$on('open', data => {
                        let editModal = new AddEditModal({
                            target: document.querySelector(`#portal-elements-foreground`),
                            props: {
                                show: true,
                                data: {...row},
                                edit: true,
                            },
                        });

                        editModal.$on('update', e => {
                            let drawer = new PaymentDrawer({
                                target: document.body,
                                props: {
                                    data: e.detail?.payment,
                                    title: 'Dettagli Pagamento',
                                    isOpen: true,
                                },
                            });

                            drawer.$on('close', () => {
                                datatable.reload();
                            });
                        });
                    });
                    let deleteBtn = new DeleteButton({
                        target: document.querySelector(`#action-col-${row.payment_id}`),
                        intro: true,
                        props: {
                            disabled: !canPerformAction('bookeeping.payments.delete'), //row.invoice,
                        },
                    });

                    deleteBtn.$on('open', data => {
                        swal.fire({
                            title: 'Vuoi eliminare il pagamento?',
                            text: "Sarà annullata l'eventuale ricevuta associata.",
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
                                        replaceUID(__bakney.env.API.PAYMENT.DELETE, row.payment_id),
                                        {
                                            method: 'DELETE',
                                        }
                                    );

                                    if (!response.error) {
                                        datatable.reload();
                                        toast.success('Pagamento eliminato!');
                                        fetchStats();
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
        apiFetch(`${__bakney.env.API.PAYMENT.EXPORT}?m=csv`)
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
        apiFetch(`${__bakney.env.API.PAYMENT.EXPORT}?m=xlsx`)
            .then(res => {
                window.tryDownloadFile(res, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
            })
            .finally(() => {
                unblockPage();
            });
    }

    function exportPrimaNota(type) {
        let filter = '';
        let from_date;
        let to_date;
        let value;
        switch (type) {
            case 'all':
                filter = '';
                break;
            case 'current-month':
                from_date = moment().startOf('month').format('YYYY/MM/DD');
                to_date = moment().endOf('month').format('YYYY/MM/DD');
                value = `${from_date} al ${to_date}`;
                filter = `&query[payment_range]=${value}`;
                break;
            case 'past-month':
                from_date = moment().subtract(1, 'month').startOf('month').format('YYYY/MM/DD');
                to_date = moment().subtract(1, 'month').endOf('month').format('YYYY/MM/DD');
                value = `${from_date} al ${to_date}`;
                filter = `&query[payment_range]=${value}`;
                break;
            case 'selected-filter':
                filter = `&query[payment_range]=${formatPaymentRange(dateRangeStart, dateRangeEnd)}`;
                // add to filter query[account] if selected
                let account = document.getElementById('bkn_datatable_search_account')?.value || null;
                if (account) filter += `&query[account]=${account}`;
                break;
        }
        // show loading
        blockPage({
            overlayColor: '#000000',
            state: 'primary',
            message: 'Esportazione in corso...',
        });

        apiFetch(`${__bakney.env.API.PAYMENT.EXPORT}?export_type=petty_cash_book&m=xlsx${filter}`)
            .then(res => {
                window.tryDownloadFile(res, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
            })
            .finally(() => {
                unblockPage();
            });
    }

    function exportCurrentSearch() {
        // show loading
        blockPage({
            overlayColor: '#000000',
            state: 'primary',
            message: 'Esportazione in corso...',
        });
        let filter = '';
        let search = document.getElementById('bkn_datatable_search_query')?.value || null;
        if (search) filter = `&query[generalSearch]=${search}`;
        // add to filter query[paid] if selected
        let paid = document.getElementById('bkn_datatable_search_status')?.value || null;
        if (paid) filter += `&query[paid]=${paid}`;
        // add to filter query[expense] if selected
        let expense = document.getElementById('bkn_datatable_search_expense')?.value || null;
        if (expense) filter += `&query[expense]=${expense}`;
        // add to filter query[subject] if selected
        let subject = document.getElementById('bkn_datatable_search_subject')?.value || null;
        if (subject) filter += `&query[subject]=${subject}`;
        // add to filter query[payment_categories] if selected
        let payment_categories = document.getElementById('bkn_datatable_search_category')?.value || null;
        if (payment_categories) filter += `&query[payment_categories]=${payment_categories}`;
        // add to filter query[account] if selected
        let account = document.getElementById('bkn_datatable_search_account')?.value || null;
        if (account) filter += `&query[account]=${account}`;
        // add to filter query[type] if selected
        let type = document.getElementById('bkn_datatable_search_type')?.value || null;
        if (type) filter += `&query[type]=${type}`;
        // add to filter query[payment_range] if selected
        const currentDateRange = formatPaymentRange(dateRangeStart, dateRangeEnd);
        if (currentDateRange) filter += `&query[payment_range]=${currentDateRange}`;

        apiFetch(`${__bakney.env.API.PAYMENT.EXPORT}?m=xlsx${filter}`)
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

    function resetFilters(update = true) {
        document.getElementById('bkn_datatable_search_query').value = '';

        document.getElementById('bkn_datatable_search_status').value = '';
        refreshSelectpicker(document.getElementById('bkn_datatable_search_status'));

        document.getElementById('bkn_datatable_search_expense').value = '';
        refreshSelectpicker(document.getElementById('bkn_datatable_search_expense'));

        document.getElementById('bkn_datatable_search_subject').value = '';
        refreshSelectpicker(document.getElementById('bkn_datatable_search_subject'));

        document.getElementById('bkn_datatable_search_category').value = '';
        refreshSelectpicker(document.getElementById('bkn_datatable_search_category'));

        document.getElementById('bkn_datatable_search_account').value = '';
        refreshSelectpicker(document.getElementById('bkn_datatable_search_account'));

        document.getElementById('bkn_datatable_search_type').value = '';
        refreshSelectpicker(document.getElementById('bkn_datatable_search_type'));

        dateRangeStart = moment().startOf('month').format('DD/MM/YYYY');
        dateRangeEnd = moment().endOf('month').format('DD/MM/YYYY');

        if (update) {
            // Trigger search update without remounting the datatable
            const currentDateRange = formatPaymentRange(dateRangeStart, dateRangeEnd);
            datatable.search(currentDateRange.toLowerCase(), 'payment_range');
            document.getElementById('bkn_datatable_search_query').dispatchEvent(new Event('keyup'));
            fetchStats();
        }
    }

    window.markAsPaid = (id, payment_date, expense = false) => {
        payment_date = payment_date
            ? moment(new Date(payment_date)).format('YYYY-MM-DD')
            : moment().format('YYYY-MM-DD');
        let send_receipt_email = false;
        swal.fire({
            title: expense ? 'Segna come pagato?' : 'Incassare il pagamento?',
            icon: 'question',
            buttonsStyling: true,
            html: `
            <div class="form-group px-4">
                <label for="payment_date font-weight-boldest">${!expense ? 'Data Incasso' : 'Data Pagamento'}</label>
                <input type="date" value=${payment_date} class="form-control form-control-solid form-control-lg" id="payment_date_${id}" value name="payment_date" placeholder="${
                !expense ? 'Data Incasso' : 'Data Pagamento'
            }" />
                <div id="receipt_email_container_${id}" style="display: ${expense ? 'none' : 'block'};">
                    <div class="form-group mt-6">
                        <div class="checkbox-inline font-weight-boldest font-size-sm">
                            <label class="checkbox" class="font-weight-boldest font-size-sm">
                                <input type="checkbox" id="generate_invoice_${id}" checked>
                                <span></span>
                                Genera ricevuta
                            </label>
                        </div>
                        <div class="checkbox-inline font-weight-boldest font-size-sm">
                            <label class="checkbox" class="font-weight-boldest font-size-sm">
                                <input type="checkbox" id="send_receipt_email_${id}">
                                <span></span>
                                Invia email
                            </label>
                        </div>
                    </div>
                </div>
            </div>
            `,
            showCancelButton: true,
            cancelButtonText: 'Annulla',
            confirmButtonText: expense ? 'Segna come pagato' : 'Incassa',
            reverseButtons: true,
        }).then(function (result) {
            if (result.isConfirmed) {
                blockPage({
                    overlayColor: '#000000',
                    state: 'primary',
                    message: 'Conferma in corso...',
                });

                const url = replaceUID(__bakney.env.API.PAYMENT.APPROVE, id);

                let body = {
                    payment_date: document.getElementById(`payment_date_${id}`).value || null,
                    send_receipt_email: document.getElementById(`send_receipt_email_${id}`).checked || false,
                    generate_invoice: document.getElementById(`generate_invoice_${id}`).checked || false,
                };

                window
                    .fetch(url, {
                        method: 'POST',
                        headers: {
                            Accept: 'application/json',
                            'Content-Type': 'application/json',
                            Authorization: 'Bearer ' + $sessionToken,
                        },
                        body: JSON.stringify(body),
                    })
                    .then(async response => {
                        let res = await response.json();
                        if (response.status == 200) {
                            toast.success('Pagamento Confermato con Successo.');

                            if (res.data.payment) {
                                let paymentDrawer = new PaymentDrawer({
                                    target: document.body,
                                    intro: true,
                                    props: {
                                        data: res.data.payment,
                                        title: 'Dettagli Pagamento',
                                    },
                                });
                                paymentDrawer.$on('close', () => {
                                    datatable.reload();
                                    fetchStats();
                                });
                            } else {
                                datatable.reload();
                                fetchStats();
                            }

                            setTimeout(() => {
                                document.dispatchEvent(
                                    new CustomEvent('onboarding-checklist-event', {detail: {key: 'approve_payment'}})
                                );
                            }, 1000);
                        } else {
                            toast.error(response.response?.msg || 'Qualcosa è andato storto.');
                        }
                    })
                    .finally(() => {
                        unblockPage();
                    });
            }
        });
    };

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
                blockPage({
                    overlayColor: '#000000',
                    state: 'primary',
                    message: 'Accesso in corso...',
                });

                const url = replaceUID(__bakney.env.API.SUBSCRIPTION.REJECT, id);

                window
                    .fetch(url, {
                        method: 'GET',
                        headers: {
                            Accept: 'application/json',
                            'Content-Type': 'application/json',
                            Authorization: 'Bearer ' + $sessionToken,
                        },
                    })
                    .then(response => {
                        response.json();
                        if (response.status == 200) {
                            toast.success('Iscrizione Rifiutata.');
                            datatable.reload();
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
                    })
                    .finally(() => {
                        unblockPage();
                    });
            }
        });
    };


    async function fetchStats() {
        UiApp.block('#payment-stats', {
            overlayColor: '#000000',
            state: 'primary',
        });
        let payment_range =
            formatPaymentRange(dateRangeStart, dateRangeEnd) ||
            `${moment().startOf('month').format('DD/MM/YYYY')} al ${moment().endOf('month').format('DD/MM/YYYY')}`;
        // subject
        let subject = document.getElementById('bkn_datatable_search_subject')?.value || null;

        // payment_categories
        let payment_categories = document.getElementById('bkn_datatable_search_category')?.value || null;

        // account
        let account = document.getElementById('bkn_datatable_search_account')?.value || null;

        // type
        let type = document.getElementById('bkn_datatable_search_type')?.value || null;

        // paid
        let paid = document.getElementById('bkn_datatable_search_status')?.value || null;

        // expense
        let expense = document.getElementById('bkn_datatable_search_expense')?.value || null;

        // generalSearch
        let generalSearch = document.getElementById('bkn_datatable_search_query')?.value || null;

        let queryStr = '';

        // add only if not null
        if (subject) queryStr += `&query[subject]=${subject}`;
        if (type) queryStr += `&query[type]=${type}`;
        if (paid) queryStr += `&query[paid]=${paid}`;
        if (expense) queryStr += `&query[expense]=${expense}`;
        if (generalSearch) queryStr += `&query[generalSearch]=${generalSearch}`;
        if (payment_range) queryStr += `&query[payment_range]=${payment_range}`;
        if (payment_categories) queryStr += `&query[payment_categories]=${payment_categories}`;
        if (account) queryStr += `&query[account]=${account}`;

        // remove first & from queryStr if first char is &
        if (queryStr.charAt(0) == '&') queryStr = queryStr.substring(1);

        // make payment_range a valid query string
        let res = await apiFetch(`${__bakney.env.API.PAYMENT.STATS}?${queryStr}`);

        UiApp.unblock('#payment-stats');

        if (!res.error) stats = res.response.data || [];
        else if (res.status != 403 && res.status != 401) toast.error('Qualcosa è andato storto.');
    }

    async function fetchCategories() {
        let res = await apiFetch(__bakney.env.API.PAYMENT.CATEGORY.LIST);

        if (!res.error) categories = res.response.data || [];
        else if (res.status != 403 && res.status != 401) toast.error('Qualcosa è andato storto.');
    }

    onMount(async () => {
        UiApp.block('#payment-card', {
            overlayColor: '#000000',
            state: 'primary',
            message: 'Attendere prego...',
        });

        // fetch stats asynchroneously withouth blocking the page
        fetchStats();

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

        await fetchCategories();
        await fetchCustomAccounts();

        initTooltips(document.body);

        ready = true;

        setTimeout(() => {
            if (params?.id) {
                document.getElementById('bkn_datatable_search_query').value = params?.id;
                document.getElementById('bkn_datatable_search_query').dispatchEvent(new Event('keyup'));
            }
        }, 200);

        UiApp.unblock('#payment-card');
    });

    async function requestPaymentsSelected() {
        swal.fire({
            text: `Vuoi sollecitare ${selectedCounter} soci?`,
            icon: 'question',
            buttonsStyling: true,
            showCancelButton: true,
            cancelButtonText: 'Annulla',
            confirmButtonText: 'Sollecita',
            reverseButtons: true,
        }).then(async function (result) {
            if (result.isConfirmed) {
                // console.info('requestPaymentsSelected');
                let checkedNodes = datatable.getSelectedRecords();
                let records = datatable.dataSet;
                blockPage({
                    overlayColor: '#000000',
                    state: 'primary',
                    message: 'Invio email in corso...',
                });
                let results = [];
                if (selectedCounter > 0) {
                    for (let i = 0; i < selectedCounter; i++) {
                        let id = checkedNodes[i].dataset.row;
                        results.push(
                            new Promise(async (resolve, reject) => {
                                let r = await apiFetch(
                                    replaceUID(__bakney.env.API.PAYMENT.REQUEST, records[id].payment_id),
                                    {
                                        method: 'POST',
                                    }
                                );
                                resolve(r.status == 200);
                            })
                        );
                    }
                }

                Promise.all(results).then(values => {
                    if (values.length > 0) {
                        if (values.length != selectedCounter) {
                            toast.warning(`${values.length} Associati sollecitati su ${selectedCounter}.`);
                        } else if (values.every(r => r)) {
                            toast.success(`${values.length} Associati sollecitati.`);
                        } else {
                            let valid = values.filter(r => r).length;
                            toast.warning(`${valid} Associati sollecitati su ${selectedCounter}.`);
                        }
                    } else {
                        toast.warning(`Nessun pagamento sollecitato.`);
                    }

                    datatable.reload();
                    hideCollapse('bkn_datatable_group_action_form');
                }).finally(() => {
                    unblockPage();
                });
            }
        });
    }

    async function deletePaymentsSelected() {
        swal.fire({
            text: `Vuoi eliminare ${selectedCounter} pagament${selectedCounter > 1 ? 'i' : 'o'}?`,
            icon: 'question',
            buttonsStyling: true,
            showCancelButton: true,
            cancelButtonText: 'Annulla',
            confirmButtonText: 'Elimina',
            reverseButtons: true,
        }).then(async function (result) {
            if (result.isConfirmed) {
                let checkedNodes = datatable.getSelectedRecords();
                let records = datatable.dataSet;
                let count = checkedNodes.length;
                blockPage({
                    overlayColor: '#000000',
                    state: 'primary',
                    message: 'Eliminazione in corso...',
                });
                let payment_ids = [];
                for (let i = 0; i < count; i++) {
                    let id = checkedNodes[i].dataset.row;
                    payment_ids.push(records[id].payment_id);
                }

                let result = await apiFetch(__bakney.env.API.PAYMENT.BULK_DELETE, {
                    method: 'POST',
                    body: JSON.stringify({
                        payment_ids: payment_ids,
                    }),
                });

                unblockPage();

                if (!result.error) {
                    toast.success(`${selectedCounter} Pagamenti Eliminati.`);
                } else {
                    toast.warning(`Nessun pagamento eliminato.`);
                }
                hideCollapse('bkn_datatable_group_action_form');
                datatable.reload();
            }
        });
    }

    async function approvePaymentsSelected() {
        let payment_date = moment().format('YYYY-MM-DD');
        swal.fire({
            text: `Vuoi approvare ${selectedCounter} pagamenti?`,
            icon: 'question',
            buttonsStyling: true,
            html: `
            <div class="form-group px-4">
                <label for="payment_date font-weight-boldest">Data Pagamento</label>
                <input type="date" value=${payment_date} class="form-control form-control-solid form-control-lg" id="payment_date_<id>" value name="payment_date" placeholder="Data Pagamento" />
                <div id="receipt_email_container_<id>" style="display: block;">
                    <div class="form-group mt-6">
                        <div class="checkbox-inline font-weight-boldest font-size-sm">
                            <label class="checkbox" class="font-weight-boldest font-size-sm">
                                <input type="checkbox" id="generate_invoice_<id>" checked>
                                <span></span>
                                Genera ricevuta
                            </label>
                        </div>
                        <div class="checkbox-inline font-weight-boldest font-size-sm">
                            <label class="checkbox" class="font-weight-boldest font-size-sm">
                                <input type="checkbox" id="send_receipt_email_<id>">
                                <span></span>
                                Invia email
                            </label>
                        </div>
                    </div>
                </div>
            </div>
            `,
            showCancelButton: true,
            cancelButtonText: 'Annulla',
            confirmButtonText: 'Segna come pagato',
            reverseButtons: true,
        }).then(async function (result) {
            if (result.isConfirmed) {
                let body = {
                    payment_date: document.getElementById(`payment_date_<id>`).value || null,
                    send_receipt_email: document.getElementById(`send_receipt_email_<id>`).checked || false,
                    generate_invoice: document.getElementById(`generate_invoice_<id>`).checked || false,
                };

                let checkedNodes = datatable.getSelectedRecords();
                let records = datatable.dataSet;
                let count = checkedNodes.length;
                blockPage({
                    overlayColor: '#000000',
                    state: 'primary',
                    message: 'Approvazione in corso...',
                });
                let results = [];
                for (let i = 0; i < count; i++) {
                    let id = checkedNodes[i].dataset.row;
                    // if it's already paid, skip
                    if (records[id].paid) continue;

                    results.push(
                        apiFetch(replaceUID(__bakney.env.API.PAYMENT.APPROVE, records[id].payment_id), {
                            method: 'POST',
                            body: JSON.stringify(body),
                        })
                    );
                }

                Promise.all(results).then(values => {
                    if (values.length > 0) {
                        toast.success(`${values.length} Pagamenti approvati.`);
                    }
                }).finally(() => {
                    unblockPage();
                });
                datatable.reload();
                fetchStats();
            }
            // reset selected counter
            selectedCounter = 0;
            hideCollapse('bkn_datatable_group_action_form');
        });
    }

    async function archiveSelected() {
        swal.fire({
            text: `Vuoi archiviare ${selectedCounter} pagamenti?`,
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
                blockPage({
                    overlayColor: '#000000',
                    state: 'primary',
                    message: 'Archiviazione in corso...',
                });
                let payment_ids = [];
                for (let i = 0; i < count; i++) {
                    let id = checkedNodes[i].dataset.row;
                    payment_ids.push(records[id].payment_id);
                }

                let result = await apiFetch(__bakney.env.API.PAYMENT.BULK_ARCHIVE, {
                    method: 'POST',
                    body: JSON.stringify({
                        payment_ids: payment_ids,
                    }),
                });

                unblockPage();

                if (!result.error) {
                    toast.success(`${selectedCounter} Pagamenti Archiviati.`);
                } else {
                    toast.warning(`Nessun pagamento archiviato.`);
                }
                hideCollapse('bkn_datatable_group_action_form');
                datatable.reload();
            }
        });
    }

    async function fetchCustomAccounts() {
        const res = await apiFetch(__bakney.env.API.BALANCE_SHEET_ACCOUNTS.LIST + '?related=false', {
            method: 'GET',
        });
        if (!res.error) {
            accounts = Array.from(res?.response?.data || []);
        } else {
            if (res.status != 403 && res.status != 401) toast.error('Qualcosa è andato storto.');
        }
        return;
    }
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
                    ...(canPerformAction('bookeeping.payments.archive.read')
                        ? [
                              {
                                  title: 'Archivio',
                                  url: '/payment/archive',
                                  icon: 'archive',
                              },
                          ]
                        : []),
                ]} />
            <div class="card-header flex-wrap border-0 p-0">
                <div class="card-title">
                    <h3 class="card-label font-size-h2">
                        Pagamenti
                        <span class="d-block text-muted pt-2 font-size-sm">Storico dei pagamenti.</span>
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
                            disabled={!canPerformAction('bookeeping.payments.read')}
                            type="button"
                            class="btn btn-light-primary btn-sm font-weight-bolder dropdown-toggle"
                            data-toggle="dropdown"
                            aria-haspopup="true"
                            aria-expanded="false">
                            <span class="navi-icon">
                                <Export size={18} weight="duotone" />
                            </span>
                            <span class="d-none d-md-inline-block">Esporta</span></button>
                        <div class="dropdown-menu dropdown-menu-sm dropdown-menu-right">
                            <!--begin::Navigation-->
                            <ul class="navi flex-column navi-hover py-2">
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
                    <!--begin::Button-->
                    <!-- svelte-ignore a11y-click-events-have-key-events -->
                    {#if canPerformAction('bookeeping.payments.create')}
                        <a
                            href={!isFreePlan() ? '/#/payment/category/list' : '/#/subscription/upgrade'}
                            class="btn btn-sm btn-light-primary font-weight-boldest d-flex align-items-center m-2">
                            <Article size={18} weight="duotone" />
                            <span class="ml-0 ml-md-1">Centri di costo (causali)</span>
                        </a>
                    {/if}

                    <!-- TODO: upgrade this when partial payments are available -->
                    {#if canPerformAction('bookeeping.payments.update')}
                        <!-- svelte-ignore a11y-click-events-have-key-events -->
                        <!-- svelte-ignore a11y-missing-attribute -->
                        <!-- svelte-ignore a11y-no-static-element-interactions -->
                        <a
                            on:click={() => {
                                let quotesModal = new SubscriptionFeesPlanModal({
                                    target: document.querySelector(`body`),
                                    intro: true,
                                    props: {
                                        id: 'quotesModal',
                                    },
                                });

                                quotesModal.on('close', () => {
                                    quotesModal.destroy();
                                    // refresh table after 500ms
                                });
                            }}
                            class="btn btn-sm btn-light-primary font-weight-bolder m-2">
                            <Coins size={18} weight="duotone" />
                            <span class="ml-0 ml-md-1"
                                ><span class="d-none d-md-inline-block">Emetti quote parziali</span></span>
                        </a>
                        <!--end::Button-->
                    {/if}

                    {#if canPerformAction('bookeeping.payments.create')}
                        <button
                            class="btn btn-sm btn-light-primary font-weight-bolder m-2"
                            on:click={() => {
                                let bulkModal = new BulkAddPaymentModal({
                                    target: document.querySelector(`#portal-elements-foreground`),
                                    props: {
                                        show: true,
                                        preSelectedAssociates: [],
                                    },
                                });

                                bulkModal.$on('update', () => {
                                    datatable.reload();
                                });

                                bulkModal.$on('close', () => {
                                    bulkModal.$destroy();
                                });
                            }}>
                            <UsersThree size={16} weight="bold" class="mr-1" />
                            <span class="d-none d-md-inline">Pagamento Multiplo</span>
                        </button>
                        <button
                            class="btn btn-sm btn-primary font-weight-bolder m-0"
                            on:click={() => {
                                let addModal = new AddEditModal({
                                    target: document.querySelector(`#portal-elements-foreground`),
                                    props: {
                                        show: true,
                                        data: {
                                            type: 'cash',
                                            expense: false,
                                            meta_payment_categories: [],
                                        },
                                    },
                                });

                                addModal.$on('update', e => {
                                    let drawer = new PaymentDrawer({
                                        target: document.body,
                                        props: {
                                            data: e.detail?.payment,
                                            title: 'Dettagli Pagamento',
                                            isOpen: true,
                                        },
                                    });

                                    drawer.$on('close', () => {
                                        datatable.reload();
                                    });
                                });
                            }}>
                            <PlusCircle size={16} weight="bold" class="mr-1" />
                            Pagamento
                        </button>
                    {/if}
                </div>
            </div>
            <div class="card-body px-0 pt-2">
                <!--begin: Search Form-->
                <!--end: Datatable-->
                <!-- <div class="mt-8 font-size-sm font-weight-bold text-center">Statistiche di ricerca</div>
                <hr class="mt-2" /> -->
                <!--begin: Datatable-->
                {#if ready}
                {#key datatableKey}
                <BKNDatatable
                    bind:datatable
                    bind:selectedCounter
                    columns={visibleColumns}
                    url={__bakney.env.API.PAYMENT.LIST}
                    params={{query: {payment_range: `${moment().startOf('month').format('DD/MM/YYYY')} al ${moment().endOf('month').format('DD/MM/YYYY')}`}}}
                    pageSizeSelect={[10, 20, 30, 50, 100]}
                    spinnerConfig={{message: ''}}
                    showDividerFilter={false}
                    mapFunction={function (raw) {
                        var dataSet = raw;
                        if (typeof raw.data !== 'undefined') {
                            dataSet = raw.data;
                        }
                        fetchStats();
                        return dataSet;
                    }}
                    clicked={function (td, obj) {
                        let basicDrawer = new PaymentDrawer({
                            target: document.querySelector(`#drawer-elements`),
                            intro: true,
                            props: {
                                data: obj,
                                title: 'Dettagli Pagamento',
                            },
                        });

                        basicDrawer.$on('close', () => {
                            datatable.reload();
                            fetchStats();
                        });
                    }}
                    loadFilters={() => {
                        initTooltips(document.body);
                        initPopovers(document.body);
                        const addChangeFilter = (id, column) => {
                            const el = document.getElementById(id);
                            el?.addEventListener('change', function (e) {
                                datatable.search(e.currentTarget.value.toLowerCase(), column);
                            });
                            initSelectpicker(el);
                        };

                        addChangeFilter('bkn_datatable_search_status', 'paid');
                        addChangeFilter('bkn_datatable_search_expense', 'expense');
                        addChangeFilter('bkn_datatable_search_subject', 'subject');
                        addChangeFilter('bkn_datatable_search_category', 'payment_categories');
                        addChangeFilter('bkn_datatable_search_account', 'account');
                        addChangeFilter('bkn_datatable_search_type', 'type');

                        // payment date range is handled by DateRangePicker component
                    }}>
                    <svelte:fragment slot="search-header">
                        <div class="my-2 my-md-0 mr-2">
                            <div class="d-flex align-items-center">
                                <select
                                    class="form-control form-control-solid"
                                    id="bkn_datatable_search_status"
                                    style="max-width: 13rem;width: 13rem">
                                    <option value="">Stato</option>
                                    <option value="true">Pagato</option>
                                    <option value="false">In attesa</option>
                                </select>
                            </div>
                        </div>
                        <div class="my-2 my-md-0 mr-2">
                            <div class="h-4">
                                <input
                                    type="hidden"
                                    id="bkn_datatable_search_account"
                                    name="bkn_datatable_search_account"
                                    value=""
                                    class="form-control form-control-solid" />
                                <SmartSelect
                                    customClasses={'px-0 mb-0 min-w-6 max-w-6 filter-select'}
                                    editable={false}
                                    active={false}
                                    on:change={e => {
                                        document.getElementById('bkn_datatable_search_account').value =
                                            e.detail.value;
                                        document
                                            .getElementById('bkn_datatable_search_account')
                                            .dispatchEvent(new Event('change'));
                                    }}
                                    props={{
                                        placeholder: 'Tutti i conti',
                                        required: false,
                                        clearable: false,
                                        searchable: true,
                                        showChevron: true,
                                        value: '',
                                        options: [
                                            {value: '', label: 'Tutti i conti'},
                                            {value: '[]', label: 'Senza conto'},
                                            ...(accounts
                                                ?.sort((a, b) => a.name.localeCompare(b.name))
                                                .map(account => ({
                                                    value: [account.custom_account_id],
                                                    label: `${account.name}`,
                                                })) || []),
                                        ],
                                    }} />
                            </div>
                        </div>
                        <div class="my-1 my-md-0 mr-2">
                            <div class="d-flex align-items-center">
                                <select
                                    style="max-width: 13rem;width: 13rem"
                                    class="form-control form-control-solid"
                                    id="bkn_datatable_search_type">
                                    <option value="">Metodo</option>
                                    {#each Array.from(paymentTypesMap || []) as type}
                                        <option value={type.key}>
                                            {type.value}
                                        </option>
                                    {/each}
                                </select>
                            </div>
                        </div>
                        <div class="my-1 my-md-0 mr-2">
                            <div class="d-flex align-items-center">
                                <select
                                    style="max-width: 13rem;width: 13rem"
                                    class="form-control form-control-solid"
                                    id="bkn_datatable_search_expense">
                                    <option value="">Tipo</option>
                                    <option value="true">Uscite</option>
                                    <option value="false">Entrate</option>
                                </select>
                            </div>
                        </div>
                        <div class="my-1 my-md-0 mr-2">
                            <div class="d-flex align-items-center">
                                <select
                                    style="max-width: 13rem;width: 13rem"
                                    class="form-control form-control-solid"
                                    id="bkn_datatable_search_subject">
                                    <option value="">Attività</option>
                                    {#each Array.from(paymentSubjectsMap || []) as subject}
                                        <option value={subject.key}>
                                            {subject.value}
                                        </option>
                                    {/each}
                                </select>
                            </div>
                        </div>
                        <div class="my-2 my-md-0 mr-2">
                            <div class="h-4">
                                <input
                                    type="hidden"
                                    id="bkn_datatable_search_category"
                                    name="bkn_datatable_search_category"
                                    value=""
                                    class="form-control form-control-solid" />
                                <SmartSelect
                                    customClasses={'px-0 mb-0 min-w-8 max-w-12 filter-select'}
                                    editable={false}
                                    active={false}
                                    on:change={e => {
                                        document.getElementById('bkn_datatable_search_category').value =
                                            e.detail.value;
                                        document
                                            .getElementById('bkn_datatable_search_category')
                                            .dispatchEvent(new Event('change'));
                                    }}
                                    props={{
                                        placeholder: 'Tutte le causali',
                                        required: false,
                                        clearable: false,
                                        searchable: true,
                                        showChevron: true,
                                        value: '',
                                        options: [
                                            {value: '', label: 'Tutte le causali'},
                                            {value: '[]', label: 'Senza Causale'},
                                            ...(categories
                                                ?.sort((a, b) => a.name.localeCompare(b.name))
                                                .map(category => ({
                                                    value: [category.payment_category_id],
                                                    label: `${category.name} ${
                                                        category.deleted ? '(Elminata)' : ''
                                                    }`,
                                                })) || []),
                                        ],
                                    }} />
                            </div>
                        </div>
                        <div class="my-1 my-md-0 mr-2">
                            <div class="d-flex align-items-center">
                                <DateRangePicker
                                    id="payment-date-range"
                                    format="DD/MM/YYYY"
                                    sizeClass=""
                                    startPlaceholder="Dal"
                                    endPlaceholder="Al"
                                    bind:startValue={dateRangeStart}
                                    bind:endValue={dateRangeEnd}
                                    on:change={handleDateRangeChange}
                                />
                            </div>
                        </div>
                        <div class="my-2 my-md-0">
                            <a
                                on:click|preventDefault={resetFilters}
                                class="btn font-weight-bolder mb-0 cursor-pointer text-primary btn-clean btn-icon">
                                <LucideX size={18} weight="bold" />
                            </a>
                        </div>
                    </svelte:fragment>
                    <div slot="search-actions" class="w-100 px-0">

                        <div class="col-12 px-0" id="payment-stats">
                            <div
                                class="d-flex justify-content-start mb-2 mt-2 mt-md-0 pb-2 pb-md-0 overflow-auto"
                                style="flex-wrap: wrap;">
                                <div class="col-12 col-md-3 p-2 pr-md-4" in:scale={{duration: 250, start: 0.92}}>
                                    <div class="card card-widget p-0 m-0 h-100">
                                        <div class="card-body p-4">
                                            <div class="mb-0">
                                                <h6 class="font-weight-boldest text-center mb-0" style="font-size: 1rem;">
                                                    PAGAMENTI DA CONTABILIZZARE
                                                </h6>
                                            </div>
                                            <div
                                                class="text-center mt-2 w-full d-flex justify-content-between font-weight-bolder text-primary">
                                                <b class="font-weight-boldest">DA PAGARE</b>
                                                <span class="text-primary">
                                                    {Number(stats.monthly_to_pay).toLocaleString('it-IT', {
                                                        maximumFractionDigits: 2,
                                                        minimumFractionDigits: 2,
                                                    })} €
                                                </span>
                                            </div>
                                            <div
                                                class="text-center w-full d-flex justify-content-between font-weight-bolder text-primary">
                                                <b class="font-weight-boldest">DA INCASSARE</b>
                                                <span class="text-primary">
                                                    {Number(stats.monthly_to_cash_in).toLocaleString('it-IT', {
                                                        maximumFractionDigits: 2,
                                                        minimumFractionDigits: 2,
                                                    })} €
                                                </span>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                <div class="col-12 col-md-3 p-2 pr-md-4" in:scale={{duration: 250, start: 0.92}}>
                                    <div class="card card-widget p-0 m-0 h-100">
                                        <div class="card-body p-4">
                                            <div class="mb-0">
                                                <h6 class="font-weight-boldest text-center mb-0" style="font-size: 1rem;">
                                                    ENTRATE INCASSATE
                                                </h6>
                                            </div>
                                            <div
                                                class="text-center font-weight-bolder text-primary mt-2"
                                                style="font-size: 1.75rem;">
                                                <span class="text-success"
                                                    >{Number(stats.monthly_income).toLocaleString('it-IT', {
                                                        maximumFractionDigits: 2,
                                                        minimumFractionDigits: 2,
                                                    })} €
                                                </span>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                <div class="col-12 col-md-3 p-2 pr-md-4" in:scale={{duration: 250, start: 0.92}}>
                                    <div class="card card-widget p-0 m-0 h-100">
                                        <div class="card-body p-4">
                                            <div class="mb-0">
                                                <h6 class="font-weight-boldest text-center mb-0" style="font-size: 1rem;">
                                                    USCITE PAGATE
                                                </h6>
                                            </div>
                                            <div
                                                class="text-center font-weight-bolder text-primary mt-2"
                                                style="font-size: 1.75rem;">
                                                <span class="text-danger"
                                                    >{Number(stats.monthly_expenses).toLocaleString('it-IT', {
                                                        maximumFractionDigits: 2,
                                                        minimumFractionDigits: 2,
                                                    })} €
                                                </span>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                <div class="col-12 col-md-3 p-2" in:scale={{duration: 250, start: 0.92}}>
                                    <div class="card card-widget p-0 m-0 h-100">
                                        <div class="card-body p-4">
                                            <div class="mb-0">
                                                <h6 class="font-weight-boldest text-center mb-0" style="font-size: 1rem;">
                                                    COMMISSIONI
                                                </h6>
                                            </div>
                                            <div class="text-center font-weight-bolder text-primary mt-2">
                                                <div
                                                    class="d-flex justify-content-center align-items-center"
                                                    style="font-size: 1.75rem;">
                                                    <span class="font-weight-boldest">
                                                        {Number(stats?.stripe_charges?.total).toLocaleString('it-IT', {
                                                            maximumFractionDigits: 2,
                                                            minimumFractionDigits: 2,
                                                        })} €
                                                    </span>

                                                    <div
                                                        class="d-flex flex-column justify-content-end ml-6 w-50"
                                                        style="font-size: .75rem;">
                                                        <div class="d-flex justify-content-between">
                                                            <b class="font-weight-boldest">STRIPE</b>
                                                            <span class="font-weight-boldest">
                                                                {Number(stats?.stripe_charges?.stripe_fee).toLocaleString(
                                                                    'it-IT',
                                                                    {
                                                                        maximumFractionDigits: 2,
                                                                        minimumFractionDigits: 2,
                                                                    }
                                                                )} €</span>
                                                        </div>
                                                        {#if __bakney.OEM_CONFIG?.isReseller}
                                                            <div class="d-flex justify-content-between">
                                                                <b class="font-weight-boldest"
                                                                    >{String(
                                                                        __bakney.OEM_CONFIG?.abbreviation
                                                                    ).toUpperCase()}</b>
                                                                <span class="font-weight-boldest">
                                                                    {Number(
                                                                        stats?.stripe_charges?.application_fee_amount
                                                                    ).toLocaleString('it-IT', {
                                                                        maximumFractionDigits: 2,
                                                                        minimumFractionDigits: 2,
                                                                    })} €</span>
                                                            </div>
                                                        {/if}
                                                    </div>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                            <div class="d-none d-lg-block">
                                <div
                                    class="bg-ghost d-flex justify-content-between col-12 col-md-12 mb-2"
                                    style="padding: 0.5rem 1rem;border-radius: 1rem;">
                                    <div class="text-center font-size-sm">
                                        <span class="font-weight-boldest font-size-lg">TOTALE ENTRATE</span><br />
                                        {Number(stats.current_income).toLocaleString('it-IT', {
                                            maximumFractionDigits: 2,
                                            minimumFractionDigits: 2,
                                        })} €
                                    </div>
                                    <div class="text-center">
                                        <span class="font-weight-boldest font-size-lg">TOTALE USCITE</span><br />
                                        {Number(stats.current_expenses).toLocaleString('it-IT', {
                                            maximumFractionDigits: 2,
                                            minimumFractionDigits: 2,
                                        })} €
                                    </div>
                                    <div class="text-center">
                                        <span class="font-weight-boldest font-size-lg">VARIAZIONE CASSA</span><br />
                                        {Number(stats.accounts['cash']).toLocaleString('it-IT', {
                                            maximumFractionDigits: 2,
                                            minimumFractionDigits: 2,
                                        })} €
                                    </div>
                                    <div class="text-center">
                                        <span class="font-weight-boldest font-size-lg">VARIAZIONE BANCA</span><br />
                                        {Number(stats.accounts['bank']).toLocaleString('it-IT', {
                                            maximumFractionDigits: 2,
                                            minimumFractionDigits: 2,
                                        })} €
                                    </div>
                                    <div class="text-center">
                                        <span class="font-weight-boldest font-size-lg">VARIAZIONE ALTRO</span><br />
                                        {Number(stats.accounts['other']).toLocaleString('it-IT', {
                                            maximumFractionDigits: 2,
                                            minimumFractionDigits: 2,
                                        })} €
                                    </div>
                                    <div class="text-center">
                                        <span class="font-weight-boldest font-size-lg">SALDO NETTO</span><br />
                                        {Number(stats.net_balance).toLocaleString('it-IT', {
                                            maximumFractionDigits: 2,
                                            minimumFractionDigits: 2,
                                        })} €
                                    </div>
                                </div>
                            </div>
                        </div>
                        {@const currentDateRange = formatPaymentRange(dateRangeStart, dateRangeEnd)}
                        {#if currentDateRange}
                            <div class="col-12 d-flex justify-content-start pb-2">
                                <div
                                    style="width: fit-content;"
                                    class="alert rounded-pill mx-auto alert-custom font-weight-bolder alert-light-warning fade show mb-2 py-2 px-4 border border-lihgt-dark d-flex justify-content-center align-items-center"
                                    role="alert">
                                    <div class="alert-text">
                                        Stai visualizzando pagamenti e statistiche per il periodo <b class="font-weight-boldest"
                                            >{currentDateRange}</b
                                        >.
                                    </div>
                                </div>
                            </div>
                        {/if}

                        <div class="col-12 d-flex justify-content-end">
                        <div class="d-flex align-items-center">
                            <div class="my-2 mr-1">
                                <div class="dropdown">
                                    <button
                                        class="btn btn-sm btn-light-primary dropdown-toggle font-weight-bolder"
                                        type="button"
                                        id="dropdownMenuButton"
                                        data-toggle="dropdown"
                                        aria-haspopup="true"
                                        aria-expanded="false">
                                        <FileArrowDown size={14} weight="duotone" />
                                        Prima nota
                                    </button>
                                    <div class="dropdown-menu" aria-labelledby="dropdownMenuButton">
                                        <!-- svelte-ignore a11y-click-events-have-key-events -->
                                        <!-- svelte-ignore a11y-missing-attribute -->
                                        <a class="dropdown-item font-weight-bolder" on:click={() => exportPrimaNota('all')}>
                                            <FileXls size={18} weight="duotone" class="mr-1" />
                                            Dall'inizio
                                        </a>
                                        <!-- svelte-ignore a11y-click-events-have-key-events -->
                                        <!-- svelte-ignore a11y-missing-attribute -->
                                        <a
                                            class="dropdown-item font-weight-bolder"
                                            on:click={() => exportPrimaNota('current-month')}>
                                            <FileXls size={18} weight="duotone" class="mr-1" />
                                            Mese corrente
                                        </a>
                                        <a
                                            class="dropdown-item font-weight-bolder"
                                            on:click={() => exportPrimaNota('past-month')}>
                                            <FileXls size={18} weight="duotone" class="mr-1" />
                                            Mese scorso
                                        </a>
                                        <a
                                            class="dropdown-item font-weight-bolder"
                                            on:click={() => exportPrimaNota('selected-filter')}>
                                            <FileXls size={18} weight="duotone" class="mr-1" />
                                            Periodo selezionato
                                        </a>
                                    </div>
                                </div>
                            </div>
                            <div class="my-2 mr-1">
                                <button
                                    class="btn btn-sm btn-light-primary btn-icon font-weight-bolder m-0"
                                    data-toggle="tooltip"
                                    data-placement="bottom"
                                    title="Esporta ricerca corrente in Excel"
                                    on:click={exportCurrentSearch}>
                                    <Export size={18} weight="duotone" />
                                </button>
                            </div>
                            <div class="m-0 my-2 my-md-0">
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

                    <div slot="multiactions">
                        {#if selectedCounter > 0}
                            <div class="mt-10 mb-5" id="bkn_datatable_group_action_form">
                                <div class="d-flex align-items-center">
                                    <div class="font-weight-bold mr-3" style="font-size:1.1rem;">
                                        <span id="bkn_datatable_selected_records">{selectedCounter}</span> selezionati
                                    </div>
                                    <div class="dropdown mr-2">
                                        <button
                                            on:click={requestPaymentsSelected}
                                            class="btn btn-sm btn-primary font-weight-bolder m-0 ml-2 p-2 d-flex align-items-center"
                                            type="button"
                                            data-toggle="tooltip"
                                            data-placement="top"
                                            title="Sollecita i pagamenti selezionati"
                                            id="bkn_datatable_ask_selected">
                                            <PaperPlaneTilt size="17" weight="duotone" class="mr-1" />
                                            Sollecita</button>
                                    </div>

                                    <div class="dropdown mr-2">
                                        <button
                                            on:click={approvePaymentsSelected}
                                            class="btn btn-sm btn-primary font-weight-bolder m-0 ml-2 p-2 d-flex align-items-center"
                                            type="button"
                                            data-toggle="tooltip"
                                            data-placement="top"
                                            title="Approva i pagamenti selezionati"
                                            id="bkn_datatable_ask_selected">
                                            <LucideCheckCircle size="17" weight="duotone" class="mr-1" />
                                            Approva</button>
                                    </div>
                                    <div class="dropdown mr-2">
                                        <button
                                            on:click={deletePaymentsSelected}
                                            class="btn btn-sm btn-light-primary font-weight-bolder m-0 ml-2 p-2 d-flex align-items-center"
                                            type="button"
                                            data-toggle="tooltip"
                                            data-placement="top"
                                            title="Elimina i pagamenti selezionati se non sono stati ancora approvati"
                                            id="bkn_datatable_delete_selected">
                                            <TrashSimple size="17" weight="duotone" class="mr-1" />
                                            Elimina</button>
                                    </div>
                                    <button
                                        on:click={archiveSelected}
                                        class="btn btn-sm btn-light-primary font-weight-bolder m-0 ml-2 p-2 d-flex align-items-center"
                                        type="button"
                                        data-toggle="tooltip"
                                        data-placement="top"
                                        title="Archivia le ricevute selezionate"
                                        id="bkn_datatable_archive_selected">
                                        <ArchiveBox size="17" weight="duotone" class="mr-1" />
                                        <span class="d-none d-md-block">Archivia</span></button>
                                </div>
                            </div>
                        {/if}
                    </div>
                </BKNDatatable>
                {/key}
                {/if}
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
                            <LucideX size={16} aria-hidden="true" />
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
        /* .svelte-select {
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
        } */

        .svelte-select {
            background-color: var(--bg-surface-secondary) !important;
            border: 0 !important;
            border-radius: 0.75rem !important;
            border-top: 1px solid #fff !important;
            outline: 1px solid #e4e6ef !important;
        }
    </style>
</svelte:head>
