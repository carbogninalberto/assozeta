<script>
    import {apiFetch, replaceUID} from 'utils/ApiMiddleware';
    import {sessionToken} from 'store/stores.js';
    import {scale, slide} from 'svelte/transition';
    import * as easing from 'svelte/easing';
    import {onDestroy, onMount} from 'svelte';
    import {Calculator, Clock} from 'phosphor-svelte';
    import AddEditModal from './modals/AddEditModal.svelte';
    import {waitForElementAndExecute} from 'utils/Functions';
    import EditButton from 'components/buttons/EditButton.svelte';
    import DeleteButton from 'components/buttons/DeleteButton.svelte';
    import DocumentButton from 'components/buttons/DocumentButton.svelte';
    import DocumentPreviewModal from 'components/modals/DocumentPreviewModal.svelte';
    import {canPerformAction} from 'utils/Permissions';
    import DocumentSignatureModal from 'components/signature/DocumentSignatureModal.svelte';
    import {toast} from 'svelte-sonner';
    import BackButton from 'components/buttons/BackButton.svelte';
    import BKNDatatable from 'components/tables/BKNDatatable.svelte';
    import { initTooltips, destroyTooltips } from 'shim/tooltip.js';
    import { initPopovers, destroyPopovers } from 'shim/popover.js';
    import DateInput from 'components/inputs/DateInput.svelte';
    import DateRangePicker from 'components/inputs/DateRangePicker.svelte';
    import {showModal} from 'shim/modal.js';
	import { UiApp } from 'shim/ui.js';
    sessionToken.useLocalStorage();

    export let params;
    let id = params.id;

    let data = {};
    let loading = true;
    let visibleMultiaction = false;
    let selectedCounter = 0;
    let datatable;
    let filterKey = 0;
    function handleInstructorDateRangeChange(e) {
        currentFilterStart = e.detail.start;
        currentFilterEnd = e.detail.end;
        const currentFilter = formatInstructorRange(currentFilterStart, currentFilterEnd);
        if (datatable) {
            datatable.search(currentFilter.toLowerCase(), 'date_range');
        }
        fetchInfoWidget();
    }

    let currentFilterStart = moment().startOf('month').format('DD/MM/YYYY');
    let currentFilterEnd = moment().endOf('month').format('DD/MM/YYYY');

    function formatInstructorRange(start, end) {
        return start && end ? `${start} al ${end}` : start || end || '';
    }

    const paidDict = {
        0: '<span class="label label-light-danger label-inline font-weight-bolder label-lg">da pagare</span>',
        1: '<span class="label label-light-success label-inline font-weight-bolder label-lg">pagato</span>',
    };

    const columns = [
        {
            field: 'instructor_hours_id',
            title: '#',
            sortable: false,
            width: 20,
            selector: {
                class: '',
            },
            textAlign: 'center',
        },
        {
            field: 'date',
            title: 'Data',
            autoHide: false,
            sortable: false,
            width: 80,
            minWidth: '100%',
            template: function (row) {
                return `<span class="font-weight-boldest">${moment(row.date).format('DD/MM/YYYY')}</span>`;
            },
        },

        {
            field: 'compensation_type',
            title: 'TIPO',
            sortable: false,
            width: 80,
            minWidth: '100%',
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                return row.compensation_type == 'hourly'
                    ? '<span class="label label-light-primary label-inline font-weight-bolder label-lg">Orario</span>'
                    : '<span class="label label-light-warning label-inline font-weight-bolder label-lg">Percentuale</span>';
            },
        },
        {
            field: 'amount',
            title: 'Compensi',
            sortable: false,
            width: 120,
            minWidth: '100%',
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                return (
                    `<div class="font-size-sm" style="line-height: 1.2;"><span class="font-weight-boldest text-success">${Number(
                        row?.amount || 0
                    ).toLocaleString('it-IT', {
                        maximumFractionDigits: 2,
                        minimumFractionDigits: 2,
                    })} €</span><br>` +
                    (row.compensation_type == 'hourly'
                        ? `<span class="font-weight-bold font-size-sm text-primary">${Number(
                              row?.hours || 0
                          ).toLocaleString('it-IT', {
                              maximumFractionDigits: 2,
                              minimumFractionDigits: 2,
                          })} ore · ${Number(row?.hourly_billing || 0).toLocaleString('it-IT', {
                              maximumFractionDigits: 2,
                              minimumFractionDigits: 2,
                          })} €</span>`
                        : `<span class="font-weight-bold font-size-sm text-primary">${Number(
                              row?.percentage_billing || 0
                          ).toLocaleString('it-IT', {
                              maximumFractionDigits: 2,
                              minimumFractionDigits: 2,
                          })} %</span></div>`)
                );
            },
        },
        {
            field: 'paid',
            title: 'Stato',
            width: 80,
            minWidth: '100%',
            sortable: false,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                return paidDict[row.paid ? 1 : 0];
            },
        },
        {
            field: 'notes',
            title: 'Note',
            sortable: false,
            minWidth: '100%',
            width: 200,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                let notes = row.notes && row.notes != '' ? row.notes : '-';
                if (row.payment && canPerformAction('bookeeping.payments.read')) {
                    notes += `<br><a href="/#/payment/list/${row.payment}" class="font-weight-boldest text-primary" style="font-size: .9rem;">pagamento rimborso associato</a>`;
                }
                return `<div class="font-size-sm" style="line-height: 1.2;">${notes}</div>`;
            },
        },
        {
            field: '',
            title: '',
            sortable: false,
            overflow: 'visible',
            textAlign: 'right',
            autoHide: false,
            width: 120,
            minWidth: '100%',
            template: function (row) {
                let pdfLink =
                    __bakney.env.API.DOCUMENT.RETRIEVE +
                    '/' +
                    row.document +
                    '?download=false&token=' +
                    row.document_token;
                waitForElementAndExecute(`#action-col-${row.instructor_hours_id}`, () => {
                    if (document.querySelector(`#action-col-${row.instructor_hours_id}`))
                        document.querySelector(`#action-col-${row.instructor_hours_id}`).innerHTML = '';

                    if (row.document) {
                        let documentButton = new DocumentButton({
                            target: document.querySelector(`#action-col-${row.instructor_hours_id}`),
                            intro: true,
                            props: {
                                disabled: false,
                                popover_text: `Scarica il compenso - ${moment(row.date).format('DD/MM/YYYY')}`,
                            },
                        });

                        documentButton.$on('open', data => {
                            let filePreview = new DocumentPreviewModal({
                                target: document.querySelector(`#action-col-${row.instructor_hours_id}`),
                                intro: true,
                                props: {
                                    pdfLink: pdfLink,
                                    id: row.instructor_hours_id,
                                    title: `Compenso istruttore - ${moment(row.date).format('DD/MM/YYYY')}`,
                                    signatureButton: true,
                                    signatureFunction: () => {
                                        // init SignatureModal
                                        let signatureModal = new DocumentSignatureModal({
                                            target: document.querySelector(
                                                `#action-col-${row.instructor_hours_id}`
                                            ),
                                            props: {
                                                id: row.payment,
                                                show: true,
                                                type: 'payment',
                                            },
                                        });

                                        // listen for close event
                                        signatureModal.$on('close', data => {
                                            // set filePreview prop show to false
                                            filePreview.$set({show: false});
                                            datatable.reload();
                                        });
                                    },
                                },
                            });
                        });
                    }

                    let editBtn = new EditButton({
                        target: document.querySelector(`#action-col-${row.instructor_hours_id}`),
                        intro: true,
                        props: {
                            disabled: row.payment || !canPerformAction('association.instructor.hours.update'),
                            hidden: false,
                        },
                    });

                    let editModal = new AddEditModal({
                        target: document.querySelector(`#action-col-${row.instructor_hours_id}`),
                        intro: true,
                        props: {
                            id: row.instructor_hours_id,
                            row: row,
                            datatableHandle: {reload: () => datatable.reload()},
                            edit: true,
                        },
                    });

                    editModal.$on('close', data => {
                        fetchInfoWidget();
                    });

                    editBtn.$on('open', data => {
                        showModal(`modal-${row.instructor_hours_id}`);
                    });
                    let deleteBtn = new DeleteButton({
                        target: document.querySelector(`#action-col-${row.instructor_hours_id}`),
                        intro: true,
                        props: {
                            disabled: row.payment || !canPerformAction('association.instructor.hours.delete'),
                        },
                    });

                    deleteBtn.$on('open', data => {
                        swal.fire({
                            text: "Vuoi eliminare l'istruttore?",
                            icon: 'warning',
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
                                    replaceUID(
                                        replaceUID(__bakney.env.API.INSTRUCTOR.HOURS.DELETE, row.instructor),
                                        row.instructor_hours_id,
                                        '<hour_uid>'
                                    ),
                                    {
                                        method: 'DELETE',
                                    }
                                );

                                UiApp.unblockPage();

                                if (!response.error) {
                                    datatable.reload();
                                    toast.success('Istruttore eliminato!');
                                } else {
                                    toast.error('Qualcosa è andato storto.');
                                }
                            }
                        });
                    });
                });
                return `<div id="action-col-${row.instructor_hours_id}" class="action-column pr-4"></div>`;
            },
        },
    ];

    function addCompensation() {
        swal.fire({
            html: `<span class="text-center"><h6>Verrà creato il compenso solo per le ore che:</h6><br><ul class="list-unstyled text-left"><li>1. non sono ancora state pagate</li><li>2. non hanno un pagamento associato</li></ul><br></span><h6>Continuare?</h6>`,
            icon: 'warning',
            buttonsStyling: true,
            showCancelButton: true,
            cancelButtonText: 'Annulla',
            confirmButtonText: 'Crea compenso',
            reverseButtons: true,
        }).then(async function (result) {
            if (result.isConfirmed) {
                let checkedNodes = datatable.getSelectedRecords();
                let records = datatable.dataSet;
                let count = checkedNodes.length;
                UiApp.blockPage({
                    overlayColor: '#000000',
                    state: 'primary',
                    message: 'Operazione in corso...',
                });
                let hours = [];
                if (count > 0) {
                    for (let i = 0; i < count; i++) {
                        let id = checkedNodes[i].dataset.row;
                        hours = [...hours, records[id].instructor_hours_id];
                    }
                }

                let res = await apiFetch(replaceUID(__bakney.env.API.INSTRUCTOR.HOURS.ADD_COMPENSATION, id), {
                    method: 'POST',
                    body: JSON.stringify({
                        hours: hours,
                    }),
                });
                visibleMultiaction = false;

                UiApp.unblockPage();

                if (res.error) {
                    toast.error('Qualcosa è andato storto.');
                } else {
                    toast.success('Compensi creati');
                }

                filterKey++;
            }
        });
    }

    async function fetchInfoWidget() {
        const response = await apiFetch(
            `${replaceUID(__bakney.env.API.INSTRUCTOR.INFO, id)}?date_range=${formatInstructorRange(currentFilterStart, currentFilterEnd)}`,
            {
                method: 'GET',
            }
        ).then(res => {
            data = res.response;
            loading = false;
        });
    }

    onMount(async () => {
        localStorage.removeItem('bkn_datatable-1-meta');
        initTooltips(document.body);
        initPopovers(document.body);
        fetchInfoWidget();
    });

    onDestroy(() => {
        document.querySelectorAll('.popover').forEach(popover => popover.remove());
    });
