<script>
    import {scale} from 'svelte/transition';
    import * as easing from 'svelte/easing';
    import {canPerformAction} from 'utils/Permissions';
    import AssociateTable from './import/AssociateTable.svelte';
    import NavigationTab from './shared/NavigationTab.svelte';
</script>

<div  class="d-flex flex-column-fluid">
    {#if canPerformAction('association.members.create')}
        <div class="container">
            <div class="card card-custom gutter-b">
                <NavigationTab />
                <AssociateTable />
            </div>
        </div>
    {/if}
</div>
