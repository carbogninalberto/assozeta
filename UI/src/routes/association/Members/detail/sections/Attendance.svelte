<script>
    import {sessionToken} from 'store/stores.js';
    import {scale} from 'svelte/transition';
    import * as easing from 'svelte/easing';
    import {onMount, onDestroy} from 'svelte';
    import {apiFetch, replaceUID} from 'utils/ApiMiddleware';
    import {isFreePlan} from 'utils/Permissions.js';
    import Upgrade from 'routes/Upgrade.svelte';
    import {toast} from 'svelte-sonner';
    import BaseNumberWidget from 'components/widgets/BaseNumberWidget.svelte';
    import BKNDatatable from 'components/tables/BKNDatatable.svelte';
    import { initTooltips, destroyTooltips } from 'shim/tooltip.js';

    sessionToken.useLocalStorage();

    export let info = {};
    let list = [];
    let courses = [];
    let stats = {
        total_attendance_last_30_days: 0,
        total_absences_last_30_days: 0,
        total_attendance: 0,
        total_absences: 0,
    };
    let datatable;
    let selectedCourse = 'all';
    let datatableKey = 0;
    let ready = false;

    async function fetchData(updateCourses = true) {
        const res = await apiFetch(
            `${replaceUID(__bakney.env.API.SUBSCRIPTION.ATTENDANCE, info.subscription_id)}?course=${selectedCourse}`,
            {
                method: 'GET',
            }
        );

        if (!res.error) {
            list = res.response.data.attendance_days;
            stats = res.response.data.stats;
            if (updateCourses) {
                courses = res.response.data.courses;
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

    const columns = [
        {
            field: 'title',
            title: 'Nome lezione',
            template: function (row) {
                return row.title;
            },
        },
        {
            field: 'course',
            title: 'Corso',
            autoHide: false,
            template: function (row) {
                return row.course
                    ? '<a class="font-weight-bolder" href="/#/course/overview/' +
                          row.course?.course_id +
                          '">' +
                          row.course?.title +
                          '</a>'
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
</script>

<!--begin::Entry-->
<div >
    {#if !isFreePlan()}
        <div class="row mb-2 d-flex justify-content-start">
            <BaseNumberWidget
                title="Presenze ultimi 30 giorni"
                value={stats.total_attendance_last_30_days}
                valueSuffix="presenze" />
            <!-- <BaseNumberWidget
                title="Assenze ultimi 30 giorni"
                value={stats.total_absences_last_30_days}
                valueSuffix="assenze"
                color="danger" /> -->
            <BaseNumberWidget title="Presenze totali" value={stats.total_attendance} valueSuffix="presenze" />
            <!-- <BaseNumberWidget
                title="Assenze totali"
                value={stats.total_absences}
                valueSuffix="assenze"
                color="danger" /> -->
        </div>
        <div class="row">
            <div class="col-12 mt-2">
                <h3 class="card-label font-size-h2">
                    Registro Presenze
                    <span class="d-block text-muted pt-2 font-size-sm">Lista completa delle presenze ai corsi.</span>
                </h3>
            </div>
        </div>
        <div class="row">
            <div class="col-12 mt-4">
                {#if ready}
                {#key datatableKey}
                <BKNDatatable
                    bind:datatable
                    id="bkn_datatable_attendance"
                    searchId="bkn_datatable_attendance_search_query"
                    {columns}
                    localData={list}
                    showDividerFilter={false}
                    loadFilters={() => {
                        initTooltips(document.body);
                    }}
                >
                    <div slot="search-header" class="d-flex flex-wrap align-items-center">
                        <div class="my-1 my-md-0 mr-2">
                            <div class="d-flex align-items-center">
                                <select
                                    class="form-control form-control-solid mb-0"
                                    bind:value={selectedCourse}
                                    on:change={async () => {
                                        await fetchData(false);
                                        datatableKey++;
                                    }}>
                                    <option value="all">Tutti i corsi</option>
                                    {#each courses as course}
                                        <option value={course.course_id}>{course.title}</option>
                                    {/each}
                                </select>
                            </div>
                        </div>
                    </div>
                </BKNDatatable>
                {/key}
                {/if}
            </div>
        </div>
    {:else}
        <Upgrade />
    {/if}
</div>
