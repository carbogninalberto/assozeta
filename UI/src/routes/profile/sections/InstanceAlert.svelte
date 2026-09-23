<script>
    import {Info, WarningCircle} from 'phosphor-svelte';
    export let tone = 'primary';
    export let role = 'status';
</script>

<div class="alert alert-custom alert-light-{tone} fade show my-4" {role}>
    <div class="alert-icon" aria-hidden="true">
        <svelte:component this={tone === 'primary' ? Info : WarningCircle} size={24} weight="duotone" />
    </div>
    <div class="alert-text"><slot /></div>
</div>

<style>
    .alert-text {min-width: 0; overflow-wrap: anywhere;}
</style>
