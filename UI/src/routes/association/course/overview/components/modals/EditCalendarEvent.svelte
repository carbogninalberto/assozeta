<script>
	import { Info as LucideInfo, X } from 'lucide-svelte';
    import {apiFetch, replaceUID} from 'utils/ApiMiddleware';
    const scrollToTop = () => window.scrollTo({ top: 0, behavior: 'smooth' });
import Portal from 'svelte-portal';
    import {getDataFromForm} from 'utils/Functions';
    import {onDestroy, onMount, createEventDispatcher} from 'svelte';
    import Select from 'svelte-select';
    import {Info} from 'phosphor-svelte';
    import {canPerformAction} from 'utils/Permissions';
    import {showModal, hideModal} from 'shim/modal.js';

    const dispatch = createEventDispatcher();

    export let instructors = [];
    export let row;

    let selectedInstructor = null;
    let form;
    let modalElement;
    let hiddenHandler;

    function initForm() {
        form?.destroy();
        form = FormValidation.formValidation(document.getElementById('form_add_calendar_event'), {
            fields: {
                event_title: {
                    validators: {
                        notEmpty: {
                            message: 'Il nome della lezione è obbligatorio.',
                        },
                    },
                },
            },
            plugins: {
                trigger: new FormValidation.plugins.Trigger(),
                bootstrap: new FormValidation.plugins.Bootstrap(),
                // submitButton: new FormValidation.plugins.SubmitButton(),
            },
        });
    }

    async function save(data) {
        return dispatch('save', data);
    }

    async function deleteEvent(groupId, before) {
        return dispatch('delete', {
            groupId: groupId || null,
            before: before,
        });
    }

    function handleValidation(e) {
        if (!form) initForm();
        form?.validate().then(function (status) {
            if (status === 'Valid') {
                save(getDataFromForm(e));
                requestClose();
            } else {
                swal.fire({
                    text: 'Per favore, inserisci tutti i dati e riprova.',
                    icon: 'error',
                    buttonsStyling: false,
                    confirmButtonText: 'Ok, capito!',
                    customClass: {
                        confirmButton: 'btn font-weight-bold btn-light-primary',
                    },
                }).then(function () {
                    scrollToTop();
                });
            }
        });
    }

    function requestClose() {
        hideModal('editEventElement');
    }

    function requestDelete(groupId, before) {
        deleteEvent(groupId, before);
        requestClose();
    }

    onMount(async () => {
        // get element with name instructor
        // let inst = document.getElementById('instructor-select');
        // inst.value = row.extendedProps?.instructor?.value || null;
        // check if row.extendedProps?.instructor is an Array
        if (Array.isArray(row.extendedProps?.instructor)) {
            selectedInstructor = row.extendedProps?.instructor || [];
        } else if (row.extendedProps?.instructor) {
            selectedInstructor = [row.extendedProps?.instructor] || [];
        }
        modalElement = document.getElementById('editEventElement');
        hiddenHandler = () => {
            document.querySelectorAll('#form_add_calendar_event').forEach(item => {
                item.remove();
            });
            dispatch('close');
        };
        modalElement?.addEventListener('hidden.bs.modal', hiddenHandler);
        showModal('editEventElement');
    });

    onDestroy(() => {
        modalElement?.removeEventListener('hidden.bs.modal', hiddenHandler);
        form?.destroy();
    });
</script>

