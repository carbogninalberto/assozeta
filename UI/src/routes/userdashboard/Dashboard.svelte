<script>
    import {scale} from 'svelte/transition';
    import * as easing from 'svelte/easing';
    import {sessionToken, userData} from 'store/stores.js';
    import {onMount} from 'svelte';
    import {
        SealQuestion,
        ArrowLeft,
        ArrowRight,
        Binoculars,
        X,
        XCircle,
        CheckCircle,
        Star,
        MagnifyingGlass,
        ThumbsUp,
    } from 'phosphor-svelte';
    import {apiFetch, replaceUID} from 'utils/ApiMiddleware.js';
    import ContentLoader from 'svelte-content-loader';
    import {toast} from 'svelte-sonner';
    import {push} from 'svelte-spa-router';
    import SimpleButton from 'components/buttons/simple-button.svelte';
    import SearchProfile from 'routes/search/profile/Profile.svelte';
    import {blockPage, unblockPage} from 'store/loadingStore.js';

    sessionToken.useLocalStorage();
    userData.useLocalStorage();

    let associations = [];
    let upcoming_lessons = [];
    let communications = [];
    let loading = false;

    onMount(async () => {
        await fetchData();
    });

    async function fetchData() {
        loading = true;
        let res = await apiFetch(__bakney.env.API.STATISTIC.ATHLETE_DASHBOARD);
        if (!res.error) {
            associations = res.response.data;
            communications = [];
            // iterate over associations and get communications
            for (let i = 0; i < associations?.length; i++) {
                for (let j = 0; j < associations[i]?.sport_association?.communications?.length; j++) {
                    let communication = associations[i]?.sport_association?.communications[j];
                    communication.sport_association_denomination = associations[i]?.sport_association?.denomination;
                    communication.avatar_image = associations[i]?.sport_association?.user?.avatar_image;
                    communications.push(communication);
                }
            }
            upcoming_lessons = res.response.upcoming_lessons || [];
        }

        loading = false;
    }

    function markAsAbsent(lesson, absent) {
        swal.fire({
            text: absent
                ? 'Sei sicuro di voler segnalare la tua assenza?'
                : 'Sei sicuro di voler segnalare la tua presenza?',
            icon: 'warning',
            buttonsStyling: true,
            showCancelButton: true,
            cancelButtonText: 'Annulla',
            confirmButtonText: 'Sarò ' + (absent ? 'assente' : 'presente'),
            reverseButtons: true,
            confirmButtonColor: 'primary',
        }).then(async function (result) {
            if (result.isConfirmed) {
                blockPage({
                    overlayColor: '#000000',
                    state: 'primary',
                    message: 'Segnalazione in corso...',
                });
                try {
                    const response = await apiFetch(
                        replaceUID(__bakney.env.API.ATTENDANCE_DAY.MARK_ABSENT, lesson.attendance_day_id),
                        {
                            method: 'POST',
                            body: JSON.stringify({
                                course_subscription_id: lesson.course_subscription_id,
                                absent: absent,
                            }),
                        }
                    );

                    if (!response.error) {
                        await fetchData();
                        toast.success('Assenza segnalata!');
                    } else {
                        toast.error('Qualcosa è andato storto.');
                    }
                } finally {
                    unblockPage();
                }
            }
        });
    }
</script>

