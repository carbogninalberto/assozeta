<script>
    import {sessionToken} from 'store/stores.js';
    import {scale} from 'svelte/transition';
    import * as easing from 'svelte/easing';
    import {onMount, onDestroy} from 'svelte';
    import {debounce} from 'utils/Functions.js';
    import {apiFetch} from 'utils/ApiMiddleware.js';
    import {Plus, Pencil, Trash} from 'phosphor-svelte';
    import QueryFilter from 'components/filters/QueryFilter.svelte';
    import AuditDetailDrawer from './AuditDetailDrawer.svelte';
    import BKNDatatable from 'components/tables/BKNDatatable.svelte';
    import { initTooltips, destroyTooltips } from 'shim/tooltip.js';
    import { initPopovers, destroyPopovers } from 'shim/popover.js';

    sessionToken.useLocalStorage();

    let datatable;
    let logs = {};
    let stats = {
        total: 0,
        by_action: {create: 0, update: 0, delete: 0},
    };
    let loading = true;
    let statsLoading = true;

    // Filter state
    let generalSearch = '';

    // Filters configuration
    let filters = [
        {
            name: 'Azione',
            value: '',
            active: false,
            type: 'checkbox',
            key: 'action',
            data: {
                options: [
                    {label: 'Creazione', value: '0', checked: false},
                    {label: 'Modifica', value: '1', checked: false},
                    {label: 'Eliminazione', value: '2', checked: false},
                ],
            },
        },
        {
            name: 'Tipo',
            value: '',
            active: false,
            type: 'checkbox',
            key: 'model',
            data: {
                options: [],
            },
        },
        {
            name: 'Periodo',
            value: '',
            active: false,
            type: 'date-range',
            key: 'date',
            data: {
                from_date: null,
                to_date: null,
            },
        },
    ];

    // Detail drawer
    let selectedLogId = null;
    let showDetailDrawer = false;

    // Object filter state
    let objectFilter = null; // stores object_pk when filtering
    let objectFilterLabel = ''; // stores object_description for display

    const actionBadges = {
        0: '<span class="label label-light-success label-inline font-weight-bolder">Creazione</span>',
        1: '<span class="label label-light-primary label-inline font-weight-bolder">Modifica</span>',
        2: '<span class="label label-light-danger label-inline font-weight-bolder">Eliminazione</span>',
    };

    async function fetchStats() {
        statsLoading = true;
        const res = await apiFetch(__bakney.env.API.AUDIT.STATS, {
            method: 'GET',
        });
        if (!res.error) {
            stats = res.response.data || stats;
        }
        statsLoading = false;
    }

    async function fetchModels() {
        const res = await apiFetch(__bakney.env.API.AUDIT.MODELS, {
            method: 'GET',
        });
        if (!res.error) {
            const models = res.response.data || [];
            // Update the model filter options
            const modelFilter = filters.find(f => f.key === 'model');
            if (modelFilter) {
                modelFilter.data.options = models.map(m => ({
                    label: m.label,
                    value: m.name,
                    checked: false,
                }));
                filters = [...filters];
            }
        }
    }

    function generateQueryParams() {
        let params = {};
        if (generalSearch) {
            params['query[generalSearch]'] = generalSearch;
        }

        // Add object filter if active
        if (objectFilter) {
            params['query[object_id]'] = objectFilter;
        }

        filters.forEach(filter => {
            if (filter.active) {
                if (filter.type === 'checkbox') {
                    const selectedValues = filter.data.options
                        .filter(opt => opt.checked)
                        .map(opt => opt.value);
                    if (selectedValues.length > 0) {
                        params[`query[${filter.key}]`] = selectedValues.join(',');
                    }
                } else if (filter.type === 'date-range') {
                    if (filter.data.from_date) {
                        params['query[date_from]'] = moment(filter.data.from_date, 'DD/MM/YYYY').format('YYYY-MM-DD');
                    }
                    if (filter.data.to_date) {
                        params['query[date_to]'] = moment(filter.data.to_date, 'DD/MM/YYYY').format('YYYY-MM-DD');
                    }
                }
            }
        });

        return params;
    }

    function handleFilterApplied(event) {
        applyFilters();
    }

    function applyFilters() {
        if (datatable) {
            datatable.setDataSourceParams(generateQueryParams());
        }
    }

    function openDetailDrawer(logId) {
        selectedLogId = logId;
        showDetailDrawer = true;
    }

    function filterByObject(pk, label) {
        objectFilter = pk;
        objectFilterLabel = label || pk;
        showDetailDrawer = false;
        selectedLogId = null;
        applyFilters();
    }

    function clearObjectFilter() {
        objectFilter = null;
        objectFilterLabel = '';
        applyFilters();
    }

    const columns = [
        {
            field: 'timestamp',
            title: 'Data/Ora',
            width: 140,
            sortable: true,
            autoHide: false,
            template: function (row) {
                if (!row.timestamp || row.timestamp === 'null') return '-';
                return moment(row.timestamp).format('DD/MM/YYYY HH:mm');
            },
        },
        {
            field: 'action',
            title: 'Azione',
            width: 100,
            sortable: true,
            autoHide: false,
            template: function (row) {
                return actionBadges[row.action] || row.action_label || '-';
            },
        },
        {
            field: 'model_verbose_name',
            title: 'Tipo',
            width: 120,
            sortable: true,
            autoHide: false,
            template: function (row) {
                const val = row.model_verbose_name || row.model_name;
                if (!val || val === 'null') return '<span class="font-weight-bold">-</span>';
                return `<span class="font-weight-bold">${val}</span>`;
            },
        },
        {
            field: 'actor_name',
            title: 'Utente',
            width: 150,
            sortable: false,
            autoHide: false,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                const name = (!row.actor_name || row.actor_name === 'null') ? '-' : row.actor_name;
                const email = row.actor_email || '';
                if (email && email !== 'null') {
                    return `<span class="font-weight-bold" title="${email}">${name}</span>`;
                }
                return `<span class="font-weight-bold">${name}</span>`;
            },
        },
        {
            field: 'object_description',
            title: 'Oggetto',
            width: 180,
            sortable: false,
            autoHide: false,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                const desc = row.object_description || row.object_repr || '-';
                if (!desc || desc === 'null' || desc === '-') return '<span class="text-muted">-</span>';
                const escapedDesc = desc.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
                return `<span class="text-truncate d-inline-block" style="max-width: 160px; cursor: pointer;" data-toggle="popover" data-trigger="hover" data-placement="top" data-content="${escapedDesc}">${desc}</span>`;
            },
        },
        {
            field: 'changes_summary',
            title: 'Modifiche',
            width: 180,
            sortable: false,
            autoHide: false,
            responsive: {
                visible: 'xl',
                hidden: 'lg',
            },
            template: function (row) {
                const summary = (!row.changes_summary || row.changes_summary === 'null') ? '-' : row.changes_summary;
                if (summary === '-') return '<span class="text-muted">-</span>';
                const escapedSummary = summary.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
                return `<span class="text-muted text-truncate d-inline-block" style="max-width: 180px; cursor: pointer;" data-toggle="popover" data-trigger="hover" data-placement="top" data-content="${escapedSummary}">${summary}</span>`;
            },
        },
        {
            field: 'Azioni',
            title: '',
            sortable: false,
            autoHide: false,
            overflow: 'visible',
            textAlign: 'right',
            width: 100,
            template: function (row) {
                const desc = row.object_description || row.object_repr || '';
                const escapedDesc = desc.replace(/'/g, "\\'").replace(/"/g, '&quot;');
                const pk = row.object_pk || '';
                let filterBtn = '';
                if (pk) {
                    filterBtn = `<button class="btn btn-xs btn-clean btn-icon text-info m-0 mr-2" onclick="window.filterAuditByObject('${pk}', '${escapedDesc}')" data-toggle="tooltip" data-placement="bottom" title="Filtra per questo oggetto">
                        <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" fill="currentColor" viewBox="0 0 256 256"><path d="M216,48H40a8,8,0,0,0-5.92,13.38L96,134.22V200a8,8,0,0,0,3.56,6.66l32,21.33A8,8,0,0,0,144,221.33V134.22l61.92-72.84A8,8,0,0,0,216,48Z" opacity="0.2"></path><path d="M230.6,49.53A16,16,0,0,0,216,40H40A16,16,0,0,0,28.19,66.76l.08.09L96,139.17V200a16,16,0,0,0,24.87,13.32l32-21.34A16,16,0,0,0,160,178.66V139.17l67.74-72.32.08-.09A16,16,0,0,0,230.6,49.53ZM144,125.82a8,8,0,0,0-2,5.34v47.51l-32,21.33V131.16a8,8,0,0,0-2-5.34L40,56H216Z"></path></svg>
                    </button>`;
                }
                return `${filterBtn}<button class="btn btn-xs btn-clean btn-icon text-primary m-0 mr-2" onclick="window.openAuditDetail(${row.id})" data-toggle="tooltip" data-placement="bottom" title="Dettagli">
                    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" fill="currentColor" viewBox="0 0 256 256"><path d="M128,56C48,56,16,128,16,128s32,72,112,72,112-72,112-72S208,56,128,56Zm0,112a40,40,0,1,1,40-40A40,40,0,0,1,128,168Z" opacity="0.2"></path><path d="M247.31,124.76c-.35-.79-8.82-19.58-27.65-38.41C194.57,61.26,162.88,48,128,48S61.43,61.26,36.34,86.35C17.51,105.18,9,124,8.69,124.76a8,8,0,0,0,0,6.5c.35.79,8.82,19.57,27.65,38.4C61.43,194.74,93.12,208,128,208s66.57-13.26,91.66-38.34c18.83-18.83,27.3-37.61,27.65-38.4A8,8,0,0,0,247.31,124.76ZM128,192c-30.78,0-57.67-11.19-79.93-33.25A133.47,133.47,0,0,1,25,128,133.33,133.33,0,0,1,48.07,97.25C70.33,75.19,97.22,64,128,64s57.67,11.19,79.93,33.25A133.46,133.46,0,0,1,231.05,128C223.84,141.46,192.43,192,128,192Zm0-112a48,48,0,1,0,48,48A48.05,48.05,0,0,0,128,80Zm0,80a32,32,0,1,1,32-32A32,32,0,0,1,128,160Z"></path></svg>
                </button>`;
            },
        },
    ];

    const mapFunction = function (raw) {
        var dataSet = raw;
        if (typeof raw.data !== 'undefined') {
            dataSet = raw.data;
        }
        logs = dataSet;
        loading = false;
        return dataSet;
    };

    // Expose functions to window for template buttons
    window.openAuditDetail = function (logId) {
        openDetailDrawer(logId);
    };

    window.filterAuditByObject = function (pk, label) {
        filterByObject(pk, label);
    };

    onMount(() => {
        localStorage.removeItem('bkn_datatable-1-meta');
        fetchStats();
        fetchModels();
        initTooltips(document.body);
        initPopovers(document.body);
    });

    onDestroy(() => {
        document.querySelectorAll('.popover').forEach(popover => popover.remove());
        document.querySelectorAll('.tooltip').forEach(tooltip => tooltip.remove());
    });
