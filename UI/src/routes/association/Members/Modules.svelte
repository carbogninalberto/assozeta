<script>
    import {sessionToken} from 'store/stores.js';
    import {onDestroy} from 'svelte';
    import {replaceUID} from 'utils/ApiMiddleware.js';
    import {PlusCircle} from 'phosphor-svelte';
    import {waitForElementAndExecute} from 'utils/Functions';
    import {apiFetch} from 'utils/ApiMiddleware';
    import DeleteButton from 'components/buttons/DeleteButton.svelte';
    import DuplicateButton from 'components/buttons/DuplicateButton.svelte';
    import {link} from 'svelte-spa-router';
    import {canPerformAction} from 'utils/Permissions';
    import {toast} from 'svelte-sonner';
    import BKNDatatable from 'components/tables/BKNDatatable.svelte';
	import { UiApp } from 'shim/ui.js';

    sessionToken.useLocalStorage();

    const enabledDictionary = {
        0: '<span class="label label-light-danger label-inline font-weight-bolder label-lg">Nascosto</span>',
        1: '<span class="label label-light-success label-inline font-weight-bolder label-lg">Visibile</span>',
    };

    let datatable;

    const columns = [
        {
            field: 'title',
            title: 'Titolo',
            sortable: true,
            width: 120,
            checked: true,
            minWidth: '100%',
            autoHide: false,
            sortCallback: function (data, sort, column) {
                // sort by alphabetical order, the column field is 'title'
                let dataArray = Object.values(data);
                return dataArray.sort(function (a, b) {
                    var x = a.title.toLowerCase();
                    var y = b.title.toLowerCase();
                    if (sort === 'asc') {
                        return x < y ? -1 : x > y ? 1 : 0;
                    } else {
                        return x > y ? -1 : x < y ? 1 : 0;
                    }
                });
            },
            template: function (row) {
                return (
                    "<a href='/#/members/modules/overview/" +
                    row.module_id +
                    '\'><span class="navi-text font-weight-bolder text-hover-primary" style="cursor: pointer"><b>' +
                    row.title.charAt(0).toUpperCase() +
                    row.title.slice(1) +
                    '</b></span></a>'
                );
            },
        },
        {
            field: 'custom_link',
            title: 'Link',
            sortable: false,
            width: 200,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            minWidth: '100%',
            template: function (row) {
                return (
                    "<a target='_blank' href='/#/forms/" +
                    row.custom_link +
                    '\'><span class="navi-text font-weight-bolder text-hover-primary" style="cursor: pointer"><b>' +
                    __bakney.env.DOMAIN +
                    '/#/forms/' +
                    row.custom_link +
                    '</b></span></a>'
                );
            },
        },
        {
            field: 'enabled',
            title: 'Abilitato',
            sortable: false,
            width: 100,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            minWidth: '100%',
            template: function (row) {
                return enabledDictionary[row.enabled || row.always_active ? 1 : 0];
            },
        },
        {
            field: 'creation_date',
            title: 'Creato il',
            width: 90,
            type: 'date',
            minWidth: '100%',
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
            textAlign: 'right',
            autoHide: false,
            width: 100,
            minWidth: '100%',
            template: function (row) {
                waitForElementAndExecute(`#action-col-${row.module_id}`, () => {
                    if (document.querySelector(`#action-col-${row.module_id}`))
                        document.querySelector(`#action-col-${row.module_id}`).innerHTML = '';

                    let duplicateBtn = new DuplicateButton({
                        target: document.querySelector(`#action-col-${row.module_id}`),
                        intro: true,
                        props: {
                            disabled: !canPerformAction('association.modules.create'),
                        },
                    });

                    duplicateBtn.$on('open', async () => {
                        UiApp.blockPage({
                            overlayColor: '#000000',
                            state: 'primary',
                            message: 'Duplicazione in corso...',
                        });

                        const response = await apiFetch(
                            replaceUID(__bakney.env.API.MODULES.DUPLICATE, row.module_id),
                            { method: 'POST' }
                        );

                        UiApp.unblockPage();

                        if (!response.error) {
                            const newId = response.response?.data?.module_id;
                            if (newId) {
                                window.location.hash = `#/members/modules/composer/${newId}`;
                            } else {
                                datatable.reload();
                            }
                            toast.success('Modulo duplicato con successo!');
                        } else {
                            if (response.status === 404) {
                                toast.error('Modulo non trovato.');
                            } else {
                                toast.error('Qualcosa è andato storto.');
                            }
                        }
                    });

                    let deleteBtn = new DeleteButton({
                        target: document.querySelector(`#action-col-${row.module_id}`),
                        intro: true,
                        props: {
                            disabled: !canPerformAction('association.modules.delete'),
                        },
                    });

                    deleteBtn.$on('open', data => {
                        swal.fire({
                            text: 'Vuoi eliminare il modulo? Perderai tutte le risposte.',
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
                                    replaceUID(__bakney.env.API.MODULES.DELETE, row.module_id),
                                    {
                                        method: 'DELETE',
                                    }
                                );

                                UiApp.unblockPage();

                                if (!response.error) {
                                    datatable.reload();
                                    toast.success('Modulo eliminato!');
                                } else {
                                    toast.error('Qualcosa è andato storto.');
                                }
                            }
                        });
                    });
                });
                return `<div id="action-col-${row.module_id}" class="action-column pr-4"></div>`;
            },
        },
    ];

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
            <div class="card-header flex-wrap border-0 pt-0 pb-0 px-0">
                <div class="card-title">
                    <h3 class="card-label font-size-h2">
                        Moduli Online
                        <span class="d-block text-muted pt-2 font-size-sm"
                            >Clicca sul nome di un modulo per gestire le risposte.</span>
                    </h3>
                </div>
                <div class="card-toolbar">
                    {#if canPerformAction('association.modules.create')}
                        <!--begin::Button-->
                        <!-- svelte-ignore a11y-click-events-have-key-events -->
                        <!-- svelte-ignore a11y-no-static-element-interactions -->
                        <a
                            href="/members/modules/composer"
                            use:link
                            class="btn btn-sm btn-primary font-weight-bolder m-2 d-flex align-items-center">
                            <PlusCircle size={18} weight="duotone" />
                            <span class="ml-0 ml-md-1"><span class="d-none d-md-inline-block">Modulo</span></span>
                        </a>
                        <!--end::Button-->
                    {/if}
                </div>
            </div>
            <div class="card-body p-0">
                <BKNDatatable
                    bind:datatable
                    {columns}
                    url={__bakney.env.API.MODULES.LIST}
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