<div  class="d-flex flex-column-fluid">
    <!--begin::Container-->
    <div class="container">
        <!--begin::Dashboard-->
        <!--begin::Row-->
        {#if communications?.length > 0 || upcoming_lessons?.length > 0}
            <div class="row d-flex nice-scrollbar border-bottom mb-8" style="max-height: 25rem; overflow-y: auto;">
                {#if communications?.length > 0}
                    <div class="col d-flex justify-content-center align-items-center w-100 mx-0 px-0 mb-4">
                        <div class="d-flex w-100" style="flex-direction:column;">
                            {#each communications as communication}
                                <div class="col-12 card-widget card card-custom p-0 my-4 w-full">
                                    <div class="card-body p-6 d-flex" style="flex-direction:column;">
                                        <div class="d-flex align-items-center justify-content-start">
                                            <div class="symbol symbol-30 symbol-circle s-wm8lqYbyGaBt">
                                                <img alt="Pic" src={communication.avatar_image} />
                                            </div>
                                            <div>
                                                <h6 class="mb-0 ml-4">{communication.sport_association_denomination}</h6>
                                                <p class="mb-0 ml-4 text-dark-75" style="font-size: .85rem;">
                                                    {new Date(communication.sent_on).toLocaleString()}
                                                </p>
                                            </div>
                                        </div>
                                        <div class="d-flex align-items-center pt-4 mb-0">
                                            {communication.message}
                                        </div>
                                    </div>
                                </div>
                            {/each}
                        </div>
                    </div>
                {/if}
                {#if upcoming_lessons?.length > 0}
                    <div class="d-flex flex-column border rounded-xl p-2 w-100 mb-4">
                        <div class="row mx-auto">
                            <h6 class="mb-2 mt-10 px-4 font-weight-boldest text-dark font-size-h6">Prossime lezioni</h6>
                        </div>
                        <div class="row p-0 p-md-4">
                            {#if loading}
                                <ContentLoader width="100%" primaryColor="#6b6b6b1f" secondaryColor="#eaf1f7" />
                            {:else}
                                {#if upcoming_lessons.length === 0}
                                    <div class="col-12 card card-custom p-4">
                                        <div class="card-body p-8">
                                            <div class="d-flex justify-content-start align-items-center p-0">
                                                <div class="mr-6">
                                                    <Binoculars size={50} weight="duotone" />
                                                </div>
                                                <div class="d-flex flex-column">
                                                    <!-- svelte-ignore a11y-invalid-attribute -->
                                                    <!-- svelte-ignore a11y-missing-attribute -->
                                                    <a
                                                        class="text-dark text-hover-primary font-weight-boldest font-size-h4 mb-2">
                                                        Nessuna Lezione in programma nei prossimi 7 giorni!
                                                    </a>
                                                    <div class="text-dark-75">
                                                        Se credi che ci sia qualche problema contatta la tua associazione.
                                                    </div>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                {/if}
                                <div class="scrolling-wrapper w-100 d-flex pb-4">
                                    {#each upcoming_lessons as lesson}
                                        <div class="col-12 col-md-3 card-widget card card-custom p-0 mr-md-4">
                                            <div class="card-body p-8">
                                                <div class="d-flex justify-content-center align-items-center">
                                                    <div class="d-flex justify-content-center flex-column">
                                                        <div class="text-center" style="font-size: 1.2rem">
                                                            <SealQuestion size={50} weight="duotone" class="text-warning" />
                                                            <br />
                                                            <span
                                                                class="text-dark text-center text-hover-primary font-weight-boldest mb-3">
                                                                Sarai presente alla lezione
                                                                <span class="text-primary">
                                                                    {lesson?.title}
                                                                </span>
                                                                del
                                                                <span class="text-primary">
                                                                    {lesson?.start}
                                                                </span>
                                                                ?
                                                            </span>
                                                        </div>
                                                        <div>
                                                            <div
                                                                class="text-dark-75 text-center font-size-lg mx-auto mt-4 border border-light-dark my-2 bg-white shadow-sm rounded-lg px-3"
                                                                style="width:fit-content">
                                                                <b
                                                                    >{lesson?.subscription?.associate?.first_name}
                                                                    {lesson?.subscription?.associate?.last_name}</b>
                                                            </div>
                                                        </div>
                                                        <!-- svelte-ignore a11y-missing-attribute -->
                                                        <!-- svelte-ignore a11y-click-events-have-key-events -->
                                                        <div class="d-flex mx-2 gap-2 justify-content-center">
                                                            <!-- {#if !lesson?.is_absent} -->
                                                            <button
                                                                style="height: 2.2rem;"
                                                                class="btn-{!lesson.is_present
                                                                    ? 'light-'
                                                                    : ''}success btn btn-xs mt-4 mr-0 border border-success font-weight-boldest d-flex align-items-center p-0 pl-1 pr-2"
                                                                on:click={() => markAsAbsent(lesson, false)}>
                                                                <CheckCircle size={22} class="mr-1" weight="duotone" />
                                                                PRESENTE
                                                            </button>
                                                            <button
                                                                style="height: 2.2rem;"
                                                                class="btn-{!lesson.is_absent
                                                                    ? 'light-'
                                                                    : ''}danger btn btn-xs mt-4 ml-3 border border-danger font-weight-boldest d-flex align-items-center p-0 pl-1 pr-2"
                                                                on:click={() => markAsAbsent(lesson, true)}>
                                                                <XCircle size={22} class="mr-1" weight="duotone" />
                                                                ASSENTE
                                                            </button>
                                                            <!-- {:else}
                                                            <span class="text-center py-4 font-weight-bold">
                                                                Non sarai presente a questa lezione.
                                                            </span>
                                                        {/if} -->
                                                        </div>
                                                    </div>
                                                </div>
                                            </div>
                                        </div>
                                    {/each}
                                </div>
                                {#if upcoming_lessons.length > 3}
                                    <div class="col-12 text-center mt-2">
                                        <!-- scorri per vedere tutto -->
                                        <span class="text-dark-50 font-weight-bold font-size-xs">
                                            <ArrowLeft />
                                            <span>scorri per vedere di più</span>
                                            <ArrowRight />
                                        </span>
                                    </div>
                                {/if}
                            {/if}
                        </div>
                    </div>
                {/if}
            </div>
        {/if}
        <!--end::Row-->

        {#if __bakney.OEM_CONFIG?.supportMultipleAssociations && associations && associations?.length > 1}
            <div class="row">
                <h1 class="mb-5 mt-10 px-4 font-weight-boldest text-dark font-size-h1">Le mie associazioni</h1>
            </div>
        {/if}
        <div class="row p-0 p-md-4">
            {#if loading}
                <ContentLoader width="100%" primaryColor="#6b6b6b1f" secondaryColor="#eaf1f7" />
            {:else if associations.length === 0}
                <div class="col-12 card card-custom p-4">
                    <div class="card-body p-8">
                        <div class="d-flex justify-content-start align-items-center p-0">
                            <div class="mr-6">
                                <Binoculars size={50} weight="duotone" />
                            </div>
                            <div class="d-flex flex-column">
                                <!-- svelte-ignore a11y-invalid-attribute -->
                                <!-- svelte-ignore a11y-missing-attribute -->
                                <a class="text-dark font-weight-boldest font-size-h4 mb-2">
                                    Nessuna associazione trovata!
                                </a>
                                <div class="text-dark-75">
                                    Non sei ancora associato ad alcuna associazione.<br />
                                    Per iscriverti ad un'associazione cercala nella barra di ricerca in alto e compila il
                                    modulo d'iscrizione.
                                    <SimpleButton
                                        classList="mt-4"
                                        color="primary"
                                        on:click={() => push('/search')}>
                                        <MagnifyingGlass size={16} weight="duotone" class="mr-1" />
                                        Cerca la tua associazione
                                    </SimpleButton>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            {:else if associations && associations?.length > 1}
                <div class="scrolling-wrapper w-100 d-flex">
                    {#each associations as association}
                        <div class="col-12 col-md-3 card-widget card card-custom p-0 mr-md-4">
                            <div class="card-body p-8">
                                <div class="d-flex justify-content-center align-items-center">
                                    <div class="d-flex justify-content-center flex-column">
                                        <a
                                            href="/#/search/profile/{association?.sport_association.user.username}"
                                            class="text-center">
                                            <!--
                                                svelte-ignore
                                                a11y-invalid-attribute
                                                -->
                                            <!-- svelte-ignore a11y-missing-attribute -->
                                            <div class="d-flex justify-content-center align-items-center mb-5 mt-5">
                                                {#if association?.sport_association.user.avatar_image}
                                                    <div class="symbol symbol-80 symbol-circle" style="margin:auto">
                                                        <img
                                                            alt="Pic"
                                                            src={association?.sport_association.user.avatar_image} />
                                                    </div>
                                                {:else}
                                                    <div
                                                        class="symbol symbol-light-info symbol-80 symbol-circle"
                                                        style="margin:auto">
                                                        <span
                                                            class="symbol-label font-weight-bold"
                                                            style="font-size: 4.5rem !important;">
                                                            {#if association?.sport_association.user.first_name && association?.sport_association.user.last_name}
                                                                {association?.sport_association.user.first_name
                                                                    .charAt(0)
                                                                    .toUpperCase()}{association?.sport_association.user.last_name
                                                                    .charAt(0)
                                                                    .toUpperCase()}
                                                            {/if}
                                                        </span>
                                                    </div>
                                                {/if}
                                            </div>
                                            <span
                                                class="text-dark text-center text-hover-primary font-weight-boldest font-size-h3 mb-3">
                                                {association?.sport_association.denomination}
                                            </span>
                                        </a>
                                        <div
                                            class="text-dark-75 text-center font-size-lg mt-4"
                                            style="background: var(--bg-surface-secondary); border: 0.1rem solid var(--border-color); border-radius: 0.55rem; padding: 0.7rem;width: 100%;">
                                            Ci sono <a
                                                href="/#/subscription/list"
                                                class="text-primary font-weight-boldest"
                                                style="cursor: pointer">{association?.subscriptions}</a>
                                            iscrizioni attive. <br />
                                            <a
                                                href="/#/subscription/list"
                                                class="text-primary font-weight-boldest"
                                                style="cursor: pointer">Vai alle iscrizioni <ArrowRight /></a>

                                            <!-- <a
                                                    href="/#/search/profile/{association?.sport_association.user.username}"
                                                    class="text-primary font-weight-boldest"
                                                    style="cursor: pointer">Apri profilo <ArrowRight /></a> -->
                                        </div>
                                        <div class="d-flex mx-2 justify-content-center flex-column align-items-center">
                                            <!-- {#if association?.sport_association?.review_url_enabled}
                                                <a
                                                    href={association?.sport_association?.review_url}
                                                    target="_blank"
                                                    class="btn-secondary d-flex align-items-center btn-sm btn mt-4 font-weight-bolder"
                                                    style="cursor: pointer">
                                                    <Star weight="duotone" class="mr-1" size={16} />
                                                    Lascia una recensione
                                                </a>
                                            {/if} -->
                                            <a
                                                href="/#/search/profile/{association?.sport_association.user.username}"
                                                class="btn-primary btn-sm btn mt-4 font-weight-boldest"
                                                style="cursor: pointer"
                                                >Iscrizioni, corsi e carnet
                                                <ArrowRight size={16} weight="bold" class="ml-1" />
                                            </a>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    {/each}
                </div>
                {#if associations.length > 3}
                    <div class="col-12 text-center mt-2">
                        <!-- scorri per vedere tutto -->
                        <span class="text-dark-50 font-weight-bold font-size-xs">
                            <ArrowLeft />
                            <span>scorri per vedere di più</span>
                            <ArrowRight />
                        </span>
                    </div>
                {/if}
            {:else if associations && associations?.length == 1}
                <SearchProfile
                    params={{username: associations[0]?.sport_association.user.username, insideDashboard: true}} />
            {/if}
        </div>
    </div>

    <!--end::Container-->
</div>

<!--end::Entry-->
<style>
    .scrolling-wrapper {
        overflow-x: auto;
    }

    .scrolling-wrapper::-webkit-scrollbar {
        height: 10px !important;
    }

    @media (max-width: 768px) {
        .scrolling-wrapper::-webkit-scrollbar {
            height: 0px !important;
        }
        .scrolling-wrapper {
            display: flex;
            flex-wrap: nowrap;
            overflow-x: auto;
        }

        .card {
            flex: 0 0 auto;
        }
        .scrolling-wrapper .card:first-child {
            margin-left: 0;
        }
        .scrolling-wrapper .card {
            max-width: 22rem;
        }
        .scrolling-wrapper .card {
            margin-right: 1rem;
            margin-bottom: 1rem;
        }
    }
</style>
