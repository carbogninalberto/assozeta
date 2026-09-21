<script>
    import {onMount} from 'svelte';
    import {apiFetch, replaceUID} from 'utils/ApiMiddleware';
    import {canPerformAction} from 'utils/Permissions';
    import {toast} from 'svelte-sonner';
    import ContentLoader from 'svelte-content-loader';
    import {NotePencil, ChatsCircle} from 'phosphor-svelte';
    import MessageModal from 'components/staffboard/MessageModal.svelte';
    import MessageContent from 'components/staffboard/MessageContent.svelte';
    import AuthorAvatar from 'components/staffboard/AuthorAvatar.svelte';
    import notificationService from 'utils/NotificationService.js';
    import swal from 'sweetalert2';
    import EditButton from 'components/buttons/EditButton.svelte';
    import DeleteButton from 'components/buttons/DeleteButton.svelte';
    import PinButton from 'components/buttons/PinButton.svelte';

    let loading = true;
    let loadError = false;
    let messages = [];
    let showComposer = false;
    let editingMessage = null;
    let refreshQueued = false;
    let disposed = false;
    let pendingAction = null;
    let fetching = false;
    const canRead = canPerformAction('association.communication.messages.read');

    async function fetchMessages(background = false) {
        if (!canRead || disposed) return;
        if (fetching) {
            refreshQueued = true;
            return;
        }
        fetching = true;
        if (!background) loading = true;
        loadError = false;
        try {
            const res = await apiFetch(__bakney.env.API.COMMUNICATIONS.STAFF_BOARD.LIST);
            if (res.error) throw new Error('Unable to load staff board');
            const list = res.response?.data || res.response || [];
            messages = Array.isArray(list) ? list : [];
        } catch {
            loadError = true;
        } finally {
            loading = false;
            fetching = false;
            if (refreshQueued && !disposed) {
                refreshQueued = false;
                await fetchMessages(true);
            }
        }
    }

    async function mutateMessage(url, method, body, successText, onSuccess = () => {}) {
        const res = await apiFetch(url, {
            method,
            ...(body ? {body: JSON.stringify(body)} : {}),
        });
        if (res.error) throw new Error('Unable to save staff board');
        if (successText) toast.success(successText);
        onSuccess();
        await fetchMessages();
    }

    function openComposer(message = null) {
        if (message && !message.is_owner) return;
        editingMessage = message;
        showComposer = true;
    }

    async function togglePin(message) {
        if (!canPerformAction('association.communication.messages.update') || pendingAction) return;
        pendingAction = 'pin';
        try {
            await mutateMessage(
                replaceUID(__bakney.env.API.COMMUNICATIONS.STAFF_BOARD.UPDATE, message.staff_board_message_id),
                'PATCH',
                {pinned: !message.pinned}
            );
        } catch {
            toast.error('Impossibile aggiornare il messaggio. Riprova.');
        } finally {
            pendingAction = null;
        }
    }

    async function deleteMessage(message) {
        if (!canPerformAction('association.communication.messages.delete') || pendingAction) return;
        if (!message.is_owner) return;
        pendingAction = 'delete';
        try {
            const result = await swal.fire({
                text: 'Vuoi eliminare il messaggio dalla bacheca?',
                icon: 'warning',
                showCancelButton: true,
                cancelButtonText: 'Annulla',
                confirmButtonText: 'Elimina',
                reverseButtons: true,
                buttonsStyling: false,
                customClass: {
                    confirmButton: 'btn btn-danger font-weight-bold mx-2',
                    cancelButton: 'btn btn-light-primary font-weight-bold mx-2',
                },
            });
            if (!result.isConfirmed) return;
            await mutateMessage(
                replaceUID(__bakney.env.API.COMMUNICATIONS.STAFF_BOARD.DELETE, message.staff_board_message_id),
                'DELETE',
                null,
                'Messaggio eliminato.'
            );
        } catch {
            toast.error('Impossibile eliminare il messaggio. Riprova.');
        } finally {
            pendingAction = null;
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

    onMount(() => {
        const unsubscribe = canRead ? notificationService.subscribeStaffBoard(() => fetchMessages(true)) : () => {};
        if (canRead) fetchMessages();
        else loading = false;
        return () => {
            disposed = true;
            unsubscribe();
        };
    });
</script>

<div class="d-flex flex-column-fluid font-weight-bold text-dark-50 staff-board">
    <div class="container">
        <div class="card card-custom gutter-b">
            <div class="card-header flex-wrap border-0 p-0">
                <div class="card-title">
                    <h3 class="card-label font-size-h2">
                        Bacheca Staff
                        <span class="d-block text-muted pt-2 font-size-sm"
                            >Messaggi e aggiornamenti per il tuo staff.</span>
                    </h3>
                </div>
                {#if canRead && canPerformAction('association.communication.messages.create')}
                    <div class="card-toolbar ml-auto">
                        <button
                            type="button"
                            class="btn btn-sm btn-primary font-weight-bolder d-flex align-items-center"
                            on:click={() => openComposer()}>
                            <NotePencil size={18} weight="duotone" class="mr-2" /> Nuovo messaggio
                        </button>
                    </div>
                {/if}
            </div>
            <div class="card-body p-0" aria-busy={loading || !!pendingAction}>
                {#if !canRead}
                    <div class="text-center py-10 text-muted" role="status">Permessi insufficienti</div>
                {:else}
                    {#if loading}
                        <div role="status" aria-label="Caricamento bacheca in corso">
                            <ContentLoader
                                width="100%"
                                height="260"
                                backgroundColor="var(--bg-surface-secondary)"
                                foregroundColor="var(--border-color)">
                                <rect x="0" y="15" rx="4" ry="4" width="40%" height="16" />
                                <rect x="0" y="45" rx="4" ry="4" width="100%" height="45" />
                                <rect x="0" y="125" rx="4" ry="4" width="40%" height="16" />
                                <rect x="0" y="155" rx="4" ry="4" width="100%" height="45" />
                            </ContentLoader>
                        </div>
                    {:else if loadError}
                        <div class="text-center py-10" role="alert">
                            <p class="text-muted">Impossibile caricare la bacheca.</p>
                            <button
                                type="button"
                                class="btn btn-sm btn-light-primary font-weight-bold"
                                on:click={() => fetchMessages()}>Riprova</button>
                        </div>
                    {:else if messages.length === 0}
                        <div class="empty-board text-center py-10 px-4" role="status">
                            <span class="empty-icon bg-light-primary text-primary mb-5"
                                ><ChatsCircle size={48} weight="duotone" /></span>
                            <h4 class="font-weight-bolder text-dark mb-3">Uno spazio per il tuo staff</h4>
                            <p class="text-muted mb-5">
                                Condividi novità, foto e promemoria.<br />I messaggi del team appariranno qui.
                            </p>
                            {#if canPerformAction('association.communication.messages.create')}
                                <button
                                    type="button"
                                    class="btn btn-light-primary font-weight-bold"
                                    on:click={() => openComposer()}>Scrivi il primo messaggio</button>
                            {/if}
                        </div>
                    {:else}
                        <div class="message-feed">
                            {#each messages as message (message.staff_board_message_id)}
                                <article
                                    class="message-card"
                                    class:pinned={message.pinned}
                                    aria-label="Messaggio di {message.author_name}">
                                    <div class="message-header">
                                        <div class="d-flex message-identity">
                                            <AuthorAvatar name={message.author_name} image={message.author_avatar} />
                                            <div class="message-meta ml-3">
                                                <span
                                                    class="font-weight-bolder text-dark-75 message-author"
                                                    title={message.author_name}>{message.author_name}</span>
                                                <time
                                                    class="text-muted font-size-sm d-block mt-1"
                                                    datetime={message.created_at}
                                                    >{formatDate(message.created_at)}</time>
                                            </div>
                                        </div>
                                        <div class="d-flex align-items-center message-actions">
                                            {#if canPerformAction('association.communication.messages.update')}
                                                <PinButton
                                                    pinned={message.pinned}
                                                    popover_text={message.pinned
                                                        ? 'Togli dai messaggi in evidenza'
                                                        : 'Metti in evidenza'}
                                                    disabled={!!pendingAction}
                                                    on:open={() => togglePin(message)} />
                                            {/if}
                                            {#if message.is_owner && canPerformAction('association.communication.messages.update')}
                                                <EditButton
                                                    disabled={!!pendingAction}
                                                    on:open={() => openComposer(message)} />
                                            {/if}
                                            {#if message.is_owner && canPerformAction('association.communication.messages.delete')}
                                                <DeleteButton
                                                    disabled={!!pendingAction}
                                                    on:open={() => deleteMessage(message)} />
                                            {/if}
                                        </div>
                                    </div>
                                    <div class="mt-4"><MessageContent {message} /></div>
                                </article>
                            {/each}
                        </div>
                    {/if}
                    <span class="sr-only" role="status">{pendingAction ? 'Operazione in corso...' : ''}</span>
                {/if}
            </div>
        </div>
    </div>
</div>

{#if showComposer}
    <MessageModal
        message={editingMessage}
        on:close={() => (showComposer = false)}
        on:saved={() => fetchMessages(true)} />
{/if}

<style>
    .staff-board,
    .message-meta,
    .message-identity {
        min-width: 0;
    }
    .message-header {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        gap: 1rem;
    }
    .message-author {
        overflow-wrap: anywhere;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
        overflow: hidden;
    }
    .message-identity {
        flex: 1;
    }
    .message-feed {
        display: flex;
        flex-direction: column;
        gap: 1.25rem;
        margin: 1.5rem auto 0;
        max-width: 64rem;
    }
    .message-card {
        padding: 1.75rem;
        border: 1px solid var(--border-color);
        border-radius: 0.85rem;
        background: var(--bg-surface);
    }
    .message-card.pinned {
        border-left: 3px solid var(--primary);
    }
    .message-actions {
        flex-shrink: 0;
    }
    .staff-board :global(button:focus-visible) {
        outline: 2px solid var(--primary) !important;
        outline-offset: 3px;
    }
    @media (max-width: 575px) {
        .message-card {
            padding: 1.25rem;
        }
        .message-header {
            gap: 0.5rem;
        }
        .message-meta {
            margin-left: 0.75rem !important;
        }
    }
    .empty-board {
        background: var(--bg-surface-secondary);
        border-radius: 0.85rem;
        margin-top: 1.5rem;
        min-height: 24rem;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
    }
    .empty-icon {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 7rem;
        height: 7rem;
        border-radius: 50%;
    }
</style>
