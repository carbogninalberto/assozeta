<script>
    // API (beta): the owner switches the web interface of every user to the
    // new API (/api/v2). The current API stays available as the fallback.
    import {toast} from 'svelte-sonner';
    import Switch from '../../../components/inputs/Switch.svelte';
    import {saveRuntimeConfig, applyApiVersion} from 'store/instanceStore.js';
    import {probeApiV2, readApiOverride, clearApiOverride, activeApiVersion} from 'utils/apiVersion.js';

    export let request;
    export let config = {};
    export let disabled = false;
    export let busy = false;
    export let onSaved = () => {};

    let enabled = config?.features?.uiApiVersion === 'v2';
    let error = '';
    let notice = '';
    let override = readApiOverride();
    let current = activeApiVersion();

    async function change(event) {
        const wanted = event.target.checked;
        if (busy || disabled) { enabled = !wanted; return; }
        busy = true;
        error = '';
        notice = '';
        try {
            if (wanted && !(await probeApiV2({force: true}))) {
                enabled = false;
                notice = 'La nuova API non risponde in questo momento, quindi non è stata attivata. L’app continua a usare l’API attuale: riprova più tardi.';
                return;
            }
            const result = await request('/admin', {method: 'PUT', body: JSON.stringify({uiApiVersion: wanted ? 'v2' : 'v1'})});
            saveRuntimeConfig(result.config);
            enabled = result.config?.features?.uiApiVersion === 'v2';
            current = await applyApiVersion(result.config);
            onSaved(result.config);
            toast.success(enabled ? 'Nuova API attivata.' : 'Nuova API disattivata.');
        } catch (e) {
            enabled = !wanted;
            error = e.message;
        } finally { busy = false; }
    }

    async function removeOverride() {
        clearApiOverride();
        override = null;
        current = await applyApiVersion({...config, features: {...config?.features, uiApiVersion: enabled ? 'v2' : 'v1'}});
    }
</script>

<section class="si-card" aria-labelledby="instance-api-version">
    <div class="si-card-head">
        <div>
            <h2 id="instance-api-version" class="si-title">Nuova API <span class="si-pill info beta">Beta</span></h2>
            <p class="si-muted si-small">L’interfaccia web di tutti gli utenti usa il nuovo server API (v2). Se non risponde, l’app torna da sola all’API attuale. Puoi disattivarla in qualsiasi momento.</p>
        </div>
    </div>
    <Switch id="instance-api-v2" name="instance-api-v2" label="Usa la nuova API (v2) — beta" bind:checked={enabled} disabled={disabled || busy} on:change={change} />
    <p class="si-hint mb-0" role="status">
        {busy ? 'Verifica della nuova API…' : `Questo browser usa ora l’API ${current === 'v2' ? 'v2' : 'attuale (v1)'}.`}
    </p>
    {#if override}
        <p class="si-hint mt-2 mb-0">
            Questo browser usa la {override} (override ?api={override}).
            <button type="button" class="si-link" on:click={removeOverride}>Rimuovi override</button>
        </p>
    {/if}
    {#if notice}<p class="si-muted mt-3 mb-0" role="status">{notice}</p>{/if}
    {#if error}<p class="text-danger mt-3 mb-0" role="alert">{error}</p>{/if}
</section>

<style>
    .beta { margin-left: .5rem; vertical-align: middle; font-size: .85rem; padding: .15rem .55rem; }
</style>
