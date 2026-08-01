<script>
	import { X } from 'lucide-svelte';
    import {userData} from 'store/stores';
    import Portal from 'svelte-portal';
    import {oemConfig} from 'store/instanceStore.js';

    userData.useLocalStorage();
</script>

<Portal>
    <div
        class="modal fade"
        id="newTicketModal"
        data-backdrop="static"
        tabindex="-1"
        role="dialog"
        style="z-index: 10001;">
        <div class="modal-dialog modal-dialog-centered modal-lg" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="ticketModalLabel">Apri Ticket</h5>
                    <button type="button" class="close" data-dismiss="modal" aria-label="Close">
                        <X size={16} data-dismiss="modal" />
                    </button>
                </div>
                <div class="modal-body p-0">
                    {#if $oemConfig?.supportUrl}
                    <!-- svelte-ignore a11y-missing-attribute -->
                    <iframe
                        width="100%"
                        height="790"
                        src={`${$oemConfig.supportUrl}?email=${encodeURIComponent($userData.email || '')}&name=${encodeURIComponent(`${$userData.first_name || ''} ${$userData.last_name || ''}`.trim())}`}
                        frameborder="0"
                        allowfullscreen />
                    {:else}
                        <div class="p-8 text-center text-muted font-weight-bold">
                            Nessun canale di supporto configurato per questa istanza.
                        </div>
                    {/if}
                </div>
            </div>
        </div>
    </div>
</Portal>
