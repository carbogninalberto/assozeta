<script>
    import {Capacitor} from '@capacitor/core';
    import {slide} from 'svelte/transition';
    import {push, replace} from 'svelte-spa-router';
    import {
        currentPage,
        subPage,
        role,
        isExpired,
        billingData,
        permissions,
        userData,
        sidebarCollapsed,
        selectedGroup,
    } from 'store/stores.js';
    import {collapseSidebar} from 'utils/Functions.js';
    import {onMount} from 'svelte';
    import {apiFetch} from 'utils/ApiMiddleware.js';
    import {setPermissions, canPerformAction, isFreePlan} from 'utils/Permissions.js';
    import {
        Stack,
        Users,
        Book,
        Scan,
        Rows,
        Calendar,
        Volleyball,
        Ticket,
        ChartPieSlice,
        Wallet,
        Files,
        FileText,
        Percent,
        ChartBar,
        UserList,
        Gear,
        Info,
        UserFocus,
        Bank,
        File,
        Coins,
        Trophy,
        Confetti,
        TreeStructure,
        GearSix,
        PaperPlaneTilt,
        NotePencil,
        Browsers,
        Tent,
        Folders,
        CreditCard,
        IdentificationCard,
        AddressBook,
        ClipboardText,
    } from 'phosphor-svelte';
    import Portal from 'svelte-portal';
    import {toast} from 'svelte-sonner';
    import SmartSelect from 'components/formBuilder/preview-blocks/smart-select-input.svelte';
    import {ChevronRight, Ellipsis} from 'lucide-svelte';
    import {oemConfig} from 'store/instanceStore.js';

    role.useLocalStorage();
    isExpired.useLocalStorage();
    billingData.useLocalStorage();
    permissions.useLocalStorage();
    userData.useLocalStorage();
    selectedGroup.useLocalStorage();

    export let newUsers = 0;
    export let newInvoices = 0;
    let visibleLogo = true;
    const IS_BETA = __bakney.IS_BETA || false;

    let planType = '🚀 Piano di Valutazione';
    let showPlan = false;
    let daysLeft = -1;
    let brandLogo;
    let prevPermissions = [];

    // Native Svelte menu expansion state
    let expandedMenus = {
        members: false,
        course: false,
        communication: false,
        invoice: false,
        balanceSheet: false,
        users: false,
    };

    // Toggle menu expansion
    function toggleMenu(menuKey) {
        expandedMenus[menuKey] = !expandedMenus[menuKey];
    }

    // Auto-expand menu based on current page
    $: {
        if ($currentPage === 'members') expandedMenus.members = true;
        if ($currentPage === 'course') expandedMenus.course = true;
        if ($currentPage === 'communication') expandedMenus.communication = true;
        if ($currentPage === 'invoice') expandedMenus.invoice = true;
        if ($currentPage === 'balance-sheet') expandedMenus.balanceSheet = true;
        if ($currentPage === 'users') expandedMenus.users = true;
    }

    $: brandLogo = $oemConfig?.logo || '';

    onMount(async () => {
        if ($role != 'athlete')
            await apiFetch(__bakney.env.API.BILLING.ACTIVE_PLAN).then(res => {
                // TODO: check if plan active & show plan in the case is expired
                if (!res.error) {
                    showPlan = true;
                    const planData = res.response.data;
                    const currentDate = new Date();
                    const planDate = new Date(planData?.ends_on);
                    planType = '🚀 ' + (planData?.active_plan?.name || 'Piano di Valutazione');
                    $isExpired = currentDate > planDate; //&& res.response.data.active_plan.billing_type != 1;
                    daysLeft = Math.max(Math.floor((planDate - currentDate) / (1000 * 60 * 60 * 24)), -1);
                    billingData.set(planData);
                    setPermissions(planData?.active_plan?.billing_type, $role);

                    if ($isExpired) {
                        window.location.href = '/#/subscription/upgrade';
                        $sidebarCollapsed = true;
                        toast.error('Abbonamento scaduto. Rinnovalo per continuare.');
                    }
                }
            });
    });
</script>

