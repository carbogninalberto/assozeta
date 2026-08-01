<script>
    import {createEventDispatcher} from 'svelte';
    import BasicModal from 'components/modals/BasicModal.svelte';
    import {toast} from 'svelte-sonner';
    import Switch from 'components/inputs/Switch.svelte';

    const dispatch = createEventDispatcher();

    export let id;
    export let show;
    export let settings = {
        showScanner: true,
        showQrCode: true,
        showPinPad: true,
    };
</script>

<BasicModal
    id={`payments-modal-${id}`}
    bind:show
    title="Impostazioni"
    actionButton="Salva"
    showActionButton={false}
    showTitle={true}
    modalSize={'md'}
    scrollable={false}
    showCloseButton={false}
    showCancelButton={false}
    on:close={() => {
        dispatch('close', settings);
    }}>
    <div class="py-3">
        <Switch bind:checked={settings.showScanner} label="Abilita check-in scanner" />
        <Switch bind:checked={settings.showQrCode} label="Abilita check-in via QR code" />
        <Switch bind:checked={settings.showPinPad} label="Abilita check-in via PIN pad" />
    </div>
    <div slot="footer">
        <button
            type="button"
            class="btn btn-primary font-weight-boldest"
            on:click={() => {
                show = false;
                dispatch('close', settings);
                toast.success('Impostazioni salvate');
            }}>Salva</button>
    </div>
</BasicModal>
