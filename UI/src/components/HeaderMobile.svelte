<script>
    import {userData, isUserPanelOpen, isNotificationsPanelOpen, isMobileSidebarOpen, unreadNotificationsCounter} from 'store/stores.js';
    import {Bell, List} from 'phosphor-svelte';
    import {BasicDrawer} from './drawer/index.js';
    import NotificationsDrawer from './NotificationsDrawer.svelte';
    import Sidebar from './Sidebar.svelte';
    import {canPerformAction} from 'utils/Permissions';
    import {oemConfig} from 'store/instanceStore.js';
    userData.useLocalStorage();
</script>

<!--begin::Header Mobile-->
<div
    id="bkn_header_mobile"
    class="header-mobile align-items-center header-mobile-fixed mx-8 rounded-xl shadow-sm"
    style="border: 0px solid var(--border-color);padding-top: env(safe-area-inset-top)">
    <!--begin::Aside Mobile Toggle-->
    <!-- svelte-ignore a11y-click-events-have-key-events -->
    <!-- svelte-ignore a11y-no-static-element-interactions -->
    <span class="menu-icon" on:click={() => $isMobileSidebarOpen = true} style="cursor: pointer;">
        <List size="24" weight="duotone" />
    </span>
    <!--end::Aside Mobile Toggle-->
    <!--begin::Logo-->
    <a href="/" style="position:relative; top:.1rem;left:.6rem;">
        <!-- svelte-ignore a11y-missing-attribute -->
        <img id="logo" class="h-30px" src={$oemConfig?.logo || ''} />
    </a>

    <!--end::Logo-->

    <!--begin::Toolbar-->
    <div class="d-flex align-items-center">
        {#if $oemConfig?.displaySettings?.navbar?.showNotifications && canPerformAction('other.notifications.read')}
            <!--begin::Notifications-->
            <!-- svelte-ignore a11y-click-events-have-key-events -->
            <!-- svelte-ignore a11y-no-static-element-interactions -->
            <div class="topbar-item" on:click={() => $isNotificationsPanelOpen = true} style="cursor: pointer;">
                <div class="btn btn-icon btn-clean btn-dropdown btn-lg pulse pulse-primary">
                    <span class="svg-icon svg-icon-xl svg-icon-primary">
                        <span class="menu-icon">
                            <Bell
                                size="24"
                                class={$unreadNotificationsCounter > 0 ? 'text-dark' : ''}
                                weight="duotone" />
                        </span>
                        {#if $unreadNotificationsCounter > 0}
                            <sup><div class="badge-notification" style="position:relative">
                                {$unreadNotificationsCounter}
                            </div></sup>
                        {/if}
                    </span>
                    {#if $unreadNotificationsCounter > 0}
                        <span class="pulse-ring" />
                    {/if}
                </div>
            </div>
            <!--end::Notifications-->
        {/if}

        <!--begin::User-->
        <!-- svelte-ignore a11y-click-events-have-key-events -->
        <!-- svelte-ignore a11y-no-static-element-interactions -->
        <div class="ml-4" on:click={() => $isUserPanelOpen = true} style="cursor: pointer;">
            <!--begin::Toggle-->
            <div class="topbar-item">
                <div class="btn btn-icon btn-light-primary h-40px w-40px p-0 profile-container">
                    {#if $userData?.avatar_image}
                        <img
                            style="width: 100% !important;height: 100% !important;"
                            src={$userData.avatar_image}
                            class="h-30px align-self-end profile-fill"
                            alt="" />
                    {:else if $userData?.first_name && $userData?.last_name}
                        <span class="symbol-label font-weight-bolder" style="font-size: 1.2rem !important;">
                            {$userData.first_name.charAt(0).toUpperCase()}{$userData.last_name
                                .charAt(0)
                                .toUpperCase()}
                        </span>
                    {/if}
                </div>
            </div>
            <!--end::Toggle-->
        </div>
        <!--end::User-->
    </div>

    <!--end::Toolbar-->
</div>

<!--end::Header Mobile-->

<!--begin::Mobile Sidebar Drawer-->
<BasicDrawer bind:isOpen={$isMobileSidebarOpen} position="left" title="" width="280px">
    <div slot="header" style="display: none;"></div>
    <div slot="content" class="mobile-sidebar-drawer">
        {#if $isMobileSidebarOpen}
            <Sidebar on:navigate={() => $isMobileSidebarOpen = false} />
        {/if}
    </div>
</BasicDrawer>
<!--end::Mobile Sidebar Drawer-->

<NotificationsDrawer bind:isOpen={$isNotificationsPanelOpen} />

<style>
    :global(.drawer-left) {
        border-radius: 0 1.5rem 1.5rem 0 !important;
        width: 280px !important;
        min-width: 280px !important;
        max-width: 280px !important;
    }

    :global(.drawer-left .drawer-header) {
        display: none !important;
    }

    :global(.mobile-sidebar-drawer) {
        height: 100%;
        overflow: hidden;
    }

    :global(.mobile-sidebar-drawer .aside) {
        position: relative !important;
        width: 100% !important;
        height: 100% !important;
        left: 0 !important;
        box-shadow: none !important;
        border: none !important;
    }
</style>
