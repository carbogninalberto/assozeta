<script>
    import EditButton from 'components/buttons/EditButton.svelte';
    import DeleteButton from 'components/buttons/DeleteButton.svelte';
    import EditModal from './modals/EditModal.svelte';
    import {apiFetch, replaceUID} from 'utils/ApiMiddleware';
    import {canPerformAction} from 'utils/Permissions';
    import {blockPage, unblockPage} from 'store/loadingStore.js';
    import {showModal} from 'shim/modal.js';
    import {toast} from 'svelte-sonner';

    export let row;
    export let datatable;

    function remove() {
        swal.fire({
            text: 'Vuoi eliminare il collaboratore?',
            icon: 'warning',
            buttonsStyling: true,
            showCancelButton: true,
            cancelButtonText: 'Annulla',
            confirmButtonText: 'Elimina',
            reverseButtons: true,
            confirmButtonColor: '#d63030',
        }).then(async function (result) {
            if (!result.isConfirmed) return;

            blockPage({message: 'Eliminazione in corso...'});

            let id = row.collaboration_invite_id || row.user_id;

            let response;

            try {
                response = await apiFetch(
                    replaceUID(__bakney.env.API.COLLABORATORS.DELETE, id),
                    {
                        method: 'DELETE',
                    }
                );
            } finally {
                unblockPage();
            }

            if (!response.error) {
                toast.success('Collaboratore eliminato!');
                datatable.reload();
            } else {
                toast.error('Qualcosa è andato storto.');
            }
        });
    }
</script>

<div class="action-column pr-4">
    {#if row.user_id}
        <EditButton
            disabled={!canPerformAction('other.users.collaborators.update')}
            on:open={() => showModal(`editModal-${row.user_id}`)} />
        <EditModal id={row.user_id} {row} {datatable} />
    {/if}
    <DeleteButton
        disabled={!canPerformAction('other.users.collaborators.delete')}
        on:open={remove} />
</div>
