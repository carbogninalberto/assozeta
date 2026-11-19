<script>
    import {createEventDispatcher, onMount} from 'svelte';
    import BasicModal from 'components/modals/BasicModal.svelte';
    import {replaceUID} from 'utils/ApiMiddleware';
    import {messageChannels, messageTemplateRequest} from '../../messageTemplates';

    export let associateId;

    const dispatch = createEventDispatcher();
    const endpoints = __bakney.env.API.COMMUNICATIONS.OUTGOING;
    const recipientURL = replaceUID(endpoints.RECIPIENT, associateId);
    let show = true;
    let options;
    let channel = 'email';
    let locale = 'it';
    let templateId = '';
    let date = '';
    let time = '';
    let link = '';
    let preview = null;
    let subject = '';
    let body = '';
    let busy = false;
    let error = '';
    let pendingAction = null;

    $: templates = options?.templates.filter(template => template.channel === channel) || [];
    $: locales = [...new Set(['it', ...templates.map(template => template.locale)])];
    $: selected = templateId
        ? templates.find(template => template.id === templateId)
        : [locale, locale.split('-')[0], 'it'].map(
              code => templates.find(template => template.is_default && template.locale === code)
          ).find(Boolean);
    $: variables = [...((selected?.body || '') + (selected?.subject || '')).matchAll(/\{\{[ \t]*(date|time|link)[ \t]*\}\}/g)]
        .map(match => match[1]);
    $: canEdit = preview?.status === 'preview' && !pendingAction;

    async function load() {
        busy = true;
        error = '';
        try {
            options = (await messageTemplateRequest(recipientURL)).data;
        } catch (failure) {
            error = failure.message;
        } finally {
            busy = false;
        }
    }

    async function renderPreview() {
        if (busy || !selected) return;
        busy = true;
        error = '';
        try {
            preview = (await messageTemplateRequest(recipientURL, 'POST', {
                channel, locale, template_id: templateId,
                date: variables.includes('date') ? date : '',
                time: variables.includes('time') ? time : '',
                link: variables.includes('link') ? link : '',
            })).data;
            subject = preview.subject;
            body = preview.body;
        } catch (failure) {
            error = failure.message;
        } finally {
            busy = false;
        }
    }

    async function finish(action) {
        if (busy) return;
        busy = true;
        error = '';
        pendingAction = action;
        try {
            preview = (await messageTemplateRequest(replaceUID(endpoints.PREVIEW, preview.id), 'POST', {
                action, subject, body,
            })).data;
            if (preview.status !== 'sending') pendingAction = null;
        } catch (failure) {
            error = failure.message;
            // A network failure leaves the outcome unknown. Repeat the same action and text.
            if (failure.status && failure.status < 500) pendingAction = null;
        } finally {
            busy = false;
        }
    }

    onMount(load);
</script>

