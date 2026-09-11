<script>
    import {onMount} from 'svelte';
    import {apiFetch, replaceUID} from 'utils/ApiMiddleware';
    import {canPerformAction} from 'utils/Permissions';
    import {toast} from 'svelte-sonner';
    import ContentLoader from 'svelte-content-loader';
    import {PencilSimple, TrashSimple, Check, X, PushPin} from 'phosphor-svelte';
    import {scale, slide} from 'svelte/transition';

    let loading = true;
    let loadError = false;
    let messages = [];
    let newContent = '';
    let submitting = false;

    // edit state
    let editingId = null;
    let editingContent = '';

    async function fetchMessages() {
        loading = true;
        loadError = false;
        let res = await apiFetch(__bakney.env.API.COMMUNICATIONS.STAFF_BOARD.LIST);
        if (!res.error) {
            messages = res.response.data || [];
        } else {
            loadError = true;
        }
        loading = false;
    }

    async function submitMessage() {
        if (!newContent.trim() || submitting) return;
        submitting = true;
        let res = await apiFetch(__bakney.env.API.COMMUNICATIONS.STAFF_BOARD.ADD, {
            method: 'POST',
            body: JSON.stringify({content: newContent.trim()}),
        });
        if (!res.error) {
            toast.success('Messaggio pubblicato.');
            newContent = '';
            await fetchMessages();
        } else {
            toast.error('Qualcosa è andato storto.');
        }
        submitting = false;
    }

    function startEdit(message) {
        editingId = message.staff_board_message_id;
        editingContent = message.content;
    }

    async function saveEdit() {
        if (!editingContent.trim()) return;
        let res = await apiFetch(
            replaceUID(__bakney.env.API.COMMUNICATIONS.STAFF_BOARD.UPDATE, editingId),
            {
                method: 'PATCH',
                body: JSON.stringify({content: editingContent.trim()}),
            }
        );
        if (!res.error) {
            toast.success('Messaggio aggiornato.');
            editingId = null;
            await fetchMessages();
        } else {
            toast.error('Qualcosa è andato storto.');
        }
    }

    async function togglePin(message) {
        let res = await apiFetch(
            replaceUID(__bakney.env.API.COMMUNICATIONS.STAFF_BOARD.UPDATE, message.staff_board_message_id),
            {
                method: 'PATCH',
                body: JSON.stringify({pinned: !message.pinned}),
            }
        );
        if (!res.error) {
            await fetchMessages();
        } else {
            toast.error('Qualcosa è andato storto.');
        }
    }

    async function deleteMessage(message) {
        if (!confirm('Eliminare questo messaggio dalla bacheca?')) return;
        let res = await apiFetch(
            replaceUID(__bakney.env.API.COMMUNICATIONS.STAFF_BOARD.DELETE, message.staff_board_message_id),
            {method: 'DELETE'}
        );
        if (!res.error) {
            toast.success('Messaggio eliminato.');
            await fetchMessages();
        } else {
            toast.error('Qualcosa è andato storto.');
        }
    }

    function formatDate(dateStr) {
        let d = new Date(dateStr);
        return d.toLocaleString('it-IT', {
            day: '2-digit',
            month: '2-digit',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
        });
    }

    onMount(async () => {
        await fetchMessages();
    });
</script>

