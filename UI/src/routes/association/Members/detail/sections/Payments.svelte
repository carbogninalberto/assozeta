<script>
    import {sessionToken, permissions, userData} from 'store/stores.js';
    import {scale} from 'svelte/transition';
    import * as easing from 'svelte/easing';
    import {onDestroy, onMount} from 'svelte';
    import {apiFetch, replaceUID} from 'utils/ApiMiddleware';
    import {canPerformAction, isFreePlan} from 'utils/Permissions.js';
    import {PaperPlaneTilt, PlusCircle, TrashSimple, X, XCircle} from 'phosphor-svelte';
    import Upgrade from 'routes/Upgrade.svelte';
    import {waitForElementAndExecute} from 'utils/Functions';
    import DocumentButton from 'components/buttons/DocumentButton.svelte';
    import ApproveButton from 'components/buttons/ApproveButton.svelte';
    import EditButton from 'components/buttons/EditButton.svelte';
    import DeleteButton from 'components/buttons/DeleteButton.svelte';
    import InvoicePreviewModal from 'components/modals/InvoicePreviewModal.svelte';
    import XCircleBtn from 'components/buttons/XCircle.svelte';
    import {toast} from 'svelte-sonner';
    import AddEditModal from 'routes/accounting/payment/modals/AddEditModal.svelte';
    import PaymentDrawer from 'routes/accounting/payment/PaymentDrawer.svelte';
    import BKNDatatable from 'components/tables/BKNDatatable.svelte';
    import { initTooltips, destroyTooltips } from 'shim/tooltip.js';
    import {initSelectpicker, refreshSelectpicker} from 'shim/select.js';
	import { UiApp, UiUtil } from 'shim/ui.js';
    import { showCollapse, hideCollapse } from 'shim/collapse.js';
    sessionToken.useLocalStorage();
    userData.useLocalStorage();

    export let uuid = Math.random().toString(36).substring(7);
    export let layoutOptions = {
        scroll: true,
        footer: false,
    };
    export let info = {};
    export let payments = {};
    export let searchKey = '';

    const statusTextDictionary = {
        false: '<span class="label label-light-warning label-inline font-weight-bolder label-lg">In attesa</span>',
        true: '<span class="label label-light-success label-inline font-weight-bolder label-lg">Pagato</span>',
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

    const typeTextDictionary = {
        default: '<span>-</span>',
        cash: '<span>Contanti</span>',
        stripe: '<span>stripe</span>',
        transfer: '<span>Bonifico</span>',
        online: '<span>Online</span>',
        'sepa-transfer': '<span>Bonifico SEPA</span>',
        pos: '<span>PoS</span>',
    };

    const paymentSubjectsMap = [
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

    let paymentTypesMap = [
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

    let categories = [];
    let selectedCounter = 0;
    let datatable;
    let datatableKey = 0;
    let ready = false;

    export let datatableHandle;
    $: datatableHandle = {
        reload: () => datatable?.reload(),
    };

    let url = replaceUID(__bakney.env.API.SUBSCRIPTION.PAYMENTS, uuid);

    $: {
        let el = document.getElementById('bkn_datatable_selected_records_' + uuid);
        if (el) el.innerHTML = selectedCounter;
        if (selectedCounter > 0) {
            showCollapse('bkn_datatable_group_action_form_' + uuid);
        } else {
            hideCollapse('bkn_datatable_group_action_form_' + uuid);
        }
    }

    const columns = [
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
            field: 'subject',
            title: 'Attività',
            width: 150,
            autoHide: false,
            minWidth: '100%',
            sortable: false,
            template: function (row) {
                let categoryDescription = null;
                if (row.subject == 0 && row.payment_category && !row.description) {
                    categoryDescription =
                        categories?.find(category => category.payment_category_id == row.payment_category)
                            ?.name || '';
                } else if (row.subject == 0 && row.payment_category && row.description) {
                    categoryDescription = row.description;
                }
                // check when subscription
                if (row.subject == 1) {
                    categoryDescription = `${row.meta?.subscription_data?.name || 'quota singola'}`;
                }
                // check if course
                else if (row.subject == 2) {
                    if (row.is_carnet) {
                        categoryDescription = `Carnet (${row.carnet?.title})`;
                    } else {
                        categoryDescription = row.description ?? '-';
                    }
                }
                if (row.meta?.description && row.meta?.description != row.description) {
                    categoryDescription += '<br>';
                    categoryDescription += row?.meta?.description ?? '';
                }
                return (
                    '<div class="d-flex flex-column flex-wrap" style="line-height: 1.1"><span class="font-size-md font-weight-boldest">' +
                    (subjectDictionary[row.subject] || '') +
                    (categoryDescription
                        ? ` <br><span class="font-weight-bold font-size-xs">${categoryDescription}</span>`
                        : '') +
                    '</span></div>'
                );
            },
        },
        {
            field: 'amount',
            title: 'Importo',
            fireClick: true,
            width: 60,
            minWidth: '100%',
            sortable: false,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                // TODO: red or green color if positive or negative
                let amount =
                    parseFloat(row.amount) >= 0
                        ? '<span class="text-success" style=\'font-weight:700;\'>€ ' +
                          row.amount.replace('.', ',') +
                          '</span>'
                        : '<span class="text-danger" style=\'font-weight:700;\'>€ ' +
                          row.amount.replace('.', ',') +
                          '</span>';
                return amount;
            },
        },
        {
            field: 'type',
            title: 'Metodo',
            fireClick: true,
            checked: true,
            width: 70,
            sortable: true,
            responsive: {
                visible: 'xxl',
                hidden: 'xl',
            },
            template: function (row) {
                return typeTextDictionary[row.type];
            },
        },
        {
            field: 'expense',
            title: 'Tipo',
            checked: true,
            fireClick: true,
            width: 50,
            sortable: true,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                return row.expense ? 'Uscita' : 'Entrata';
            },
        },
        {
            field: 'custom_account_type',
            title: 'Conto',
            checked: true,
            fireClick: true,
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
            field: 'creation_date',
            title: 'Data',
            fireClick: true,
            width: 70,
            type: 'date',
            minWidth: '100%',
            responsive: {
                visible: 'md',
                hidden: 'sm',
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
                // Keep this compact in the enrollment payment tab.
                let date = moment(row.creation_date).format('DD/MM');
                return date;
            },
        },
        {
            field: 'payment_date',
            title: 'Pagato',
            fireClick: true,
            width: 70,
            type: 'date',
            minWidth: '100%',
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            sortCallback: function (data, sort, column) {
                let dataArray = Object.values(data);
                dataArray.sort(function (a, b) {
                    let timeA = new Date(a['payment_date']).getTime();
                    let timeB = new Date(b['payment_date']).getTime();
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
                if (row.payment_date == null) return '-';
                // Keep this compact in the enrollment payment tab.
                return moment(row.payment_date).format('DD/MM');
            },
        },
        {
            field: 'paid',
            title: 'Stato',
            fireClick: true,
            width: 65,
            minWidth: '100%',
            responsive: {
                visible: 'sm',
                hidden: 'xs',
            },
            sortable: false,
            template: function (row) {
                return statusTextDictionary[row.paid];
            },
        },
        {
            field: '',
            title: '',
            sortable: false,
            overflow: 'visible',
            textAlign: 'right',
            autoHide: false,
            width: info.archived ? 0 : 140,
            minWidth: '100%',
            template: function (row) {
                if (info.archived) return '';
                let pdfLink =
                    __bakney.env.API.DOCUMENT.RETRIEVE +
                    '/' +
                    row.invoice?.document_pdf +
                    '?download=false&token=' +
                    row.invoice?.document_token;
                waitForElementAndExecute(`#action-col-${row.payment_id}`, () => {
                    if (document.querySelector(`#action-col-${row.payment_id}`))
                        document.querySelector(`#action-col-${row.payment_id}`).innerHTML = '';

                    if (row.invoice && row.invoice.document_pdf) {
                        let documentButton = new DocumentButton({
                            target: document.querySelector(`#action-col-${row.payment_id}`),
                            intro: true,
                            props: {
                                disabled: false,
                                popover_text: `Ricevuta n.${row.invoice.number}`,
                            },
                        });

                        documentButton.$on('open', data => {
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
                                text: "Il pagamento verrà segnato come non pagato e sarà segnata come annullata l'eventuale ricevuta associata.",
                                icon: 'warning',
                                buttonsStyling: true,
                                showCancelButton: true,
                                cancelButtonText: 'Annulla',
                                confirmButtonText: 'Conferma',
                                reverseButtons: true,
                                confirmButtonColor: '#d63030',
                            }).then(async function (result) {
                                if (result.isConfirmed) {
                                    UiApp.blockPage({
                                        overlayColor: '#000000',
                                        state: 'primary',
                                        message: 'Annullamento in corso...',
                                    });

                                    const response = await apiFetch(
                                        replaceUID(__bakney.env.API.PAYMENT.CANCEL, row.payment_id),
                                        {
                                            method: 'POST',
                                        }
                                    );

                                    UiApp.unblockPage();

                                    if (!response.error) {
                                        let drawer = new PaymentDrawer({
                                            target: document.querySelector(`#portal-elements`),
                                            props: {
                                                data: response.response.data.payment,
                                                title: 'Dettagli Pagamento',
                                                isOpen: true,
                                            },
                                        });

                                        drawer.$on('close', () => {
                                            datatable.reload();
                                        });
                                        toast.success('Pagamento annullato!');
                                    } else {
                                        toast.error('Qualcosa è andato storto.');
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
                            target: document.querySelector(`#portal-elements`),
                            props: {
                                show: true,
                                data: {...row},
                                edit: true,
                            },
                        });

                        editModal.$on('update', e => {
                            let drawer = new PaymentDrawer({
                                target: document.querySelector(`#portal-elements`),
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
                            target: document.querySelector(`#portal-elements-foreground`),
                            buttonsStyling: true,
                            showCancelButton: true,
                            cancelButtonText: 'Annulla',
                            confirmButtonText: 'Elimina',
                            reverseButtons: true,
                            confirmButtonColor: '#d63030',
                        }).then(async function (result) {
                            if (result.isConfirmed) {
                                UiApp.blockPage({
                                    overlayColor: '#000000',
                                    state: 'primary',
                                    message: 'Eliminazione in corso...',
                                });

                                const response = await apiFetch(
                                    replaceUID(__bakney.env.API.PAYMENT.DELETE, row.payment_id),
                                    {
                                        method: 'DELETE',
                                    }
                                );

                                UiApp.unblockPage();

                                if (!response.error) {
                                    datatable.reload();
                                    toast.success('Pagamento eliminato!');
                                } else {
                                    toast.error('Qualcosa è andato storto.');
                                }
                            }
                        });
                    });
                });
                return `<div id="action-col-${row.payment_id}" class="action-column pr-4"></div>`;
            },
        },
    ];

    async function requestPaymentsSelected() {
        swal.fire({
            title: 'Sollecita pagamenti',
            html: `
                <p>Vuoi sollecitare ${selectedCounter} pagementi?</p>
                <div class="d-flex flex-column align-items-center">
                    <button
                        class="btn btn-primary mt-4 mx-0 font-weight-boldest w-100"
                        type="button"
                        id="emailButton">
                        Sollecita via Email
                    </button>
                    <button
                        class="btn btn-success mt-4 mx-0 font-weight-boldest w-100"
                        type="button"
                        id="whatsappButton">
                        Sollecita via WhatsApp
                    </button>
                    <button
                        class="btn btn-secondary mt-4 mx-0 font-weight-boldest w-100"
                        type="button"
                        id="cancelButton">
                        Annulla
                    </button>
                </div>
            `,
            showConfirmButton: false,
            showCancelButton: false,
            didOpen: () => {
                let checkedNodes = datatable.getSelectedRecords();
                let records = datatable.dataSet;
                // get an array of the selected row ids
                let selectedRowIds = Array.from(checkedNodes || []).map(node => node.dataset.row);

                const createWhatsAppLink = () => {
                    let text = `Buongiorno,\n`;

                    let unpaidPayments =
                        records?.filter((record, i) => !record.paid && selectedRowIds.includes(String(i))) || [];
                    let counterUnpaidPayments = 0;
                    let textDescription = '';
                    unpaidPayments.forEach((record, i) => {
                        textDescription += `Pagamento ${i + 1}:\n`;
                        textDescription += `Importo da pagare: ${record.amount}€\n`;
                        textDescription += `Informazioni: ${record.description}\n`;
                        textDescription += `Data scadenza: ${moment(record.creation_date).format('DD/MM/YYYY')}\n\n`;
                        counterUnpaidPayments++;
                    });
                    text += `Le ricordiamo che ha ${
                        counterUnpaidPayments > 1 ? 'dei pagamenti in sospeso' : 'un pagamento in sospeso'
                    }.\n\n`;
                    text += textDescription;
                    text += `La preghiamo di regolarizzare la sua posizione.\n`;
                    text += `Grazie\n\n`;
                    text += `Cordialmente,\n${$userData?.sport_association?.denomination || ''}`;

                    return `https://wa.me/?text=${encodeURIComponent(text)}`;
                };

                document.getElementById('whatsappButton')?.addEventListener('click', () => {
                    window.open(createWhatsAppLink(), '_blank');
                    swal.close();
                });

                document.getElementById('emailButton')?.addEventListener('click', async () => {
                    swal.close();
                    UiApp.blockPage({
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
                        UiApp.unblockPage();

                        if (values.length > 0) {
                            if (values.length != selectedCounter) {
                                toast.warning(`${values.length} Pagamenti sollecitati su ${selectedCounter}.`);
                            } else if (values.every(r => r)) {
                                toast.success(`${values.length} Pagamenti sollecitati.`);
                            } else {
                                let valid = values.filter(r => r).length;
                                toast.warning(`${valid} Pagamenti sollecitati su ${selectedCounter}.`);
                            }
                        } else {
                            toast.warning(`Nessun pagamento sollecitato.`);
                        }

                        datatable.reload();
                        hideCollapse('bkn_datatable_group_action_form_' + uuid);
                    });
                });

                document.getElementById('cancelButton')?.addEventListener('click', () => {
                    swal.close();
                });
            },
            willClose: () => {
                document.getElementById('whatsappButton')?.removeEventListener('click', () => {});
                document.getElementById('emailButton')?.removeEventListener('click', () => {});
                document.getElementById('cancelButton')?.removeEventListener('click', () => {});
            },
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
                UiApp.blockPage({
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

                UiApp.unblockPage();

                if (!result.error) {
                    toast.success(`${selectedCounter} Pagamenti Eliminati.`);
                } else {
                    toast.warning(`Nessun pagamento eliminato.`);
                }
                hideCollapse('bkn_datatable_group_action_form_' + uuid);
                datatable.reload();
            }
        });
    }

    let markAsPaid = async (id, payment_date, expense = false) => {
        payment_date = payment_date
            ? moment(new Date(payment_date)).format('YYYY-MM-DD')
            : moment().format('YYYY-MM-DD');
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
                            <label class="checkbox">
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
                UiApp.blockPage({
                    overlayColor: '#000000',
                    state: 'primary',
                    message: 'Conferma in corso...',
                });

                const approveUrl = replaceUID(__bakney.env.API.PAYMENT.APPROVE, id);

                let body = {
                    payment_date: document.getElementById(`payment_date_${id}`).value || null,
                    send_receipt_email: document.getElementById(`send_receipt_email_${id}`).checked || false,
                    generate_invoice: document.getElementById(`generate_invoice_${id}`).checked || false,
                };

                window
                    .fetch(approveUrl, {
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
                        // refresh detail view
                        if (response.status == 200) {
                            // spinner stop
                            UiApp.unblockPage();

                            if (res.data.payment) {
                                let paymentDrawer = new PaymentDrawer({
                                    target: document.querySelector(`#portal-elements`),
                                    intro: true,
                                    props: {
                                        isOpen: true,
                                        data: res.data.payment,
                                        title: 'Pagamento',
                                    },
                                });
                                paymentDrawer.$on('close', e => {
                                    datatable.reload();
                                });
                            }

                            // dispatch('reset', {
                            //     page: 'payments',
                            //     searchKey: id,
                            // });
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
                            // spinner stop
                            UiApp.unblockPage();
                        }
                    });
            }
        });
    };

    async function fetchCategories() {
        let res = await apiFetch(__bakney.env.API.PAYMENT.CATEGORY.LIST);

        if (!res.error) categories = res.response.data || [];
        else if (res.status != 403 && res.status != 401) toast.error('Qualcosa è andato storto.');
    }

    function resetFilters(update = true) {
        document.getElementById('bkn_datatable_search_query_' + uuid).value = '';

        document.getElementById('bkn_datatable_search_status_' + uuid).value = '';
        refreshSelectpicker(document.getElementById('bkn_datatable_search_status_' + uuid));

        document.getElementById('bkn_datatable_search_expense_' + uuid).value = '';
        refreshSelectpicker(document.getElementById('bkn_datatable_search_expense_' + uuid));

        document.getElementById('bkn_datatable_search_subject_' + uuid).value = '';
        refreshSelectpicker(document.getElementById('bkn_datatable_search_subject_' + uuid));

        document.getElementById('bkn_datatable_search_type_' + uuid).value = '';
        refreshSelectpicker(document.getElementById('bkn_datatable_search_type_' + uuid));

        if (update) {
            document.getElementById('bkn_datatable_search_query_' + uuid).dispatchEvent(new Event('keyup'));
            datatableKey++;
        }
    }

    onMount(async () => {
        await fetchCategories();

        if (canPerformAction('bookeeping.payments.read')) ready = true;
        initTooltips(document.body);

        if (searchKey && searchKey != 'payments') {
            setTimeout(() => {
                let searchInput = document.getElementById('bkn_datatable_search_query_' + uuid);
                if (searchInput) {
                    searchInput.value = searchKey;
                    searchInput.dispatchEvent(new Event('keyup'));
                }
            }, 500);
        }
        // stop scroll of body
        //document.body.style.overflow = 'hidden';
    });

    onDestroy(() => {
        document.querySelectorAll('.popover').forEach(popover => popover.remove());
        document.querySelectorAll('.tooltip').forEach(popover => popover.remove());
        searchKey = '';
        //document.body.style.overflow = 'auto';
    });
</script>

<!--begin::Entry-->
<div >
    {#if !isFreePlan()}
        <div class="row mt-2">
            <div class="col-10">
                <h3 class="card-label font-size-h2">
                    Pagamenti
                    <span class="d-block text-muted pt-2 font-size-sm">Storico dei pagamenti dell'iscrizione.</span>
                </h3>
            </div>
            <div class="col-2 d-flex justify-content-end px-0">
                {#if !info.archived && canPerformAction('bookeeping.payments.create')}
                    <button
                        on:click={() => {
                            // perform tasks

                            let addModal = new AddEditModal({
                                target: document.querySelector(`#portal-elements-foreground`),
                                props: {
                                    show: true,
                                    editableComplexItem: false,
                                    data: {
                                        type: 'cash',
                                        expense: false,
                                        associate: {
                                            associate_id: info.associate.associate_id,
                                        },
                                        subscription_id: info.subscription_id,
                                        meta_payment_categories: [],
                                    },
                                },
                            });

                            addModal.$on('update', e => {
                                let drawer = new PaymentDrawer({
                                    target: document.querySelector(`#portal-elements-foreground`),
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
                        }}
                        class="btn btn-sm btn-primary font-weight-bolder m-2 my-4">
                        <PlusCircle size={16} weight="bold" />
                        <span class="ml-0 ml-md-1"><span class="d-none d-md-inline-block">Pagamento</span></span>
                    </button>
                {/if}
            </div>
        </div>
        <div class="row">
            <div class="col-12 mt-4">
                <div class="mt-10 mb-5 collapse" id="bkn_datatable_group_action_form_{uuid}">
                    <div class="d-flex align-items-center">
                        <div class="font-weight-bold mr-3" style="font-size:1.1rem;">
                            <span id="bkn_datatable_selected_records_{uuid}">0</span> selezionati
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
                    </div>
                </div>
                <!--begin: Datatable-->
                {#if ready}
                {#key datatableKey}
                <BKNDatatable
                    bind:datatable
                    bind:selectedCounter
                    id={`bkn_datatable_payments_${uuid}`}
                    searchId={`bkn_datatable_search_query_${uuid}`}
                    {columns}
                    {url}
                    params={{query: {generalSearch: searchKey}}}
                    showDividerFilter={false}
                    clicked={function (td, obj) {
                        let basicDrawer = new PaymentDrawer({
                            target: document.querySelector(`#portal-elements`),
                            intro: true, // This enables the mount animation
                            props: {
                                data: obj,
                                title: 'Dettagli Pagamento',
                            },
                        });

                        basicDrawer.$on('close', () => {
                            datatable.reload();
                        });
                    }}
                    loadFilters={() => {
                        const statusEl = document.getElementById(`bkn_datatable_search_status_${uuid}`);
                        statusEl?.addEventListener('change', function (e) {
                            datatable.search(e.currentTarget.value.toLowerCase(), 'paid');
                        });
                        initSelectpicker(statusEl);

                        const expenseEl = document.getElementById('bkn_datatable_search_expense_' + uuid);
                        expenseEl?.addEventListener('change', function (e) {
                            datatable.search(e.currentTarget.value.toLowerCase(), 'expense');
                        });
                        initSelectpicker(expenseEl);

                        const subjectEl = document.getElementById('bkn_datatable_search_subject_' + uuid);
                        subjectEl?.addEventListener('change', function (e) {
                            datatable.search(e.currentTarget.value.toLowerCase(), 'subject');
                        });
                        initSelectpicker(subjectEl);

                        const typeEl = document.getElementById('bkn_datatable_search_type_' + uuid);
                        typeEl?.addEventListener('change', function (e) {
                            datatable.search(e.currentTarget.value.toLowerCase(), 'type');
                        });
                        initSelectpicker(typeEl);
                    }}
                >
                    <div slot="search-header" class="d-flex flex-wrap align-items-center">
                        <div class="my-1 my-md-0 mr-2">
                            <div class="d-flex align-items-center">
                                <!-- svelte-ignore a11y-label-has-associated-control -->
                                <select
                                    class="form-control form-control-solid mb-0"
                                    id="bkn_datatable_search_status_{uuid}">
                                    <option value="">Stato</option>
                                    <option value="true">Pagato</option>
                                    <option value="false">In attesa</option>
                                </select>
                            </div>
                        </div>
                        <div class="my-1 my-md-0 mr-2">
                            <div class="d-flex align-items-center">
                                <select
                                    class="form-control form-control-solid mb-0"
                                    id="bkn_datatable_search_type_{uuid}">
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
                                    class="form-control form-control-solid mb-0"
                                    id="bkn_datatable_search_expense_{uuid}">
                                    <option value="">Tipo</option>
                                    <option value="true">Uscite</option>
                                    <option value="false">Entrate</option>
                                </select>
                            </div>
                        </div>
                        <div class="my-1 my-md-0 mr-2">
                            <div class="d-flex align-items-center">
                                <select
                                    class="form-control form-control-solid mb-0"
                                    id="bkn_datatable_search_subject_{uuid}">
                                    <option value="">Attività</option>
                                    {#each Array.from(paymentSubjectsMap || []) as subject}
                                        <option value={subject.key}>
                                            {subject.value}
                                        </option>
                                    {/each}
                                </select>
                            </div>
                        </div>
                        <div class="my-1 my-md-0 ml-auto">
                            <!-- svelte-ignore a11y-click-events-have-key-events -->
                            <!-- svelte-ignore a11y-missing-attribute -->
                            <!-- svelte-ignore a11y-no-static-element-interactions -->
                            <a
                                on:click|preventDefault={resetFilters}
                                class=" btn font-weight-bolder mb-0 cursor-pointer text-primary btn-clean btn-icon">
                                <X size={18} weight="bold" />
                            </a>
                        </div>
                    </div>
                    <div slot="multiactions">
                    </div>
                </BKNDatatable>
                {/key}
                {/if}
                <!--end: Datatable-->
            </div>
        </div>
    {:else}
        <Upgrade />
    {/if}
</div>

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