<BasicModal
    bind:show
    title={preview ? 'Anteprima del messaggio' : 'Nuovo messaggio da modello'}
    showTitle
    modalSize="lg"
    scrollable
    hideOnClickOutside={false}
    closeDisabled={busy || (preview?.status === 'preview' && !pendingAction)}
    showCancelButton={false}
    showActionButton={false}
    on:close={() => dispatch('close')}>
    <div aria-busy={busy}>
        {#if error}<div class="alert alert-danger" role="alert">{error}</div>{/if}
        {#if preview}
            <dl class="mb-4">
                <dt>Destinatario</dt>
                <dd class="recipient mb-0">{options.recipient_name} · {preview.recipient}</dd>
            </dl>
            <fieldset disabled={busy || !canEdit}>
                {#if preview.channel === 'email'}
                    <div class="form-group">
                        <label for="outgoing-subject">Oggetto</label>
                        <input id="outgoing-subject" class="form-control form-control-solid" maxlength="100" bind:value={subject} />
                    </div>
                {/if}
                <div class="form-group">
                    <label for="outgoing-body">Testo del messaggio</label>
                    <textarea id="outgoing-body" class="form-control form-control-solid" rows="6" maxlength="10000" bind:value={body}></textarea>
                </div>
            </fieldset>
            {#if preview.warning}
                <div class="alert alert-warning mb-0" role="status">{preview.warning}</div>
            {:else if preview.status === 'submitted'}
                <div class="alert alert-success mb-0" role="status">Messaggio affidato al servizio di invio.</div>
            {:else if preview.status === 'sending'}
                <div class="alert alert-warning mb-0" role="status">L’invio è in attesa di conferma.</div>
            {:else if !preview.can_send}
                <div class="alert alert-warning mb-0" role="status">
                    {preview.can_open_whatsapp ? 'Completa l’invio manualmente in WhatsApp.' : 'Invio automatico non disponibile per questo canale.'}
                </div>
            {/if}
        {:else if busy && !options}
            <p role="status">Caricamento dei modelli…</p>
        {:else if options}
            <p class="mb-4"><strong>Destinatario</strong> {options.recipient_name}</p>
            <form id="outgoing-template-form" on:submit|preventDefault={renderPreview}>
                <fieldset disabled={busy}>
                    <div class="row">
                        <div class="col-sm-6 form-group">
                            <label for="outgoing-channel">Canale</label>
                            <select id="outgoing-channel" class="form-control form-control-solid" bind:value={channel}
                                on:change={() => { templateId = ''; locale = 'it'; }}>
                                {#each Object.entries(messageChannels) as [key, label]}
                                    <option value={key}>{label}</option>
                                {/each}
                            </select>
                        </div>
                        <div class="col-sm-6 form-group">
                            <label for="outgoing-locale">Lingua del messaggio</label>
                            <select id="outgoing-locale" class="form-control form-control-solid" bind:value={locale} on:change={() => templateId = ''}>
                                {#each locales as code}
                                    <option value={code}>{code}</option>
                                {/each}
                            </select>
                        </div>
                    </div>
                    <div class="form-group">
                        <label for="outgoing-template">Modello</label>
                        <select id="outgoing-template" class="form-control form-control-solid" bind:value={templateId}>
                            <option value="">Usa il predefinito</option>
                            {#each templates as template (template.id)}
                                <option value={template.id}>{template.name} · {template.locale}</option>
                            {/each}
                        </select>
                        {#if selected}<div class="form-text text-muted">Modello selezionato: {selected.name} · {selected.locale}</div>
                        {:else}<div class="form-text text-muted">Scegli un modello attivo per continuare.</div>{/if}
                    </div>
                    {#if variables.includes('date')}
                        <div class="form-group">
                            <label for="outgoing-date">Data</label>
                            <input id="outgoing-date" class="form-control form-control-solid" type="date" bind:value={date} required />
                        </div>
                    {/if}
                    {#if variables.includes('time')}
                        <div class="form-group">
                            <label for="outgoing-time">Ora</label>
                            <input id="outgoing-time" class="form-control form-control-solid" type="time" bind:value={time} required />
                        </div>
                    {/if}
                    {#if variables.includes('link')}
                        <div class="form-group mb-0">
                            <label for="outgoing-link">Link</label>
                            <input id="outgoing-link" class="form-control form-control-solid" type="url" bind:value={link} maxlength="2048" required />
                        </div>
                    {/if}
                </fieldset>
            </form>
        {/if}
    </div>
    <div slot="footer" class="d-flex flex-wrap justify-content-end">
        {#if !preview && options}
            <button type="submit" form="outgoing-template-form" class="btn btn-primary font-weight-bold" disabled={busy || !selected}>Mostra anteprima</button>
        {:else if !preview && error}
            <button type="button" class="btn btn-light-primary font-weight-bold" disabled={busy} on:click={load}>Riprova</button>
        {:else if pendingAction}
            <button type="button" class="btn btn-primary font-weight-bold" disabled={busy} on:click={() => finish(pendingAction)}>Verifica esito</button>
        {:else if canEdit}
            <button type="button" class="btn btn-light-danger font-weight-bold mr-2" disabled={busy}
                on:click={() => finish('discard')}>Scarta anteprima</button>
            {#if preview.can_send}
                <button type="button" class="btn btn-primary font-weight-bold" disabled={busy || !body.trim() || (preview.channel === 'email' && !subject.trim())}
                    on:click={() => finish('send')}>Invia ora</button>
            {:else if preview.can_open_whatsapp}
                <button type="button" class="btn btn-primary font-weight-bold" disabled={busy || !body.trim()}
                    on:click={() => finish('open_whatsapp')}>Prepara WhatsApp</button>
            {/if}
        {:else if preview}
            <button type="button" class="btn btn-light-primary font-weight-bold mr-2" on:click={() => dispatch('close')}>Fine</button>
            {#if preview.whatsapp_url}
                <a class="btn btn-primary font-weight-bold" href={preview.whatsapp_url} target="_blank" rel="noopener noreferrer">Apri in WhatsApp</a>
            {/if}
        {/if}
    </div>
</BasicModal>

<style>
    .recipient {
        overflow-wrap: anywhere;
    }
</style>
