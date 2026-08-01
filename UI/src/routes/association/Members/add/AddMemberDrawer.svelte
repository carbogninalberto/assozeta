<script>
    import BasicDrawer from 'components/drawer/basic-drawer.svelte';
    import {createEventDispatcher} from 'svelte';
    import AddMember from './AddMember.svelte';

    const dispatch = createEventDispatcher();

    export let title = '';
</script>

<BasicDrawer isOpen={true} {title} width="85vw" on:close={() => dispatch('close')}>
    <svelte:fragment slot="content">
        <AddMember inModal={true} on:subscriptionCreated={() => dispatch('created')} />
    </svelte:fragment>
</BasicDrawer>
