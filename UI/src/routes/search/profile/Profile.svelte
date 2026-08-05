<script>
    import {userData, role} from 'store/stores.js';
    import {Rows, Ticket, NotePencil, At, Cube, MapPin, Lock, Repeat} from 'phosphor-svelte';

    import {onMount} from 'svelte';
    import {apiFetch} from 'utils/ApiMiddleware.js';
    import {FacebookLoader} from 'svelte-content-loader';
    import {scale} from 'svelte/transition';
    import * as easing from 'svelte/easing';
    import AvailableCourses from './sections/AvailableCourses.svelte';
    import AvailableCarnets from './sections/AvailableCarnets.svelte';
    import {push} from 'svelte-spa-router';
	import { UiApp } from 'shim/ui.js';

    userData.useLocalStorage();

    export let params = {
        insideDashboard: false,
    };

    let loaded = false;

    let searchData = {
        user: {
            first_name: null,
            last_name: null,
            avatar_image: null,
            sport_association: {
                denomination: null,
            },
        },
    };
    let associatesUnsubscribed = [];
    let full = false;
    let open = false;
    let activeTab = 'courses';
    function switchTab(tab) {
        switch (tab) {
            case 'courses':
                activeTab = 'courses';
                break;
            case 'carnet':
                activeTab = 'carnet';
                break;
            default:
                activeTab = 'info';
        }
    }

    onMount(async () => {
        await loadProfile();

        if (!searchData?.is_athlete) await fetchDataAssociates();
        loaded = true;

        if (params?.subscribe == 'open') {
            sessionStorage.removeItem('redirectAfterLogin');
            sessionStorage.removeItem('sportAssociationUsername');
        }
    });

    async function fetchDataAssociates() {
        const res = await apiFetch(
            `${__bakney.env.API.PROFILE.ASSOCIATES_SPORT_ASSOCIATION}/${searchData?.sport_association_id}`
        );
        if (res.status == 400) {
            push('/404');
        }
        if (!res.error) {
            associatesUnsubscribed = res.response.associates_unsubscribed;
            full = associatesUnsubscribed?.length == 0;
        }
    }

    let loadProfile = async function () {
        searchData.courses = undefined;
        let result = await apiFetch(`${__bakney.env.API.SEARCH.PROFILE}/${params?.username}`);
        // spinner stop
        UiApp.unblockPage();

        // console.log('result', result.error);

        if (!result.error) searchData = result.response.data;
        else console.log(result.response);
    };
</script>

