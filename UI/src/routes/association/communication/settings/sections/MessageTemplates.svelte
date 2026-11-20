<script>
    import {onMount} from 'svelte';
    import {FileText, Plus} from 'phosphor-svelte';
    import swal from 'sweetalert2';
    import {toast} from 'svelte-sonner';
    import {replaceUID} from 'utils/ApiMiddleware';
    import {canPerformAction} from 'utils/Permissions';
    import {messageChannels, messageTemplateRequest} from '../../messageTemplates';

    const endpoints = __bakney.env.API.COMMUNICATIONS.SETTINGS.MESSAGE_TEMPLATES;
    const canEdit = canPerformAction('association.communication.settings.update');
    let rows = [];
    let variables = [];
    let loading = true;
    let busy = false;
    let error = '';
    let draft = null;
    let sample = null;
    let sampleInput = '';

    $: if (draft && JSON.stringify(draft) !== sampleInput) sample = null;

    async function load() {
        loading = true;
        error = '';
        try {
            const result = await messageTemplateRequest(endpoints.LIST);
            rows = result.data;
            variables = result.variables;
        } catch (failure) {
            error = failure.message;
        } finally {
            loading = false;
        }
    }

    function edit(row) {
        draft = row ? {...row} : {
            name: '', channel: 'email', locale: 'it', subject: '', body: '', enabled: true, is_default: false,
        };
        error = '';
        sample = null;
    }

    async function submit(showSample) {
        busy = true;
        error = '';
        try {
            if (showSample) {
                const result = await messageTemplateRequest(endpoints.SAMPLE, 'POST', draft);
                sampleInput = JSON.stringify(draft);
                sample = result.data;
                return;
            }
            const target = draft.id ? replaceUID(endpoints.DETAIL, draft.id) : endpoints.LIST;
            await messageTemplateRequest(target, draft.id ? 'PATCH' : 'POST', draft);
            draft = null;
            toast.success('Modello salvato.');
            await load();
        } catch (failure) {
            error = failure.message;
        } finally {
            busy = false;
        }
    }

    async function remove() {
        busy = true;
        error = '';
        try {
            const result = await swal.fire({
                text: 'Vuoi eliminare questo modello?',
                icon: 'warning',
                showCancelButton: true,
                confirmButtonText: 'Elimina',
                cancelButtonText: 'Annulla',
            });
            if (!result.isConfirmed) return;
            await messageTemplateRequest(replaceUID(endpoints.DETAIL, draft.id), 'DELETE');
            draft = null;
            toast.success('Modello eliminato.');
            await load();
        } catch (failure) {
            error = failure.message;
        } finally {
            busy = false;
        }
    }

    onMount(load);
</script>

