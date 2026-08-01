<script>
    import {createEventDispatcher} from 'svelte';
    import {UserList} from 'phosphor-svelte';
    import BasicModal from 'components/modals/BasicModal.svelte';
    import SmartMultiselectInput from 'components/formBuilder/preview-blocks/smart-multiselect-input.svelte';

    export let wizardData;

    const dispatch = createEventDispatcher();

    let show = true;
    let selectedMembers = [];
    let currentSubscriptions = wizardData.currentSubscriptions || [];
    let availableMembers = [];
    let multiselectOptions = [];

    // Filter out members that are already added (by tax_code)
    $: {
        const existingTaxCodes = wizardData.multipleEntryFormData
            .map(entry => entry.associate_data?.tax_code)
            .filter(Boolean);
        availableMembers = currentSubscriptions.filter(
            member => !existingTaxCodes.includes(member.associate?.tax_code)
        );

        multiselectOptions = availableMembers.map(member => ({
            label:
                `${member.associate?.first_name || ''} ${member.associate?.last_name || ''}`.trim() ||
                'Nome non disponibile',
            value: member.subscription_id,
            member: member,
        }));
    }

    function close() {
        show = false;
        dispatch('close');
    }

    function importAll() {
        selectedMembers = [...multiselectOptions];
    }

    function importSelectedMembers() {
        let members = [];
        selectedMembers.forEach(selectedOption => {
            console.log(selectedOption.member);
            // convert date from YYYY-MM-DD to DD/MM/YYYY
            selectedOption.member.associate.born_date = selectedOption.member.associate.born_date
                .split('-')
                .reverse()
                .join('/');
            let member = {...wizardData.formData};
            member.associate_data = selectedOption.member.associate;
            member.custom_data = selectedOption.member.custom_data;
            member.membership_plan_id = selectedOption.member.membership_plan_id;
            member.membership_plan_label = selectedOption.member.membership_plan_label;
            member.plan_id = selectedOption.member.meta?.plan_id;
            member.plan_label = selectedOption.member.meta?.plan_label;
            member.membership_plan_id = selectedOption.member.meta?.membership_plan_id;
            member.membership_plan_label = selectedOption.member.meta?.membership_plan_label;
            member.preregistration = wizardData.formData.preregistration;
            member.type = selectedOption.member.type;
            member.associate_data.type = selectedOption.member.type;
            members.push(member);
        });

        dispatch('close', members);
        show = false;
    }
</script>

<BasicModal
    bind:show
    title="Importa iscritti dall'anno precedente"
    showTitle={true}
    cancelButton="Annulla"
    actionButton="Importa selezionati"
    showActionButton={selectedMembers.length > 0}
    modalSize="lg"
    on:cancel={close}
    on:confirm={importSelectedMembers}
    on:close={close}>
    {#if availableMembers.length === 0}
        <div class="d-flex flex-column justify-content-center align-items-center text-dark-50 my-5 font-weight-bolder">
            <UserList size={64} weight="duotone" class="mb-3" />
            <p class="text-center">
                {#if currentSubscriptions.length === 0}
                    Non ci sono iscrizioni precedenti disponibili per l'importazione.
                {:else}
                    Tutti gli iscritti dell'anno precedente sono già stati aggiunti.
                {/if}
            </p>
        </div>
    {:else}
        <div class="mb-4">
            <p class="text-muted mb-4">Seleziona gli iscritti dall'anno precedente che vuoi importare:</p>

            <div class="d-flex gap-2 w-100 justify-content-between align-items-center">
                <SmartMultiselectInput
                    props={{
                        label: 'Iscritti disponibili',
                        placeholder: 'Cerca e seleziona gli iscritti...',
                        options: multiselectOptions,
                        value: selectedMembers,
                        searchable: true,
                        clearable: true,
                    }}
                    bind:value={selectedMembers}
                    editable={false}
                    customClasses="mb-4 w-fill" />

                {#if multiselectOptions.length > 0}
                    <div class="d-flex justify-content-between align-items-center" style="min-width:8rem;">
                        <button
                            type="button"
                            style="height: 3.3rem;margin-top:1.3rem;"
                            class="btn btn-primary btn-sm w-fill font-weight-bold mb-0"
                            on:click={importAll}>
                            Seleziona tutti
                        </button>
                    </div>
                {/if}
            </div>

            {#if selectedMembers.length > 0}
                <div class="mt-4">
                    <h6 class="font-weight-bolder mb-3">Anteprima iscritti selezionati ({selectedMembers.length}):</h6>
                    <div class="max-h-300px overflow-auto">
                        {#each selectedMembers as selectedOption}
                            {@const member = selectedOption.member}
                            <div class="mb-2 py-2 px-3 border border-light rounded">
                                <div class="d-flex justify-content-between align-items-center">
                                    <div class="font-weight-boldest">
                                        {member.associate?.first_name || '-'}
                                        {member.associate?.last_name || '-'}
                                    </div>
                                    <div class="text-muted small">
                                        {#if member.associate?.tax_code}
                                            CF: {member.associate.tax_code}
                                        {/if}
                                    </div>
                                </div>
                                {#if member.associate?.email || member.associate?.phone}
                                    <div class="mt-1 text-muted small">
                                        {#if member.associate.email}
                                            <span class="me-3">Email: {member.associate.email}</span>
                                        {/if}
                                        {#if member.associate.phone}
                                            <span>Tel: {member.associate.phone}</span>
                                        {/if}
                                    </div>
                                {/if}
                            </div>
                        {/each}
                    </div>
                </div>
            {/if}
        </div>
    {/if}
</BasicModal>

<style>
    .max-h-300px {
        max-height: 300px;
    }
</style>
