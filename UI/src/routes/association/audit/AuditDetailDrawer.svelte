<script>
    import moment from 'moment';
    import {createEventDispatcher, onMount} from 'svelte';
    import BasicDrawer from 'components/drawer/basic-drawer.svelte';
    import {apiFetch} from 'utils/ApiMiddleware.js';
    import {Plus, Pencil, Trash, ArrowRight, FunnelSimple} from 'phosphor-svelte';

    export let logId;

    const dispatch = createEventDispatcher();

    let loading = true;
    let log = null;
    let error = null;

    const actionLabels = {
        0: {label: 'Creazione', class: 'label-light-success', icon: Plus},
        1: {label: 'Modifica', class: 'label-light-primary', icon: Pencil},
        2: {label: 'Eliminazione', class: 'label-light-danger', icon: Trash},
    };

    async function fetchLogDetail() {
        loading = true;
        error = null;

        const url = __bakney.env.API.AUDIT.DETAIL.replace('<id>', logId);
        const res = await apiFetch(url, {
            method: 'GET',
        });

        if (!res.error) {
            log = res.response.data || res.response;
        } else {
            error = 'Errore nel caricamento dei dettagli';
        }
        loading = false;
    }

    function hasChanges() {
        return log?.display_changes && log.display_changes.length > 0;
    }

    function displayValue(value, fallback = '-') {
        if (value === null || value === undefined || value === 'null' || value === 'None' || value === '') {
            return fallback;
        }
        return value;
    }

    function handleFilterByObject() {
        const pk = log?.object_pk;
        const label = log?.object_description || log?.object_repr || pk;
        if (pk) {
            dispatch('filter-by-object', {pk, label});
        }
    }

    onMount(() => {
        fetchLogDetail();
    });
</script>

<BasicDrawer isOpen={true} title="Dettaglio Audit Log" width="50vw" on:close={() => dispatch('close')}>
    <svelte:fragment slot="content">
        <div class="p-6">
            {#if loading}
                <div class="d-flex justify-content-center align-items-center py-10">
                    <div class="spinner spinner-primary spinner-lg"></div>
                </div>
            {:else if error}
                <div class="alert alert-danger">
                    {error}
                </div>
            {:else if log}
                <!--begin::Header Info-->
                <div class="d-flex flex-wrap mb-6">
                    <div class="mr-8 mb-4">
                        <span class="text-muted font-size-sm d-block mb-1">Log ID</span>
                        <span class="text-muted font-size-sm">
                            <code>{log.id}</code>
                        </span>
                    </div>
                    <div class="mr-8 mb-4">
                        <span class="text-muted font-size-sm d-block mb-1">Data/Ora</span>
                        <span class="font-weight-bolder font-size-lg">
                            {moment(log.timestamp).format('DD/MM/YYYY HH:mm:ss')}
                        </span>
                    </div>
                    <div class="mr-8 mb-4">
                        <span class="text-muted font-size-sm d-block mb-1">Azione</span>
                        <span class="label {actionLabels[log.action]?.class || 'label-light'} label-inline font-weight-bolder">
                            {log.action_label || actionLabels[log.action]?.label || '-'}
                        </span>
                    </div>
                    <div class="mr-8 mb-4">
                        <span class="text-muted font-size-sm d-block mb-1">Tipo</span>
                        <span class="font-weight-bolder font-size-lg">
                            {log.model_verbose_name || log.model_name || '-'}
                        </span>
                    </div>
                </div>

                <div class="separator separator-dashed mb-6"></div>

                <!--begin::Object Info-->
                <div class="mb-6">
                    <div class="d-flex align-items-center justify-content-between mb-4">
                        <h5 class="font-weight-bolder mb-0">Oggetto</h5>
                        {#if log.object_pk}
                            <button
                                type="button"
                                class="btn btn-sm btn-light-info font-weight-bold"
                                on:click={handleFilterByObject}
                                title="Mostra tutte le modifiche a questo oggetto">
                                <FunnelSimple size={16} class="mr-1" weight="bold" />
                                Filtra per questo oggetto
                            </button>
                        {/if}
                    </div>
                    <div class="bg-light rounded p-4">
                        <div class="row">
                            <div class="col-12 col-md-6 mb-3">
                                <span class="text-muted font-size-sm d-block mb-1">Descrizione</span>
                                <span class="font-weight-bold">
                                    {displayValue(log.object_description) !== '-' ? displayValue(log.object_description) : displayValue(log.object_repr)}
                                </span>
                            </div>
                            <div class="col-12 col-md-6 mb-3">
                                <span class="text-muted font-size-sm d-block mb-1">ID</span>
                                <code class="font-size-sm">{displayValue(log.object_pk)}</code>
                            </div>
                        </div>
                    </div>
                </div>

                <!--begin::Actor Info-->
                <div class="mb-6">
                    <h5 class="font-weight-bolder mb-4">Utente</h5>
                    <div class="bg-light rounded p-4">
                        <div class="row">
                            <div class="col-12 col-md-6 mb-3">
                                <span class="text-muted font-size-sm d-block mb-1">Nome</span>
                                <span class="font-weight-bold">
                                    {displayValue(log.actor_name)}
                                </span>
                            </div>
                            <div class="col-12 col-md-6 mb-3">
                                <span class="text-muted font-size-sm d-block mb-1">Email</span>
                                <span class="font-weight-bold">
                                    {displayValue(log.actor_email)}
                                </span>
                            </div>
                        </div>
                    </div>
                </div>

                <!--begin::Changes-->
                {#if hasChanges()}
                    <div class="mb-6">
                        <h5 class="font-weight-bolder mb-4">Modifiche</h5>
                        <div class="table-responsive">
                            <table class="table table-bordered table-head-solid">
                                <thead>
                                    <tr>
                                        <th class="font-weight-bolder" style="width: 25%;">Campo</th>
                                        <th class="font-weight-bolder" style="width: 35%;">Valore precedente</th>
                                        <th class="font-weight-bolder text-center" style="width: 5%;"></th>
                                        <th class="font-weight-bolder" style="width: 35%;">Nuovo valore</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {#each log.display_changes as change}
                                        <tr>
                                            <td class="font-weight-bold align-middle">
                                                {change.field}
                                            </td>
                                            <td class="align-middle">
                                                {#if log.action === 0}
                                                    <span class="text-muted font-italic">-</span>
                                                {:else}
                                                    <span class="text-danger">
                                                        {change.old ?? change.value ?? '-'}
                                                    </span>
                                                {/if}
                                            </td>
                                            <td class="text-center align-middle">
                                                <ArrowRight size={16} class="text-muted" />
                                            </td>
                                            <td class="align-middle">
                                                {#if log.action === 2}
                                                    <span class="text-muted font-italic">-</span>
                                                {:else}
                                                    <span class="text-success">
                                                        {change.new ?? change.value ?? '-'}
                                                    </span>
                                                {/if}
                                            </td>
                                        </tr>
                                    {/each}
                                </tbody>
                            </table>
                        </div>
                    </div>
                {:else if log.changes_summary}
                    <div class="mb-6">
                        <h5 class="font-weight-bolder mb-4">Riepilogo Modifiche</h5>
                        <div class="bg-light rounded p-4">
                            <span class="font-weight-bold">{log.changes_summary}</span>
                        </div>
                    </div>
                {/if}

            {/if}
        </div>
    </svelte:fragment>
</BasicDrawer>
