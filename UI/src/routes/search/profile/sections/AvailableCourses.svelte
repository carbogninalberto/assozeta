<script>
    import { scale, slide } from 'svelte/transition';
    import AddToCourse from '../../modals/AddToCourse.svelte';
    import TagFilterView from '../../../../components/filters/TagFilterView.svelte';
    import {PencilSimple, PushPin, Sparkle} from 'phosphor-svelte';
    export let searchData;
    export let loadProfile;

    let activeTags = [];

    function includesAny(arr, target) {
        return target.some(t => arr.includes(t)) || target.length === 0;
    }
</script>

{#if searchData && searchData.courses_pinned?.length > 0}
    <div class="row">
        <div class="col d-flex align-items-center justify-content-center mb-5" style="margin: auto;">
            <!-- svelte-ignore a11y-missing-attribute -->
            <span class="font-size-h3 font-weight-boldest text-primary d-flex align-items-center">
                <PushPin size={20} weight="duotone" class="mr-2" />
                In evidenza</span>
        </div>
    </div>
    <!--  && searchData.courses && searchData?.courses?.filter(c => c.pinned).length > 0 -->
    <div class="row">
        <!-- ?.filter(c => c.pinned) -->
        {#each searchData.courses_pinned || [] as course_pinned, idx}
            <div class="col-xl-4" in:scale={{start: 0.9, duration: 80}}>
                <!--begin::Stats Widget 10-->
                <div class="card card-widget card-custom card-stretch gutter-b bg-white">
                    <!--begin::Body-->
                    <div class="card-body p-6 pb-0">
                        <div class="d-flex flex-column justify-content-between">
                            <div class="d-flex justify-content-between align-items-center mb-3">
                                <!--begin::Title-->
                                <h2 class="col-8 p-0 font-size-h6 font-weight-bolder text-dark m-0">
                                    {course_pinned.title.toUpperCase() || 'n.a.'}
                                </h2>
                                {#if course_pinned.course_type == 1}
                                    <!--end::Title-->
                                    <span
                                        class="label label-lg label-light-info label-inline font-size-xl font-weight-bolder py-5">
                                        {course_pinned.fee} €
                                    </span>
                                {:else if course_pinned.course_type == 2}
                                    <span
                                        class="label label-lg label-light-info label-inline font-size-xl font-weight-bolder py-5">
                                        Quote multiple
                                    </span>
                                {:else if course_pinned.course_type == 3}
                                    <span
                                        class="label label-lg label-light-info label-inline font-size-xl font-weight-bolder py-5">
                                        Abbonamento
                                    </span>
                                {/if}
                            </div>
                            <!--begin::Text-->
                            <div
                                class="font-size-sm bg-white text-dark-75 mt-3 border border-secondary"
                                style="padding: 1rem;border-radius: 0.55rem;">
                                {@html course_pinned.description
                                    .replace(/&lt;/g, '<')
                                    .replace(/&gt;/g, '>')
                                    .substring(0, 120)}{course_pinned.description.length > 120 ? '...' : ''}
                            </div>
                        </div>
                    </div>
                    <!--end::Body-->
                    <!--begin::Footer-->
                    <div class="card-footer border-0 d-flex align-items-center justify-content-between p-6 pt-0">
                        <!--begin::Label-->
                        <!-- <span class="label label-lg label-light-primary label-inline font-size-sm font-weight-bolder py-5"> -->
                        {#if __bakney.OEM_CONFIG?.showCourseAthletes}
                            <span class="label label-lg label-pill label-inline font-weight-boldest mr-2">
                                {course_pinned.total_subscriptions} iscritti
                            </span>
                        {/if}
                        <button
                            data-toggle="modal"
                            data-target="#subscription-course-{course_pinned.course_id}"
                            class="btn btn-sm btn-primary font-size-lg font-weight-bolder px-4 d-flex align-items-center mb-0"
                            class:disabled={course_pinned.full || false}
                            style={course_pinned.full ? 'pointer-events: none;' : ''}
                            on:click={course_pinned.modal.reset}>
                            <PencilSimple size={18} weight="duotone" class="mr-2" />
                            Iscriviti
                        </button>
                    </div>
                    <!--end::Footer-->
                </div>
            </div>
            <!-- ADD TO COURSE MODAL -->
            <AddToCourse
                bind:this={course_pinned.modal}
                id={course_pinned.course_id}
                callback={loadProfile}
                course={course_pinned.title}
                multiple_quotes={course_pinned.multiple_quotes}
                bind:full={course_pinned.full} />
        {/each}
    </div>
{/if}


{#if searchData?.courses?.length == 0 && searchData?.courses_pinned?.length == 0}
    <div class="text-center">Nessun corso disponibile.</div>
{:else if searchData?.courses?.length > 0}
    <div class="row">
        <div class="col d-flex align-items-center justify-content-center mb-5" style="margin: auto;">
            <!-- svelte-ignore a11y-missing-attribute -->
            <span class="font-size-h3 font-weight-boldest text-dark d-flex align-items-center">
                <Sparkle size={20} weight="duotone" class="mr-2" />
                Corsi</span>
        </div>
    </div>
    <div class="row">
    {#if searchData}
        {#if searchData.courses_tags && searchData.courses_tags.length > 0}
            <TagFilterView bind:tags={searchData.courses_tags} bind:activeTags />
        {/if}

        {#each searchData?.courses || [] as course, idx}
            {#if includesAny( activeTags, course.tags?.map(t => t.tag_name) )}
                <div class="col-xl-4" in:scale={{start: 0.9, duration: 80}}>
                    <!--begin::Stats Widget 10-->
                    <div class="card card-widget card-custom card-stretch gutter-b bg-white">
                        <!--begin::Body-->
                        <div class="card-body p-6 pb-0">
                            <div class="d-flex flex-column justify-content-between">
                                <div class="d-flex justify-content-between align-items-center mb-3">
                                    <!--begin::Title-->
                                    <h2 class="col-{course.one_fee_payment ? '12' : '8'} p-0 font-size-h6 font-weight-bolder text-dark m-0">
                                        {course.title.toUpperCase() || 'n.a.'}
                                    </h2>
                                    {#if !course.one_fee_payment}
                                        {#if course.course_type == 1}
                                            {#if !course.one_fee_payment && parseFloat(course.one_fee) > 0}
                                                <span
                                                    class="label label-lg label-light-info label-inline font-size-xl font-weight-bolder py-5">
                                                    {course.fee} €
                                                </span>
                                            {/if}
                                        {:else if course.course_type == 2}
                                            <span
                                                class="label label-lg label-light-info label-inline font-size-xl font-weight-bolder py-5">
                                                Quote multiple
                                            </span>
                                        {:else if course.course_type == 3}
                                            <span
                                                class="label label-lg label-light-info label-inline font-size-xl font-weight-bolder py-5">
                                                Abbonamento
                                            </span>
                                        {/if}
                                    {/if}
                                </div>
                                {#if course.one_fee_payment && parseFloat(course.one_fee) > 0}
                                <div class="text-left mb-3">
                                    Puoi pagare il corso in un'unica soluzione oppure in rate.
                                </div>
                                <div class="d-flex justify-content-center align-items-center mb-3" style="gap: 1rem;flex-wrap: wrap;">
                                    <span
                                        class="label label-lg label-light-info label-inline font-size-xl font-weight-bolder py-5">
                                        {course.one_fee} € unica soluzione
                                    </span>
                                    <span
                                        class="label label-lg label-light-info label-inline font-size-xl font-weight-bolder py-5">
                                        {course.fee} € totale rate
                                    </span>
                                </div>
                                <div class="text-center mb-3 mt-4">
                                    <h5 class="mb-0 font-weight-bold">Rate</h5>
                                    <button 
                                        class="btn btn-sm btn-link font-weight-boldp-0 m-0 text-primary font-weight-bold"
                                        style="text-decoration: underline;"
                                        on:click={() => course.showInstallments = !course.showInstallments}
                                        type="button">
                                        {course.showInstallments ? 'Nascondi dettagli' : 'Mostra dettagli'}
                                    </button>
                                </div>

                                {#if course.showInstallments}
                                    <div class="table-responsive" transition:slide={{duration: 200}}>
                                        <table class="table table-sm table-borderless table-hover">
                                            <thead>
                                                <tr class="border-bottom">
                                                    <th class="text-center font-weight-bolder text-dark-75 font-size-sm py-2">
                                                        Rata
                                                    </th>
                                                    <th class="text-center font-weight-bolder text-dark-75 font-size-sm py-2">
                                                        Importo
                                                    </th>
                                                    <th class="text-center font-weight-bolder text-dark-75 font-size-sm py-2">
                                                        Scadenza
                                                    </th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {#each course.events || [] as event, index}
                                                    <tr class="border-0">
                                                        <td class="text-center py-2">
                                                            <span class="font-weight-bold text-dark">
                                                                Rata {index + 1}
                                                            </span>
                                                        </td>
                                                        <td class="text-center py-2">
                                                            <span class="text-primary font-weight-bolder font-size-lg">
                                                                {event.amount} €
                                                            </span>
                                                        </td>
                                                        <td class="text-center py-2">
                                                            <span class="text-muted font-weight-bold font-size-sm">
                                                                {event.payment_date}
                                                            </span>
                                                        </td>
                                                    </tr>
                                                {/each}
                                            </tbody>
                                        </table>
                                    </div>
                                {/if}
                                {/if}
                                <!--begin::Text-->
                                <div
                                    class="font-size-sm bg-white text-dark-75 mt-3 border border-secondary"
                                    style="padding: 1rem;border-radius: 0.55rem;">
                                    {@html course.description
                                        .replace(/&lt;/g, '<')
                                        .replace(/&gt;/g, '>')
                                        .substring(0, 120)}{course.description.length > 120 ? '...' : ''}
                                </div>
                            </div>
                        </div>
                        <!--end::Body-->
                        <!--begin::Footer-->
                        <div class="card-footer border-0 d-flex align-items-center justify-content-between p-6 pt-0">
                            <!--begin::Label-->
                            <!-- <span class="label label-lg label-light-primary label-inline font-size-sm font-weight-bolder py-5"> -->
                            {#if __bakney.OEM_CONFIG?.showCourseAthletes}
                                <span class="label label-lg label-pill label-inline font-weight-boldest mr-2">
                                    {course.total_subscriptions} iscritti
                                </span>
                            {/if}
                            <button
                                data-toggle="modal"
                                data-target="#subscription-course-{course.course_id}"
                                class="btn btn-sm btn-primary font-size-lg font-weight-bolder px-4 d-flex align-items-center mb-0"
                                class:disabled={course.full || false}
                                style={course.full ? 'pointer-events: none;' : ''}
                                on:click={course.modal.reset}>
                                <PencilSimple size={18} weight="duotone" class="mr-2" />
                                Iscriviti
                            </button>
                        </div>
                        <!--end::Footer-->
                    </div>
                    <!--end::Stats Widget 10-->
                    <!-- <sup style="position:absolute;"><div class="coming-soon-badge">Funzionalità in Arrivo</div></sup> -->
                </div>
                <!-- ADD TO COURSE MODAL -->
                <AddToCourse
                    bind:this={course.modal}
                    id={course.course_id}
                    callback={loadProfile}
                    course={course.title}
                    multiple_quotes={course.multiple_quotes}
                    one_fee_payment={course.one_fee_payment}
                    bind:full={course.full} />
            {/if}
        {/each}
    {/if}
</div>
{/if}