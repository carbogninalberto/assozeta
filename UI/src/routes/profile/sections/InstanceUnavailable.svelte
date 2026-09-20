<script>
    import {onMount, onDestroy} from 'svelte';
    import {originalFetch} from 'utils/ApiMiddleware.js';
    import {getApiHost} from 'store/instanceStore.js';
    import {readStoredStatus} from './independentStatus.js';
    import {mergeRunnerStatus} from './updateStatus.js';
    import InstanceUpdateStatus from './InstanceUpdateStatus.svelte';

    let owner = false;
    let runner = {available: false, active: null, history: []};
    let reconnecting = false;
    let disposed = false;
    let timer;

    async function poll() {
        const result = await readStoredStatus(originalFetch);
        if (disposed) return;
        if (result.kind === 'owner') {
            owner = true;
            runner = mergeRunnerStatus(runner, result.status);
            reconnecting = !result.status.available;
        } else if (result.kind === 'denied') {
            owner = false;
            runner = {available: false, active: null, history: []};
        } else {
            reconnecting = true;
        }
        // Bootstrap can resume when the API is back. Never refresh/login out
        // through the API interceptor merely because services are restarting.
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 5000);
        try {
            const response = await originalFetch(`${getApiHost()}/instance/status`, {cache: 'no-store', signal: controller.signal});
            if (response.ok && (await response.json()).configured === true && !disposed) {
                window.location.reload();
                return;
            }
        } catch { /* Retry after the next status check. */ }
        finally { clearTimeout(timeout); }
        if (!disposed) timer = setTimeout(poll, 4000);
    }

    onMount(() => { poll(); });
    onDestroy(() => { disposed = true; clearTimeout(timer); });
</script>

<main class="container py-8">
    <div class="card card-custom"><div class="card-body">
        <h1>{owner ? 'Self Instance' : 'Applicazione temporaneamente non disponibile'}</h1>
        {#if owner}
            <InstanceUpdateStatus {runner} {reconnecting} apiUnavailable />
        {:else}
            <p role="status">Riconnessione in corso. La sessione verrà mantenuta durante il riavvio dei servizi.</p>
        {/if}
        <button class="btn btn-light-primary" on:click={() => window.location.reload()}>Riprova ad aprire l’applicazione</button>
    </div></div>
</main>
