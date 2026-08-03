<script>
    import {PhoneInput, SmartSelect} from 'components/formBuilder/preview-blocks';
    import TextInput from 'components/formBuilder/preview-blocks/text-input.svelte';
    import {billingData, role, userData} from 'store/stores';
    import {apiFetch} from 'utils/ApiMiddleware';
    import {getDataFromForm} from 'utils/Functions';
    import confetti from 'canvas-confetti';
    import {toast} from 'svelte-sonner';
    import {setPermissions} from 'utils/Permissions';

    userData.useLocalStorage();

    export let isLoading = false;

    const sportAssociationSizes = [
        {label: 'meno di 50 tesserati', value: 'meno di 50 tesserati'},
        {label: '50 - 100 tesserati', value: '50 - 100 tesserati'},
        {label: '100 - 200 tesserati', value: '100 - 200 tesserati'},
        {label: '200 - 500 tesserati', value: '200 - 500 tesserati'},
        {label: 'più di 500 tesserati', value: 'più di 500 tesserati'},
    ];

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
        isLoading = true;
        // get the form data
        let data = getDataFromForm(e);
        let res = await apiFetch(__bakney.env.API.ONBOARDING.UPDATE, {
            method: 'PATCH',
            body: JSON.stringify({
                lead_data: {
                    lead_sport_association_role: data.lead_sport_association_role,
                    lead_sport_market_channel: data.lead_sport_market_channel,
                    lead_sport_association_size: JSON.parse(data.lead_sport_association_size)?.value,
                    phone: data.phone,
                },
            }),
        });
        await completeWelcomeStep(res);
    }
</script>

<form on:submit|preventDefault={handleSubmit}>
    <div class="row">
        <TextInput
            customClasses={'px-0 mx-0 my-6 px-md-4 col-12'}
            editable={false}
            active={false}
            props={{
                id: 'lead_sport_association_role',
                name: 'lead_sport_association_role',
                label: 'Che ruolo ricopri?',
                placeholder: 'Inserisci il tuo ruolo',
                required: true,
                value: $userData?.lead_sport_association_role || '',
            }} />
    </div>
    <div class="row">
        <SmartSelect
            customClasses={'px-0 mx-0 my-6 px-md-4 col-12'}
            editable={false}
            active={false}
            props={{
                id: 'lead_sport_market_channel',
                name: 'lead_sport_market_channel',
                label: 'Da dove hai conosciuto Assozeta?',
                placeholder: 'Seleziona da dove hai conosciuto Assozeta',
                required: true,
                clearable: false,
                searchable: false,
                showChevron: true,
                options: [
                    {label: 'Facebook', value: 'facebook'},
                    {label: 'Instagram', value: 'instagram'},
                    {label: 'Linkedin', value: 'linkedin'},
                    {label: 'TikTok', value: 'tiktok'},
                    {label: 'YouTube', value: 'youtube'},
                    {label: 'Cercando su Google', value: 'google'},
                    {label: 'Consigliato da un amico', value: 'friends'},
                    {label: 'Fiera o evento', value: 'event'},
                    {label: 'Email o newsletter', value: 'email'},
                    {label: 'Pubblicità online', value: 'ads'},
                    {label: 'Altro', value: 'altro'},
                ],
                value: $userData?.lead_sport_market_channel || '',
            }} />
    </div>
    <div class="row">
        <SmartSelect
            customClasses={'px-0 mx-0 my-6 px-md-4 col-12'}
            editable={false}
            active={false}
            props={{
                id: 'lead_sport_association_size',
                name: 'lead_sport_association_size',
                label: 'Quanti tesserati ha la tua associazione?',
                placeholder: 'Seleziona quanti tesserati ha la tua associazione',
                required: true,
                clearable: false,
                searchable: false,
                showChevron: true,
                options: sportAssociationSizes,
                value: $userData?.lead_sport_association_size || sportAssociationSizes[0].value,
            }} />
    </div>
    <div class="row">
        <PhoneInput
            customClasses={'px-0 mx-0 my-6 px-md-4 col-12'}
            editable={false}
            active={false}
            props={{
                id: 'phone',
                name: 'phone',
                label: 'Numero di telefono',
                placeholder: '3401112435...',
                helperLabel: '<b>Non è obbligatorio</b>, ma utile per contattarti in caso di necessità',
                required: false,
                value: $userData?.phone || '',
            }} />
    </div>

    <slot />
</form>
