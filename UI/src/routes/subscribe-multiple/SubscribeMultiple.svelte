<script>
    import {scale} from 'svelte/transition';
    import * as easing from 'svelte/easing';
    import {onMount} from 'svelte';
    import {
        ArrowRight,
        FirstAid,
        FirstAidKit,
        Info,
        PaperPlaneTilt,
        PersonSimpleRun,
        Signature,
        UsersThree,
    } from 'phosphor-svelte';
    import {apiFetch} from 'utils/ApiMiddleware';
    import {register, init, _, waitLocale, locale} from 'svelte-i18n';
    import Step0 from './wizard/Step0.svelte';
    import Step1 from './wizard/Step1.svelte';
    import Step2 from './wizard/Step2.svelte';
    import Step3 from './wizard/Step3.svelte';
    import Step4 from './wizard/Step4.svelte';
    import {toast} from 'svelte-sonner';
    import {oemConfig} from 'store/instanceStore.js';
    import StepFamily from './wizard/StepFamily.svelte';
    import {push} from 'svelte-spa-router';

    export let params;
    let loaded = false;
    let language = 'it';
    let creatingSubscription = false;

    let wizardData = {
        sportAssociationData: {
            user: {
                avatar_image: null,
                sport_association: {},
            },
            sport_association_id: null,
        },
        is_family_wizard: true,
        type: 'grouped_subscriptions',
        multipleEntryFormData: [],
        defaultCustomData: {
            type: 2,
            type_of_associate: null,
            type_of_request: 'affiliazione',
            membership_number: null,
            type_of_qualifications: null,
        },
        currentSubscriptions: [],
        formData: {
            subscription_id: null,
            preregistration: params?.preregistration === 'true' ? true : false,
            membership_plan_id: null,
            membership_plan_label: null,
            plan_id: null,
            plan_label: null,
            additional_fields: [],
            custom_data: {
                type: 2,
                type_of_associate: null,
                type_of_request: 'affiliazione',
                membership_number: null,
                type_of_qualifications: null,
            },
            new_user_account: {
                new_member: false,
                new_member_info: {
                    first_name: '',
                    last_name: '',
                    phone: '',
                    email: '',
                },
            },
            associate_data: {
                type: null,
                is_minor: false,
                first_name: '',
                last_name: '',
                sex: 'F',
                tax_code: '',
                born_date: '01/01/2000',
                born_city: '',
                address: '',
                address_city: '',
                address_cap: '',
                phone: '',
                email: '',
            },
            associate_tutor_data: {
                first_name: '',
                last_name: '',
                sex: 'F',
                tax_code: '',
                born_date: '01/01/1990',
                born_city: '',
                address: '',
                address_city: '',
                address_cap: '',
                phone: '',
                email: '',
            },
            signature: {
                there_is_signature: false,
                data: '',
            },
            medical_certificate: {
                certificate_expring_date: null,
                medical_id: null,
                filename: null,
                certificate_expring_date: null,
            },
        },
        activeStep: 0,
        maxStep: 5,
        steps: [],
        nextStep: nextStep,
        prevStep: prevStep,
        toStep: toStep,
        createSubscription: createSubscription,
    };

    function nextStep() {
        wizardData.activeStep++;
        if (wizardData.activeStep > wizardData.maxStep) {
            wizardData.activeStep = wizardData.maxStep;
        }
    }

    function prevStep() {
        wizardData.activeStep--;
        if (wizardData.activeStep < 0) {
            wizardData.activeStep = 0;
        }
    }

    function toStep(step) {
        wizardData.activeStep = step;
    }

    async function createSubscription() {
        // loop through wizardData.multipleEntryFormData and set the subscription_id to null
        for (let entry of wizardData.multipleEntryFormData) {
            entry.subscription_id = null;
        }
        wizardData.multipleEntryFormData = [...wizardData.multipleEntryFormData];
        let payments_ids = [];
        for (let entry of wizardData.multipleEntryFormData) {
            creatingSubscription = true;
            let bodyRequest = {
                sport_association: wizardData.sportAssociationData.user.username,
                ...entry,
            };

            bodyRequest.medical_certificate.certificate_expring_date = moment(
                bodyRequest.medical_certificate.certificate_expring_date,
                'DD/MM/YYYY'
            ).format('YYYY-MM-DD');

            // check if bodyRequest.medical_certificate.certificate_expring_date is a valid date
            // if not, set it to null
            if (!moment(bodyRequest.medical_certificate.certificate_expring_date).isValid()) {
                bodyRequest.medical_certificate.certificate_expring_date = null;
            }

            // perform nested object cleanup, replace all empty strings with null
            // it should go deep into the nested object until all empty strings are replaced
            const cleanObject = obj => {
                for (const key in obj) {
                    if (typeof obj[key] === 'object') {
                        cleanObject(obj[key]);
                    } else {
                        if (obj[key] === '') {
                            obj[key] = null;
                        }
                    }
                }
            };

            cleanObject(bodyRequest);

            let res = await apiFetch(`${__bakney.env.API.SUBSCRIPTION.ADD}`, {
                method: 'POST',
                body: JSON.stringify(bodyRequest),
            });

            if (!res.error) {
                toast.success('Iscrizioni create con successo.');
                // remove the entry from wizardData.multipleEntryFormData
                wizardData.multipleEntryFormData = wizardData.multipleEntryFormData.filter(
                    entry => entry.subscription_id !== null
                );
                wizardData.multipleEntryFormData = [...wizardData.multipleEntryFormData];
                payments_ids.push(res.response.payment_id);
            } else {
                if (parseInt(res.response.code ?? '400') === 409) {
                    toast.warning('Questa iscrizione esiste già.');
                } else {
                    toast.error(
                        'Si è verificato un errore durante la creazione della nuova iscrizione.\n' +
                            'Apri un ticket con il seguente errore: ' +
                            JSON.stringify(res.response)
                    );
                }
                creatingSubscription = false;
            }
        }
        if (payments_ids.length > 0) {
            // set on the session storage the payments_ids
            // redirect to the page #/stripe/cart-pay
            push('/stripe/cart-pay/?payments_ids=' + payments_ids.join(','));
        }
    }

    async function loadData() {
        // check if params?.token is a valid token
        if (params?.token) {
            let res = await apiFetch(
                `${__bakney.env.API.SUBSCRIPTION.VALIDATE_TOKEN_LINK_AND_GET_SUBSCRIPTIONS}?token=${params?.token}`,
                {
                    method: 'GET',
                }
            );
            if (res.status === 200) {
                if (res.response?.expired) {
                    // redirect to 404
                    push('/404');
                } else {
                    wizardData.defaultCustomData = res.response.default_custom_data;
                    wizardData.currentSubscriptions = res.response.subscriptions;
                }
            }
        }
        let result = await apiFetch(
            `${__bakney.env.API.SEARCH.PROFILE}/${params?.sportAssociationUsername}?module_info=1`
        );
        wizardData.sportAssociationData = result.response.data;
        wizardData.formData.additional_fields =
            wizardData?.sportAssociationData?.user?.sport_association?.additional_fields || [];
        // check and set if federation
        wizardData.sportAssociationData.isFederation =
            wizardData.sportAssociationData?.user?.preview_and_custom_features?.find(
                x => x === 'Iscrizioni per Enti e Federazioni'
            ) !== undefined;

        if (wizardData.sportAssociationData.isFederation) {
            wizardData.formData.associate_data.type = 3;
        }

        sessionStorage.setItem('wizardData', JSON.stringify(wizardData));
    }

    onMount(async () => {
        params.preregistration = params.preregistration === 'true' ? true : false;
        // load i18n
        register('it', () => import('./lang/it.json'));
        register('en', () => import('./lang/en.json'));

        init({
            fallbackLocale: 'en',
            initialLocale: 'it',
        });
        await waitLocale();
        // localStorage.clear();
        sessionStorage.clear();
        sessionStorage.setItem('sportAssociationUsername', params?.sportAssociationUsername);
        // sessionStorage.setItem('redirectAfterLogin', 1);
        await loadData();
        loaded = true;
    });
