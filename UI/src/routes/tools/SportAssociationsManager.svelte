<script>
    import BKNDatatable from 'components/tables/BKNDatatable.svelte';
    import {getApiHost} from 'store/instanceStore.js';
    import {startImpersonation} from 'utils/impersonation.js';
    import ImpersonationActions from './ImpersonationActions.svelte';
    export let embedded = false;
    let datatable;
    let selectedRole = '';
    let switching = false;
    let error = '';
    const labels = {association: 'Associazione sportiva', athlete: 'Utente', collaborator: 'Collaboratore'};
    const escape = value => String(value || '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
    const columns = [
        {field:'username',title:'Username',width:190,minWidth:150,autoHide:false,template:row => `<strong class="text-dark">${escape(row.username)}</strong>`},
        {field:'first_name',title:'Nome',width:180,minWidth:130,responsive:{hidden:'md'},template:row => escape(`${row.first_name} ${row.last_name}`.trim()) || '—'},
        {field:'email',title:'Email',width:220,minWidth:150,responsive:{hidden:'lg'},template:row => escape(row.email)},
        {field:'role',title:'Tipo di account',width:170,minWidth:155,responsive:{hidden:'md'},template:row => `<span class="badge badge-light-primary">${labels[row.role] || ''}</span>`},
        {field:'association.name',title:'Associazione',width:190,minWidth:140,responsive:{hidden:'xl'},sortable:false,template:row => escape(row.association?.name || 'Account personale')},
        {field:'last_login',title:'Ultimo accesso',width:140,minWidth:130,responsive:{hidden:'xxl'},template:row => row.last_login ? escape(new Date(row.last_login).toLocaleDateString('it-IT')) : 'Mai'},
        {field:'actions',title:'Azioni',width:80,autoHide:false,sortable:false,fireClick:false,component:ImpersonationActions},
    ];
    const mapUsers = response => (response.users || []).map(user => ({...user,onSelect:select}));
    async function select(user) {
        if (switching) return;
        switching = true;
        error = '';
        try { await startImpersonation(user); }
        catch (failure) { error = failure.message; switching = false; }
    }
</script>

<div class="admin-accounts d-flex flex-column-fluid">
    <div class:container={!embedded} class="w-100">
        <div class:card={!embedded} class:card-custom={!embedded} class:gutter-b={!embedded}>
            {#if !embedded}
                <div class="card-header flex-wrap border-0 p-0">
                    <div class="card-title">
                        <h1 class="card-label font-size-h2">Impersona utenti
                            <span class="d-block text-muted pt-2 font-size-sm">Associazioni, utenti e collaboratori: scegli l’account con cui operare.</span>
                        </h1>
                    </div>
                </div>
            {/if}
            <div class="card-body p-0">
            {#if error}<p role="alert" class="alert alert-danger">{error}</p>{/if}
            {#if switching}<p role="status" class="text-primary">Cambio utente in corso…</p>{/if}
            <BKNDatatable bind:datatable {columns} url={`${getApiHost()}/administration/impersonation/users`}
                mapFunction={mapUsers} pageSize={25} pageSizeSelect={[10,25,50,100]} showDividerFilter={false} wrapText={false} serverPaging serverFiltering serverSorting>
                <div slot="filter-bar" class="account-type-filter d-flex align-items-center">
                    <label class="mb-0 mr-3" for={embedded ? 'impersonation-modal-role' : 'impersonation-page-role'}>Tipo di account</label>
                    <select id={embedded ? 'impersonation-modal-role' : 'impersonation-page-role'} class="form-control form-control-sm"
                        bind:value={selectedRole} on:change={() => datatable?.search(selectedRole, 'role')} disabled={switching}>
                        <option value="">Tutti</option>
                        {#each Object.entries(labels) as [value,label]}<option {value}>{label}</option>{/each}
                    </select>
                </div>
            </BKNDatatable>
            </div>
        </div>
    </div>
</div>

<style>
    .admin-accounts { min-width: 0; max-width: 100%; }
    .admin-accounts :global(.datatable-body .datatable-row > .datatable-cell) { padding-top: .45rem; padding-bottom: .45rem; }
    .admin-accounts :global(.datatable-filter-controls) { align-items: center; }
    @media (min-width: 768px) { .admin-accounts :global(.datatable-search input) { height: 38px; } }

    .account-type-filter { gap: .75rem; min-height: 38px; }
    .account-type-filter label { white-space: nowrap; line-height: 1.25; }
    .account-type-filter select { min-width: 12rem; height: 38px; margin: 0; }
</style>