<section class="card card-custom gutter-b" aria-labelledby="message-templates-title">
    <div class="card-header flex-wrap">
        <div class="card-title">
            <FileText size={24} weight="duotone" class="mr-3 text-primary" />
            <h2 id="message-templates-title" class="card-label">Modelli di messaggio</h2>
        </div>
        {#if canEdit && !draft}
            <div class="card-toolbar">
                <button type="button" class="btn btn-primary font-weight-bold" disabled={loading} on:click={() => edit()}>
                    <Plus size={17} class="mr-1" /> Nuovo modello
                </button>
            </div>
        {/if}
    </div>
    <div class="card-body">
        {#if error}<div class="alert alert-danger" role="alert">{error}</div>{/if}
        {#if loading}
            <p role="status">Caricamento dei modelli…</p>
        {:else if draft}
            <h3 class="font-size-h5 mb-5">{draft.id ? 'Modifica modello' : 'Nuovo modello'}</h3>
            <form on:submit|preventDefault={event => submit(event.submitter?.value === 'sample')}>
                <fieldset disabled={busy}>
                    <div class="form-group">
                        <label for="message-template-name">Nome modello</label>
                        <input id="message-template-name" class="form-control form-control-solid" bind:value={draft.name} maxlength="100" required />
                    </div>
                    <div class="row">
                        <div class="col-sm-6 form-group">
                            <label for="message-template-channel">Canale</label>
                            <select id="message-template-channel" class="form-control form-control-solid" bind:value={draft.channel}
                                on:change={event => { if (event.currentTarget.value !== 'email') draft.subject = ''; }}>
                                {#each Object.entries(messageChannels) as [key, label]}
                                    <option value={key}>{label}</option>
                                {/each}
                            </select>
                        </div>
                        <div class="col-sm-6 form-group">
                            <label for="message-template-locale">Lingua del modello</label>
                            <input id="message-template-locale" class="form-control form-control-solid" bind:value={draft.locale}
                                maxlength="35" placeholder="it, en, en-gb" required />
                        </div>
                    </div>
                    {#if draft.channel === 'email'}
                        <div class="form-group">
                            <label for="message-template-subject">Oggetto</label>
                            <input id="message-template-subject" class="form-control form-control-solid" bind:value={draft.subject} maxlength="100" required />
                        </div>
                    {/if}
                    <div class="form-group">
                        <label for="message-template-body">Testo del modello</label>
                        <textarea id="message-template-body" class="form-control form-control-solid" rows="5" maxlength="10000"
                            bind:value={draft.body} aria-describedby="message-template-variables" required></textarea>
                        <div class="form-text text-muted" id="message-template-variables">
                            Variabili disponibili
                            {#each variables as name}
                                <code>{'{{' + name + '}}'}</code>
                            {/each}
                        </div>
                    </div>
                    <div class="checkbox-list mb-5">
                        <label class="checkbox">
                            <input type="checkbox" bind:checked={draft.enabled}
                                on:change={event => { if (!event.currentTarget.checked) draft.is_default = false; }} />
                            <span></span> Attivo
                        </label>
                        <label class="checkbox">
                            <input type="checkbox" bind:checked={draft.is_default} disabled={!draft.enabled} />
                            <span></span> Predefinito per questo canale e lingua
                        </label>
                    </div>
                    {#if sample}
                        <div class="border rounded p-4 mb-5" aria-live="polite">
                            <h4 class="font-size-h6">Anteprima di esempio</h4>
                            {#if sample.subject}<p class="font-weight-bold mb-2">{sample.subject}</p>{/if}
                            <p class="preview-copy mb-0">{sample.body}</p>
                        </div>
                    {/if}
                    <div class="d-flex flex-wrap align-items-center justify-content-between">
                        <div class="d-flex flex-wrap">
                            <button type="submit" value="save" class="btn btn-primary font-weight-bold mr-2 mb-2">Salva modello</button>
                            <button type="submit" value="sample" class="btn btn-light-primary font-weight-bold mr-2 mb-2">Mostra esempio</button>
                            <button type="button" class="btn btn-light mb-2" on:click={() => { draft = null; error = ''; }}>Annulla</button>
                        </div>
                        {#if draft.id}
                            <button type="button" class="btn btn-light-danger font-weight-bold mb-2" on:click={remove}>Elimina modello</button>
                        {/if}
                    </div>
                </fieldset>
            </form>
        {:else if rows.length}
            <div class="table-responsive">
                <table class="table mb-0">
                    <thead><tr><th scope="col">Modello</th><th scope="col">Canale</th><th scope="col">Lingua</th><th scope="col">Stato</th>
                        {#if canEdit}<th scope="col" class="text-right">Azioni</th>{/if}
                    </tr></thead>
                    <tbody>
                        {#each rows as row (row.id)}
                            <tr>
                                <th scope="row" class="align-middle">{row.name}</th>
                                <td class="align-middle">{messageChannels[row.channel]}</td>
                                <td class="align-middle">{row.locale}</td>
                                <td class="align-middle">{row.is_default ? 'Predefinito' : row.enabled ? 'Attivo' : 'Disattivato'}</td>
                                {#if canEdit}
                                    <td class="align-middle text-right">
                                        <button type="button" class="btn btn-sm btn-light-primary" aria-label={'Modifica ' + row.name + ' ' + messageChannels[row.channel]}
                                            on:click={() => edit(row)}>Modifica</button>
                                    </td>
                                {/if}
                            </tr>
                        {/each}
                    </tbody>
                </table>
            </div>
        {:else}
            <p class="text-muted mb-0">Nessun modello di messaggio.</p>
        {/if}
    </div>
</section>

<style>
    .preview-copy {
        white-space: pre-wrap;
        overflow-wrap: anywhere;
    }
</style>