<!-- svelte-ignore missing-declaration -->
<!-- Modal-->
<form class="form" id="form_add_calendar_event" on:submit|preventDefault={handleValidation}>
    <div
        class="modal fade"
        id="editEventElement"
        tabindex="-1"
        role="dialog"
        aria-labelledby="staticBackdrop"
        aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered modal-dialog-scrollable" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="exampleModalLabel">Lezione</h5>
                    <button type="button" class="close" aria-label="Close" on:click={requestClose}>
                        <X size={16} aria-hidden="true" />
                    </button>
                </div>
                <div class="modal-body" style="overflow: visible">
                    <div>
                        <div class="row">
                            <div class="col-12">
                                <div
                                    class="d-flex align-items-center text-bold text-info bg-light-info p-4 mb-4"
                                    style="border-radius: .35rem;">
                                    <LucideInfo size={18} weight="duotone" class="mr-2" />
                                    {#if row.allDay == 'on' || row.allDay == true}
                                        La lezione si terrà il giorno {moment(row.start).format('DD/MM/YYYY')}
                                    {:else}
                                        La lezione si terrà il giorno {moment(row.start).format('DD/MM/YYYY')} dalle ore
                                        {moment(row.start).format('HH:mm')} alle ore {moment(row.end).format('HH:mm')}
                                    {/if}
                                </div>
                            </div>
                        </div>
                        <div class="form-group">
                            <!-- svelte-ignore a11y-label-has-associated-control -->
                            <label>Nome*</label>
                            <input
                                bind:value={row.title}
                                name="event_title"
                                type="text"
                                class="form-control form-control-solid form-control-lg margin-tb-2"
                                placeholder="Nome lezione" />
                        </div>
                        <div class="row mt-2 mb-4">
                            <div class="form-group col-12 mb-0">
                                <!-- svelte-ignore a11y-label-has-associated-control -->
                                <label class="font-size-h6 font-weight-bold">Descrizione</label>
                                <textarea
                                    value={row.extendedProps?.description || ''}
                                    class="form-control form-control-solid"
                                    rows="3"
                                    name="description"
                                    label="Descrizione"
                                    placeholder="Inserisci una descrizione" />
                            </div>
                        </div>
                        <div class="form-group" style="z-index:9000">
                            <!-- svelte-ignore a11y-label-has-associated-control -->
                            <label>Istruttore lezione</label>
                            <Select
                                hideEmptyState={true}
                                multiple={true}
                                id="instructor-select"
                                bind:items={instructors}
                                placeholder="Seleziona l'istruttore"
                                name="instructor"
                                bind:value={selectedInstructor} />
                        </div>
                    </div>
                    {#if row.extendedProps?.groupId && canPerformAction('association.courses.update')}
                        <div class="row px-4">
                            <!-- vuoi eliminare tutti gli eventi periodici? -->
                            <div
                                class="col-12"
                                style="border: 1px solid var(--border-color); padding: 1rem 1.5rem; border-radius: 0.55rem;">
                                <h6 class="mb-2">Eliminazione periodici associati</h6>
                                <button
                                    type="button"
                                    class="btn btn-sm btn-light-danger font-weight-bold mb-0 mt-2"
                                    on:click={() => requestDelete(row.extendedProps?.groupId)}>Tutti</button>
                                <button
                                    type="button"
                                    class="btn btn-sm btn-light-danger font-weight-bold mb-0 mt-2"
                                    on:click={() => requestDelete(row.extendedProps?.groupId, false)}
                                    >Questo e successivi</button>
                                <button
                                    type="button"
                                    class="btn btn-sm btn-light-danger font-weight-bold mb-0 mt-2"
                                    on:click={() => requestDelete(row.extendedProps?.groupId, true)}
                                    >Questo e precedenti</button>
                            </div>
                        </div>
                    {/if}
                </div>
                <div class="modal-footer">
                    <button
                        type="button"
                        disabled={!canPerformAction('association.courses.update')}
                        class="btn btn-light-danger font-weight-bold"
                        on:click={() => requestDelete()}>Elimina</button>
                    <button type="button" class="btn btn-light-primary font-weight-bold" on:click={requestClose}
                        >Chiudi</button>
                    <button
                        disabled={!canPerformAction('association.courses.update')}
                        type="submit"
                        class="btn btn-primary font-weight-bold">Salva</button>
                </div>
            </div>
        </div>
    </div>
</form>

<svelte:head>
    <style>
        .svelte-select {
            font-size: 13px !important;
            padding-left: 1rem !important;
            border: 0 !important;
            background: var(--bg-surface-secondary) !important;
            font-size: 13px !important;
            color: var(--text-primary) !important;
        }
        .svelte-select input:focus {
            border: 0 !important;
            outline: 0 !important;
        }
        .svelte-select input {
            font-size: 13px !important;
            color: var(--text-primary) !important;
        }
        .svelte-select .selected-item {
            font-size: 13px !important;
        }
        .svelte-select-list {
            z-index: 90000 !important;
        }
    </style>
</svelte:head>
