<script>
	import { X } from 'lucide-svelte';
    import {onMount, createEventDispatcher} from 'svelte';
    import {apiFetch} from 'utils/ApiMiddleware';
    import {slide} from 'svelte/transition';
    import {Circle} from 'svelte-loading-spinners';
    import {toast} from 'svelte-sonner';
    import {hideModal} from 'shim/modal.js';

    const dispatch = createEventDispatcher();

    let updating = false;
    let carnetList = [];
    let fetching = true;
    let selectedCarnetId = null;

    onMount(() => {
        document.getElementById('assign-carnet-modal')?.addEventListener('shown.bs.modal', () => {
            fetchData();
        });
    });

    async function fetchData() {
        const response = await apiFetch(__bakney.env.API.CARNET.LIST, {
            method: 'GET',
        });

        if (!response.error) {
            if (response.response.data.length > 0) {
                carnetList = response.response.data;
            }
        } else {
            toast.error('Qualcosa è andato storto.');
        }
        fetching = false;
    }

    async function updateData() {
        updating = true;
        dispatch('update', {
            carnet_id: selectedCarnetId,
        });
        hideModal(`assign-carnet-modal`);
        updating = false;
    }
</script>

<div
    class="modal fade"
    id="assign-carnet-modal"
    tabindex="-1"
    role="dialog"
    aria-labelledby="exampleModalLabel"
    aria-hidden="true">
    <div class="modal-dialog modal-dialog-centered modal-md" role="document">
        <div class="modal-content">
            <div class="modal-header">
                <h5 class="modal-title" id="exampleModalLabel">Assegna Carnet</h5>
                <button type="button" class="close" data-dismiss="modal" aria-label="Close">
                    <X size={16} aria-hidden="true" />
                </button>
            </div>
            <div class="modal-body">
                <div>
                    <p>Seleziona il carnet da assegnare.</p>
                    <div class="form-group row mb-0">
                        <div class="col-12 col-form-label">
                            <div
                                class="checkbox-list scroll scroll-pull"
                                style="max-height: 30rem; overflow-y: scroll;"
                                data-scroll="true"
                                max-data-height="400px"
                                in:slide>
                                {#if carnetList.length == 0 && fetching}
                                    <div class="d-flex justify-content-center align-items-center">
                                        <Circle size="40" color="#351DC2" unit="px" duration="1s" />
                                    </div>
                                {/if}
                                {#if !fetching}
                                    <form>
                                        {#each Array.from(carnetList || []) as carnet, idx}
                                            <div
                                                in:slide={{duration: 100, delay: (1 + idx) * 5}}
                                                style="min-height: 3rem;"
                                                class="d-flex align-items-center justify-content-between mb-3 pl-4 list-item-attendees">
                                                <div>
                                                    <span />
                                                    <strong>{carnet.title}</strong>
                                                    ({carnet.fee.replace('.', ',')} € - {carnet.lessons_number} lezioni)
                                                </div>
                                                <span>
                                                    <label class="radio radio-lg">
                                                        <input
                                                            type="radio"
                                                            bind:group={selectedCarnetId}
                                                            value={carnet.carnet_id}
                                                            name="carnet-select" />
                                                        <span />
                                                    </label>
                                                </span>
                                            </div>
                                        {/each}
                                    </form>
                                {/if}
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            <div class="modal-footer">
                <!-- svelte-ignore a11y-missing-attribute -->
                <a class="btn btn-light-primary font-weight-bolder" data-dismiss="modal">Chiudi</a>
                <!-- svelte-ignore a11y-missing-attribute -->
                <!-- svelte-ignore a11y-click-events-have-key-events -->
                <button
                    disabled={selectedCarnetId === null}
                    class="btn btn-primary font-weight-bold"
                    on:click={updateData}>
                    {#if updating}
                        <Circle size="18" color="#ffffff" unit="px" duration="1s" />
                    {:else}
                        Assegna carnet
                    {/if}
                </button>
            </div>
        </div>
    </div>
</div>

<svelte:head>
    <style>
        .disabled-item {
            opacity: 0.6;
            pointer-events: none;
        }
        .list-item-attendees {
            border-left: 0.5rem solid #351dc2;
            padding: 0.5rem;
            border-radius: 0.3rem;
        }
        /* width */
        .checkbox-list::-webkit-scrollbar {
            width: 8px;
            border-radius: 0.55rem;
        }

        /* Track */
        .checkbox-list::-webkit-scrollbar-track {
            background: var(--bg-surface-secondary);
        }

        /* Handle */
        .checkbox-list::-webkit-scrollbar-thumb {
            background: var(--border-color);
            border-radius: 0.55rem;
        }

        /* Handle on hover */
        .checkbox-list::-webkit-scrollbar-thumb:hover {
            background: var(--border-color-dark);
        }
    </style>
</svelte:head>
