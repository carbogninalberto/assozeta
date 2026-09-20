<script>
    import {PushPin} from 'phosphor-svelte';
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

    async function fetchData() {
        loading = true;
        failed = false;
        const res = await apiFetch(`${__bakney.env.API.COMMUNICATIONS.STAFF_BOARD.LIST}`, {
            method: 'GET',
        });
        loading = false;
        failed = !!res.error;
        if (!res.error) {
            // the endpoint wraps the list in {data: [...]}; be defensive about the shape
            const list = res.response?.data || res.response || [];
            messages = Array.isArray(list) ? list : [];
        }
    }

    onMount(async () => {
        if (canRead) await fetchData();
        else loading = false;
    });
</script>

<div
    class="card card-widget card-custom rounded-xl overflow-hidden bg-white card-stretch dashboard-widget"
    style="max-height: fit-content;">
    <!--begin::Header-->
    <div class="border-0 pt-6 mb-0 px-8">
        <h3 class="card-title align-items-start flex-column mb-0 text-center">
            <span class="card-label font-weight-bolder font-size-h4">Bacheca Staff</span>
        </h3>
    </div>
    <!--end::Header-->
    <!--begin::Body-->
    <div class="card-body d-flex flex-column mx-0 mt-3 mb-0 pb-3 py-0 px-1">
        {#if !canRead}
            <div
                class="d-flex flex-column align-items-center justify-content-center m-auto"
                style="margin: auto !important;">
                <PushPin size={60} class="my-4" weight="duotone" />
                <span class="font-size-lg font-weight-bolder text-muted">Permessi insufficienti</span>
            </div>
        {:else if loading}
            <div
                class="d-flex flex-column align-items-center justify-content-center m-auto"
                style="margin: auto !important;">
                <div class="spinner-border text-primary" role="status"></div>
            </div>
        {:else if failed}
            <div class="text-center p-4" role="alert">
                <p>Impossibile caricare la bacheca.</p>
                <button type="button" class="btn btn-sm btn-light-primary" on:click={fetchData}>Riprova</button>
            </div>
        {:else if messages.length === 0}
            <div
                class="d-flex flex-column align-items-center justify-content-center m-auto"
                style="margin: auto !important;">
                <PushPin size={60} class="my-4" weight="duotone" />
                <span class="font-size-lg font-weight-bolder text-muted">Nessun messaggio in bacheca</span>
                <a
                    href="/communication/staff-board"
                    use:link
                    class="btn btn-sm btn-outline-secondary font-weight-boldest mt-4">Vai alla bacheca</a>
            </div>
        {:else}
            <div class="d-flex flex-column w-100">
                {#each messages.slice(0, 5) as message (message.staff_board_message_id)}
                    <div class="d-flex flex-column border-bottom border-light py-2 px-4">
                        <div class="d-flex align-items-center justify-content-between">
                            <span class="font-weight-boldest text-primary font-size-md text-truncate">
                                {#if message.pinned}<PushPin size={14} weight="fill" class="mr-1" />{/if}
                                {message.author_name}
                            </span>
                            <span class="font-size-xs text-muted">
                                {moment(message.created_at).format('DD/MM HH:mm')}
                            </span>
                        </div>
                        <div class="d-flex flex-column align-items-start">
                            <span class="font-size-sm text-dark-75 text-break">{message.content}</span>
                        </div>
                    </div>
                {/each}
                <div class="d-flex justify-content-center mt-3">
                    <a
                        href="/communication/staff-board"
                        use:link
                        class="btn btn-sm btn-outline-secondary font-weight-boldest">Apri bacheca</a>
                </div>
            </div>
        {/if}
    </div>
    <!--end::Body-->
</div>