<!--begin::Entry-->
<div class="d-flex flex-column-fluid">
    <!--begin::Container-->
    <div class="container">
        <!--begin::Card-->
        <div class="card card-custom gutter-b">
            <div
                class="card-header px-0 pt-0 pb-0 mb-6 header-mobile-btn-back border-0"
                style="padding-bottom: 0 !important;min-height: auto !important;">
                <div class="card-toolbar m-0">
                    <h3 class="card-title font-size-h2">Bacheca Staff</h3>
                </div>
            </div>
            <div class="card-body pt-0 px-4">
                {#if loading}
                    <ContentLoader width="100%" height="400">
                        <rect x="15" y="15" rx="4" ry="4" width="100%" height="80" />
                        <rect x="15" y="110" rx="4" ry="4" width="100%" height="80" />
                        <rect x="15" y="205" rx="4" ry="4" width="100%" height="80" />
                    </ContentLoader>
                {:else if loadError}
                    <div class="text-center py-10">
                        <p class="text-muted">Impossibile caricare la bacheca.</p>
                    </div>
                {:else}
                    <!--begin::Nuovo messaggio-->
                    {#if canPerformAction('association.communication.messages.create')}
                        <div class="form-group mb-8">
                            <textarea
                                bind:value={newContent}
                                class="form-control form-control-solid form-control-lg"
                                rows="3"
                                style="resize: vertical;"
                                placeholder="Scrivi un messaggio per il tuo staff..." />
                            <div class="d-flex justify-content-end mt-2">
                                <button
                                    type="button"
                                    class="btn btn-primary font-weight-bold"
                                    disabled={!newContent.trim() || submitting}
                                    on:click={submitMessage}>
                                    Pubblica
                                </button>
                            </div>
                        </div>
                    {/if}
                    <!--end::Nuovo messaggio-->

                    {#if messages.length === 0}
                        <div class="text-center py-10">
                            <PushPin size={48} weight="duotone" class="text-muted" />
                            <p class="text-muted mt-4">Nessun messaggio. La bacheca è vuota.</p>
                        </div>
                    {:else}
                        {#each messages as message (message.staff_board_message_id)}
                            <div
                                class="card card-custom mb-4 {message.pinned ? 'border-left-warning' : ''}"
                                in:slide={{duration: 200}}
                                out:scale={{duration: 200}}>
                                <div class="card-body p-6">
                                    <div class="d-flex align-items-center justify-content-between flex-wrap">
                                        <div class="d-flex align-items-center">
                                            {#if message.pinned}
                                                <PushPin size={20} weight="fill" class="text-warning mr-2" />
                                            {/if}
                                            <span class="font-weight-bolder text-dark-75">
                                                {message.author_name}
                                            </span>
                                            <span class="text-muted ml-3" style="font-size: 0.85rem;">
                                                {formatDate(message.created_at)}
                                            </span>
                                        </div>
                                        <div class="d-flex align-items-center">
                                            {#if canPerformAction('association.communication.messages.update')}
                                                <button
                                                    type="button"
                                                    class="btn btn-icon btn-light btn-hover-primary btn-sm mr-1"
                                                    title={message.pinned ? 'Togli pin' : 'Metti in evidenza'}
                                                    on:click={() => togglePin(message)}>
                                                    <PushPin size={16}
                                                         weight={message.pinned ? 'fill' : 'duotone'}
                                                         class={message.pinned ? 'text-warning' : ''} />
                                                </button>
                                                <button
                                                    type="button"
                                                    class="btn btn-icon btn-light btn-hover-primary btn-sm mr-1"
                                                    title="Modifica"
                                                    on:click={() => startEdit(message)}>
                                                    <PencilSimple size={16} />
                                                </button>
                                            {/if}
                                            {#if canPerformAction('association.communication.messages.delete')}
                                                <button
                                                    type="button"
                                                    class="btn btn-icon btn-light btn-hover-danger btn-sm"
                                                    title="Elimina"
                                                    on:click={() => deleteMessage(message)}>
                                                    <TrashSimple size={16} />
                                                </button>
                                            {/if}
                                        </div>
                                    </div>

                                    {#if editingId == message.staff_board_message_id}
                                        <div class="mt-4">
                                            <textarea
                                                bind:value={editingContent}
                                                class="form-control form-control-solid"
                                                rows="3"
                                                style="resize: vertical;" />
                                            <div class="d-flex justify-content-end mt-2">
                                                <button
                                                    type="button"
                                                    class="btn btn-light-primary font-weight-bold mr-2"
                                                    on:click={() => (editingId = null)}>
                                                    <X size={16} /> Annulla
                                                </button>
                                                <button
                                                    type="button"
                                                    class="btn btn-primary font-weight-bold"
                                                    disabled={!editingContent.trim()}
                                                    on:click={saveEdit}>
                                                    <Check size={16} /> Salva
                                                </button>
                                            </div>
                                        </div>
                                    {:else}
                                        <p class="mt-4 mb-0 text-dark-75" style="white-space: pre-line;">
                                            {message.content}
                                        </p>
                                    {/if}
                                </div>
                            </div>
                        {/each}
                    {/if}
                {/if}
            </div>
        </div>
        <!--end::Card-->
    </div>
    <!--end::Container-->
</div>
<!--end::Entry-->