</script>

<!--begin::Entry-->
<div  class="d-flex flex-column-fluid">
    <!--begin::Container-->
    <div class="container container-overlay">
        <!--begin::Card-->
        <div class="card card-custom gutter-b">
            <div class="card-header p-0 header-mobile-btn-back border-0">
                <div class="card-toolbar d-flex gap-4" style="gap: .5rem;">
                    <BackButton />
                </div>
                <div class="card-toolbar">
                    <h3 class="card-title font-size-h2">
                        {data.data?.first_name?.toUpperCase() || ''}
                        {data.data?.last_name?.toUpperCase() || ''}
                    </h3>
                </div>
                <div class="card-toolbar">
                    <!--begin::Button-->
                    <!-- svelte-ignore a11y-click-events-have-key-events -->
                    <button
                        disabled={!canPerformAction('association.instructor.hours.create')}
                        class="btn btn-sm btn-primary font-weight-bolder m-2"
                        on:click={() => {
                            showModal(`modal-${id}`);
                        }}>
                        <Clock size={18} weight="duotone" />
                        <span class="ml-1"><span class="d-none d-md-inline-block">Aggiungi</span></span>
                    </button>
                    <!--end::Button-->
                </div>
            </div>
            <div class="card-body p-0">
                <div class="mb-2">
                    <h2>Scheda compensi</h2>
                    <span class="text-muted">Riepilogo di ore e compensi istruttore.</span>
                </div>
                {#if !loading}
                    <div
                        class="d-none d-md-flex justify-content-start mb-2 mt-2 mt-md-0 pb-2 pb-md-0 overflow-auto"
                        style="flex-wrap: wrap;">
                        <div class="col-12 col-md-3 p-2 pr-md-4" in:scale={{duration: 250, start: 0.92}}>
                            <div class="card-widget card p-0 m-0">
                                <div class="card-body p-4">
                                    <div class="mb-0">
                                        <h6 class="font-weight-boldest text-center mb-0" style="font-size: 1rem;">
                                            ORE LAVORATE
                                            <br />
                                        </h6>
                                    </div>
                                    <div
                                        class="text-center font-weight-bolder text-primary"
                                        style="font-size: 1.75rem;">
                                        <span class="text-primary"
                                            >{Number(data.stats?.hours || 0).toLocaleString('it-IT', {
                                                maximumFractionDigits: 2,
                                                minimumFractionDigits: 2,
                                            })}</span>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="col-12 col-md-3 p-2 pr-md-4" in:scale={{duration: 250, start: 0.92}}>
                            <div class="card-widget card p-0 m-0">
                                <div class="card-body p-4">
                                    <div class="mb-0">
                                        <h6 class="font-weight-boldest text-center mb-0" style="font-size: 1rem;">
                                            COMPENSO TOTALE
                                            <br />
                                        </h6>
                                    </div>
                                    <div
                                        class="text-center font-weight-bolder text-primary"
                                        style="font-size: 1.75rem;">
                                        <span class="text-primary"
                                            >{Number(data.stats?.total_amount || 0).toLocaleString('it-IT', {
                                                maximumFractionDigits: 2,
                                                minimumFractionDigits: 2,
                                            })} €
                                        </span>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="col-12 col-md-3 p-2 pr-md-4" in:scale={{duration: 250, start: 0.92}}>
                            <div class="card-widget card p-0 m-0">
                                <div class="card-body p-4">
                                    <div class="mb-0">
                                        <h6 class="font-weight-boldest text-center mb-0" style="font-size: 1rem;">
                                            COMPENSO DA PAGARE
                                            <br />
                                        </h6>
                                    </div>
                                    <div
                                        class="text-center font-weight-bolder text-primary"
                                        style="font-size: 1.75rem;">
                                        <span class="text-danger"
                                            >{Number(data.stats?.total_amount_to_pay || 0).toLocaleString('it-IT', {
                                                maximumFractionDigits: 2,
                                                minimumFractionDigits: 2,
                                            })} €
                                        </span>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="col-12 col-md-3 p-2 pr-md-4" in:scale={{duration: 250, start: 0.92}}>
                            <div class="card-widget card p-0 m-0">
                                <div class="card-body p-4">
                                    <div class="mb-0">
                                        <h6 class="font-weight-boldest text-center mb-0" style="font-size: 1rem;">
                                            COMPENSO PAGATO
                                            <br />
                                        </h6>
                                    </div>
                                    <div
                                        class="text-center font-weight-bolder text-primary"
                                        style="font-size: 1.75rem;">
                                        <span class="text-success"
                                            >{Number(data.stats?.total_amount_paid || 0).toLocaleString('it-IT', {
                                                maximumFractionDigits: 2,
                                                minimumFractionDigits: 2,
                                            })} €
                                        </span>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                {/if}
                {#key filterKey}
                    <BKNDatatable
                        bind:datatable
                        bind:visibleMultiaction
                        bind:selectedCounter
                        {columns}
                        url={replaceUID(__bakney.env.API.INSTRUCTOR.HOURS.LIST, id)}
                        params={{
                            query: {
                                date_range: formatInstructorRange(currentFilterStart, currentFilterEnd),
                            },
                        }}
                        loadFilters={() => {
                            // date range is handled by DateInput component
                        }}
                    >
                        <div slot="search-header" class="d-flex align-items-center">
                            <div class="my-1 my-md-0 mr-2">
                                <div class="d-flex align-items-center">
                                    <DateRangePicker
                                        id="instructor-date-range"
                                        format="DD/MM/YYYY"
                                        sizeClass=""
                                        startPlaceholder="Dal"
                                        endPlaceholder="Al"
                                        bind:startValue={currentFilterStart}
                                        bind:endValue={currentFilterEnd}
                                        on:change={handleInstructorDateRangeChange}
                                    />
                                </div>
                            </div>
                        </div>
                        <div slot="multiactions">
                            {#if visibleMultiaction}
                                <div in:slide={{duration: 100}} class="mt-10 mb-5" id="bkn_datatable_group_action_form">
                                    <div class="d-flex align-items-center">
                                        <div class="font-weight-boldest mr-3 ml-2" style="font-size:1.1rem;">
                                            {selectedCounter} selezionati
                                        </div>
                                        <button
                                            disabled={!canPerformAction('association.instructor.hours.create')}
                                            on:click={() => addCompensation()}
                                            class="btn btn-sm btn-primary font-weight-bolder m-0 ml-2 p-2 d-flex align-items-center"
                                            type="button"
                                            data-toggle="tooltip"
                                            data-placement="top"
                                            title="Crea compenso"
                                            id="bkn_datatable_approve_selected">
                                            <Calculator size="17" weight="duotone" class="mr-1" />

                                            <span class="d-none d-md-block">Crea compenso</span></button>
                                    </div>
                                </div>
                            {/if}
                        </div>
                    </BKNDatatable>
                {/key}
            </div>
        </div>
    </div>
    <!--end::Container-->
</div>
<!--end::Entry-->

<AddEditModal
    edit={false}
    {id}
    datatableHandle={{reload: () => datatable.reload()}}
    bind:instructorData={data}
    on:close={() => {
        fetchInfoWidget();
    }} />
