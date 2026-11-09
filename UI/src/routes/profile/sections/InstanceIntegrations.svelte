<script>
    import InstanceIntegration from './InstanceIntegration.svelte';
    export let request;
    export let checks = [];
    export let disabled = false;
    export let changes = false;
    export let busy = false;
    export let onSaved = () => {};
    const providers = [{id: 'stripe', title: 'Stripe'}, {id: 'google', title: 'Google'}, {id: 'apple', title: 'Apple'}];
    let changed = {};
    let pending = {};
    $: changes = Object.values(changed).some(Boolean);
    $: busy = Object.values(pending).some(Boolean);
</script>
<section class="si-stack" aria-labelledby="instance-integrations">
    <h2 id="instance-integrations" class="si-sr-only">Integrazioni</h2>
    {#each providers as provider}
        <InstanceIntegration provider={provider.id} title={provider.title} check={checks.find(value => value.id === provider.id)} {request} {disabled} {onSaved} bind:changes={changed[provider.id]} bind:busy={pending[provider.id]} />
    {/each}
</section>
