<script>
    import BasicModal from 'components/modals/BasicModal.svelte';
    import Select from 'svelte-select';
    import DateInput from 'components/inputs/DateInput.svelte';
    import {apiFetch, replaceUID} from 'utils/ApiMiddleware.js';
    import {createMembershipDraft, membershipEndDate, validateMembershipForm, membershipApiError} from 'utils/membershipForm.js';
    import {toast} from 'svelte-sonner';
    import {blockPage, unblockPage} from 'store/loadingStore.js';
    import {createEventDispatcher, onMount} from 'svelte';
    import {userData} from 'store/stores';

    const dispatch = createEventDispatcher();
    userData.useLocalStorage();
    export let show;
    export let data = {};
    export let info = {};
    export let edit = false;

    // Initialize once: changing dates must not reapply record/course defaults to other fields.
    let draft = createMembershipDraft(data, info, $userData);
    const associate = data.subscription?.associate || data.associate;
    let selectedAthletes = data.subscription_id || data.subscription?.subscription_id ? [{
        value: data.subscription_id || data.subscription.subscription_id,
        label: [associate?.first_name, associate?.last_name].filter(Boolean).join(' ').toUpperCase(),
    }] : [];
    let availableAssociates = [];
    let loading = true;
    let saving = false;
    let errors = {};
    let requestError = '';
    let loadError = '';
    $: billedUntil = membershipEndDate(draft, info);

    async function save() {
        if (saving) return;
        const validation = validateMembershipForm(draft, selectedAthletes, info, edit);
        errors = validation.errors;
        requestError = '';
        if (Object.keys(errors).length) return;
        const subscriptions = selectedAthletes.map(athlete => ({
            course: info.course_id, subscription_id: athlete.value, ...validation.billing,
        }));
        saving = true;
        blockPage({overlayColor: '#000000', state: 'primary', message: edit ? 'Aggiornamento...' : 'Creazione...'});
        try {
            const response = await apiFetch(edit
                ? replaceUID(__bakney.env.API.COURSE_SUBSCRIPTIONS.UPDATE, data.course_subscription_id)
                : __bakney.env.API.COURSE_SUBSCRIPTIONS.ADD, {
                method: edit ? 'PATCH' : 'POST',
                body: JSON.stringify(edit ? subscriptions[0] : subscriptions),
            });
            if (response.error || ![200, 201].includes(response.status)) {
                requestError = membershipApiError(response.response) || "Impossibile salvare l'abbonamento. Riprova.";
                return;
            }
            toast.success(edit ? 'Abbonamento aggiornato con successo.' : 'Abbonamento creato con successo.');
            dispatch('update');
            show = false;
        } catch {
            requestError = 'Connessione non disponibile. I dati inseriti sono stati mantenuti: riprova.';
        } finally {
            saving = false;
            unblockPage();
        }
    }

    async function fetchAvailableAssociates() {
        loading = true;
        loadError = '';
        try {
            const response = await apiFetch(`${__bakney.env.HOST}/subscription/list/all?current_year=true&type=athletes&course_id=${info.course_id}`);
            if (response.error) throw new Error();
            availableAssociates = Object.values(response.response.data || {}).map(athlete => ({
                value: athlete.subscription_id,
                label: `${athlete.associate.first_name.toUpperCase()} ${athlete.associate.last_name.toUpperCase()} (${athlete.current_year ? 'Anno corrente' : 'Anni precedenti'})`,
            }));
        } catch {
            loadError = 'Impossibile caricare i tesserati. Riprova.';
        } finally {
            loading = false;
        }
    }
    onMount(fetchAvailableAssociates);
</script>

