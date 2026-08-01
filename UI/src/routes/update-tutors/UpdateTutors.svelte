<script>
	import { AlertTriangle } from 'lucide-svelte';
    import {onMount} from 'svelte';
    import {slide} from 'svelte/transition';
    import TutorData from './TutorData.svelte';

    let inconsistencies = [];
    let currentIdxOpen = null;

    onMount(() => {
        if (inconsistencies.length == 0) location.href = '/';
    });

    function toggleModal(idx) {
        currentIdxOpen = idx;
    }
</script>

{#if inconsistencies.length > 0}
    <div class="row" in:slide>
        <div class="d-flex justify-content-center p-8 px-12 m-auto">
            <img id="logo" class="h-40px" src="/oem/assozeta/brand/logo.svg" alt="logo" />
        </div>
        <div class="col-12 px-md-16 p-1">
            <div class="card card-custom card-stretch gutter-b m-auto" style="max-width:50rem;">
                <!--begin::Header-->
                <div class="card-header border-0 pt-6">
                    <h3 class="card-title align-items-start flex-column">
                        <span class="card-label font-weight-bolder font-size-h2 text-dark-75"
                            >Dati dei tutor mancanti</span>
                        <div class="alert alert-custom alert-light-danger fade show my-7" role="alert">
                            <div class="alert-icon">
                                <AlertTriangle size={16} />
                            </div>
                            <div class="alert-text font-size-md font-size-sm-xl">
                                Alcuni atleti minori non hanno un tutor associato. Per favore, compila questi dati prima
                                di continuare.
                            </div>
                        </div>
                    </h3>
                </div>
                <!--end::Header-->
                <!--begin::Body-->
                <div class="card-body pt-7">
                    {#each inconsistencies as element, idx}
                        <div class="d-flex align-items-center mb-2 py-5 px-4">
                            <!--begin::Content-->
                            <div class="d-flex align-items-center flex-wrap flex-row-fluid">
                                <!--begin::Text-->
                                <div class="d-flex flex-column pr-5 flex-grow-1 col-7">
                                    <!-- svelte-ignore a11y-missing-attribute -->
                                    <a class="text-dark text-hover-primary mb-1 font-weight-bolder font-size-lg"
                                        >{element.first_name} {element.last_name}</a>
                                </div>
                                <!-- svelte-ignore a11y-missing-attribute -->
                                <div class="col-5 d-flex justify-content-end">
                                    <a
                                        data-toggle="modal"
                                        data-target="#tutor-{idx}"
                                        class="btn btn-light-danger btn-sm"
                                        on:click={() => toggleModal(idx)}>Inserisci Tutore</a>
                                </div>
                            </div>
                            <!--end::Content-->
                        </div>

                        <div
                            class="modal fade p-0"
                            id="tutor-{idx}"
                            tabindex="-1"
                            role="dialog"
                            aria-labelledby="exampleModalLabel"
                            aria-hidden="true">
                            <div class="modal-dialog modal-dialog-centered modal-xl" role="document">
                                {#if currentIdxOpen == idx}
                                    <TutorData bind:athlete={element} {idx} />
                                {/if}
                            </div>
                        </div>
                    {/each}
                </div>
                <!--end::Body-->
            </div>
        </div>
    </div>
{/if}
