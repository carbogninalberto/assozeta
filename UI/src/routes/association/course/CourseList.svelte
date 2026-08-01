<script>
	import { X } from 'lucide-svelte';
    import {sessionToken, courseListFilter, courseListFilterDatatable} from 'store/stores.js';
    import {scale, slide} from 'svelte/transition';
    import * as easing from 'svelte/easing';
    import {onMount, onDestroy} from 'svelte';
    import NavigationTab from './shared/NavigationTab.svelte';
    import {PlusCircle, Printer, Users} from 'phosphor-svelte';
    import BKNDatatable from 'components/tables/BKNDatatable.svelte';
    import DeleteButton from 'components/buttons/DeleteButton.svelte';
    import UsersButton from 'components/buttons/UsersButton.svelte';
    import EyeButton from 'components/buttons/EyeButton.svelte';
    import EyeClosedButton from 'components/buttons/EyeClosedButton.svelte';
    import {debounce, waitForElementAndExecute} from 'utils/Functions.js';
    import {canPerformAction} from 'utils/Permissions.js';
    import AssignTag from 'components/tables/multiactions/AssignTag.svelte';
    import {toast} from 'svelte-sonner';
    import PinButton from 'components/buttons/PinButton.svelte';
    import {apiFetch, replaceUID} from 'utils/ApiMiddleware.js';
    import AddCourseDrawer from './add/AddCourseDrawer.svelte';
    import {push} from 'svelte-spa-router';
    import QueryFilter from 'components/filters/QueryFilter.svelte';
    import CurrentViewPrintingModal from 'components/modals/CurrentViewPrintingModal.svelte';
    import MoveToArchiveButton from 'components/buttons/MoveToArchiveButton.svelte';
    import { initTooltips, destroyTooltips } from 'shim/tooltip.js';
    import { initPopovers, destroyPopovers } from 'shim/popover.js';
    import {initSelectpicker} from 'shim/select.js';
	import { UiApp, UiUtil } from 'shim/ui.js';

    sessionToken.useLocalStorage();

    // ApiMiddleware();

    let courseId = null;
    let courses = {};
    let activeCourse = {};
    let athletes = [];
    let visibleMultiaction = false;
    let selectedCounter = 0;
    let datatable;
    let tags;
    let filters = [
        {
            name: 'Filtra Tag ',
            value: '',
            active: false,
            type: 'tags',
            key: 'tags',
            data: {
                options: [],
                and: true,
            },
        },
    ];

    const statusTextDictionary = {
        1: '<span class="label label-light-warning label-inline font-weight-bolder label-lg" data-toggle="tooltip" data-html="true" title="Il corso non è visibile dal profilo dell\'associazione">non visibile</span>',
        2: '<span class="label label-light-success label-inline font-weight-bolder label-lg" data-toggle="tooltip" data-html="true" title="Il corso è visibile dal profilo dell\'associazione">visibile</span>',
    };

    const courseTypeDictionary = {
        1: '<span class="label label-light-info label-inline font-weight-bolder label-lg">standard</span>',
        2: '<span class="label label-light-info label-inline font-weight-bolder label-lg">quote multiple</span>',
        3: '<span class="label label-light-info label-inline font-weight-bolder label-lg">abbonamento</span>',
    };

    window.updateCourse = id => {
        courseId = id;
        for (let i = 0; i < Object.keys(courses).length; i++) {
            if (courses[i].course_id == courseId) {
                activeCourse = courses[i];

                let tmpAthletes = [];
                for (let j = 0; j < Object.keys(activeCourse.athletes).length; j++) {
                    tmpAthletes.push(activeCourse.athletes[j]);
                }
                // this fires the Svelte update to athletes variable: IMPORTANT!
                athletes = tmpAthletes;
            }
        }
    };

    window.enableCourse = id => {
        swal.fire({
            text: 'Vuoi attivare il corso?',
            icon: 'question',
            buttonsStyling: true,
            showCancelButton: true,
            cancelButtonText: 'Annulla',
            confirmButtonText: 'Attiva',
            reverseButtons: true,
        }).then(function (result) {
            if (result.isConfirmed) {
                UiApp.blockPage({
                    overlayColor: '#000000',
                    state: 'primary',
                    message: 'Attivazione in corso...',
                });

                const url = replaceUID(__bakney.env.API.COURSE.ENABLE, id);

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
toast.success('Attivazione avvenuta con Successo.');
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
                                UiUtil.scrollTop();
                            });
                        }
                    });
            }
        });
    };

    window.disableCourse = id => {
        swal.fire({
            text: 'Vuoi spostare in archivio il corso?',
            icon: 'question',
            buttonsStyling: true,
            showCancelButton: true,
            cancelButtonText: 'Annulla',
            confirmButtonText: 'Sposta in archivio',
            reverseButtons: true,
        }).then(function (result) {
            if (result.isConfirmed) {
                UiApp.blockPage({
                    overlayColor: '#000000',
                    state: 'primary',
                    message: 'Spostamento in bozza in corso...',
                });

                const url = replaceUID(__bakney.env.API.COURSE.DISABLE, id);

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
toast.success('Operazione eseguita con successo.');
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
                                UiUtil.scrollTop();
                            });
                        }
                    });
            }
        });
    };

    window.deleteCourse = id => {
        swal.fire({
            text: 'Vuoi eliminare definitivamente il corso?',
            icon: 'question',
            buttonsStyling: true,
            showCancelButton: true,
            cancelButtonText: 'Annulla',
            confirmButtonText: 'Elimina',
            reverseButtons: true,
            confirmButtonColor: '#d63030',
        }).then(function (result) {
            if (result.isConfirmed) {
                UiApp.blockPage({
                    overlayColor: '#000000',
                    state: 'primary',
                    message: 'Eliminazione in corso...',
                });

                const url = replaceUID(__bakney.env.API.COURSE.DELETE, id);

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
toast.success('Eliminazione avvenuta con Successo.');
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
                                UiUtil.scrollTop();
                            });
                        }
                    });
            }
        });
    };

    async function fetchTags() {
        apiFetch(__bakney.env.API.COURSE.TAGS.LIST, {
            method: 'GET',
        }).then(res => {
            if (res.status == 200) {
                tags = [...res.response?.tags];
                // update filter options
                filters.find(filter => filter.key === 'tags').data.options = tags;
                filters = [...filters];
            }
        });
    }

    const columns = [
        {
            field: 'course_id',
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
            field: 'title',
            title: 'Titolo',
            autoHide: false,
            sortable: true,
            fireClick: true,
            width: 100,
            minWidth: '100%',
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
                    "<a href='/#/course/overview/" +
                    row.course_id +
                    '\'><span class="navi-text font-weight-bolder text-hover-primary" style="cursor: pointer"><b>' +
                    row.title.charAt(0).toUpperCase() +
                    row.title.slice(1) +
                    '</b></span></a>'
                );
            },
        },
        {
            field: 'description',
            title: 'Descrizione',
            autoHide: false,
            minWidth: '100%',
            fireClick: true,
            sortable: false,
            template: function (row) {
                const desc = row.description || '';
                return `<div class="description-course text-truncate" data-toggle="tooltip" data-html="true" title="${desc}">${desc
                    .replace(/&lt;/g, '<')
                    .replace(/&gt;/g, '>')
                    .replace('<br>', '')
                    .substring(0, 100)}</div>`;
            },
        },
        {
            field: 'course_type',
            title: 'Tipo',
            width: 75,
            sortable: false,
            minWidth: '100%',
            fireClick: true,
            overflow: 'visible',
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                return courseTypeDictionary[row.course_type];
            },
        },
        {
            field: 'status_flag',
            title: 'Stato',
            width: 80,
            minWidth: '100%',
            fireClick: true,
            sortable: false,
            overflow: 'visible',
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                return statusTextDictionary[row.status_flag];
            },
        },

        {
            field: 'start_date',
            title: 'Attivo',
            width: 100,
            minWidth: '100%',
            fireClick: true,
            sortable: false,
            overflow: 'visible',
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                let text = '';
                if (row.start_date && row.end_date) {
                    text = `dal ${moment(row.start_date).format('DD/MM/YYYY')} al ${moment(row.end_date).format(
                        'DD/MM/YYYY'
                    )}`;
                } else if (row.start_date) {
                    text = `dal ${moment(row.start_date).format('DD/MM/YYYY')}`;
                } else if (row.end_date) {
                    text = `fino al ${moment(row.end_date).format('DD/MM/YYYY')}`;
                } else {
                    text = 'Sempre attivo';
                }
                let color = 'primary';
                if (row.start_date && row.end_date) {
                    color =
                        moment().isAfter(row.start_date) && moment().isBefore(row.end_date)
                            ? 'primary'
                            : 'warning';
                } else if (row.start_date) {
                    color = moment().isAfter(row.start_date) ? 'primary' : 'warning';
                } else if (row.end_date) {
                    color = moment().isBefore(row.end_date) ? 'primary' : 'warning';
                }
                return `<div class="d-flex font-size-xs rounded-xl font-weight-boldest text-${color}" style="width: 100%;">${text}</div>`;
            },
        },
        {
            field: 'creation_date',
            title: 'Creato',
            width: 65,
            type: 'date',
            fireClick: true,
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
            field: 'athletes',
            title: 'ATLETI',
            width: 40,
            sortable: false,
            fireClick: true,
            minWidth: '100%',
            responsive: {
                visible: 'xxl',
                hidden: 'xl',
            },
            template: function (row) {
                return Object.keys(row?.athletes || {}).length || 0;
            },
        },
        {
            field: 'tags',
            title: 'Tag',
            checked: true,
            fireClick: true,
            sortable: false,
            width: 130,
            // responsive: {
            //     visible: 'xl',
            //     hidden: 'lg',
            // },
            template: function (row) {
                let tags = '';
                row.tags?.forEach(tag => {
                    tags += `<span class="label label-inline label-outline-secondary mb-1 mt-0 ml-1 mr-0">${tag?.tag_name}</span>`;
                });

                if (tags == '') {
                    tags = '-';
                }
                return tags;
            },
        },
        {
            field: 'Azioni',
            title: '',
            sortable: false,
            autoHide: false,
            overflow: 'visible',
            textAlign: 'right',
            minWidth: '100%',
            width: 120,
            template: function (row) {
                let cannotDeleteCourse = row.status_flag && row.status_flag == 2 ? true : false;

                waitForElementAndExecute(`#action-col-${row.course_id}`, () => {
                    if (document.querySelector(`#action-col-${row.course_id}`))
                        document.querySelector(`#action-col-${row.course_id}`).innerHTML = '';

                    if (!cannotDeleteCourse) {
                        let publishButton = new EyeButton({
                            target: document.querySelector(`#action-col-${row.course_id}`),
                            intro: true,
                            props: {
                                disabled: !canPerformAction('association.courses.update'),
                                popover_text: "Rendi visibile il corso dal profilo dell'associazione",
                            },
                        });

                        publishButton.$on('open', data => {
                            enableCourse(row.course_id);
                        });
                    } else {
                        let moveToDraftButton = new MoveToArchiveButton({
                            target: document.querySelector(`#action-col-${row.course_id}`),
                            intro: true,
                            props: {
                                disabled: !canPerformAction('association.courses.update'),
                                popover_text: "Nascondi il corso dal profilo dell'associazione archiviandolo",
                            },
                        });

                        moveToDraftButton.$on('open', data => {
                            disableCourse(row.course_id);
                        });
                    }

                    let usersButton = new UsersButton({
                        target: document.querySelector(`#action-col-${row.course_id}`),
                        intro: true,
                        props: {
                            disabled: false,
                            popover_text: 'Atleti iscritti',
                            dataToggle: 'modal',
                            dataTarget: '#modalAthlete',
                        },
                    });

                    usersButton.$on('open', data => {
                        updateCourse(row.course_id);
                        var modalEl = document.getElementById('modalAthlete');
                        if (modalEl) modalEl.style.display = 'block';
                    });

                    let pinButton = new PinButton({
                        target: document.querySelector(`#action-col-${row.course_id}`),
                        intro: true,
                        props: {
                            disabled: !canPerformAction('association.courses.update'),
                            popover_text: row.pinned
                                ? 'Rimuovi dai corsi in evidenza'
                                : "Metti in evidenza nel profilo dell'associazione",
                            pinned: row.pinned,
                        },
                    });

                    pinButton.$on('open', async data => {
                        let res = await apiFetch(__bakney.env.API.COURSE.PIN.replace('<uid>', row.course_id), {
                            method: 'POST',
                        });
                        if (!res.error) {
toast.success(res.response.data?.msg || 'Corso aggiornato');
                        } else {
                            toast.error(res.response.data?.msg || "Errore nell'aggiornamento del corso");
                        }
                        datatable.reload();
                    });

                    let deleteBtn = new DeleteButton({
                        target: document.querySelector(`#action-col-${row.course_id}`),
                        intro: true,
                        props: {
                            disabled: cannotDeleteCourse || !canPerformAction('association.courses.delete'),
                            popover_text: 'Elimina Corso',
                        },
                    });

                    deleteBtn.$on('open', data => {
                        deleteCourse(row.course_id);
                    });
                });
                return `<div id="action-col-${row.course_id}" class="action-column pr-4"></div>`;
            },
        },
    ];

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
                }
            }
        });
        return filterObj;
    }

    function handleFilterApplied(event) {
        // remove tags from filterObj
        let filterObj = generateFilterObject();
        let t_list = filterObj?.tags?.join(',') || [];
        let tags_and = filterObj?.tags_and == true ? 1 : 0;
        $courseListFilter = {
            ...$courseListFilter,
            ...filterObj,
            tags: t_list,
            tags_and: tags_and,
        };
        // apply search
        datatable?.setDataSourceParams($courseListFilterDatatable);
    }

    function loadFilters() {
        initTooltips(document.body);
        initPopovers(document.body);
        const searchQueryEl = document.getElementById('bkn_datatable_search_query');
        searchQueryEl?.addEventListener('keyup', debounce(function (e) {
            $courseListFilter.generalSearch = e.currentTarget.value;
            datatable?.setDataSourceParams($courseListFilterDatatable);
        }, 300));

        const statusEl = document.getElementById('bkn_datatable_search_status');
        statusEl?.addEventListener('change', function (e) {
            datatable.search(e.currentTarget.value.toLowerCase(), 'status_flag');
        });
        initSelectpicker(statusEl);
    }

    onMount(() => {
        fetchTags();
        localStorage.removeItem('bkn_datatable-1-meta');
        initTooltips(document.body);
        initPopovers(document.body);
        $courseListFilter.status_flag = 2;
    });

    onDestroy(() => {
        document.querySelectorAll('.popover').forEach(popover => popover.remove());
    });
