<script>
    import BasicDrawer from 'components/drawer/basic-drawer.svelte';
    import {createEventDispatcher} from 'svelte';
    import AddInstructor from './AddInstructor.svelte';

    const dispatch = createEventDispatcher();

    export let title = '';
</script>

<BasicDrawer isOpen={true} {title} width="60vw" on:close={() => dispatch('close')}>
    <svelte:fragment slot="content">
        <AddInstructor inDrawer={true} on:created={() => dispatch('created')} />
    </svelte:fragment>
</BasicDrawer>
