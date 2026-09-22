<script>
    import FilterSelect from 'components/filters/FilterSelect.svelte';
    let selectedFilter = '';
    function resetPageFilters() {
        selectedFilter = '';
        const query = {...datatable.getDataSourceQuery(), generalSearch: datatable.getSearchValue()};
        delete query['course.course_id'];

        datatable.setDataSourceQuery(query);
    }

    import {sessionToken} from 'store/stores.js';
    import {onMount, onDestroy} from 'svelte';
    import {apiFetch, replaceUID} from 'utils/ApiMiddleware';
    import {toast} from 'svelte-sonner';
    import BackButton from 'components/buttons/BackButton.svelte';
    import BKNDatatable from 'components/tables/BKNDatatable.svelte';
    import { initTooltips, destroyTooltips } from 'shim/tooltip.js';

    sessionToken.useLocalStorage();

    export let params = {};
    let list = [];
    let courses = [];
    let datatable;

    $: list, datatable?.reload();

    const columns = [
        {
            field: 'title',
            title: 'Nome lezione',
            autoHide: false,
            template: function (row) {
                return row.title;
            },
        },
        {
            field: 'course',
            title: 'Corso',
            template: function (row) {
                return row.course
                    ? '<span class="font-weight-bolder">' + row.course?.title + '</span>'
                    : 'Nessun corso associato';
            },
        },
        {
            field: 'creation_date',
            title: 'Data',
            type: 'date',
            autoHide: false,
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
                return moment(new Date(row.date)).format('DD/MM/YYYY');
            },
        },
    ];

    async function fetchData() {
        const res = await apiFetch(replaceUID(__bakney.env.API.SUBSCRIPTION.ATTENDANCE, params.subscriptionId), {
            method: 'GET',
        });

        if (!res.error) {
            list = res.response.data.attendance_days;
            courses = res.response.data.courses;
        } else {
            toast.error('Qualcosa è andato storto.');
        }
    }

    onMount(async () => {
        await fetchData();
        initTooltips(document.body);
    });

    onDestroy(() => {
        document.querySelectorAll('.popover').forEach(popover => popover.remove());
        document.querySelectorAll('.tooltip').forEach(popover => popover.remove());
    });
</script>

<!--begin::Entry-->
<div  class="d-flex flex-column-fluid">
    <!--begin::Container-->
    <div class="container container-overlay">
        <!--begin::Card-->
        <div class="card card-custom gutter-b">
            <div class="card-header pt-4 pb-0 header-mobile-btn-back" style="padding-bottom: 0 !important;">
                <div class="card-toolbar d-flex gap-4" style="gap: .5rem;">
                    <BackButton />
                </div>
                <div class="card-toolbar">
                    <h3 class="card-title font-size-h2">Registro Presenze</h3>
                </div>
                <div class="card-toolbar" />
            </div>
            <div class="card-body pt-4">
                <!--begin::Entry-->
                <div >
                    <div class="row">
                        <div class="col-12 mt-2">
                            <h3 class="card-label font-size-h2">
                                Registro Presenze
                                <span class="d-block text-muted pt-2 font-size-sm"
                                    >Lista completa delle presenze ai corsi.</span>
                            </h3>
                        </div>
                    </div>
                    <div class="row">
                        <div class="col-12 mt-4">
                            <BKNDatatable resetFilters={resetPageFilters}
                                bind:datatable
                                {columns}
                                url={replaceUID(__bakney.env.API.SUBSCRIPTION.ATTENDANCE, params.subscriptionId)}
                                mapFunction={raw => {
                                    if (typeof raw.data !== 'undefined') {
                                        return raw.data.attendance_days || [];
                                    }
                                    return [];
                                }}
                                serverPaging={false}
                                serverFiltering={false}
                                serverSorting={false}
                                >
                                <div slot="search-header">
                                    <div class="d-flex align-items-center">
                                        <!-- svelte-ignore a11y-label-has-associated-control -->
                                        <label class="mr-3 mb-0 d-none d-md-block">Corso</label>
                                        <FilterSelect
                                            label="Corso"
                                            bind:value={selectedFilter}
                                            on:change={event => datatable.search(event.detail.value, 'course.course_id')}
                                            options={[{value: '', label: 'Tutti'}, ...courses.map(course => ({value: course.course_id, label: course.title}))]} />
                                    </div>
                                </div>
                            </BKNDatatable>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <!--end::Container-->
</div>
<!--end::Entry-->

<svelte:head>
    <style>
        .nav-link {
            cursor: pointer;
        }
        .nav-link.active {
            border-bottom: 4px solid #351dc2 !important;
        }
        .nav-link:hover {
            border-bottom: 4px solid #351dc2 !important;
        }
        .card-toolbar::-webkit-scrollbar {
            display: none;
        }
    </style>
</svelte:head>
