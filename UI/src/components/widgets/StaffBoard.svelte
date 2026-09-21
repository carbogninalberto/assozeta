<script>
    import {PushPin, ChatsCircle, Image} from 'phosphor-svelte';
    import AuthorAvatar from 'components/staffboard/AuthorAvatar.svelte';
    import notificationService from 'utils/NotificationService.js';
    import {onMount} from 'svelte';
    import {link} from 'svelte-spa-router';
    import moment from 'moment';
    import {apiFetch} from 'utils/ApiMiddleware';
    import {canPerformAction} from 'utils/Permissions.js';

    let messages = [];
    let loading = true;
    let failed = false;

    // show the widget only to users who can read the staff board;
    // keep the container card even without permission so layout does not break
    const canRead = canPerformAction('association.communication.messages.read');

    let fetching = false;
    let refreshQueued = false;
    let disposed = false;

    async function fetchData(background = false) {
        if (!canRead || disposed) return;
        if (fetching) {
            refreshQueued = true;
            return;
        }
        fetching = true;
        if (!background) loading = true;
        failed = false;
        try {
            const res = await apiFetch(__bakney.env.API.COMMUNICATIONS.STAFF_BOARD.LIST, {method: 'GET'});
            if (res.error) throw new Error('Unable to load staff board');
            const list = res.response?.data || res.response || [];
            messages = Array.isArray(list) ? list : [];
        } catch {
            failed = true;
        } finally {
            loading = false;
            fetching = false;
            if (refreshQueued && !disposed) {
                refreshQueued = false;
                await fetchData(true);
            }
        }
    }

    onMount(() => {
        const unsubscribe = canRead ? notificationService.subscribeStaffBoard(() => fetchData(true)) : () => {};
        if (canRead) fetchData();
        else loading = false;
        return () => {
            disposed = true;
            unsubscribe();
        };
    });
</script>

<div
    class="card card-widget card-custom rounded-xl overflow-hidden bg-white card-stretch dashboard-widget staff-board-widget">
    <div class="border-0 pt-6 mb-0 px-8">
        <h3 class="card-title align-items-start flex-column mb-0 text-center">
            <span class="card-label font-weight-bolder font-size-h4">Bacheca Staff</span>
        </h3>
    </div>
    <div class="card-body d-flex flex-column mx-0 mt-3 mb-0 pb-3 py-0 px-4 widget-body" aria-busy={loading}>
        {#if !canRead}
            <div class="widget-state text-center text-muted" role="status">
                <PushPin size={40} class="mb-3" weight="duotone" />
                <span class="font-size-sm font-weight-bolder">Permessi insufficienti</span>
            </div>
        {:else if loading}
            <div class="widget-state" role="status">
                <div class="spinner-border text-primary" aria-hidden="true" />
                <span class="sr-only">Caricamento bacheca in corso</span>
            </div>
        {:else if failed}
            <div class="widget-state text-center" role="alert">
                <p class="text-muted font-size-sm">Impossibile caricare la bacheca.</p>
                <button type="button" class="btn btn-sm btn-light-primary font-weight-bold" on:click={() => fetchData()}
                    >Riprova</button>
            </div>
        {:else}
            {#if messages.length === 0}
                <div class="widget-state text-center text-muted" role="status">
                    <ChatsCircle size={40} class="mb-2 text-primary" weight="duotone" />
                    <span class="font-size-sm font-weight-bolder text-dark">Il tuo team, sempre aggiornato</span>
                    <span class="font-size-xs mt-1">Novità, foto e promemoria in un solo posto.</span>
                </div>
            {:else}
                <!-- Keyboard users must be able to scroll the message preview region. -->
                <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
                <div class="widget-messages" tabindex="0" role="region" aria-label="Ultimi messaggi dello staff">
                    {#each messages.slice(0, 5) as message (message.staff_board_message_id)}
                        <article class="border-bottom py-2" aria-label="Messaggio di {message.author_name}">
                            <div class="preview-header">
                                <AuthorAvatar small name={message.author_name} image={message.author_avatar} />
                                <span class="font-weight-bolder text-primary font-size-sm preview-author">
                                    {#if message.pinned}<PushPin size={14} weight="fill" /><span class="sr-only"
                                            >In evidenza:
                                        </span>{/if}
                                    {message.author_name}
                                </span>
                                <time
                                    class="font-size-xs text-muted"
                                    datetime={message.created_at}
                                    title={moment(message.created_at).format('DD/MM/YYYY HH:mm')}>
                                    {moment(message.created_at).format('DD/MM HH:mm')}
                                </time>
                            </div>
                            <p class="font-size-sm text-dark-75 mb-0 message-preview">
                                {#if message.content}{message.content}{:else}<Image size={14} /> Immagine allegata{/if}
                            </p>
                        </article>
                    {/each}
                </div>
            {/if}
            <div class="d-flex justify-content-center pt-3 widget-footer">
                <a
                    href="/communication/staff-board"
                    use:link
                    class="btn btn-sm btn-outline-secondary font-weight-boldest">Apri bacheca</a>
            </div>
        {/if}
    </div>
</div>

<style>
    .staff-board-widget,
    .widget-body,
    .preview-author {
        min-width: 0;
    }
    .widget-body,
    .widget-messages {
        min-height: 0;
    }
    .widget-messages {
        overflow-y: auto;
        flex: 1;
        padding: 0 0.25rem;
    }
    .widget-state {
        display: flex;
        flex: 1;
        flex-direction: column;
        align-items: center;
        justify-content: center;
    }
    .widget-footer {
        flex-shrink: 0;
    }
    .preview-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 0.5rem;
    }
    .preview-author {
        flex: 1;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    time {
        flex-shrink: 0;
        white-space: nowrap;
    }
    .message-preview {
        display: -webkit-box;
        -webkit-box-orient: vertical;
        -webkit-line-clamp: 2;
        overflow: hidden;
        overflow-wrap: anywhere;
        white-space: pre-wrap;
    }
    .staff-board-widget :global(a:focus-visible),
    button:focus-visible,
    .widget-messages:focus-visible {
        outline: 2px solid var(--primary) !important;
        outline-offset: -2px;
    }
</style>
