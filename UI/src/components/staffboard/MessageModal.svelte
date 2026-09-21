<script>
    import {createEventDispatcher} from 'svelte';
    import BasicModal from 'components/modals/BasicModal.svelte';
    import TipTapEditor from 'components/inputs/TipTapEditor.svelte';
    import {apiFetch, replaceUID} from 'utils/ApiMiddleware';
    import {documentSummary, plainTextDocument} from 'utils/staffBoardDocument.js';
    import {toast} from 'svelte-sonner';

    export let message = null;
    const dispatch = createEventDispatcher();
    let show = true;
    let submitting = false;
    let error = '';
    let editorContainer;
    let draft = message?.document || plainTextDocument(message?.content || '');
    let previousDraft = draft;
    $: if (draft !== previousDraft) {
        previousDraft = draft;
        error = '';
    }
    $: summary = documentSummary(draft);
    $: tooLarge = JSON.stringify(draft).length > 14 * 1024 * 1024;

    function reportError(message, focus = false) {
        error = message;
        toast.error(message, {id: 'staff-board-validation'});
        if (focus) editorContainer?.querySelector('[contenteditable]')?.focus();
    }

    function validateImageFile(file) {
        if (!['image/png', 'image/jpeg', 'image/gif', 'image/webp'].includes(file.type)) {
            reportError('Scegli un’immagine PNG, JPEG, GIF o WebP.');
            return false;
        }
        if (file.size > 10 * 1024 * 1024) {
            reportError('L’immagine è troppo grande. Scegli un file fino a 10 MB.');
            return false;
        }
        return true;
    }

    async function save() {
        if (submitting) return;
        if (summary.empty) return reportError('Scrivi un messaggio o aggiungi un’immagine.', true);
        if (summary.text.length > 10000) return reportError('Il testo può contenere al massimo 10000 caratteri.', true);
        if (tooLarge) return reportError('Il messaggio con le immagini è troppo grande (massimo 14 MB).', true);
        submitting = true;
        error = '';
        try {
            const endpoints = __bakney.env.API.COMMUNICATIONS.STAFF_BOARD;
            const res = await apiFetch(
                message ? replaceUID(endpoints.UPDATE, message.staff_board_message_id) : endpoints.ADD,
                {
                    method: message ? 'PATCH' : 'POST',
                    body: JSON.stringify({content: summary.text || 'Immagine allegata', document: draft}),
                }
            );
            if (res.error) {
                const details = res.response;
                const fieldError = details?.document || details?.content || details?.non_field_errors;
                const serverMessage =
                    (Array.isArray(fieldError) ? fieldError[0] : fieldError) || details?.msg || details?.error;
                reportError(
                    typeof serverMessage === 'string' ? serverMessage : 'Impossibile salvare il messaggio. Riprova.'
                );
                return;
            }
            toast.success(message ? 'Messaggio aggiornato.' : 'Messaggio pubblicato.');
            dispatch('saved');
            show = false;
            dispatch('close');
        } catch {
            reportError('Connessione non disponibile. La bozza è conservata: riprova.');
        } finally {
            submitting = false;
        }
    }
</script>

<BasicModal
    bind:show
    title={message ? 'Modifica messaggio' : 'Nuovo messaggio'}
    showTitle
    modalSize="lg"
    hideOnClickOutside={false}
    closeDisabled={submitting}
    showCancelButton={false}
    showActionButton={false}
    on:close={() => dispatch('close')}>
    <div class="staff-message-editor" class:has-error={!!error} aria-busy={submitting} bind:this={editorContainer}>
        <p class="text-muted font-size-sm mb-2">
            Condividi aggiornamenti, immagini e informazioni utili con il tuo staff.
        </p>
        <fieldset disabled={submitting}>
            <TipTapEditor
                bind:json={draft}
                mentions={false}
                disabled={submitting}
                validateImage={validateImageFile}
                accessibleLabel="Messaggio per lo staff"
                placeholder="Scrivi un messaggio per il tuo staff..." />
        </fieldset>
        {#if error}<p class="text-danger" role="alert">{error}</p>{/if}
        {#if tooLarge || summary.text.length > 10000}
            <p class="text-danger" role="alert">
                Il messaggio può contenere al massimo 10000 caratteri e 14 MB di immagini.
            </p>
        {/if}
    </div>
    <div slot="footer" class="d-flex justify-content-end w-100">
        <button
            type="button"
            class="btn btn-light-primary font-weight-bold mr-2"
            disabled={submitting}
            on:click={() => dispatch('close')}>Annulla</button>
        <button type="button" class="btn btn-primary font-weight-bold" disabled={submitting} on:click={save}>
            {submitting ? 'Salvataggio in corso...' : message ? 'Salva' : 'Pubblica'}
        </button>
    </div>
</BasicModal>

<style>
    .has-error :global(.editor-container) {
        border-color: var(--danger) !important;
    }
    fieldset {
        min-width: 0;
    }
    .staff-message-editor :global(.editor-container) {
        max-height: 45vh;
        overflow-y: auto;
    }
    .staff-message-editor :global(.ProseMirror) {
        min-height: 12rem;
        overflow-wrap: anywhere;
    }
    .staff-message-editor :global(img) {
        max-width: 100%;
        height: auto;
    }
    .staff-message-editor :global(button:focus-visible) {
        outline: 2px solid var(--primary) !important;
        outline-offset: 2px;
    }
</style>
