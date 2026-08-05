<script>
    import {ImageUpload} from 'components/formBuilder/preview-blocks';
    import CurrencyInput from 'components/formBuilder/preview-blocks/currency-input.svelte';
    import TextInput from 'components/formBuilder/preview-blocks/text-input.svelte';
    import {DayMonthPicker} from 'components/pickers';
    import {Info, Warning} from 'phosphor-svelte';
    import {billingData, role, userData} from 'store/stores';
    import {apiFetch} from 'utils/ApiMiddleware';
    import {getDataFromForm} from 'utils/Functions';
    import confetti from 'canvas-confetti';
    import {toast} from 'svelte-sonner';
    import {setPermissions} from 'utils/Permissions';

    userData.useLocalStorage();

    export let currentStep;
    export let isFinalStep = false;
    export let isLoading = false;
    let selectedDay = $userData?.balance_sheet_start_day || 1;
    let selectedMonth = $userData?.balance_sheet_start_month || 1;
    let selectedBalanceSheetDay = $userData?.balance_sheet_year || 1;
    let selectedBalanceSheetMonth = $userData?.balance_sheet_year || 1;

    async function completeWelcomeStep(res) {
        if (!res.error) {
            confetti({
                particleCount: 200,
                spread: 400,
                origin: {
                    y: 0.5,
                },
            });
            toast.success('Configurazione del tuo gestionale completata');

            const [profileResult, billingResult] = await Promise.all([
                apiFetch(__bakney.env.API.PROFILE.INFO),
                apiFetch(__bakney.env.API.BILLING.ACTIVE_PLAN),
            ]);

            if (profileResult.error || billingResult.error) {
                isLoading = false;
                toast.error('Impossibile aggiornare i dati del profilo. Riprova.');
                return;
            }

            const currentRole = profileResult.response.info.role;
            userData.set(profileResult.response.user_data);
            role.set(currentRole);
            billingData.set(billingResult.response.data);
            setPermissions(billingResult.response.data?.active_plan?.billing_type, currentRole);

            window.location.replace('/#/');
        } else {
            isLoading = false;
            toast.error('Errore nella configurazione del tuo gestionale');
        }
    }

    async function handleSubmit(e) {
        if (isFinalStep) isLoading = true;
        // get the form data
        let data = getDataFromForm(e);
        let res = await apiFetch(__bakney.env.API.ONBOARDING.UPDATE, {
            method: 'PATCH',
            body: JSON.stringify({
                membership_data: {
                    season: {
                        day: selectedDay,
                        month: selectedMonth,
                    },
                    fiscal: {
                        day: selectedBalanceSheetDay,
                        month: selectedBalanceSheetMonth,
                    },
                    subscription_fee: parseFloat(String(data.subscription_fee).replace(',', '.')),
                    membership_fee: parseFloat(String(data.membership_fee).replace(',', '.')),
                },
            }),
        });

        if (!res.error) {
            if (isFinalStep) {
                await completeWelcomeStep(res);
            } else {
                currentStep += 1;
            }
        } else if (isFinalStep) {
            isLoading = false;
            toast.error('Errore nella configurazione del tuo gestionale');
        }
    }
</script>

<form on:submit|preventDefault={handleSubmit}>
    <div class="d-flex flex-wrap justify-content-between">
        <div class="form-group mb-0">
            <label for="season_start">Data di inizio stagione sportiva<b>*</b></label>
            <DayMonthPicker id="season_start" bind:selectedDay bind:selectedMonth />
            <div class="text-primary align-items-center d-flex font-weight-bold mt-1 font-size-sm">
                <Info size={14} weight={'duotone'} class="mr-1" />
                Inserisci la data di inizio della stagione sportiva
            </div>
        </div>
        <div class="form-group mb-0">
            <label for="fiscal_start">Anno fiscale<b>*</b></label>
            <DayMonthPicker
                id="fiscal_start"
                bind:selectedDay={selectedBalanceSheetDay}
                bind:selectedMonth={selectedBalanceSheetMonth} />
            <div class="text-primary align-items-center d-flex font-weight-bold mt-1 font-size-sm">
                <Info size={14} weight={'duotone'} class="mr-1" />
                Inserisci la data di inizio dell'anno fiscale
            </div>
        </div>
    </div>
    <div class="row">
        <CurrencyInput
            customClasses={'px-0 mx-0 my-6 px-md-4  col-12 col-md-6'}
            editable={false}
            active={false}
            props={{
                id: 'subscription_fee',
                name: 'subscription_fee',
                label: 'Quota associativa',
                required: true,
                helperLabel: 'Questa è la quota associativa pagata solo dai soci.',
                value: parseFloat($userData?.sport_association?.subscription_fee) || null,
            }} />

        <CurrencyInput
            customClasses={'px-0 mx-0 my-6 px-md-4  col-12 col-md-6'}
            editable={false}
            active={false}
            props={{
                id: 'membership_fee',
                name: 'membership_fee',
                label: 'Quota tesseramento',
                required: false,
                helperLabel: 'Lascia vuoto se non hai una quota tesseramento.',
                value: parseFloat($userData?.sport_association?.membership_fee) || null,
            }} />
    </div>

    <div
        class="alert alert-info bg-light-info d-flex align-items-center text-info font-weight-bold border border-info rounded-lg">
        <div class="alert-icon mr-2">
            <Info size={14} weight={'duotone'} />
        </div>
        <div class="alert-text">
            Potrai modificare le impostazioni in seguito e aggiungere <b class="font-weight-boldest">ulteriori quote</b>
            o impostare la durata di
            <b class="font-weight-boldest">tesseramenti</b>
            e <b>iscrizioni</b>.
        </div>
    </div>

    <slot />
</form>