</script>

<!--begin::Entry-->
<div
    
    class="d-flex flex-column-fluid font-weight-bold text-dark-50">
    <!--begin::Container-->
    <div class="container">
        <!--begin::Stats Cards-->
        <div class="row mb-6 d-none">
            <div class="col-12 col-md-3 mb-4 mb-md-0">
                <div class="card card-custom bg-light-primary card-stretch gutter-b">
                    <div class="card-body d-flex flex-column p-6">
                        <span class="font-weight-bold text-primary font-size-sm">Totale Log</span>
                        <span class="font-weight-bolder text-primary font-size-h2 mt-2">
                            {#if statsLoading}
                                ...
                            {:else}
                                {stats.total?.toLocaleString('it-IT') || 0}
                            {/if}
                        </span>
                    </div>
                </div>
            </div>
            <div class="col-12 col-md-3 mb-4 mb-md-0">
                <div class="card card-custom bg-light-success card-stretch gutter-b">
                    <div class="card-body d-flex flex-column p-6">
                        <span class="font-weight-bold text-success font-size-sm">Creazioni</span>
                        <span class="font-weight-bolder text-success font-size-h2 mt-2">
                            {#if statsLoading}
                                ...
                            {:else}
                                {stats.by_action?.create?.toLocaleString('it-IT') || 0}
                            {/if}
                        </span>
                    </div>
                </div>
            </div>
            <div class="col-12 col-md-3 mb-4 mb-md-0">
                <div class="card card-custom bg-light-info card-stretch gutter-b">
                    <div class="card-body d-flex flex-column p-6">
                        <span class="font-weight-bold text-info font-size-sm">Modifiche</span>
                        <span class="font-weight-bolder text-info font-size-h2 mt-2">
                            {#if statsLoading}
                                ...
                            {:else}
                                {stats.by_action?.update?.toLocaleString('it-IT') || 0}
                            {/if}
                        </span>
                    </div>
                </div>
            </div>
            <div class="col-12 col-md-3">
                <div class="card card-custom bg-light-danger card-stretch gutter-b">
                    <div class="card-body d-flex flex-column p-6">
                        <span class="font-weight-bold text-danger font-size-sm">Eliminazioni</span>
                        <span class="font-weight-bolder text-danger font-size-h2 mt-2">
                            {#if statsLoading}
                                ...
                            {:else}
                                {stats.by_action?.delete?.toLocaleString('it-IT') || 0}
                            {/if}
                        </span>
                    </div>
                </div>
            </div>
        </div>
        <!--end::Stats Cards-->

        <!--begin::Card-->
        <div class="card card-custom gutter-b">
            <div class="card-header flex-wrap border-0 p-0">
                <div class="card-title">
                    <h3 class="card-label font-size-h2">
                        Registro Audit
                        <span class="d-block text-muted pt-2 font-size-sm">Cronologia delle operazioni eseguite negli ultimi 365 giorni.</span>
                    </h3>
                </div>
            </div>
            <div class="card-body p-0">

                <!--begin::Object Filter Indicator-->
                {#if objectFilter}
                    <div class="mb-4 px-2">
                        <div class="d-flex align-items-center">
                            <span class="text-muted mr-2">Filtrato per:</span>
                            <span class="label label-light-primary label-inline font-weight-bolder d-flex align-items-center py-2 px-3">
                                {objectFilterLabel}
                                <button
                                    type="button"
                                    class="btn btn-icon btn-xs btn-light-primary ml-2 p-0 mb-0"
                                    style="width: 18px; height: 18px; min-width: 18px;"
                                    on:click={clearObjectFilter}
                                    title="Rimuovi filtro">
                                    <X size={12} />
                                </button>
                            </span>
                        </div>
                    </div>
                {/if}
                <!--end::Object Filter Indicator-->

                <!--begin: Datatable-->
                <BKNDatatable
                    bind:datatable
                    {columns}
                    url={__bakney.env.API.AUDIT.LIST}
                    {mapFunction}
                    clicked={(td, obj) => openDetailDrawer(obj.id)}
                    showDividerFilter={false}
                    loadFilters={() => {
                        const searchQueryEl = document.getElementById('bkn_datatable_search_query');
                        searchQueryEl?.addEventListener('keyup', debounce(function (e) {
                            generalSearch = e.currentTarget.value;
                            applyFilters();
                        }, 300));
                    }}>
                    <div slot="search-header">
                        <div class="col-12 col-md-auto p-0 text-right text-md-left p-md-auto m-0 mx-md-1 my-2 my-md-0">
                            <QueryFilter
                                {filters}
                                showMore={false}
                                on:filter-applied={handleFilterApplied} />
                        </div>
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

<!--begin::Detail Drawer-->
{#if showDetailDrawer}
    <AuditDetailDrawer
        logId={selectedLogId}
        on:close={() => {
            showDetailDrawer = false;
            selectedLogId = null;
        }}
        on:filter-by-object={(e) => {
            filterByObject(e.detail.pk, e.detail.label);
        }} />
{/if}
<!--end::Detail Drawer-->