<BasicModal id="subscription-modal" bind:show title="{edit ? 'Modifica' : 'Crea'} abbonamento"
    showTitle={true} showFooter={false} modalSize="md" scrollable={false}
    hideOnClickOutside={!saving} bodyClass="py-2 px-0">
    {#if loading}
        <div class="d-flex justify-content-center align-items-center" style="height: 100px;"><div class="spinner spinner-primary spinner-lg" /></div>
    {:else}
        <form id="course_subscription_form" novalidate on:submit|preventDefault={save}>
            <div class="px-7">
                {#if requestError}<div class="alert alert-danger" role="alert">{requestError}</div>{/if}
                {#if loadError}
                    <div class="alert alert-danger" role="alert">{loadError} <button type="button" class="btn btn-sm btn-light" on:click={fetchAvailableAssociates}>Riprova</button></div>
                {/if}
                {#if Object.keys(errors).length}
                    <div class="alert alert-danger" role="alert"><ul class="mb-0">{#each Object.values(errors) as message}<li>{message}</li>{/each}</ul></div>
                {/if}
                <fieldset disabled={saving}>
                    <div class="form-group mb-4">
                        <label class="font-weight-bolder" for="membership-athletes">Tesserati*</label>
                        <Select inputAttributes={{id: 'membership-athletes', 'aria-label': 'Tesserati'}}
                            items={availableAssociates} bind:value={selectedAthletes} multiple={true}
                            disabled={saving || edit || Boolean(data.disable_selection_of_athletes)}
                            placeholder="Seleziona i tesserati" class="form-control selectpicker form-control-solid form-control-lg h-auto" />
                    </div>
                    <div class="form-group mb-4">
                        <label class="font-weight-bolder" for="membership-fee">Quota di iscrizione (€)*</label>
                        <input id="membership-fee" name="membership_fee" type="text" inputmode="decimal" bind:value={draft.membership_fee}
                            aria-invalid={Boolean(errors.membership_fee)} class="form-control form-control-solid form-control-lg" />
                    </div>
                    {#if !info.billed_duration_is_sport_season}
                        <div class="form-group mb-4">
                            <label class="font-weight-bolder" for="membership-frequency">Durata abbonamento (mesi)*</label>
                            <select id="membership-frequency" bind:value={draft.billed_frequency} aria-invalid={Boolean(errors.billed_frequency)} class="form-control form-control-solid form-control-lg">
                                {#each Array.from({length: 12}, (_, index) => index + 1) as months}<option value={months}>{months} {months === 1 ? 'mese' : 'mesi'}</option>{/each}
                            </select>
                        </div>
                        {#if !info.billed_from_subscription_date}
                            <div class="form-group mb-4">
                                <label class="font-weight-bolder" for="membership-day">Si rinnova il giorno*</label>
                                <select id="membership-day" bind:value={draft.billed_from_day_of_month} aria-invalid={Boolean(errors.billed_from_day_of_month)} class="form-control form-control-solid form-control-lg">
                                    {#each Array.from({length: 28}, (_, index) => index + 1) as day}<option value={day}>{day} del mese</option>{/each}
                                </select>
                            </div>
                        {/if}
                    {/if}
                    <div class="form-group mb-4">
                        <label class="font-weight-bolder" for="membership-from">Data inizio*</label>
                        <DateInput id="membership-from" name="billed_from" format="YYYY-MM-DD" required={true} bind:value={draft.billed_from} disabled={saving} />
                    </div>
                    <div class="form-group mb-4">
                        <label class="font-weight-bolder" for="membership-until">Data fine{info.billed_duration_is_sport_season ? '*' : ' (calcolata)'}</label>
                        {#if info.billed_duration_is_sport_season}
                            <DateInput id="membership-until" name="billed_until" format="YYYY-MM-DD" required={true} bind:value={draft.billed_until} min={draft.billed_from || undefined} disabled={saving} />
                        {:else}
                            <input id="membership-until" type="date" value={billedUntil} readonly class="form-control form-control-solid form-control-lg" />
                            <small class="form-text text-muted">Calcolata dalla data di inizio e dalla durata. In modifica, la scadenza corrente viene mantenuta finché non cambi questi valori.</small>
                        {/if}
                    </div>
                    <h5 class="font-weight-bolder font-size-h4 mb-4 mt-4">Opzioni</h5>
                    <div class="d-flex flex-wrap justify-content-between mb-4" style="gap: 1.5rem;">
                        <label class="checkbox"><input type="checkbox" bind:checked={draft.membership_active} /><span />Abbonamento attivo</label>
                        <label class="checkbox"><input type="checkbox" bind:checked={draft.auto_renewal} /><span />Rinnovo automatico</label>
                    </div>
                </fieldset>
            </div>
            {#if edit}<div class="bg-light-info py-2 px-4 rounded-xl text-primary mx-6 mb-8">Le modifiche aggiornano il pagamento corrente solo se non è già stato pagato.</div>{/if}
            <div class="modal-footer d-flex justify-content-end mt-2">
                <button type="button" disabled={saving} class="btn btn-light-primary font-weight-bold" on:click={() => (show = false)}>Annulla</button>
                <button type="submit" disabled={saving || (Boolean(loadError) && !selectedAthletes.length)} class="btn btn-primary font-weight-bold">{saving ? 'Salvataggio…' : edit ? 'Salva' : 'Crea'}</button>
            </div>
        </form>
    {/if}
</BasicModal>