</script>

<!-- <pre style="min-height:50vh">
    {JSON.stringify(wizardData, null, 2)}
</pre> -->
{#if loaded}
    <div in:scale={{duration: 100, start: 0.98, easing: easing.cubicInOut}}>
        <div
            class="bg-{params?.preregistration ? 'warning' : 'primary'} pb-20"
            style="color: rgb(255, 255, 255); background-image: url('/static/forms/pattern.png'); background-size: cover;">
            <div class="d-flex align-items-center justify-content-between">
                <div class="align-items-center d-flex p-4">
                    <a href={$oemConfig?.url || '/'} target="_blank">
                        <img
                            class="h-20px"
                            alt="logo {$oemConfig?.name || 'assozeta'}"
                            src={$oemConfig?.logo || ''}
                            style="filter:brightness(100)" />
                    </a>
                    {#if params?.preregistration}
                        <span class="badge badge-primary font-weight-boldest ml-4">Preiscrizione</span>
                    {/if}
                </div>
                <div class="card-toolbar align-items-center p-4">
                    <ul class="d-flex justify-content-end m-0">
                        <button
                            class:btn-white={language == 'it'}
                            class:text-dark={language == 'it'}
                            class="btn p-0 btn-sm btn-icon btn-ghost text-white px-4 font-weight-bolder font-size-sm mr-2"
                            on:click={() => {
                                language = 'it';
                                locale.set('it');
                            }}>IT</button>
                        <button
                            class:btn-white={language != 'it'}
                            class:text-dark={language != 'it'}
                            class="btn p-0 btn-sm btn-icon btn-ghost text-white px-4 font-weight-bolder font-size-sm"
                            on:click={() => {
                                language = 'en';
                                locale.set('en');
                            }}>EN</button>
                    </ul>
                </div>
            </div>
            <div class="mx-auto w-100 w-md-50 px-12" style="min-width: 75% !important;">
                <div class="py-2">
                    <!-- {JSON.stringify(wizardData?.sportAssociationData?.user?.avatar_image || {})} -->
                    <div style="display: flex; flex-direction:column; align-items: center; justify-content:center;">
                        {#if wizardData?.sportAssociationData?.user?.avatar_image}
                            <img
                                alt="logo associazione {wizardData?.sportAssociationData?.user?.sport_association
                                    ?.denomination || ''}}"
                                src={wizardData?.sportAssociationData?.user?.avatar_image || ''}
                                class="rounded-circle" />
                        {:else}
                            <div class="symbol symbol-light-info symbol-100 symbol-circle" style="margin: auto;">
                                <span
                                    class="symbol-label font-weight-bold"
                                    style="font-size: 4rem !important;text-align: center;line-height: 4rem">
                                    {wizardData?.sportAssociationData?.user?.sport_association?.denomination?.charAt(
                                        0
                                    ) || ''}
                                    {wizardData?.sportAssociationData?.user?.sport_association?.denomination?.charAt(
                                        1
                                    ) || ''}
                                </span>
                            </div>
                        {/if}
                        <h1 class="text-center mt-10">
                            <span class="font-weight-boldest" style="font-size: 3.2rem; line-height: 1.2;"
                                >{wizardData?.sportAssociationData?.user?.sport_association?.denomination || ''}</span>
                        </h1>
                    </div>
                </div>
                <p class="m-auto text-center font-size-h6 pb-10">
                    <span class="font-size-h2 font-weight-boldest">
                        {#if params?.preregistration}
                            {$_('general.fill_out_module_preregistration')} <br />
                        {:else}
                            {$_('general.fill_out_module')} <br />
                        {/if}
                    </span>
                    {$_('general.make_login')}
                </p>
            </div>
        </div>
        <div
            class="bg-white"
            style="border-radius: 1rem 1rem 0 0;position:relative;top:-3.5rem;box-shadow:var(--shadow-lg);">
            <div class="d-flex justify-content-center align-items-center mx-auto wizard-card font-size-xs">
                <div
                    class:text-primary={wizardData.activeStep >= 0}
                    class="my-4 d-flex justify-content-between align-items-center">
                    <div class="d-flex flex-column font-weight-boldest align-items-center">
                        <Info
                            size={24}
                            weight={wizardData.activeStep >= 0 ? 'duotone' : 'light'}
                            class="mx-auto mb-1" />
                        {$_('toolbar.info')}
                    </div>
                </div>
                {#if wizardData?.formData?.subscription_id === null}
                    <div class:text-primary={wizardData.activeStep >= 1} class="my-auto">
                        <ArrowRight size={12} weight={'bold'} class="mx-4 mx-md-8" />
                    </div>

                    {#if !wizardData.is_family_wizard}
                        <div
                            class:text-primary={wizardData.activeStep >= 1}
                            class="my-4 d-flex justify-content-between align-items-center">
                            <div class="d-flex flex-column font-weight-boldest">
                                <PersonSimpleRun
                                    weight={wizardData.activeStep >= 1 ? 'duotone' : 'light'}
                                    size={24}
                                    class="mx-auto mb-1" />
                                {$_('toolbar.personal_data')}
                            </div>
                        </div>
                        <div class:text-primary={wizardData.activeStep >= 2} class="my-auto">
                            <ArrowRight size={12} weight={'bold'} class="mx-4 mx-md-8" />
                        </div>
                        <div
                            class:text-primary={wizardData.activeStep >= 2}
                            class="my-4 d-flex justify-content-between align-items-center">
                            <div class="d-flex flex-column font-weight-boldest">
                                <Signature
                                    weight={wizardData.activeStep >= 2 ? 'duotone' : 'light'}
                                    size={24}
                                    class="mx-auto mb-1" />
                                {$_('toolbar.signature')}
                            </div>
                        </div>
                        <div class:text-primary={wizardData.activeStep >= 3} class="my-auto">
                            <ArrowRight size={12} weight={'bold'} class="mx-4 mx-md-8" />
                        </div>
                        <div
                            class:text-primary={wizardData.activeStep >= 3}
                            class="my-4 d-flex justify-content-between align-items-center">
                            <div class="d-flex flex-column font-weight-boldest">
                                <FirstAidKit
                                    weight={wizardData.activeStep >= 3 ? 'duotone' : 'light'}
                                    size={24}
                                    class="mx-auto mb-1" />
                                {$_('toolbar.medical_certificate')}
                            </div>
                        </div>
                        <div class:text-primary={wizardData.activeStep >= 4} class="my-auto">
                            <ArrowRight size={12} weight={'bold'} class="mx-4 mx-md-8" />
                        </div>
                        <div
                            class:text-primary={wizardData.activeStep >= 4}
                            class="my-4 d-flex justify-content-between align-items-center">
                            <div class="d-flex flex-column font-weight-boldest">
                                <PaperPlaneTilt
                                    weight={wizardData.activeStep >= 4 ? 'duotone' : 'light'}
                                    size={24}
                                    class="mx-auto mb-1" />
                                {$_('toolbar.send')}
                            </div>
                        </div>
                    {:else}
                        <div
                            class:text-primary={wizardData.activeStep >= 1}
                            class="my-4 d-flex justify-content-between align-items-center">
                            <div class="d-flex flex-column font-weight-boldest">
                                <UsersThree
                                    weight={wizardData.activeStep >= 1 ? 'duotone' : 'light'}
                                    size={24}
                                    class="mx-auto mb-1" />
                                {$_('toolbar.family_data')}
                            </div>
                        </div>
                    {/if}
                {/if}
            </div>
        </div>
        <div class="w-100 w-md-50 mx-auto font-weight-semibold">
            <form id="subscribe-form" class="mx-auto p-2" on:submit|preventDefault>
                {#if wizardData?.activeStep == 0}
                    <Step0 bind:wizardData />
                {:else if !wizardData.is_family_wizard && wizardData?.activeStep == 1}
                    <Step1 bind:wizardData />
                {:else if wizardData.is_family_wizard && wizardData?.activeStep == 1}
                    <StepFamily bind:wizardData bind:creatingSubscription />
                {:else if !wizardData.is_family_wizard && wizardData?.activeStep == 2}
                    <Step2 bind:wizardData />
                {:else if !wizardData.is_family_wizard && wizardData?.activeStep == 3}
                    <Step3 bind:wizardData />
                {:else if !wizardData.is_family_wizard && wizardData?.activeStep == 4}
                    <Step4 bind:wizardData bind:creatingSubscription />
                {/if}
            </form>
        </div>
    </div>
{/if}

<svelte:head>
    <style>
        body {
            background: var(--bg-surface);
        }
        #bkn_body {
            overflow-x: hidden !important;
        }
        .wizard-card {
            /* border: 1px solid #ebedf2; */
            border-radius: 0.55rem;
            /* padding: 1rem; */
        }
        .rounded-circle {
            width: 100px;
            height: 100px;
            object-fit: cover;
            border-radius: 50%;
            overflow: hidden;
        }
        body,
        .offcanvas-right {
            border-left: 0 !important;
        }
    </style>
</svelte:head>
