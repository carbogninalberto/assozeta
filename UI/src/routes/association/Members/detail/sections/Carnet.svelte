<script>
    import {sessionToken} from 'store/stores.js';
    import {scale, slide} from 'svelte/transition';
    import * as easing from 'svelte/easing';
    import {onMount, onDestroy, createEventDispatcher} from 'svelte';
    import {apiFetch, replaceUID} from 'utils/ApiMiddleware';
    import DeleteButton from 'components/buttons/DeleteButton.svelte';
    import EditButton from 'components/buttons/EditButton.svelte';
    import EditModal from './modals/EditModal.svelte';
    import {waitForElementAndExecute} from 'utils/Functions';
    import Portal from 'svelte-portal';
    import {Circle} from 'svelte-loading-spinners';
    import {canPerformAction} from 'utils/Permissions';
    import {toast} from 'svelte-sonner';
    import {blockPage, unblockPage} from 'store/loadingStore.js';
    import {PlusCircle} from 'phosphor-svelte';
    import AddCarnetModal from 'routes/association/course/carnet/detail/modals/AddCarnetModal.svelte';
    import ConnectCourseCarnetModal from './modals/ConnectCourseCarnetModal.svelte';
    import BKNDatatable from 'components/tables/BKNDatatable.svelte';
    import { initTooltips, destroyTooltips } from 'shim/tooltip.js';
    import {showModal} from 'shim/modal.js';

    const dispatch = createEventDispatcher();

    sessionToken.useLocalStorage();

    export let info = {};
    export let courses = [];
    let carnets = [];
    let datatable;
    let selectedCourseId;
    let selectedCarnet;
    let selectedCarnetId;
    let updating = false;
    let datatableKey = 0;
    let ready = false;

    async function fetchData() {
        const res = await apiFetch(
            `${__bakney.env.API.CARNET_SUBSCRIPTION.LIST}?subscription_id=${info.subscription_id}`,
            {
                method: 'GET',
            }
        );

        if (!res.error) {
            carnets = res.response.data;
            if (ready) {
                datatableKey++;
            }
        } else {
            toast.error('Qualcosa è andato storto.');
        }
    }

    onMount(async () => {
        await fetchData();
        ready = true;
    });

    onDestroy(() => {
        document.querySelectorAll('.popover').forEach(popover => popover.remove());
        document.querySelectorAll('.tooltip').forEach(popover => popover.remove());
    });

    window.selectCarnetSub = function (carnet_sub_id, carnet_id) {
        // selectedCarnet = carnet_sub_id;
        // selectedCarnetId = carnet_id;

        let connectCourseCarnetModal = new ConnectCourseCarnetModal({
            target: document.body,
            props: {
                show: true,
                carnetSubscriptionId: carnet_sub_id,
                carnetId: carnet_id,
                subscriptionId: info.subscription_id,
                courses: courses,
                assignedCourses: info.courses,
            },
        });

        connectCourseCarnetModal.$on('update', () => {
            dispatch('reset', 'carnet');
        });
    };

    function assignCarnet(carnet_id, subscription_id, course_id, carnet_subscription_id) {
        return apiFetch(
            replaceUID(replaceUID(__bakney.env.API.CARNET.ASSIGN, carnet_id), subscription_id, '<sub_uid>'),
            {
                method: 'POST',
                body: JSON.stringify({
                    course_id: course_id,
                    carnet_subscription_id: carnet_subscription_id,
                }),
            }
        );
    }

    window.unsubscribeCarnetSub = function (course_subscription_id, carnet_subscription_id) {
        swal.fire({
            text: `Vuoi rimuovere l'assegnazione del carnet al corso?`,
            icon: 'warning',
            buttonsStyling: true,
            showCancelButton: true,
            cancelButtonText: 'Annulla',
            confirmButtonText: 'Elimina',
            reverseButtons: true,
            confirmButtonColor: '#d63030',
        }).then(async function (result) {
            if (result.isConfirmed) {
                let response;
                try {
                    blockPage({
                        overlayColor: '#000000',
                        state: 'primary',
                        message: 'Eliminazione in corso...',
                    });

                    response = await apiFetch(
                        replaceUID(
                            replaceUID(__bakney.env.API.CARNET_SUBSCRIPTION.DELETE, carnet_subscription_id),
                            course_subscription_id,
                            '<sub_uid>'
                        ),
                        {
                            method: 'DELETE',
                        }
                    );
                } finally {
                    unblockPage();
                }

                if (!response.error) {
                    dispatch('reset', 'carnet');
                    toast.success('Assegnazione rimossa!');
                } else {
                    toast.error('Qualcosa è andato storto.');
                }
            }
        });
    };

    const columns = [
        {
            field: 'title',
            title: 'Nome',
            autoHide: false,
            minWidth: '100%',
            width: 80,
            template: function (row) {
                return (
                    '<a class="font-weight-bolder" href="/#/course/carnet/list/detail/' +
                    row.carnet_id +
                    '/info">' +
                    row.title +
                    '</a>'
                );
            },
        },
        {
            field: 'amount',
            title: 'Importo',
            sortable: true,
            minWidth: '100%',
            width: 80,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                let amount =
                    "<span style='font-weight:700;color:#2eb132'>€ " +
                    String(row.payment?.amount).replace('.', ',') +
                    '</span>';
                return amount;
            },
        },
        {
            field: 'course',
            title: 'Corso',
            minWidth: '100%',
            width: 200,
            template: function (row) {
                let contentText = '';
                if (row.course.length == 0) {
                    contentText = `<span class="label label-light-success label-inline font-weight-bolder label-lg">Valido per tutti i corsi</span>`;
                }
                for (let i = 0; i < row.course.length; i++) {
                    contentText +=
                        '<span style="cursor:pointer" class="mr-1 label label-xl label-inline label-light-primary">' +
                        (canPerformAction('association.carnet.update')
                            ? '<span onclick=unsubscribeCarnetSub("' +
                              row.course[i].course_subscription_id +
                              '",' +
                              `"${row.carnet_subscription_id}"` +
                              ')><span style="font-size: 11px; cursor:pointer;" class="mr-2 text-primary">&times;</span></span>'
                            : '') +
                        "<a href='/#/course/overview/" +
                        row.course[i].course_id +
                        '\'><span class="navi-text font-weight-bolder" style="cursor: pointer">' +
                        row.course[i].course_title +
                        '</span></a></span>';
                }

                let unassignedCourses = courses?.filter(
                    x => !row.course.map(x => x.course_id).includes(x.course_id)
                );

                if (row.course.length == 0 && unassignedCourses.length <= 0) {
                    contentText = 'Nessun corso associato';
                }
                if (unassignedCourses.length > 0) {
                    contentText +=
                        `<button onclick="selectCarnetSub('${row.carnet_subscription_id}', '${row.carnet_id}')" class="btn btn-xs btn-light font-weight-bolder text-dark-50 m-1" style="padding: 0.2rem 0.5rem;"` +
                        ` aria-haspopup="true"` +
                        ` data-toggle="tooltip" data-placement="bottom" title="Collega ad un corso">` +
                        '<span class="text-dark-50 mr-1">+</span>collega corso</button>';
                }
                return contentText;
            },
        },
        {
            field: 'creation_date',
            title: 'Data',
            type: 'date',
            width: 80,
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
            field: 'meta',
            title: 'Lezioni',
            sortable: false,
            textAlign: 'right',
            width: 70,
            minWidth: '100%',
            autoHide: false,
            template: function (row) {
                return (
                    '<span class="font-weight-bolder text-primary" style="word-break: keep-all;">' +
                    '<span class="text-success">' +
                    row.lessons_left +
                    '</span>' +
                    '/' +
                    row.lessons_counter +
                    '<span>'
                );
            },
        },
        {
            field: '',
            title: '',
            sortable: false,
            textAlign: 'right',
            width: info.archived ? 0 : 80,
            autoHide: false,
            minWidth: '100%',
            template: function (row) {
                if (info.archived) return '';

                waitForElementAndExecute(`#action-col-${row.carnet_subscription_id}`, () => {
                    if (document.querySelector(`#action-col-${row.carnet_subscription_id}`))
                        document.querySelector(`#action-col-${row.carnet_subscription_id}`).innerHTML = '';

                    let editBtn = new EditButton({
                        target: document.querySelector(`#action-col-${row.carnet_subscription_id}`),
                        intro: true,
                        props: {
                            disabled: !canPerformAction('association.carnet.update'),
                            hidden: false,
                        },
                    });

                    let editModal = new EditModal({
                        target: document.querySelector(`#action-col-${row.carnet_subscription_id}`),
                        intro: true,
                        props: {
                            id: row.carnet_subscription_id,
                            row: row,
                        },
                    });

                    editBtn.$on('open', data => {
                        showModal(`editModal-${row.carnet_subscription_id}`);
                    });

                    editModal.$on('update', e => {
                        dispatch('reset', 'carnet');
                        datatable?.reload();
                    });

                    let deleteBtn = new DeleteButton({
                        target: document.querySelector(`#action-col-${row.carnet_subscription_id}`),
                        intro: true,
                        props: {
                            disabled: !canPerformAction('association.carnet.delete'),
                        },
                    });

                    deleteBtn.$on('open', data => {
                        swal.fire({
                            text: `Vuoi eliminare il carnet?`,
                            icon: 'warning',
                            buttonsStyling: true,
                            showCancelButton: true,
                            cancelButtonText: 'Annulla',
                            confirmButtonText: 'Elimina',
                            reverseButtons: true,
                            confirmButtonColor: '#d63030',
                        }).then(async function (result) {
                            if (result.isConfirmed) {
                                let response;
                                try {
                                    blockPage({
                                        overlayColor: '#000000',
                                        state: 'primary',
                                        message: 'Eliminazione in corso...',
                                    });

                                    response = await apiFetch(
                                        replaceUID(
                                            replaceUID(__bakney.env.API.CARNET.UNASSIGN, row.carnet_id),
                                            row.carnet_subscription_id,
                                            '<sub_uid>'
                                        ),
                                        {
                                            method: 'DELETE',
                                        }
                                    );
                                } finally {
                                    unblockPage();
                                }

                                if (!response.error) {
                                    dispatch('reset', 'carnet');
                                    toast.success('Carnet eliminato!');
                                } else {
                                    toast.error(response.response?.msg || 'Qualcosa è andato storto.');
                                }
                            }
                        });
                    });
                });
                return `<div id="action-col-${row.carnet_subscription_id}" class="action-column pr-4"></div>`;
            },
        },
    ];
