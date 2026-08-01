<script>
    import {sessionToken, userData} from 'store/stores.js';
    import {scale} from 'svelte/transition';
    import * as easing from 'svelte/easing';
    import {onMount, onDestroy} from 'svelte';
    import {push} from 'svelte-spa-router';
    import {replaceUID, apiFetch} from 'utils/ApiMiddleware.js';
    import {Printer, UserPlus} from 'phosphor-svelte';
    import EditButton from 'components/buttons/EditButton.svelte';
    import DeleteButton from 'components/buttons/DeleteButton.svelte';
    import EditModal from './modals/EditModal.svelte';
    import {waitForElementAndExecute} from 'utils/Functions';
    import {canPerformAction} from 'utils/Permissions';
    import {toast} from 'svelte-sonner';
    import ReportModal from './modals/ReportModal.svelte';
    import BKNDatatable from 'components/tables/BKNDatatable.svelte';
    import { initTooltips, destroyTooltips } from 'shim/tooltip.js';
    import { initPopovers, destroyPopovers } from 'shim/popover.js';
    import {showModal} from 'shim/modal.js';
	import { UiApp } from 'shim/ui.js';

    sessionToken.useLocalStorage();
    userData.useLocalStorage();

    let instructors = [];
    let datatable;

    const connectedUser = {
        0: '<span class="label label-light-danger label-inline font-weight-bolder label-lg">nessuno</span>',
        1: '<span class="label label-light-success label-inline font-weight-bolder label-lg">associato</span>',
    };

    const mapFunction = function (raw) {
        var dataSet = raw;
        if (typeof raw.data !== 'undefined') {
            dataSet = raw.data;
        }
        if ($userData.instructor_id != null) {
            dataSet = dataSet.filter(function (item) {
                return item.instructor_id == $userData.instructor_id;
            });
        }
        instructors = dataSet;
        return dataSet;
    };

    const columns = [
        {
            field: 'name',
            title: 'Nome e Cognome',
            autoHide: false,
            sortable: false,
            minWidth: '100%',
            template: function (row) {
                return (
                    `<a class="link link-primary font-weight-bolder mb-0" href="/#/course/instructor/info/${row.instructor_id}" use:link >` +
                    row.first_name.toUpperCase() +
                    ' ' +
                    row.last_name.toUpperCase() +
                    '</a>'
                );
            },
        },
        {
            field: 'email',
            title: 'Email',
            sortable: false,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                return '<p class="text-dark-75 font-weight-bolder mb-0">' + row.email.toLowerCase() + '</p>';
            },
        },
        {
            field: 'phone',
            title: 'Telefono',
            sortable: false,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                let phone = row.phone != '' ? row.phone : '-';
                return '<p class="text-dark-75 font-weight-bolder mb-0">' + phone + '</p>';
            },
        },
        {
            field: 'associated_user_id',
            title: 'Account',
            sortable: false,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                let associato = row.associated_user_id != null ? 1 : 0;
                return connectedUser[associato];
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
                let disablePayment = row.paid;
                let disableAccount = true;
                waitForElementAndExecute(`#action-col-${row.instructor_id}`, () => {
                    if (document.querySelector(`#action-col-${row.instructor_id}`))
                        document.querySelector(`#action-col-${row.instructor_id}`).innerHTML = '';

                    let editBtn = new EditButton({
                        target: document.querySelector(`#action-col-${row.instructor_id}`),
                        intro: true,
                        props: {
                            disabled: !canPerformAction('association.instructor.update'),
                            hidden: false,
                        },
                    });

                    let editModal = new EditModal({
                        target: document.querySelector(`#action-col-${row.instructor_id}`),
                        intro: true,
                        props: {
                            id: row.instructor_id,
                            row: row,
                            datatableHandle: {reload: () => datatable.reload()},
                        },
                    });

                    editBtn.$on('open', data => {
                        showModal(`editModal-${row.instructor_id}`);
                    });
                    let deleteBtn = new DeleteButton({
                        target: document.querySelector(`#action-col-${row.instructor_id}`),
                        intro: true,
                        props: {
                            disabled: row.invoice || !canPerformAction('association.instructor.delete'),
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
                                    replaceUID(__bakney.env.API.INSTRUCTOR.DELETE, row.instructor_id),
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
                return `<div id="action-col-${row.instructor_id}" class="action-column pr-4"></div>`;
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
                        Istruttori
                        <span class="d-block text-muted pt-2 font-size-sm"
                            >Contiene la lista completa degli istruttori.</span>
                    </h3>
                </div>
                <div class="card-toolbar">
                    <!--begin::Button-->
                    <!-- svelte-ignore a11y-click-events-have-key-events -->
                    <button
                        class="btn btn-sm btn-light-primary font-weight-bolder m-2"
                        on:click={() => {
                            let reportModal = new ReportModal({
                                target: document.querySelector(`#portal-elements`),
                                intro: true,
                                props: {
                                    show: true,
                                },
                            });
                        }}>
                        <Printer size={18} weight="duotone" />
                        <span class="ml-1">Stampa Report</span>
                    </button>
                    <!--end::Button-->
                    <!--begin::Button-->
                    <!-- svelte-ignore a11y-click-events-have-key-events -->
                    <button
                        disabled={!canPerformAction('association.instructor.create')}
                        on:click={() => push('/course/instructor/add')}
                        class="btn btn-sm btn-primary font-weight-bolder m-2">
                        <UserPlus size={18} weight="duotone" />
                        <span class="ml-1"><span class="d-none d-md-inline-block">Istruttore</span></span>
                    </button>
                    <!--end::Button-->
                </div>
            </div>
            <div class="card-body p-0">
                <BKNDatatable
                    bind:datatable
                    {columns}
                    url={__bakney.env.API.INSTRUCTOR.LIST}
                    {mapFunction}
                    serverPaging={false}
                    serverFiltering={false}
                    serverSorting={false}
                />
            </div>
        </div>
        <!--end::Card-->
    </div>
    <!--end::Container-->
</div>
<!--end::Entry-->
