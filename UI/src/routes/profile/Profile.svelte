<script>
    import {readImpersonation} from 'utils/impersonation.js';
    import Account from './sections/Account.svelte';
    import Password from './sections/Password.svelte';
    import ProfileMenu from './ProfileMenu.svelte';
    import TwoFactor from './sections/TwoFactor.svelte';
    import Stripe from './sections/Stripe.svelte';
    import Settings from './sections/Settings.svelte';
    import {scale} from 'svelte/transition';
    import {subPage, role} from 'store/stores.js';
    import * as easing from 'svelte/easing';
    import Integrations from './sections/Integrations.svelte';
    import DataManagement from './sections/DataManagement.svelte';
    import SelfInstance from './sections/SelfInstance.svelte';
    import {onMount} from 'svelte';
    import {readProfileLocation, navigateProfile} from 'utils/profileNavigation.js';
    import {apiFetch, originalFetch} from 'utils/ApiMiddleware.js';
    import {readStoredStatus} from './sections/independentStatus.js';
    import {getApiHost, isSelfHostedMode} from 'store/instanceStore.js';
    role.useLocalStorage();

    function syncAdministratorPage() {
        if ($role !== 'administrator') return;
        const {page} = readProfileLocation();
        const next = ['self-instance', 'data-management'].includes(page) ? page : 'self-instance';
        subPage.set(next);
        if (page !== next) navigateProfile(next, undefined, true);
    }
    onMount(() => {
        syncAdministratorPage();
        window.addEventListener('hashchange', syncAdministratorPage);
        return () => window.removeEventListener('hashchange', syncAdministratorPage);
    });
    let changes = false;
    let instanceOwner = false;
    let checkingOwner = true;
    let ownerError = '';
    let dataAssociation = null;
    onMount(async () => {
        try {
            if (isSelfHostedMode()) {
                const result = await apiFetch(`${getApiHost()}/instance/access`, {method: 'GET', skipForbidden: true});
                instanceOwner = !readImpersonation() && !result.error && (result.response.is_owner === true || result.response.is_administrator === true);
                dataAssociation = result.response?.data_association;
                if (result.error) {
                    const status = await readStoredStatus(originalFetch);
                    instanceOwner = !readImpersonation() && status.kind === 'owner';
                    if (status.kind === 'unavailable') ownerError = 'Impossibile verificare l’accesso all’istanza. Ricarica la pagina per riprovare.';
                }
            }
        } catch {
            ownerError = 'Impossibile verificare l’accesso all’istanza. Ricarica la pagina per riprovare.';
        } finally {
            checkingOwner = false;
        }
    });
</script>

<!--begin::Entry-->
<div  class="d-flex flex-column-fluid">
    <!--begin::Container-->
    <div class="container">
        <!--begin::Profile Personal Information-->
        <div class="row">
            {#if $role !== 'administrator'}
                <div class="col-lg-3 p-0 pr-md-2">
                    <ProfileMenu bind:changes {instanceOwner} />
                </div>
            {/if}
            <div id="content-profile-menu" class="{$role === 'administrator' ? 'col-12' : 'col-lg-9 pl-md-2'} p-0 pb-24 pb-md-0">
                {#if $subPage === 'self-instance'}
                    {#if checkingOwner}
                        <p class="p-8" role="status">Verifica accesso…</p>
                    {:else if instanceOwner}
                        <SelfInstance bind:changes />
                    {:else}
                        <p class="p-8" role="alert">{ownerError || 'Questa sezione è riservata al proprietario dell’istanza.'}</p>
                    {/if}
                {/if}
                {#if $role !== 'administrator'}
                {#if $subPage == 'info'}
                    <Account bind:changes />
                {/if}

                <!-- svelte-ignore missing-declaration -->
                {#if $role != 'athlete'}
                    {#if $subPage == 'stripe'}
                        <Stripe />
                    {/if}
                {/if}

                {#if $subPage == 'twofa'}
                    <TwoFactor bind:changes />
                {/if}

                {#if $subPage == 'password'}
                    <Password bind:changes />
                {/if}

                {#if $subPage == 'settings'}
                    <Settings bind:changes />
                {/if}

                {#if $subPage == 'integrations'}
                    <Integrations bind:changes />
                {/if}

                {/if}

                {#if $subPage == 'data-management'}
                    {#if $role === 'administrator'}
                        <p class="p-4" role="status">I dati gestiti appartengono all’associazione principale: <strong>{dataAssociation?.name || 'non configurata'}</strong>.</p>
                    {/if}
                    <DataManagement {instanceOwner} accessPending={checkingOwner} />
                {/if}
            </div>

            <!--end::Content-->
        </div>
        <!--end::Profile Personal Information-->
    </div>
    <!--end::Container-->
</div>
<!--end::Entry-->

<svelte:head>
    <style>
        #content-profile-menu .card-header {
            /* position: fixed; */
            /* width: -webkit-fill-available; */
            z-index: 10;
            background: var(--bg-surface);
            padding-top: 25px !important;
        }
        #content-profile-menu .card-body {
            /* padding-top: 120px !important; */
        }
        .content {
            padding: 0 !important;
        }
        @media (max-width: 991.98px) {
            #content-profile-menu .card-header {
                padding-top: 10px !important;
            }
            /* #content-profile-menu .card-body {
                padding-top: 180px !important;
            } */
        }
    </style>
</svelte:head>