</script>

<!-- Modal-->
<div
    class="modal fade"
    id="modalAthlete"
    tabindex="-1"
    role="dialog"
    aria-labelledby="staticBackdrop"
    aria-hidden="true">
    <div class="modal-dialog modal-dialog-centered modal-lg" role="document">
        <div class="modal-content">
            <div class="modal-header">
                <h5 class="modal-title" id="exampleModalLabel">Atleti Iscritti ({athletes.length})</h5>
                <button type="button" class="close" data-dismiss="modal" aria-label="Close">
                    <X size={16} aria-hidden="true" />
                </button>
            </div>
            <div class="modal-body my-0">
                <!--begin::Body-->
                <div data-scroll="true" data-height="500">
                    <div class="card-body pt-2 mb-0 p-1">
                        <!--begin::Item-->
                        {#each athletes as athlete, i}
                            <div class="d-flex flex-wrap align-items-center mb-2">
                                <div class="d-flex flex-column ml-0 mr-8">
                                    <span class="text-dark-75 font-weight-bold font-size-lg" style="font-size:1.5rem;"
                                        >{i + 1}</span>
                                </div>
                                <!--begin::Title-->
                                <div class="d-flex flex-column flex-grow-1 ml-0 mr-0">
                                    <span
                                        class="text-dark-75 font-weight-bold font-size-lg mb-1"
                                        style="text-transform: capitalize">
                                        {athlete.subscription.associate.first_name}
                                        {athlete.subscription.associate.last_name}
                                    </span>
                                    <span class="text-muted font-size-md mb-1" style="text-transform: capitalize">
                                        {athlete.subscription.associate.is_minor ? 'Minore' : 'Maggiorenne'},
                                        {athlete.subscription.associate.sex == 'M'
                                            ? 'Maschio'
                                            : athlete.subscription.associate.sex == 'F'
                                            ? 'Femmina'
                                            : 'Altro'},
                                        {athlete.subscription.associate.born_date}
                                    </span>
                                </div>
                            </div>
                        {/each}

                        {#if athletes.length == 0}
                            <span class="text-dark-75 font-weight-bold text-hover-primary font-size-lg"
                                >Nessun atleta iscritto a questo corso.</span>
                        {/if}
                        <!--end::Item-->
                    </div>
                </div>
                <!--end::Body-->
            </div>
            <div class="modal-footer" style="padding:1rem;">
                <button type="button" class="btn btn-primary font-weight-bold" data-dismiss="modal">Chiudi</button>
            </div>
        </div>
    </div>
</div>

<!--begin::Entry-->
<div
    
    class="d-flex flex-column-fluid font-weight-bold text-dark-50">
    <!--begin::Container-->
    <div class="container">
        <!--begin::Card-->
        <div class="card card-custom gutter-b">
            <div class="d-block d-md-flex justify-content-between">
                <NavigationTab />
            </div>
            <div class="card-header flex-wrap border-0 p-0">
                <div class="card-title">
                    <h3 class="card-label font-size-h2">
                        Corsi e abbonamenti
                        <span class="d-block text-muted pt-2 font-size-sm">Lista dei corsi e abbonamenti.</span>
                    </h3>
                </div>
                <div class="card-toolbar">
                    <!--begin::Button-->
                    {#if canPerformAction('association.courses.create')}
                        <button
                            on:click={() => {
                                let basicDrawer = new AddCourseDrawer({
                                    target: document.querySelector(`#drawer-elements`),
                                    intro: true, // This enables the mount animation
                                    props: {
                                        title: 'Aggiungi corso o abbonamento',
                                    },
                                });
                                basicDrawer.$on('close', () => {
                                    // reload datatable
                                    datatable.reload();
                                });
                            }}
                            class="btn btn-primary btn-sm m-2 d-flex align-items-center font-weight-boldest">
                            <PlusCircle size={16} weight="duotone" class="mr-1" />Corso o Abbonamento
                        </button>
                    {/if}
                    <!--end::Button-->
                </div>
            </div>
            <div class="card-body p-0">
                <!--begin: Datatable-->
                <BKNDatatable
                    bind:datatable
                    bind:visibleMultiaction
                    bind:selectedCounter
                    {columns}
                    url={__bakney.env.API.COURSE.LIST}
                    params={{'query[status_flag]': 2}}
                    mapFunction={function (raw) {
                        var dataSet = raw;
                        if (typeof raw.data !== 'undefined') {
                            dataSet = raw.data;
                        }
                        courses = dataSet;
                        return dataSet;
                    }}
                    clicked={function (td, obj) {
                        push(`/course/overview/${obj.course_id}`);
                    }}
                    {loadFilters}
                    showDividerFilter={false}>
                    <div slot="search-header" class="my-1 my-md-0 mr-2">
                        <QueryFilter
                            {filters}
                            showMore={false}
                            on:filter-applied={handleFilterApplied} />
                    </div>
                    <div slot="search-actions">
                        <div class="my-2 mr-1">
                            <button
                                class="btn btn-sm btn-light-primary font-weight-bolder m-0"
                                data-toggle="tooltip"
                                data-placement="bottom"
                                title="Esporta ricerca corrente in Excel o PDF"
                                on:click={() => {
let query = $courseListFilterDatatable;
                                    let queryString = Object.keys(query)
                                        .map(key => {
                                            return `${key}` + '=' + query[key];
                                        })
                                        .join('&');
                                    let sort = datatable.getDataSourceParam('sort');
                                    if (sort) {
                                        queryString += '&sort[field]=' + sort.field + '&sort[sort]=' + sort.sort;
                                    }
                                    queryString += '&type=athletes';
                                    let endpointWithQueryString = __bakney.env.API.COURSE.LIST + '?' + queryString;
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
                    <div slot="multiactions">
                        {#if visibleMultiaction}
                            <div in:slide={{duration: 100}} class="mt-10 mb-5" id="bkn_datatable_group_action_form">
                                <div class="d-flex align-items-center">
                                    <div class="font-weight-boldest mr-3 ml-2" style="font-size:1.1rem;">
                                        {selectedCounter} selezionati
                                    </div>
                                    <AssignTag
                                        datatableId={'bkn_datatable'}
                                        bind:datatable
                                        row_id_name={'course_id'}
                                        replace_row_id={'<course_uid>'}
                                        endpoint_to_assign_tag={__bakney.env.API.COURSE.TAGS.ASSIGN}
                                        endpoint_to_unassign_tag={__bakney.env.API.COURSE.TAGS.UNASSIGN}
                                        endpoint_to_delete_tag={__bakney.env.API.COURSE.TAGS.DELETE}
                                        endpoint_to_create_tag={__bakney.env.API.COURSE.TAGS.ADD}
                                        endpoint_to_fetch_tags={__bakney.env.API.COURSE.TAGS.LIST}
                                        permission_to_perform={'association.courses.update'}
                                        bind:visibleMultiaction />
                                </div>
                            </div>
                        {/if}
                    </div>
                </BKNDatatable>
                <!--end: Datatable-->
            </div>
        </div>
        <!--end::Card-->
    </div>
    <!--end::Container-->
</div>
<!--end::Entry-->
