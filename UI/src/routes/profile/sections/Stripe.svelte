<script>
    import {apiFetch} from 'utils/ApiMiddleware.js';
    import {subPage, userData} from 'store/stores.js';
    import {canPerformAction} from 'utils/Permissions.js';
    import {toast} from 'svelte-sonner';
    import {SmartMultiSelect} from 'components/formBuilder/preview-blocks/index.js';
    import {stripeConfig} from 'store/instanceStore.js';

    subPage.useLocalStorage();
    userData.useLocalStorage();

    const methodOptions = [
        {label: 'Carte', value: 'card'},
        {label: 'Addebito Sepa', value: 'sepa_debit'},
    ];
    const toOptions = methods => (methods || []).map(x => methodOptions.find(o => o.value === x)).filter(Boolean);

    // Payments go straight to the instance's own Stripe account: the runtime public key is the source of truth.
    $: stripeConfigured = !!$stripeConfig?.publicKey;
    let selectedMethods = toOptions($userData?.sport_association?.stripe_available_methods);

    async function updateStripeMethods() {
        $userData.sport_association.stripe_available_methods = selectedMethods.map(item => item.value);
        let res = await apiFetch(__bakney.env.API.PROFILE.UPDATE, {
            method: 'PATCH',
            body: JSON.stringify({
                user_data: {
                    first_name: $userData.first_name,
                    last_name: $userData.last_name,
                    username: $userData.username,
                    email: $userData.email,
                    avatar_image: $userData.avatar_image,
                    sport_association: $userData.sport_association,
                },
            }),
        });

        if (!res.error) {
            toast.success('Impostazioni stripe aggiornate.');
        } else {
            toast.error(res.status == 403 ? 'Operazione non permessa.' : 'Scusa, ho individuato degli errori, riprova.');
        }
    }
</script>

{#if $subPage == 'stripe'}
    <!--begin::Content-->
    <div class="flex-row-fluid">
        <!--begin::Card-->
        <div class="card card-custom card-stretch">
            <!--begin::Header-->
            <div class="card-header py-3">
                <div class="card-title align-items-start flex-column">
                    <h3 class="card-label font-weight-bolder text-dark font-size-h1">Pagamenti Stripe</h3>
                    <span class="text-muted font-weight-bold font-size-sm mt-1"
                        >I pagamenti sono gestiti direttamente tramite il tuo account Stripe.</span>
                </div>
            </div>
            <!--end::Header-->
            <!--begin::Body-->
            <div class="card-body">
                {#if stripeConfigured}
                    <div class="form-group row">
                        <div class="col-12">
                            <span
                                class="label label-light-success label-inline font-weight-bolder label-lg"
                                style="display: flex; padding: 2rem; font-size: 1.3rem;">
                                Stripe Configurato
                            </span>
                        </div>
                    </div>

                    <div class="form-group row">
                        <div class="col-12">
                            <SmartMultiSelect
                                customClasses={'min-w-100'}
                                editable={false}
                                active={false}
                                minNumberOfSelectedItems={1}
                                on:change={updateStripeMethods}
                                bind:value={selectedMethods}
                                props={{
                                    id: `stripe_available_methods`,
                                    name: `stripe_available_methods`,
                                    label: 'Accetta pagamenti con',
                                    placeholder: 'Seleziona quali modalità di pagamento vuoi offrire',
                                    required: true,
                                    clearable: false,
                                    searchable: true,
                                    showChevron: true,
                                    options: methodOptions,
                                    value: selectedMethods,
                                    disabled: !canPerformAction('other.settings.update'),
                                }} />
                        </div>
                    </div>

                    <div class="form-group row mb-0">
                        <div class="col-12">
                            <small class="text-muted font-size-sm lh-xs">
                                Per ricevere i pagamenti online attiva anche "Pagamenti Online" in Impostazioni.
                            </small>
                        </div>
                    </div>
                {:else}
                    <div class="alert alert-custom alert-light-warning fade show mb-10" role="alert">
                        <div class="alert-icon">
                            <span class="svg-icon svg-icon-3x svg-icon-warning">
                                <svg
                                    xmlns="http://www.w3.org/2000/svg"
                                    xmlns:xlink="http://www.w3.org/1999/xlink"
                                    width="24px"
                                    height="24px"
                                    viewBox="0 0 24 24"
                                    version="1.1">
                                    <g stroke="none" stroke-width="1" fill="none" fill-rule="evenodd">
                                        <rect x="0" y="0" width="24" height="24" />
                                        <circle fill="#000000" opacity="0.3" cx="12" cy="12" r="10" />
                                        <rect fill="#000000" x="11" y="10" width="2" height="7" rx="1" />
                                        <rect fill="#000000" x="11" y="7" width="2" height="2" rx="1" />
                                    </g>
                                </svg>
                            </span>
                        </div>
                        <div class="alert-text font-weight-bold">
                            La chiave pubblica Stripe non è configurata. Contatta l'amministratore per abilitare i pagamenti.
                        </div>
                    </div>
                {/if}
            </div>
            <!--end::Body-->
        </div>
    </div>
{/if}
