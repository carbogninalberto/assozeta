<script>
    import {BasicDrawer} from './drawer/index.js';
    import {
        userData,
        sessionToken,
        notifications,
        unreadNotificationsCounter,
        role,
        sidebarCollapsed,
        isUserPanelOpen,
        isNotificationsPanelOpen,
    } from 'store/stores.js';
    import {push} from 'svelte-spa-router';
    import {
        AddressBook,
        Bell,
        Book,
        CaretDoubleLeft,
        CaretDoubleRight,
        Gift,
        HandHeart,
        IdentificationCard,
        PlusCircle,
        Question,
        Robot,
        UserFocus,
        Volleyball,
        Wallet,
    } from 'phosphor-svelte';
    import {isAgentOpen, agentProcessing} from 'store/agentStore.js';
    import NotificationsDrawer from './NotificationsDrawer.svelte';
    import {canPerformAction} from 'utils/Permissions';
    import BasicDropdown from 'components/dropdowns/basic-dropdown.svelte';
    import {oemConfig} from 'store/instanceStore.js';

    userData.useLocalStorage();
    sessionToken.useLocalStorage();
    role.useLocalStorage();

    function signOut() {
        localStorage.clear();
        // Clear all cookies
        document.cookie.split(';').forEach(cookie => {
            const name = cookie.split('=')[0].trim();
            document.cookie = `${name}=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/`;
        });
        // Reset to light mode
        document.documentElement.removeAttribute('data-theme');
        push('/login');
    }

    const allQuickAddItems = [
        {id: 'persona', label: 'Anagrafica', icon: IdentificationCard, permission: 'association.members.create'},
        {id: 'subscription', label: 'Iscrizione', icon: Book, permission: 'association.members.create'},
        {id: 'course', label: 'Corso', icon: Volleyball, permission: 'association.courses.create'},
        {id: 'payment', label: 'Pagamento', icon: Wallet, permission: 'bookeeping.payments.create'},
        {id: 'supplier', label: 'Fornitore', icon: AddressBook, permission: 'bookeeping.management.suppliers.create'},
        {id: 'instructor', label: 'Istruttore', icon: UserFocus, permission: 'association.instructor.create'},
    ];

    $: quickAddItems = allQuickAddItems.filter(item => canPerformAction(item.permission));

    let quickAddInstance;

    function destroyQuickAdd() {
        if (quickAddInstance) {
            quickAddInstance.$destroy();
            quickAddInstance = null;
        }
    }

    async function openQuickAdd(event) {
        const item = event.detail;
        destroyQuickAdd();

        switch (item.id) {
            case 'persona':
                const {default: AddPersonaDrawer} = await import(
                    'routes/association/personas/detail/AddPersonaDrawer.svelte'
                );
                quickAddInstance = new AddPersonaDrawer({
                    target: document.getElementById('drawer-elements'),
                    props: {title: 'Nuova Anagrafica'},
                });
                quickAddInstance.$on('close', () => {
                    push('/personas/list');
                    destroyQuickAdd();
                });
                break;
            case 'subscription':
                const {default: AddMemberDrawer} = await import(
                    'routes/association/Members/add/AddMemberDrawer.svelte'
                );
                quickAddInstance = new AddMemberDrawer({
                    target: document.getElementById('drawer-elements'),
                    props: {title: 'Nuova Iscrizione'},
                });
                quickAddInstance.$on('created', () => {
                    push('/members/list');
                    destroyQuickAdd();
                });
                quickAddInstance.$on('close', () => {
                    destroyQuickAdd();
                });
                break;
            case 'course':
                const {default: AddCourseDrawer} = await import(
                    'routes/association/course/add/AddCourseDrawer.svelte'
                );
                quickAddInstance = new AddCourseDrawer({
                    target: document.getElementById('drawer-elements'),
                    props: {title: 'Nuovo Corso'},
                });
                quickAddInstance.$on('close', () => {
                    push('/course/list');
                    destroyQuickAdd();
                });
                break;
            case 'payment':
                const {default: AddEditModal} = await import(
                    'routes/accounting/payment/modals/AddEditModal.svelte'
                );
                quickAddInstance = new AddEditModal({
                    target: document.getElementById('portal-elements-foreground'),
                    props: {show: true, data: {type: 'cash', expense: false, meta_payment_categories: []}},
                });
                quickAddInstance.$on('update', () => {
                    push('/payment/list');
                    destroyQuickAdd();
                });
                break;
            case 'supplier':
                const {default: SupplierDrawer} = await import(
                    'routes/accounting/suppliers-and-customers/SupplierDrawer.svelte'
                );
                quickAddInstance = new SupplierDrawer({
                    target: document.getElementById('drawer-elements'),
                    props: {title: 'Nuovo Fornitore'},
                });
                quickAddInstance.$on('close', () => {
                    push('/suppliers-and-customers/list');
                    destroyQuickAdd();
                });
                break;
            case 'instructor':
                const {default: AddInstructorDrawer} = await import(
                    'routes/association/course/instructor/add/AddInstructorDrawer.svelte'
                );
                quickAddInstance = new AddInstructorDrawer({
                    target: document.getElementById('drawer-elements'),
                    props: {title: 'Nuovo Istruttore'},
                });
                quickAddInstance.$on('created', () => {
                    push('/course/instructor/list');
                    destroyQuickAdd();
                });
                quickAddInstance.$on('close', () => {
                    destroyQuickAdd();
                });
                break;
        }
    }
