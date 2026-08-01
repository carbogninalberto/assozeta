<script>
    import BasicDrawer from './drawer/basic-drawer.svelte';
    import {notifications, unreadNotificationsCounter, role} from 'store/stores.js';
    import notificationService from 'utils/NotificationService.js';
    import {push} from 'svelte-spa-router';
    import {toast} from 'svelte-sonner';
    import {Money, Info, Checks, X} from 'phosphor-svelte';
    import {initTooltips, destroyTooltips} from 'shim/tooltip.js';
    import {onMount, onDestroy} from 'svelte';

    export let isOpen = false;
    let headerEl;
    let contentEl;

    const options = {
        weekday: 'long',
        year: 'numeric',
        month: 'long',
        day: 'numeric',
        minute: 'numeric',
        hour: 'numeric',
    };

    function readNotification(notificationId) {
        notificationService.markRead(notificationId);

        $notifications = $notifications.map(n => (n.id === notificationId ? {...n, read: true} : n));
        $unreadNotificationsCounter = Math.max(0, $unreadNotificationsCounter - 1);
    }

    function readAllNotifications() {
        notificationService.markAllRead();

        $notifications = $notifications.map(n => ({...n, read: true}));
        $unreadNotificationsCounter = 0;

        toast.success('Notifiche lette.');
    }

    function handleNotificationClick(notification) {
        readNotification(notification.id);
        isOpen = false;

        switch (notification.type) {
            case 'payment':
                push('/payment/list');
                break;
            case 'subscription':
                if ($role === 'association') push('/members/list');
                else push('/subscription/list');
                break;
            default:
                console.warn(`[notification] type '${notification.type}' not found`);
        }
    }

    onMount(() => {
        initTooltips(headerEl);
        initTooltips(contentEl);
    });

    onDestroy(() => {
        destroyTooltips(headerEl);
        destroyTooltips(contentEl);
    });
</script>

<BasicDrawer bind:isOpen width="400px" position="right">
    <div
        slot="header"
        class="d-flex align-items-center justify-content-between w-100"
        bind:this={headerEl}>
        <h1 class="mb-0 font-weight-boldest">Notifiche</h1>
        <div class="d-flex align-items-center">
            {#if $notifications.length > 0}
                <button
                    class="btn btn-icon btn-xs btn-light-primary mr-3 mb-0 {$unreadNotificationsCounter == 0
                        ? 'disabled-read-all'
                        : ''}"
                    type="button"
                    data-toggle="tooltip"
                    data-placement="bottom"
                    title="Segna tutte come già lette"
                    on:click|preventDefault={readAllNotifications}>
                    <Checks size={14} weight="bold" />
                </button>
            {/if}
            <button
                class="btn btn-icon btn-xs rounded-circle close btn-secondary mb-0 font-weight-boldest p-0"
                type="button"
                on:click={() => (isOpen = false)}>
                <X size="14" weight="bold" />
            </button>
        </div>
    </div>

    <div slot="content" class="notifications-content" bind:this={contentEl}>
        <div
            class="card-body p-0 pt-0 pb-0 notifications-list"
            data-scroll="true"
            max-data-height="400px">
            {#if $notifications.length > 0}
                {#each $notifications as notification}
                    <!--begin::Item-->
                    <div
                        class="m-3 bg-gray-100 d-flex align-items-center p-5 rounded notification"
                        style={notification.read ? '' : 'background: var(--light-primary) !important;'}
                        on:click|preventDefault={() => handleNotificationClick(notification)}>
                        <!--begin::Icon-->
                        <div class="d-flex flex-center position-relative ml-4 mr-6 ml-lg-6 mr-lg-10">
                            <span class="svg-icon svg-icon-4x svg-icon-primary position-absolute opacity-10">
                                <svg xmlns="http://www.w3.org/2000/svg" width="40px" height="40px" viewBox="0 0 70 70" fill="none">
                                    <g stroke="none" stroke-width="1" fill-rule="evenodd">
                                        <path
                                            d="M28 4.04145C32.3316 1.54059 37.6684 1.54059 42 4.04145L58.3109 13.4585C62.6425 15.9594 65.3109 20.5812 65.3109 25.5829V44.4171C65.3109 49.4188 62.6425 54.0406 58.3109 56.5415L42 65.9585C37.6684 68.4594 32.3316 68.4594 28 65.9585L11.6891 56.5415C7.3575 54.0406 4.68911 49.4188 4.68911 44.4171V25.5829C4.68911 20.5812 7.3575 15.9594 11.6891 13.4585L28 4.04145Z"
                                            fill="#000000" />
                                    </g>
                                </svg>
                            </span>

                            <span class="svg-icon svg-icon-lg svg-icon-primary position-absolute">
                                {#if notification.type == 'payment'}
                                    <Money size={24} weight="duotone" class="text-primary" />
                                {:else}
                                    <Info size={24} weight="duotone" class="text-primary" />
                                {/if}
                            </span>
                        </div>
                        <!--end::Icon-->

                        <!--begin::Description-->
                        <div class="ml-1">
                            <p class="m-0 text-dark-50 font-weight-bold" style="font-size: 0.8rem;">
                                {new Date(notification.date).toLocaleString('it-IT', options)}
                            </p>
                            <p class="m-0 {notification.read ? 'text-dark-50' : 'text-dark-75'} font-weight-bold">
                                {notification.msg}
                            </p>
                        </div>
                        {#if !notification.read}
                            <div class="ml-1">
                                <svg xmlns="http://www.w3.org/2000/svg" width="20px" height="20px" viewBox="0 0 20 20" fill="none">
                                    <g stroke="none" stroke-width="1" fill="none" fill-rule="evenodd">
                                        <circle fill="#351DC2" cx="10" cy="10" r="6" />
                                    </g>
                                </svg>
                            </div>
                        {/if}
                        <!--end::Description-->
                    </div>
                    <!--end::Item-->
                {/each}
            {:else}
                <div class="p-5 pb-8">
                    <span class="font-size-h6 text-weight-bolder">Nessuna notifica recente da mostrare.</span>
                </div>
            {/if}
        </div>
    </div>
</BasicDrawer>

<style>
    .notifications-content {
        display: flex;
        flex-direction: column;
        height: calc(100% - 5rem);
    }
    .notifications-list {
        flex: 1 1 auto;
        min-height: 0;
        overflow-y: auto;
    }
</style>