</script>

<!--begin::Entry-->
<div >
    <div class="row">
        <div class="col-10 mt-2">
            <h3 class="card-label font-size-h2">
                Carnet
                <span class="d-block text-muted pt-2 font-size-sm"
                    >Lista dei carnet assegnati a questa anagrafica.</span>
            </h3>
        </div>
        <div class="col-2 mt-2 d-flex justify-content-end align-items-center">
            <button
                disabled={!canPerformAction('association.carnet.create')}
                on:click={() => {
                    let addCarnetModal = new AddCarnetModal({
                        target: document.body,
                        intro: true,
                        props: {
                            show: true,
                            value: info.subscription_id,
                            selectableCarnet: true,
                        },
                    });

                    addCarnetModal.$on('update', () => {
                        dispatch('reset', 'carnet');
                    });
                }}
                class="btn btn-sm btn-primary font-weight-bolder d-flex align-items-center">
                <PlusCircle size={16} weight="bold" class="mr-1" />
                <span class="d-none d-md-inline-block">Assegna carnet</span>
            </button>
        </div>
    </div>
    <div class="row">
        <div class="col-12 mt-4">
            {#if ready}
            {#key datatableKey}
            <BKNDatatable
                bind:datatable
                id="bkn_datatable_carnet"
                searchId="bkn_datatable_search_query_carnet"
                {columns}
                localData={carnets}
                showDividerFilter={false}
                loadFilters={() => {
                    initTooltips(document.body);
                }}
            />
            {/key}
            {/if}
        </div>
    </div>
</div>