</script>

<!--begin::Header-->
<!-- svelte-ignore a11y-no-static-element-interactions -->
<div
    id="bkn_header"
    class="header header-fixed"
    collapsed={$sidebarCollapsed}
    style="padding-top: env(safe-area-inset-top);">
    <!-- svelte-ignore a11y-click-events-have-key-events -->
    <!-- svelte-ignore a11y-no-static-element-interactions -->
    <!-- svelte-ignore a11y-missing-attribute -->
    <a
        class="btn btn-white btn-sm btn-icon font-weight-boldest border text-dark-25 text-hover-dark d-none d-md-flex"
        style="margin: auto; left: -15px !important; display: flex; position: relative;"
        on:click={() => {
            // apply to .aside the collapsed="true" or collapsed="false" attribute
            $sidebarCollapsed = !$sidebarCollapsed;
        }}>
        {#if $sidebarCollapsed}
            <CaretDoubleRight size={18} weight="bold" />
        {:else}
            <CaretDoubleLeft size={18} weight="bold" />
        {/if}
    </a>
    <!--begin::Container-->
    <div class="container-fluid d-flex align-items-stretch justify-content-between">
        <!--begin::Header Menu Wrapper-->
        <div
            class="header-menu-wrapper header-menu-wrapper-left d-none d-lg-flex align-items-center gap-2"
            id="bkn_header_menu_wrapper"
            style=" width: 200%; margin: auto;">
            {#if $role !== 'athlete' && quickAddItems.length > 0}
                <div class="d-flex align-items-center h-100">
                        <BasicDropdown
                            variant="clean"
                            size="sm"
                            buttonClass="px-2 py-2"
                            items={quickAddItems}
                            on:itemClick={openQuickAdd}>
                        <span slot="button-content">
                            <PlusCircle size={18} weight="bold" />
                        </span>
                    </BasicDropdown>
                </div>
            {/if}

            {#if $role === 'association' && canPerformAction('association.report.read')}
                <!-- svelte-ignore a11y-click-events-have-key-events -->
                <div
                    class="topbar-item agent-toggle-btn"
                    class:agent-toggle-active={$isAgentOpen}
                    on:click={() => ($isAgentOpen = !$isAgentOpen)}
                    title="Agente AI">
                    <div class="btn btn-icon btn-clean btn-sm position-relative">
                        <Robot size={20} weight={$isAgentOpen ? 'fill' : 'duotone'} />
                        {#if $agentProcessing}
                            <span class="agent-processing-dot" />
                        {/if}
                    </div>
                </div>
            {/if}
        </div>
        <!--end::Header Menu Wrapper-->

        <!--begin::Topbar-->
        <div class="topbar">
            <!-- svelte-ignore a11y-click-events-have-key-events -->
            {#if $role !== 'athlete' && $oemConfig?.manualUrl && $oemConfig?.displaySettings?.sidebar?.showManual}
                <!-- svelte-ignore a11y-click-events-have-key-events -->
                <div
                    class="topbar-item"
                    on:click={() => window.open($oemConfig.manualUrl, '_blank')}
                    data-offset="10px,0px">
                    <div class="btn btn-icon btn-clean btn-dropdown btn-lg pulse pulse-light-primary">
                        <span class="svg-icon svg-icon-xl svg-icon-primary">
                            <span class="menu-icon">
                                <Question size="24" weight="duotone" />
                            </span>
                        </span>
                        <span class="pulse-ring" />
                    </div>
                </div>
            {/if}

            {#if $oemConfig?.displaySettings?.navbar?.showNotifications && canPerformAction('other.notifications.read')}
                <!--begin::Notifications-->
                <!-- svelte-ignore a11y-click-events-have-key-events -->
                <!-- svelte-ignore a11y-no-static-element-interactions -->
                <div
                    class="topbar-item mr-1"
                    style="cursor: pointer;"
                    on:click={() => ($isNotificationsPanelOpen = true)}
                    data-offset="10px,0px">
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
            <div class="ml-4 my-auto" on:click={() => ($isUserPanelOpen = true)} style="cursor: pointer;">
                <!--begin::Toggle-->
                <div class="topbar-item">
                    <div class="btn btn-icon btn-light-primary h-40px w-40px p-0 profile-container">
                        {#if $userData?.avatar_image != null}
                            <img
                                style="width: 100% !important;height: 100% !important;"
                                src={$userData?.avatar_image}
                                class="h-30px align-self-end profile-fill"
                                alt="" />
                        {:else if $userData?.first_name && $userData?.last_name}
                            <span class="symbol-label font-weight-bolder" style="font-size: 1.2rem !important;">
                                {$userData?.first_name?.charAt(0)?.toUpperCase()}{$userData?.last_name
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

        <!--end::Topbar-->
    </div>

    <!--end::Container-->
</div>

<!--end::Header-->

<BasicDrawer bind:isOpen={$isUserPanelOpen} title="Profilo" width="400px">
    <div slot="content" class="p-8">
        <!--begin::Header-->
        <div class="d-flex align-items-center mt-2">
            <div class="d-flex flex-column fill-available-space-width">
                <span class="font-weight-bold font-size-h3 text-dark-75"
                    >{$userData.first_name} {$userData.last_name}</span>
                <div class="navi mt-5">
                    <span class="navi-link p-0 pb-2">
                        <span class="navi-icon mr-1">
                            <span class="svg-icon svg-icon-lg svg-icon-primary">
                                <svg
                                    xmlns="http://www.w3.org/2000/svg"
                                    xmlns:xlink="http://www.w3.org/1999/xlink"
                                    width="24px"
                                    height="24px"
                                    viewBox="0 0 24 24"
                                    version="1.1">
                                    <g stroke="none" stroke-width="1" fill="none" fill-rule="evenodd">
                                        <rect x="0" y="0" width="24" height="24" />
                                        <path
                                            d="M11.575,21.2 C6.175,21.2 2.85,17.4 2.85,12.575 C2.85,6.875 7.375,3.05 12.525,3.05 C17.45,3.05 21.125,6.075 21.125,10.85 C21.125,15.2 18.825,16.925 16.525,16.925 C15.4,16.925 14.475,16.4 14.075,15.65 C13.3,16.4 12.125,16.875 11,16.875 C8.25,16.875 6.85,14.925 6.85,12.575 C6.85,9.55 9.05,7.1 12.275,7.1 C13.2,7.1 13.95,7.35 14.525,7.775 L14.625,7.35 L17,7.35 L15.825,12.85 C15.6,13.95 15.85,14.825 16.925,14.825 C18.25,14.825 19.025,13.725 19.025,10.8 C19.025,6.9 15.95,5.075 12.5,5.075 C8.625,5.075 5.05,7.75 5.05,12.575 C5.05,16.525 7.575,19.1 11.575,19.1 C13.075,19.1 14.625,18.775 15.975,18.075 L16.8,20.1 C15.25,20.8 13.2,21.2 11.575,21.2 Z M11.4,14.525 C12.05,14.525 12.7,14.35 13.225,13.825 L14.025,10.125 C13.575,9.65 12.925,9.425 12.3,9.425 C10.65,9.425 9.45,10.7 9.45,12.375 C9.45,13.675 10.075,14.525 11.4,14.525 Z"
                                            fill="#000000" />
                                    </g>
                                </svg>
                            </span>
                        </span>
                        <span class="navi-text text-muted font-weight-bolder" style="text-wrap: break-word;"
                            >{$userData.username}</span>
                    </span>
                </div>
                <div class="navi mt-1">
                    <span class="navi-link p-0 pb-2">
                        <span class="navi-icon mr-1">
                            <span class="svg-icon svg-icon-lg svg-icon-primary">
                                <svg
                                    xmlns="http://www.w3.org/2000/svg"
                                    xmlns:xlink="http://www.w3.org/1999/xlink"
                                    width="24px"
                                    height="24px"
                                    viewBox="0 0 24 24"
                                    version="1.1">
                                    <g stroke="none" stroke-width="1" fill="none" fill-rule="evenodd">
                                        <rect x="0" y="0" width="24" height="24" />
                                        <path
                                            d="M4,9.67471899 L10.880262,13.6470401 C10.9543486,13.689814 11.0320333,13.7207107 11.1111111,13.740321 L11.1111111,21.4444444 L4.49070127,17.526473 C4.18655139,17.3464765 4,17.0193034 4,16.6658832 L4,9.67471899 Z M20,9.56911707 L20,16.6658832 C20,17.0193034 19.8134486,17.3464765 19.5092987,17.526473 L12.8888889,21.4444444 L12.8888889,13.6728275 C12.9050191,13.6647696 12.9210067,13.6561758 12.9368301,13.6470401 L20,9.56911707 Z"
                                            fill="#000000" />
                                        <path
                                            d="M4.21611835,7.74669402 C4.30015839,7.64056877 4.40623188,7.55087574 4.5299008,7.48500698 L11.5299008,3.75665466 C11.8237589,3.60013944 12.1762411,3.60013944 12.4700992,3.75665466 L19.4700992,7.48500698 C19.5654307,7.53578262 19.6503066,7.60071528 19.7226939,7.67641889 L12.0479413,12.1074394 C11.9974761,12.1365754 11.9509488,12.1699127 11.9085461,12.2067543 C11.8661433,12.1699127 11.819616,12.1365754 11.7691509,12.1074394 L4.21611835,7.74669402 Z"
                                            fill="#000000"
                                            opacity="0.3" />
                                    </g>
                                </svg>
                            </span>
                        </span>
                        <span class="navi-text text-muted font-weight-bolder" style="text-wrap: break-word;"
                            >{$role == 'association' ? 'Associazione Sportiva' : 'Atleta'}</span>
                    </span>
                </div>
                <div class="navi mt-1">
                    <a href="mailto:{$userData.email}" class="navi-item">
                        <span class="navi-link p-0 pb-2">
                            <span class="navi-icon mr-1">
                                <span class="svg-icon svg-icon-lg svg-icon-primary">
                                    <svg
                                        xmlns="http://www.w3.org/2000/svg"
                                        xmlns:xlink="http://www.w3.org/1999/xlink"
                                        width="24px"
                                        height="24px"
                                        viewBox="0 0 24 24"
                                        version="1.1">
                                        <g stroke="none" stroke-width="1" fill="none" fill-rule="evenodd">
                                            <rect x="0" y="0" width="24" height="24" />
                                            <path
                                                d="M21,12.0829584 C20.6747915,12.0283988 20.3407122,12 20,12 C16.6862915,12 14,14.6862915 14,18 C14,18.3407122 14.0283988,18.6747915 14.0829584,19 L5,19 C3.8954305,19 3,18.1045695 3,17 L3,8 C3,6.8954305 3.8954305,6 5,6 L19,6 C20.1045695,6 21,6.8954305 21,8 L21,12.0829584 Z M18.1444251,7.83964668 L12,11.1481833 L5.85557487,7.83964668 C5.4908718,7.6432681 5.03602525,7.77972206 4.83964668,8.14442513 C4.6432681,8.5091282 4.77972206,8.96397475 5.14442513,9.16035332 L11.6444251,12.6603533 C11.8664074,12.7798822 12.1335926,12.7798822 12.3555749,12.6603533 L18.8555749,9.16035332 C19.2202779,8.96397475 19.3567319,8.5091282 19.1603533,8.14442513 C18.9639747,7.77972206 18.5091282,7.6432681 18.1444251,7.83964668 Z"
                                                fill="#000000" />
                                            <circle fill="#000000" opacity="0.3" cx="19.5" cy="17.5" r="2.5" />
                                        </g>
                                    </svg>
                                </span>
                            </span>
                            <span
                                class="navi-text text-muted font-weight-bolder text-hover-primary"
                                style="text-wrap: break-word;">{$userData.email}</span>
                        </span>
                    </a>
                </div>
            </div>
        </div>
        <!--end::Header-->

        <!--begin::Separator-->
        <div class="separator separator-dashed mt-8 mb-5" />
        <!--end::Separator-->

        <!--begin::Nav-->
        <div class="navi navi-spacer-x-0 p-0">
            <!--begin::Item-->
            <a href="/#/profile" class="navi-item" on:click={() => ($isUserPanelOpen = false)}>
                <div class="navi-link">
                    <div class="symbol symbol-40 bg-light mr-3">
                        <div class="symbol-label">
                            <span class="svg-icon svg-icon-md svg-icon-danger">
                                <svg
                                    xmlns="http://www.w3.org/2000/svg"
                                    xmlns:xlink="http://www.w3.org/1999/xlink"
                                    width="24px"
                                    height="24px"
                                    viewBox="0 0 24 24"
                                    version="1.1">
                                    <g stroke="none" stroke-width="1" fill="none" fill-rule="evenodd">
                                        <rect x="0" y="0" width="24" height="24" />
                                        <path
                                            d="M18,2 L20,2 C21.6568542,2 23,3.34314575 23,5 L23,19 C23,20.6568542 21.6568542,22 20,22 L18,22 L18,2 Z"
                                            fill="#000000"
                                            opacity="0.3" />
                                        <path
                                            d="M5,2 L17,2 C18.6568542,2 20,3.34314575 20,5 L20,19 C20,20.6568542 18.6568542,22 17,22 L5,22 C4.44771525,22 4,21.5522847 4,21 L4,3 C4,2.44771525 4.44771525,2 5,2 Z M12,11 C13.1045695,11 14,10.1045695 14,9 C14,7.8954305 13.1045695,7 12,7 C10.8954305,7 10,7.8954305 10,9 C10,10.1045695 10.8954305,11 12,11 Z M7.00036205,16.4995035 C6.98863236,16.6619875 7.26484009,17 7.4041679,17 C11.463736,17 14.5228466,17 16.5815,17 C16.9988413,17 17.0053266,16.6221713 16.9988413,16.5 C16.8360465,13.4332455 14.6506758,12 11.9907452,12 C9.36772908,12 7.21569918,13.5165724 7.00036205,16.4995035 Z"
                                            fill="#000000" />
                                    </g>
                                </svg>
                            </span>
                        </div>
                    </div>
                    <div class="navi-text">
                        <div class="font-weight-bold">Il mio account</div>
                        <div class="text-muted">Informazioni Profilo</div>
                    </div>
                </div>
            </a>
            <!--end:Item-->

            <!--begin::Item-->
            <span class="navi-item mt-2">
                <span class="navi-link">
                    <!-- svelte-ignore a11y-missing-attribute -->
                    <a on:click={signOut} class="btn btn-sm btn-primary font-weight-bolder py-3 px-6">Esci</a>
                </span>
            </span>
            <!--end:Item-->
        </div>
        <!--end::Nav-->

        <!--begin::Separator-->
        <div class="separator separator-dashed my-7" />

        <div>
            {#if $oemConfig?.displaySettings?.sidebar?.showPrivacyPolicy && $oemConfig?.privacyPolicyUrl}
                <a
                    href={$oemConfig.privacyPolicyUrl}
                    style="font-size:9px"
                    class="m-0 p-0 text-primary font-weight-bolder font-size-xs"
                    title="Privacy Policy ">Privacy Policy</a>
            {/if}
            {#if $oemConfig?.displaySettings?.sidebar?.showTermsOfService && $oemConfig?.termsOfServiceUrl}
                <span class="pl-2 pr-2 text-primary font-size-xs" style="font-size: 9px;">|</span>
                <a
                    href={$oemConfig.termsOfServiceUrl}
                    style="font-size:9px"
                    class="m-0 p-0 text-primary font-weight-bolder font-size-xs"
                    title="Termini e Condizioni ">Termini e Condizioni</a>
            {/if}
        </div>
        <!--end::Separator-->

    </div>
</BasicDrawer>

<NotificationsDrawer bind:isOpen={$isNotificationsPanelOpen} />

<style>
    .agent-toggle-btn {
        cursor: pointer;
    }
    .agent-toggle-active .btn {
        color: var(--primary, #351dc2) !important;
        background: color-mix(in srgb, var(--primary, #351dc2) 10%, transparent) !important;
        border-radius: 0.42rem;
    }
    .agent-processing-dot {
        position: absolute;
        top: 0.15rem;
        right: 0.15rem;
        width: 0.5rem;
        height: 0.5rem;
        border-radius: 50%;
        background: var(--success, #08d1ad);
        animation: agentPulse 1.5s infinite;
    }
    @keyframes agentPulse {
        0%,
        100% {
            opacity: 1;
        }
        50% {
            opacity: 0.35;
        }
    }
</style>