{#if daysLeft && daysLeft > 0 && parseInt(daysLeft) < 4}
    <Portal target="body">
        <div
            transition:slide={{duration: 350, y: 5}}
            class="plan-status text-white"
            style="backdrop-filter: blur(5px);background: var(--danger) !important;-webkit-backdrop-filter: blur(5px);opacity: 0.85;">
            Piano in scadenza tra {daysLeft}
            {daysLeft == 1 ? 'giorno' : 'giorni'}
            <a href="/#/subscription" class="mx-1 text-danger bg-light-danger px-2 py-0 rounded my-4 ml-2">
                RINNOVA ORA
            </a>
        </div>
    </Portal>
{:else if parseInt(daysLeft) === 0}
    <Portal target="body">
        <div
            transition:slide={{duration: 350, y: 5}}
            class="plan-status text-white"
            style="backdrop-filter: blur(5px);background: var(--danger) !important;-webkit-backdrop-filter: blur(5px);opacity: 0.85;">
            Piano scaduto
            <a href="/#/subscription" class="mx-1 text-white bg-dark px-2 py-0 rounded my-4"> RINNOVALO ORA </a>
        </div>
    </Portal>
{/if}

<!--begin::Aside-->
<div class="aside aside-left aside-fixed d-flex flex-column flex-row-auto" collapsed={$sidebarCollapsed} id="bkn_aside">
    <!--begin::Brand #351DC2-->
    <div class="brand flex-column-auto" id="bkn_brand">
        <!--begin::Logo-->
        <a href="/" class="brand-logo mx-auto">
            <!-- svelte-ignore a11y-missing-attribute -->
            <img id="logo" class="h-30px" style={visibleLogo ? '' : 'display:none;'} src={brandLogo} />
            <!-- svelte-ignore missing-declaration -->
            {#if IS_BETA}
                <div class="beta-banner">beta</div>
            {/if}
        </a>

        <!--end::Logo-->
    </div>

    <!--end::Brand-->
    <!--begin::Aside Menu-->
    <div
        class="aside-menu-wrapper"
        id="bkn_aside_menu_wrapper"
        style="flex: 1; overflow: hidden; display: flex; flex-direction: column;">
        <!--begin::Menu Container-->
        <div
            id="bkn_aside_menu"
            class="aside-menu mt-4 mt-md-0 mb-4"
            data-menu-vertical="1"
            style="display: flex; flex-direction: column; flex: 1; overflow: hidden;">
            <!--begin::Menu Nav - Scrollable-->
            <div class="menu-nav" style="flex: 1; overflow-y: auto; overflow-x: hidden;">
                {#if canPerformAction('association.dashboard.read') || $role == 'athlete'}
                    <div
                        class={$currentPage == 'dashboard' ? 'menu-item menu-item-active' : 'menu-item'}
                        aria-haspopup="true">
                        <a href="/#/" class="menu-link" on:click={collapseSidebar}>
                            <span class="menu-icon">
                                <Stack size={24} weight="duotone" />
                            </span>
                            <span class="menu-text">Bacheca</span>
                        </a>
                    </div>
                {/if}

                {#if $role != 'athlete'}
                    {#if canPerformAction('association.calendar.read')}
                        <div
                            class={$currentPage == 'calendar' ? 'menu-item menu-item-active' : 'menu-item'}
                            aria-haspopup="true">
                            <a href="/#/calendar" class="menu-link" on:click={collapseSidebar}>
                                <span class="menu-icon">
                                    <Calendar size={24} weight="duotone" />
                                </span>
                                <span class="menu-text">Calendario</span>
                            </a>
                        </div>
                    {/if}
                {/if}

                {#if $role != 'athlete'}
                    <div class="menu-section">
                        <h4 class="menu-text">GESTIONE</h4>
                        <Ellipsis class="menu-icon" size={18} />
                    </div>
                    {#if canPerformAction('association.members.read') || canPerformAction('association.members.archive.read')}
                        <!-- svelte-ignore a11y-click-events-have-key-events -->
                        <!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
                        <div
                            class="menu-item menu-item-submenu {$currentPage == 'members'
                                ? 'menu-item-active'
                                : ''} {expandedMenus.members ? 'menu-item-open' : ''}"
                            aria-haspopup="true">
                            <!-- svelte-ignore a11y-invalid-attribute -->
                            <span class="menu-link menu-toggle" on:click={() => toggleMenu('members')}>
                                <span class="menu-icon">
                                    <Users size={24} weight="duotone" />
                                </span>
                                <span class="menu-text">Organizzazione</span>
                                <span class="menu-arrow"><ChevronRight class="menu-arrow-icon" size={14} /></span>
                            </span>
                            {#if expandedMenus.members}
                                <div class="menu-submenu" style="display: block;" transition:slide={{duration: 200}}>
                                    <div class="menu-subnav">
                                        {#if canPerformAction('association.personas.read')}
                                            <div
                                                class="menu-item menu-item-submenu {$subPage == 'personas-list'
                                                    ? 'menu-item-active'
                                                    : ''}"
                                                aria-haspopup="true"
                                                data-menu-toggle="hover">
                                                <a href="/#/personas/list" class="menu-link" on:click={collapseSidebar}>
                                                    <span class="menu-icon">
                                                        <IdentificationCard size={24} weight="duotone" />
                                                    </span>
                                                    <span class="menu-text">Anagrafiche</span>
                                                    <!-- <span
                                                    class="badge badge-danger font-weight-boldest font-size-xs px-3 py-2"
                                                    style="border-radius: 1rem;">Novità</span> -->
                                                </a>
                                            </div>
                                        {/if}
                                        {#if canPerformAction('association.members.read')}
                                            <div
                                                class="menu-item menu-item-submenu {$subPage == 'members-list'
                                                    ? 'menu-item-active'
                                                    : ''}"
                                                aria-haspopup="true"
                                                data-menu-toggle="hover">
                                                <a href="/#/members/list" class="menu-link" on:click={collapseSidebar}>
                                                    <span class="menu-icon">
                                                        <Book size={24} weight="duotone" />
                                                    </span>
                                                    <span class="menu-text">Iscrizioni</span>
                                                    {#if newUsers > 0}
                                                        <span class="menu-label">
                                                            <span class="label label-primary label-rounded"
                                                                >{newUsers}</span>
                                                        </span>
                                                    {/if}
                                                </a>
                                            </div>
                                            <div
                                                class="menu-item menu-item-submenu {$subPage == 'members-template'
                                                    ? 'menu-item-active'
                                                    : ''}"
                                                aria-haspopup="true"
                                                data-menu-toggle="hover">
                                                <!-- class="menu-link coming-soon-item" -->
                                                <a
                                                    href={isFreePlan()
                                                        ? '/#/subscription/upgrade'
                                                        : '/#/members/subscription/template'}
                                                    class="menu-link"
                                                    on:click={collapseSidebar}>
                                                    <span class="menu-icon">
                                                        <NotePencil size={24} weight="duotone" />
                                                    </span>
                                                    <span class="menu-text"
                                                        >Modulo Iscrizioni
                                                        <!-- <sup><div class="coming-soon-badge">In Arrivo</div></sup> -->
                                                    </span>
                                                </a>
                                            </div>
                                        {/if}
                                        {#if canPerformAction('association.modules.read')}
                                            <!-- svelte-ignore a11y-role-supports-aria-props -->
                                            <div
                                                class="menu-item menu-item-submenu {$subPage == 'modules'
                                                    ? 'menu-item-active'
                                                    : ''}"
                                                aria-haspopup="true"
                                                data-menu-toggle="hover">
                                                <!-- class="menu-link coming-soon-item" -->
                                                <a
                                                    href={isFreePlan()
                                                        ? '/#/subscription/upgrade'
                                                        : '/#/members/modules'}
                                                    class="menu-link"
                                                    on:click={collapseSidebar}>
                                                    <span class="menu-icon">
                                                        <Browsers size={24} weight="duotone" />
                                                    </span>
                                                    <span class="menu-text"
                                                        >Moduli Web
                                                        <!-- <sup><div class="coming-soon-badge">In Arrivo</div></sup> -->
                                                    </span>
                                                </a>
                                            </div>
                                        {/if}
                                        <!-- {/if} -->
                                    </div>
                                </div>
                            {/if}
                        </div>
                    {/if}
                    {#if canPerformAction('association.courses.read') || canPerformAction('association.carnet.read') || canPerformAction('association.instructor.read')}
                        <!-- svelte-ignore a11y-click-events-have-key-events -->
                        <!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
                        <div
                            class="menu-item menu-item-submenu {$currentPage == 'course'
                                ? 'menu-item-active'
                                : ''} {expandedMenus.course ? 'menu-item-open' : ''}"
                            aria-haspopup="true">
                            <!-- svelte-ignore a11y-invalid-attribute -->
                            <span class="menu-link menu-toggle" on:click={() => toggleMenu('course')}>
                                <span class="menu-icon">
                                    <Rows size={24} weight="duotone" />
                                </span>
                                <span class="menu-text">Attività</span>
                                <span class="menu-arrow"><ChevronRight class="menu-arrow-icon" size={14} /></span>
                            </span>
                            {#if expandedMenus.course}
                                <div class="menu-submenu" style="display: block;" transition:slide={{duration: 200}}>
                                    <div class="menu-subnav">
                                        {#if canPerformAction('association.courses.read')}
                                            <div
                                                class="menu-item menu-item-submenu {$subPage == 'course-list'
                                                    ? 'menu-item-active'
                                                    : ''}"
                                                aria-haspopup="true"
                                                data-menu-toggle="hover">
                                                <a href="/#/course/list" class="menu-link" on:click={collapseSidebar}>
                                                    <span class="menu-icon">
                                                        <Volleyball size={24} weight="duotone" />
                                                    </span>
                                                    <span class="menu-text">Corsi e Abbonamenti</span>
                                                </a>
                                            </div>
                                        {/if}
                                        <!--TODO: update permissions-->
                                        {#if canPerformAction('association.campsandretreats.read')}
                                            <div
                                                class="menu-item menu-item-submenu {$subPage ==
                                                'camps-and-retreats-list'
                                                    ? 'menu-item-active'
                                                    : ''}"
                                                aria-haspopup="true"
                                                data-menu-toggle="hover">
                                                <a
                                                    href="/#/course/camps-and-retreats/list"
                                                    class="menu-link"
                                                    on:click={collapseSidebar}>
                                                    <span class="menu-icon">
                                                        <Tent size={24} weight="duotone" />
                                                    </span>
                                                    <span class="menu-text">Camp e Ritiri</span>
                                                </a>
                                            </div>
                                        {/if}

                                        {#if canPerformAction('association.carnet.read')}
                                            <div
                                                class="menu-item menu-item-submenu {$subPage == 'carnet-list'
                                                    ? 'menu-item-active'
                                                    : ''}"
                                                aria-haspopup="true"
                                                data-menu-toggle="hover">
                                                <!-- class="menu-link coming-soon-item" -->
                                                <a
                                                    href={isFreePlan()
                                                        ? '/#/subscription/upgrade'
                                                        : '/#/course/carnet/list/'}
                                                    class="menu-link"
                                                    on:click={collapseSidebar}>
                                                    <span class="menu-icon">
                                                        <Ticket size={24} weight="duotone" />
                                                    </span>
                                                    <span class="menu-text"
                                                        >Carnet
                                                        <!-- <sup><div class="coming-soon-badge">In Arrivo</div></sup> -->
                                                    </span>
                                                </a>
                                            </div>
                                        {/if}

                                        {#if canPerformAction('association.instructor.read')}
                                            <div
                                                class="menu-item menu-item-submenu {$subPage == 'instructor-list'
                                                    ? 'menu-item-active'
                                                    : ''}"
                                                aria-haspopup="true"
                                                data-menu-toggle="hover">
                                                <!-- class="menu-link coming-soon-item" -->
                                                <a
                                                    href={isFreePlan()
                                                        ? '/#/subscription/upgrade'
                                                        : '/#/course/instructor/list/'}
                                                    class="menu-link"
                                                    on:click={collapseSidebar}>
                                                    <span class="menu-icon">
                                                        <UserFocus size={24} weight="duotone" />
                                                    </span>
                                                    <span class="menu-text"
                                                        >Istruttori
                                                        <!-- <sup><div class="coming-soon-badge">In Arrivo</div></sup> -->
                                                    </span>
                                                </a>
                                            </div>
                                        {/if}
                                    </div>
                                </div>
                            {/if}
                        </div>
                    {/if}
                    {#if canPerformAction('association.communication.messages.read') || canPerformAction('association.communication.workflows.read') || canPerformAction('association.communication.settings.read')}
                        <!-- svelte-ignore a11y-click-events-have-key-events -->
                        <!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
                        <div
                            class="menu-item menu-item-submenu {$currentPage == 'communication'
                                ? 'menu-item-active'
                                : ''} {expandedMenus.communication ? 'menu-item-open' : ''}"
                            aria-haspopup="true">
                            <!-- svelte-ignore a11y-invalid-attribute -->
                            <span class="menu-link menu-toggle" on:click={() => toggleMenu('communication')}>
                                <span class="menu-icon">
                                    <Confetti size={24} weight="duotone" />
                                </span>
                                <span class="menu-text">Comunicazioni</span>
                                <span class="menu-arrow"><ChevronRight class="menu-arrow-icon" size={14} /></span>
                            </span>
                            {#if expandedMenus.communication}
                                <div class="menu-submenu" style="display: block;" transition:slide={{duration: 200}}>
                                    <div class="menu-subnav">
                                        {#if canPerformAction('association.communication.messages.read')}
                                            <div
                                                class="menu-item menu-item-submenu {$subPage == 'messages-list'
                                                    ? 'menu-item-active'
                                                    : ''}"
                                                aria-haspopup="true"
                                                data-menu-toggle="hover">
                                                <!-- class="menu-link coming-soon-item" -->
                                                <a
                                                    href={isFreePlan()
                                                        ? '/#/subscription/upgrade'
                                                        : '/#/communication/messages'}
                                                    class="menu-link"
                                                    on:click={collapseSidebar}>
                                                    <span class="menu-icon">
                                                        <PaperPlaneTilt size={24} weight="duotone" />
                                                    </span>
                                                    <span class="menu-text"
                                                        >Messaggi
                                                        <!-- <sup><div class="coming-soon-badge">In Arrivo</div></sup> -->
                                                    </span>
                                                </a>
                                            </div>
                                        {/if}
                                        {#if canPerformAction('association.communication.workflows.read')}
                                            <div
                                                class="menu-item menu-item-submenu {$subPage == 'message-automation'
                                                    ? 'menu-item-active'
                                                    : ''}"
                                                aria-haspopup="true"
                                                data-menu-toggle="hover">
                                                <a
                                                    href={isFreePlan()
                                                        ? '/#/subscription/upgrade'
                                                        : '/#/communication/automation'}
                                                    class="menu-link"
                                                    on:click={collapseSidebar}>
                                                    <span class="menu-icon">
                                                        <TreeStructure size={24} weight="duotone" />
                                                    </span>
                                                    <span class="menu-text">Automazioni</span>
                                                </a>
                                            </div>
                                        {/if}
                                        {#if canPerformAction('association.communication.settings.read')}
                                            <div
                                                class="menu-item menu-item-submenu {$subPage == 'email' ||
                                                $subPage == 'stats'
                                                    ? 'menu-item-active'
                                                    : ''}"
                                                aria-haspopup="true"
                                                data-menu-toggle="hover">
                                                <!-- class="menu-link coming-soon-item" -->
                                                <a
                                                    href={isFreePlan()
                                                        ? '/#/subscription/upgrade'
                                                        : '/#/communication/configuration'}
                                                    class="menu-link"
                                                    on:click={collapseSidebar}>
                                                    <span class="menu-icon">
                                                        <GearSix size={24} weight="duotone" />
                                                    </span>
                                                    <span class="menu-text"
                                                        >Configurazioni
                                                        <!-- <sup><div class="coming-soon-badge">In Arrivo</div></sup> -->
                                                    </span>
                                                </a>
                                            </div>
                                        {/if}
                                    </div>
                                </div>
                            {/if}
                        </div>
                    {/if}

                    {#if canPerformAction('association.archive.read')}
                        <div
                            class={$currentPage == 'archive'
                                ? 'menu-item menu-item-active'
                                : 'menu-item menu-item-submenu'}
                            aria-haspopup="true"
                            data-menu-toggle="hover">
                            <a href={'/#/archive'} class="menu-link" on:click={collapseSidebar}>
                                <span class="menu-icon">
                                    <Folders size={24} weight="duotone" />
                                </span>
                                <span class="menu-text">Archivio</span>
                            </a>
                        </div>
                    {/if}

                    {#if canPerformAction('association.report.read')}
                        <div
                            class={$currentPage == 'report'
                                ? 'menu-item menu-item-active'
                                : 'menu-item menu-item-submenu'}
                            aria-haspopup="true"
                            data-menu-toggle="hover">
                            <a
                                href={isFreePlan() ? '/#/subscription/upgrade' : '/#/report'}
                                class="menu-link"
                                on:click={collapseSidebar}>
                                <span class="menu-icon">
                                    <ChartPieSlice size={24} weight="duotone" />
                                </span>
                                <span class="menu-text">Report</span>
                            </a>
                        </div>
                        <div
                            class={$currentPage == 'saved-reports'
                                ? 'menu-item menu-item-active'
                                : 'menu-item menu-item-submenu'}
                            aria-haspopup="true"
                            data-menu-toggle="hover">
                            <a
                                href={isFreePlan() ? '/#/subscription/upgrade' : '/#/saved-reports'}
                                class="menu-link"
                                on:click={collapseSidebar}>
                                <span class="menu-icon">
                                    <FileText size={24} weight="duotone" />
                                </span>
                                <span class="menu-text">Report salvati</span>
                            </a>
                        </div>
                    {/if}
                    {#if canPerformAction('other.audit.read')}
                        <div
                            class={$currentPage == 'audit'
                                ? 'menu-item menu-item-active'
                                : 'menu-item menu-item-submenu'}
                            aria-haspopup="true"
                            data-menu-toggle="hover">
                            <a href="/#/audit/list" class="menu-link" on:click={collapseSidebar}>
                                <span class="menu-icon">
                                    <ClipboardText size={24} weight="duotone" />
                                </span>
                                <span class="menu-text">Auditlog</span>
                            </a>
                        </div>
                    {/if}
                    <div class="menu-section">
                        <h4 class="menu-text">Contabilità</h4>
                        <Ellipsis class="menu-icon" size={18} />
                    </div>
                    <!-- svelte-ignore a11y-click-events-have-key-events -->
                    {#if canPerformAction('bookeeping.payments.read')}
                        <!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
                        <div
                            class={$currentPage == 'payment'
                                ? 'menu-item menu-item-active'
                                : 'menu-item menu-item-submenu'}
                            on:click={() => push('/payment/list')}
                            aria-haspopup="true"
                            data-menu-toggle="hover">
                            <a href="/#/payment/list" class="menu-link menu-toggle" on:click={collapseSidebar}>
                                <span class="menu-icon">
                                    <Wallet size={24} weight="duotone" />
                                </span>
                                <span class="menu-text">Pagamenti</span>
                            </a>
                        </div>
                    {/if}

                    {#if canPerformAction('bookeeping.documents.invoices.read')}
                        <!-- svelte-ignore a11y-click-events-have-key-events -->
                        <!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
                        <div
                            class="menu-item menu-item-submenu {$currentPage == 'invoice'
                                ? 'menu-item-active'
                                : ''} {expandedMenus.invoice ? 'menu-item-open' : ''}"
                            aria-haspopup="true">
                            <span class="menu-link menu-toggle" on:click={() => toggleMenu('invoice')}>
                                <span class="menu-icon">
                                    <Files size={24} weight="duotone" />
                                </span>
                                <span class="menu-text">Documenti fiscali</span>
                                <span class="menu-arrow"><ChevronRight class="menu-arrow-icon" size={14} /></span>
                            </span>
                            {#if expandedMenus.invoice}
                                <div class="menu-submenu" style="display: block;" transition:slide={{duration: 200}}>
                                    <div class="menu-subnav">
                                        {#if canPerformAction('bookeeping.documents.invoices.read')}
                                            <!-- svelte-ignore a11y-click-events-have-key-events -->
                                            <!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
                                            <!-- svelte-ignore a11y-role-supports-aria-props -->
                                            <div
                                                class="menu-item menu-item-submenu {$subPage == 'invoice-list'
                                                    ? 'menu-item-active'
                                                    : ''}"
                                                on:click={() => replace('/invoice/list')}
                                                aria-haspopup="true"
                                                data-menu-toggle="hover">
                                                <!-- svelte-ignore a11y-missing-attribute -->
                                                <!-- svelte-ignore a11y-no-static-element-interactions -->
                                                <a class="menu-link" on:click={collapseSidebar}>
                                                    <span class="menu-icon">
                                                        <FileText size={24} weight="duotone" />
                                                    </span>
                                                    <span class="menu-text">Ricevute</span>
                                                    {#if newInvoices > 0}
                                                        <span class="menu-label">
                                                            <span class="label label-primary label-rounded"
                                                                >{newInvoices}</span>
                                                        </span>
                                                    {/if}
                                                </a>
                                            </div>
                                        {/if}
                                        {#if canPerformAction('bookeeping.documents.clientinvoices.read')}
                                            <!-- svelte-ignore a11y-click-events-have-key-events -->
                                            <!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
                                            <!-- svelte-ignore a11y-role-supports-aria-props -->
                                            <div
                                                on:click={() => {
                                                    isFreePlan()
                                                        ? (location.href = '/#/subscription/upgrade')
                                                        : (location.href = '/#/customers-invoice/list');
                                                }}
                                                class="menu-item menu-item-submenu {$subPage == 'customers-invoice-list'
                                                    ? 'menu-item-active'
                                                    : ''}"
                                                aria-haspopup="true"
                                                data-menu-toggle="hover">
                                                <!-- svelte-ignore a11y-missing-attribute -->
                                                <!-- svelte-ignore a11y-no-static-element-interactions -->
                                                <a class="menu-link" on:click={collapseSidebar}>
                                                    <span class="menu-icon">
                                                        <File size={24} weight="duotone" />
                                                    </span>
                                                    <span class="menu-text">Fatture Attive</span>
                                                </a>
                                            </div>
                                        {/if}

                                        {#if canPerformAction('bookeeping.documents.supplierinvoices.read')}
                                            <!-- svelte-ignore a11y-click-events-have-key-events -->
                                            <!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
                                            <!-- svelte-ignore a11y-role-supports-aria-props -->
                                            <div
                                                on:click={() => {
                                                    isFreePlan()
                                                        ? (location.href = '/#/subscription/upgrade')
                                                        : (location.href = '/#/suppliers-invoice/list');
                                                }}
                                                class="menu-item menu-item-submenu {$subPage == 'suppliers-invoice-list'
                                                    ? 'menu-item-active'
                                                    : ''}"
                                                aria-haspopup="true"
                                                data-menu-toggle="hover">
                                                <!-- svelte-ignore a11y-missing-attribute -->
                                                <!-- svelte-ignore a11y-no-static-element-interactions -->
                                                <a class="menu-link" on:click={collapseSidebar}>
                                                    <span class="menu-icon">
                                                        <File size={24} weight="duotone" />
                                                    </span>
                                                    <span class="menu-text">Fatture Passive</span>
                                                </a>
                                            </div>
                                        {/if}
                                    </div>
                                </div>
                            {/if}
                        </div>
                    {/if}
                    <!-- svelte-ignore a11y-click-events-have-key-events -->
                    {#if canPerformAction('bookeeping.management.balancesheet.read') || canPerformAction('bookeeping.management.suppliers.read') || canPerformAction('bookeeping.management.accounts.read') || canPerformAction('bookeeping.management.accountstransfer.read')}
                        <!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
                        <!-- svelte-ignore a11y-click-events-have-key-events -->
                        <div
                            class="menu-item menu-item-submenu {$currentPage == 'balance-sheet'
                                ? 'menu-item-active'
                                : ''} {expandedMenus.balanceSheet ? 'menu-item-open' : ''}"
                            aria-haspopup="true">
                            <span
                                class="menu-link menu-toggle"
                                on:click={() => {
                                    if (isFreePlan()) {
                                        location.href = '/#/subscription/upgrade';
                                    } else {
                                        toggleMenu('balanceSheet');
                                    }
                                }}>
                                <span class="menu-icon">
                                    <Percent size={24} weight="duotone" />
                                </span>
                                <span class="menu-text">Gestione</span>
                                <span class="menu-arrow"><ChevronRight class="menu-arrow-icon" size={14} /></span>
                            </span>
                            {#if expandedMenus.balanceSheet}
                                <div class="menu-submenu" style="display: block;" transition:slide={{duration: 200}}>
                                    <div class="menu-subnav">
                                        <div class="menu-item menu-item-parent" aria-haspopup="true">
                                            <span class="menu-link">
                                                <span class="menu-text">Gestione</span>
                                            </span>
                                        </div>
                                        {#if canPerformAction('bookeeping.management.suppliers.read')}
                                            <!-- svelte-ignore a11y-click-events-have-key-events -->
                                            <!-- svelte-ignore a11y-role-supports-aria-props -->
                                            <div
                                                class="menu-item menu-item-submenu {$subPage == 'suppliers-list'
                                                    ? 'menu-item-active'
                                                    : ''}"
                                                on:click={() => replace('/suppliers-and-customers/list')}
                                                aria-haspopup="true"
                                                data-menu-toggle="hover">
                                                <!-- svelte-ignore a11y-missing-attribute -->
                                                <!-- svelte-ignore a11y-no-static-element-interactions -->
                                                <a class="menu-link" on:click={collapseSidebar}>
                                                    <span class="menu-icon">
                                                        <AddressBook size={24} weight="duotone" />
                                                    </span>
                                                    <span class="menu-text">Fornitori e Clienti</span>
                                                </a>
                                            </div>
                                        {/if}
                                        <!-- svelte-ignore a11y-click-events-have-key-events -->
                                        {#if canPerformAction('bookeeping.management.accounts.read')}
                                            <!-- svelte-ignore a11y-role-supports-aria-props -->
                                            <div
                                                class="menu-item menu-item-submenu {$subPage == 'accounting-list'
                                                    ? 'menu-item-active'
                                                    : ''}"
                                                on:click={() => replace('/accounting/list')}
                                                aria-haspopup="true"
                                                data-menu-toggle="hover">
                                                <!-- svelte-ignore a11y-missing-attribute -->
                                                <!-- svelte-ignore a11y-no-static-element-interactions -->
                                                <a class="menu-link" on:click={collapseSidebar}>
                                                    <span class="menu-icon">
                                                        <Bank size={24} weight="duotone" />
                                                    </span>
                                                    <span class="menu-text">Conti Finanziari</span>
                                                </a>
                                            </div>
                                        {/if}
                                        <!-- svelte-ignore a11y-click-events-have-key-events -->
                                        {#if canPerformAction('bookeeping.management.accountstransfers.read')}
                                            <!-- svelte-ignore a11y-click-events-have-key-events -->
                                            <!-- svelte-ignore a11y-role-supports-aria-props -->
                                            <div
                                                class="menu-item menu-item-submenu {$subPage ==
                                                'accounting-transfer-list'
                                                    ? 'menu-item-active'
                                                    : ''}"
                                                on:click={() => replace('/accounting-transfer/list')}
                                                aria-haspopup="true"
                                                data-menu-toggle="hover">
                                                <!-- svelte-ignore a11y-missing-attribute -->
                                                <!-- svelte-ignore a11y-no-static-element-interactions -->
                                                <a class="menu-link" on:click={collapseSidebar}>
                                                    <span class="menu-icon">
                                                        <Coins size={24} weight="duotone" />
                                                    </span>
                                                    <span class="menu-text">Giroconti</span>
                                                </a>
                                            </div>
                                        {/if}
                                        <!-- svelte-ignore a11y-click-events-have-key-events -->
                                        <!-- <div
                                    class="menu-item menu-item-submenu {$subPage == 'accounting-transactions-list'
                                        ? 'menu-item-active'
                                        : ''}"
                                    on:click={() => replace('/balance-sheet/list')}
                                    aria-haspopup="true"
                                    data-menu-toggle="hover">
                                    <a class="menu-link" on:click={collapseSidebar}>
                                        <span class="menu-icon">
                                            <TrendUp size={24} weight="duotone" />
                                        </span>
                                        <span class="menu-text">Entrate e Uscite</span>
                                    </a>
                                </div> -->
                                        <!-- svelte-ignore a11y-click-events-have-key-events -->
                                        {#if canPerformAction('bookeeping.management.balancesheet.read')}
                                            <!-- svelte-ignore a11y-click-events-have-key-events -->
                                            <!-- svelte-ignore a11y-role-supports-aria-props -->
                                            <div
                                                class="menu-item menu-item-submenu {$subPage == 'balance-sheet-manage'
                                                    ? 'menu-item-active'
                                                    : ''}"
                                                on:click={() => replace('/balance-sheet/list')}
                                                aria-haspopup="true"
                                                data-menu-toggle="hover">
                                                <!-- svelte-ignore a11y-missing-attribute -->
                                                <a class="menu-link" on:click={collapseSidebar}>
                                                    <span class="menu-icon">
                                                        <ChartBar size={24} weight="duotone" />
                                                    </span>
                                                    <span class="menu-text">Bilancio</span>
                                                </a>
                                            </div>
                                        {/if}
                                    </div>
                                </div>
                            {/if}
                        </div>
                    {/if}
                {:else}
                    <div class="menu-section">
                        <h4 class="menu-text">Attività</h4>
                        <Ellipsis class="menu-icon" size={18} />
                    </div>
                    <!-- svelte-ignore a11y-click-events-have-key-events -->
                    <div
                        class={$currentPage == 'subscription'
                            ? 'menu-item menu-item-active'
                            : 'menu-item menu-item-submenu'}
                        on:click={() => push('/subscription/list')}
                        aria-haspopup="true"
                        data-menu-toggle="hover">
                        <a href="/#/subscription/list" class="menu-link menu-toggle" on:click={collapseSidebar}>
                            <span class="menu-icon">
                                <Users size={24} weight="duotone" />
                            </span>
                            <span class="menu-text">Iscrizioni</span>
                        </a>
                    </div>
                    <div
                        class={$currentPage == 'carnet' ? 'menu-item menu-item-active' : 'menu-item menu-item-submenu'}
                        on:click={() => push('/carnet/list')}
                        aria-haspopup="true"
                        data-menu-toggle="hover">
                        <a href="/#/carnet/list" class="menu-link menu-toggle" on:click={collapseSidebar}>
                            <span class="menu-icon">
                                <Ticket size={24} weight="duotone" />
                            </span>
                            <span class="menu-text">Carnet</span>
                        </a>
                    </div>
                    <!-- svelte-ignore a11y-click-events-have-key-events -->
                    <div
                        class={$currentPage == 'payment' ? 'menu-item menu-item-active' : 'menu-item menu-item-submenu'}
                        on:click={() => push('/payment/list')}
                        aria-haspopup="true"
                        data-menu-toggle="hover">
                        <a href="/#/payment/list" class="menu-link menu-toggle" on:click={collapseSidebar}>
                            <span class="menu-icon">
                                <Wallet size={24} weight="duotone" />
                            </span>
                            <span class="menu-text">Pagamenti</span>
                        </a>
                    </div>
                {/if}

                <div class="menu-section">
                    <h4 class="menu-text">Altro</h4>
                    <Ellipsis class="menu-icon" size={18} />
                </div>
                {#if canPerformAction('other.users.collaborators.read')}
                    {#if $role != 'athlete' && canPerformAction('other.users.collaborators.read')}
                        <div
                            class={$subPage == 'connected-collaborators' ? 'menu-item menu-item-active' : 'menu-item'}
                            aria-haspopup="true"
                            data-menu-toggle="hover">
                            <a href="/#/connected-collaborators" class="menu-link" on:click={collapseSidebar}>
                                <span class="menu-icon">
                                    <UserList size={24} weight="duotone" />
                                </span>
                                <span class="menu-text">Collaboratori</span>
                            </a>
                        </div>
                    {/if}

                    <!-- svelte-ignore a11y-click-events-have-key-events -->
                    <!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
                    <div
                        class="menu-item menu-item-submenu d-none {$currentPage == 'users'
                            ? 'menu-item-active'
                            : ''} {expandedMenus.users ? 'menu-item-open' : ''}"
                        aria-haspopup="true">
                        <span class="menu-link menu-toggle" on:click={() => toggleMenu('users')}>
                            <span class="menu-icon">
                                <UserList size={24} weight="duotone" />
                            </span>
                            <span class="menu-text">Utenti</span>
                            <span class="menu-arrow"><ChevronRight class="menu-arrow-icon" size={14} /></span>
                        </span>
                        {#if expandedMenus.users}
                            <div class="menu-submenu" style="display: block;" transition:slide={{duration: 200}}>
                                <div class="menu-subnav" />
                            </div>
                        {/if}
                    </div>
                {/if}
                <div
                    class={$currentPage == 'profile' ? 'menu-item menu-item-active' : 'menu-item'}
                    aria-haspopup="true"
                    data-menu-toggle="hover">
                    <a href="/#/profile" class="menu-link" on:click={collapseSidebar}>
                        <span class="menu-icon">
                            <Gear size={24} weight="duotone" />
                        </span>
                        <span class="menu-text">
                            {$role !== 'athlete' ? 'Impostazioni' : 'Profilo'}
                        </span>
                    </a>
                </div>

                {#if $role != 'athlete'}
                    {#if canPerformAction('association.courses.attendance.update')}
                        <div
                            class={$currentPage == 'checkin-attendance' ? 'menu-item menu-item-active' : 'menu-item'}
                            aria-haspopup="true">
                            <a href="/#/attendance-scanner-mode" class="menu-link" on:click={collapseSidebar}>
                                <span class="menu-icon">
                                    <Scan size={24} weight="duotone" />
                                </span>
                                <span class="menu-text">Check-in Presenze</span>
                            </a>
                        </div>
                    {/if}
                {/if}
                {#if $oemConfig?.displaySettings?.sidebar?.showManual && $oemConfig?.manualUrl}
                    <!-- svelte-ignore a11y-role-supports-aria-props -->
                    <div
                        id="manuale_assozeta"
                        class:d-none={$role == 'athlete'}
                        class="menu-item menu-item-submenu"
                        aria-haspopup="true"
                        data-menu-toggle="hover">
                        <a href={$oemConfig.manualUrl} class="menu-link" target="_blank" on:click={collapseSidebar}>
                            <span class="menu-icon">
                                <Info size={24} weight="duotone" />
                            </span>
                            <span class="menu-text">Manuale d'uso</span>
                        </a>
                    </div>
                {/if}
                <div
                    class={$currentPage == 'third-party-licenses' ? 'menu-item menu-item-active' : 'menu-item'}
                    aria-haspopup="true"
                    data-menu-toggle="hover">
                    <a href="/#/third-party-licenses" class="menu-link" on:click={collapseSidebar}>
                        <span class="menu-icon">
                            <FileText size={24} weight="duotone" />
                        </span>
                        <span class="menu-text">LICENZE</span>
                    </a>
                </div>
                <!-- {#if $role != 'athlete' && $userData?.sport_association?.imported_from_associami === true}
                    <div class="menu-item d-flex justify-content-center px-6 my-4">
                        <SmartSelect
                            customClasses={'w-100 p-0 mx-auto font-weight-bold mb-0 border border-2 border-secondary filter-select'}
                            editable={false}
                            active={false}
                            bind:value={$selectedGroup}
                            on:change={e => {
                                // if value is null, remove the selectedGroup from localStorage
                                if (e.detail.value == null) {
                                    localStorage.removeItem('selectedGroup');
                                }
                                // replace the current page with the same page
                                push('/');
                            }}
                            props={{
                                placeholder: 'Seleziona il ruolo del socio',
                                required: true,
                                id: 'selectedGroup',
                                name: 'selectedGroup',
                                clearable: false,
                                showChevron: true,
                                options: [
                                    {
                                        label: 'Tutte le sedi',
                                        value: null,
                                    },
                                    ...($userData?.sport_association?.groups || []),
                                ],
                                value: $selectedGroup,
                            }} />
                        <div class="position-absolute" style="top: -12px; right: 12px;">
                            <span
                                class="badge badge-danger font-weight-boldest font-size-xs px-3 py-2"
                                style="border-radius: 1rem;">Beta</span>
                        </div>
                    </div>
                {/if} -->
            </div>
            <!--end::Menu Nav-->
            <div
                class="version border border-2 mx-auto"
                style="flex-shrink: 0; width: 200px; background: var(--bg-surface-secondary); border-radius: 1rem; margin-bottom:0; padding: 1rem; max-width: -webkit-fill-available;">
                {#if showPlan && $userData.collaborator_role == 1 && $oemConfig?.displaySettings?.sidebar?.showPlanUpgrades}
                    <div class="d-flex align-items-center justify-content-center flex-column text-dark">
                        {#if Capacitor.getPlatform() !== 'ios'}
                            <a
                                class="btn btn-primary btn-sm font-weight-boldest py-1 px-2 mb-2 d-flex align-items-center font-size-lg shadow-xs"
                                href="/#/subscription"
                                style="outline: 1px solid var(--primary) !important; border-top: 1px solid var(--bg-surface);">
                                <CreditCard size={22} weight="duotone" class="mr-2" />
                                Piano e rinnovi
                            </a>
                        {/if}
                        <div class="mb-2">
                            <span class="plan-bold">{planType}</span>
                            {#if $billingData?.active_plan?.billing_type != 1}
                                ·
                                <span class="font-weight-bolder text-dark">{daysLeft} giorni rimasti</span>
                            {/if}
                        </div>
                    </div>
                {/if}
                <!-- svelte-ignore missing-declaration -->
                <span
                    ><b>build {__bakney.build.VERSION}</b> · {$oemConfig?.abbreviation || 'assozeta'} © {new Date().getFullYear()}
                    <br />
                    {#if $oemConfig?.displaySettings?.sidebar?.showPrivacyPolicy && $oemConfig?.privacyPolicyUrl}
                        <a
                            href={$oemConfig.privacyPolicyUrl}
                            style="font-size:.6rem"
                            class="m-0 p-0 text-primary font-weight-bolder font-size-xs"
                            title="Privacy Policy ">Privacy Policy</a>
                        ·
                    {/if}
                    {#if $oemConfig?.displaySettings?.sidebar?.showTermsOfService && $oemConfig?.termsOfServiceUrl}
                        <a
                            href={$oemConfig.termsOfServiceUrl}
                            style="font-size:.6rem"
                            class="m-0 p-0 text-primary font-weight-bolder font-size-xs"
                            title="Termini e Condizioni ">Termini e Condizioni</a>
                        <!-- svelte-ignore a11y-invalid-attribute -->
                        ·
                    {/if}
                    {#if $userData && $userData.is_superuser}
                        ·
                        <a
                            href="/#/tools/sport-associations-manager"
                            style="font-size:.6rem"
                            class="m-0 p-0 text-primary font-weight-bolder font-size-xs">Admin tools</a>
                    {/if}
                </span>
            </div>
        </div>
        <!--end::Menu Container-->
    </div>

    <!--end::Aside Menu-->
</div>

<!--end::Aside-->

<style>
    /* Hide scrollbar on all parent containers - only menu-nav should scroll */
    :global(.aside),
    :global(.aside-menu-wrapper),
    :global(.aside-menu) {
        overflow: hidden !important;
    }

    :global(.menu-nav:hover) {
        overflow-y: auto !important;
    }

    :global(.menu-nav) {
        scrollbar-gutter: stable;
        overflow-y: hidden;
        overscroll-behavior: contain;
    }

    :global(.menu-nav::-webkit-scrollbar) {
        width: 4px;
        position: absolute;
        background: transparent;
    }
    :global(.menu-nav::-webkit-scrollbar-track) {
        background: transparent;
    }
    :global(.menu-nav::-webkit-scrollbar-thumb) {
        background: var(--scrollbar-thumb);
        border-radius: 0.35rem;
    }

    .plan-bold {
        font-weight: bolder;
    }
    .current-plan {
        margin: auto;
        margin-bottom: 0.5rem;
        font-size: 0.85rem;
        width: fit-content;
        padding: 0.5rem 1rem;
        font-weight: normal;
        color: var(--text-secondary);
        border: 0.125rem solid var(--border-color);
        background-color: var(--bg-surface-secondary);
        border-radius: 0.5rem;
        cursor: pointer;
    }
    .version {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: flex-end;
        text-align: center;
        /* position: absolute; */
        bottom: 0;
        padding: 0.5rem;
        width: 100%;
        font-size: 0.75rem;
        color: var(--text-muted);
    }

    .plan-status {
        position: fixed;
        z-index: 3331017;
        width: 100vw;
        height: 2rem;
        align-items: center;
        display: flex;
        justify-content: center;
        font-weight: bold;
        padding: 1.3rem;
        bottom: 0;
        font-size: 1rem;
    }

    .beta-banner {
        position: absolute;
        top: 3.8rem;
        font-size: 0.7rem;
        font-weight: 500;
        padding: 0.2rem 0.8rem;
        border-radius: 0.7rem;
        height: fit-content;
        display: flex;
        text-align: center;
        justify-content: center;
        align-content: center;
        left: 1.2rem;
    }
</style>
