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
<section aria-labelledby="instance-integrations">
    <h2 id="instance-integrations">Integrazioni</h2>
    <p>I valori iniziali provengono dall’ambiente del server. Le modifiche salvate qui hanno precedenza e restano disponibili dopo riavvii e aggiornamenti. Puoi ripristinare .env per ogni integrazione.</p>
    <p>I controlli indicano la configurazione presente: pagamenti e accessi reali richiedono una verifica separata.</p>
    {#each providers as provider}
        <InstanceIntegration provider={provider.id} title={provider.title} check={checks.find(value => value.id === provider.id)} {request} {disabled} {onSaved} bind:changes={changed[provider.id]} bind:busy={pending[provider.id]} />
    {/each}
</section>