<!--begin::Entry-->
<div class="d-flex flex-column-fluid mt-16 mt-md-0 {params?.insideDashboard ? 'p-0' : 'p-2'}">
    <!--begin::Container-->
    <div class="container {params?.insideDashboard ? 'p-0' : ''}">
        {#if loaded}
            <div class="card card-custom gutter-b">
                <div
                    class={params?.insideDashboard ? 'card-body p-0 mx-auto' : 'card-body'}
                    style="max-width: 99vw;width: 100%;">
                    {#if params?.insideDashboard}
                        <div
                            class="d-flex align-items-center align-items-md-center justify-content-between flex-column flex-md-row">
                            <div class="d-flex align-items-center">
                                {#if searchData?.user.avatar_image}
                                    <div class="symbol symbol-80 symbol-circle mr-5">
                                        <img alt="Pic" src={searchData?.user.avatar_image} />
                                    </div>
                                {:else}
                                    <div class="symbol symbol-light-info symbol-80 symbol-circle mr-5">
                                        <span
                                            class="symbol-label font-weight-bold"
                                            style="font-size: 2.5rem !important;">
                                            {#if searchData?.user.first_name && searchData?.user.last_name}
                                                {searchData?.user.first_name
                                                    .charAt(0)
                                                    .toUpperCase()}{searchData?.user.last_name.charAt(0).toUpperCase()}
                                            {/if}
                                        </span>
                                    </div>
                                {/if}
                                <div class="d-flex flex-column">
                                    <h2 class="text-dark font-weight-boldest mb-1">
                                        {#if searchData?.user.is_athlete}
                                            {searchData?.user.first_name} {searchData?.user.last_name}
                                        {:else}
                                            {searchData?.user.sport_association.denomination}
                                        {/if}
                                    </h2>
                                    <div
                                        class="d-flex flex-column flex-md-row justify-content-center my-2"
                                        style="max-width: 100%;">
                                        <!-- svelte-ignore a11y-missing-attribute -->
                                        <a
                                            class="text-muted text-hover-primary d-flex align-items-center font-weight-bold mr-lg-8 mr-5 mb-lg-0 mb-2">
                                            <At size={16} weight="duotone" class="mr-1" />
                                            {searchData?.user.username}</a>
                                        <!-- svelte-ignore a11y-missing-attribute -->
                                        <a
                                            class="text-muted text-hover-primary d-flex align-items-center font-weight-bold mr-lg-8 mr-5 mb-lg-0 mb-2">
                                            <Cube size={16} weight="duotone" class="mr-1" />
                                            {searchData?.user.is_athlete ? 'Atleta' : 'Associazione Sportiva'}</a>

                                        {#if searchData?.user.sport_association && searchData?.user.sport_association.address && searchData?.user.sport_association.address_city}
                                            <!-- svelte-ignore a11y-missing-attribute -->
                                            <a
                                                class="text-muted text-hover-primary d-flex align-items-center font-weight-bold mr-lg-8 mr-5 mb-lg-0 mb-2">
                                                <MapPin size={16} weight="duotone" class="mr-1" />
                                                {searchData?.user.sport_association.address_city}, {searchData?.user
                                                    .sport_association.address}</a>
                                        {/if}
                                    </div>
                                </div>
                            </div>

                            {#if $userData.username != params.username && !searchData?.user.is_athlete && $role != 'association'}
                                <div class="d-flex align-items-center gap-2">
                                    <a
                                        class="btn btn-primary font-weight-bolder d-flex align-items-center"
                                        href="/#/subscribe/{searchData?.user.username}">
                                        <NotePencil size={16} weight="duotone" class="mr-2" />
                                        Iscriviti
                                    </a>
                                    <a
                                        class="btn btn-primary font-weight-bolder d-flex align-items-center"
                                        href="/#/subscription/list/past">
                                        <Repeat size={16} weight="duotone" class="mr-2" />
                                        Rinnova
                                    </a>
                                </div>
                            {/if}
                        </div>
                    {:else}
                        <!--begin::Top-->
                        <div class="d-flex align-items-center mb-5 mt-5">
                            {#if searchData?.user.avatar_image}
                                <div class="symbol symbol-150 symbol-circle" style="margin:auto">
                                    <img alt="Pic" src={searchData?.user.avatar_image} />
                                </div>
                            {:else}
                                <div class="symbol symbol-light-info symbol-150 symbol-circle" style="margin:auto">
                                    <span class="symbol-label font-weight-bold" style="font-size: 4.5rem !important;">
                                        {#if searchData?.user.first_name && searchData?.user.last_name}
                                            {searchData?.user.first_name
                                                .charAt(0)
                                                .toUpperCase()}{searchData?.user.last_name.charAt(0).toUpperCase()}
                                        {/if}
                                    </span>
                                </div>
                            {/if}
                        </div>

                        <div class="d-flex align-items-center mb-5">
                            <div style="margin:auto;width: 90%;">
                                <div class="row mb-5">
                                    <div class="col-lg-6 d-flex align-items-center justify-content-center">
                                        <a
                                            href="/#/"
                                            class="d-flex text-dark text-hover-primary font-size-h1 font-weight-bold mr-3">
                                            {#if searchData?.user.is_athlete}
                                                {searchData?.user.first_name} {searchData?.user.last_name}
                                            {:else}
                                                {searchData?.user.sport_association.denomination}
                                            {/if}
                                        </a>
                                    </div>

                                    <div class="col-lg-6 d-flex align-items-center justify-content-center">
                                        {#if $userData.username != params.username}
                                            <div class="my-lg-0 my-1">
                                                {#if !searchData?.user.is_athlete && $role != 'association'}
                                                    <!-- svelte-ignore a11y-invalid-attribute -->
                                                    <!-- svelte-ignore a11y-missing-attribute -->
                                                    <div class="btn-group dropup align-items-center">
                                                        <a
                                                            class="btn btn-sm btn-primary font-weight-bolder ml-1 mt-1 mb-1 mr-0 d-flex align-items-center"
                                                            href="/#/subscribe/{searchData?.user.username}">
                                                            <NotePencil size={15} weight="duotone" class="mr-1" />
                                                            Compila modulo iscrizione
                                                        </a>
                                                        <a
                                                            class="btn btn-sm btn-primary font-weight-bolder ml-1 mt-1 mb-1 mr-0 d-flex align-items-center"
                                                            href="/#/subscription/list/past">
                                                            <Repeat size={15} weight="duotone" class="mr-1" />
                                                            Rinnova
                                                        </a>
                                                    </div>
                                                {/if}
                                            </div>
                                        {/if}
                                    </div>
                                </div>
                                <!--begin::Contacts-->
                                <div class="d-flex justify-content-center my-2" style="max-width: 100%;">
                                    <!-- svelte-ignore a11y-missing-attribute -->
                                    <a
                                        class="text-muted text-hover-primary d-flex align-items-center font-weight-bold mr-lg-8 mr-5 mb-lg-0 mb-2">
                                        <At size={16} weight="duotone" class="mr-1" /> {searchData?.user.username}</a>
                                    <!-- svelte-ignore a11y-missing-attribute -->
                                    <a
                                        class="text-muted text-hover-primary d-flex align-items-center font-weight-bold mr-lg-8 mr-5 mb-lg-0 mb-2">
                                        <Cube size={16} weight="duotone" class="mr-1" />
                                        {searchData?.user.is_athlete ? 'Atleta' : 'Associazione Sportiva'}</a>

                                    {#if searchData?.user.sport_association && searchData?.user.sport_association.address && searchData?.user.sport_association.address_city}
                                        <!-- svelte-ignore a11y-missing-attribute -->
                                        <a
                                            class="text-muted text-hover-primary d-flex align-items-center font-weight-bold mr-lg-8 mr-5 mb-lg-0 mb-2">
                                            <MapPin size={16} weight="duotone" class="mr-1" />
                                            {searchData?.user.sport_association.address_city}, {searchData?.user
                                                .sport_association.address}</a>
                                    {/if}
                                </div>
                                <!--end::Contacts-->
                            </div>
                        </div>
                    {/if}
                    <!--end::Top-->
                    <!--begin::Separator-->
                    <!-- <div class="separator separator-solid my-7"></div> -->
                    <!--end::Separator-->
                    <!--begin::Bottom-->
                    {#if searchData && !searchData?.user.is_athlete && $role != 'association'}
                        <div class="card-toolbar m-auto pt-4" style="width: 100%;cursor:pointer">
                            <ul class="nav nav-tabs nav-bold nav-tabs-line border-1 justify-content-center">
                                <!-- svelte-ignore a11y-click-events-have-key-events -->
                                <!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
                                <li class="nav-item" on:click={() => switchTab('courses')}>
                                    <!-- svelte-ignore a11y-missing-attribute -->
                                    <a class="nav-link" class:active={activeTab === 'courses'}>
                                        <span class="nav-icon mr-1">
                                            <Rows
                                                size="19"
                                                color={activeTab == 'courses' ? '#351DC2' : '#888'}
                                                weight="duotone" />
                                        </span>
                                        <span class="nav-text">Corsi</span>
                                    </a>
                                </li>

                                <!-- svelte-ignore a11y-click-events-have-key-events -->
                                <!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
                                <li class="nav-item" on:click={() => switchTab('carnet')}>
                                    <!-- svelte-ignore a11y-missing-attribute -->
                                    <a class="nav-link" class:active={activeTab === 'carnet'}>
                                        <span class="nav-icon mr-1">
                                            <Ticket
                                                size="19"
                                                color={activeTab == 'carnet' ? '#351DC2' : '#888'}
                                                weight="duotone" />
                                        </span>
                                        <span class="nav-text">Carnet</span>
                                    </a>
                                </li>
                            </ul>
                        </div>
                    {/if}

                    <!-- content -->
                    <div class="mt-12">
                        {#if searchData && !searchData?.user.is_athlete && $role != 'association'}
                            {#if activeTab === 'courses'}
                                <AvailableCourses bind:loadProfile bind:searchData />
                            {/if}
                            {#if activeTab === 'carnet'}
                                <AvailableCarnets bind:loadProfile bind:searchData />
                            {/if}
                        {:else}
                            <div class="d-flex align-items-center justify-content-center flex-wrap">
                                <span
                                    class="text-muted d-flex align-items-center font-weight-bolder py-2 px-4 rounded-xl bg-light border border-light-dark">
                                    <Lock size={16} weight="bold" class="mr-2 text-dark" />
                                    <span class="text-dark">Profilo privato</span>
                                </span>
                            </div>
                        {/if}
                    </div>
                </div>
            </div>
        {:else}
            <div class="card card-custom gutter-b">
                <div class="card-body">
                    <FacebookLoader width={window.screen.width / 1.4} />
                </div>
            </div>
        {/if}
    </div>
</div>

<!-- SUBSCRIPTION MODAL -->

<!-- <div
    class="modal fade"
    id="subscriptionModal"
    tabindex="-1"
    role="dialog"
    aria-labelledby="exampleModalLabel"
    aria-hidden="true">
    <div class="modal-dialog modal-dialog-centered modal-xl" role="document">
        <div class="modal-content">
            {#if open}
                <AddMember
                    configuration={searchData?.user?.sport_association?.configuration}
                    inModal={true}
                    on:subscriptionCreated={() => {
                        window.location.reload();
                    }}
                    opts={{username: searchData?.user.username, searchData: searchData}} />
            {/if}
        </div>
    </div>
</div> -->
