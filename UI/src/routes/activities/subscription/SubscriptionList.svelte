<script>
	import { X } from 'lucide-svelte';
    import {SunDim, Warning, MoonStars, Smiley} from 'phosphor-svelte';
    import {sessionToken} from 'store/stores.js';
    import {onMount} from 'svelte';
    import {apiFetch, replaceUID} from 'utils/ApiMiddleware';
    import {scale} from 'svelte/transition';
    import * as easing from 'svelte/easing';
    import {toast} from 'svelte-sonner';
    import InplaceTabs from 'components/InplaceTabs.svelte';
    import SubscriptionElement from './partials/subscription-element.svelte';
    import {blockPage, unblockPage} from 'store/loadingStore.js';
    import { initTooltips, destroyTooltips } from 'shim/tooltip.js';
    import DateInput from 'components/inputs/DateInput.svelte';
    import {createDropzone, getDropzone} from 'shim/dropzone.js';
    import {hideModal} from 'shim/modal.js';

    export let params = {};

    let subscriptionList = [];
    let subscriptionId = null;
    let certificate_expring_date = moment().format('DD/MM/YYYY');
    let isValid = false;
    let uploadedFile = false;
    let aiSuggestion = false;
    let isLoading = true;
    let activeTab = 'active';

    let signatureComponent;
    let signatureData;

    $: {
        isValid = !moment(certificate_expring_date, 'DD/MM/YYYY').isBefore(moment());
    }

    async function getSubscriptionList() {
        try {
            isLoading = true;
            subscriptionList = [];
            let res = await apiFetch(__bakney.env.API.SUBSCRIPTION.LIST, {method: 'GET'});
            if (!res.error) {
                subscriptionList = Object.values(res.response.data) || [];
                subscriptionList.forEach(sub => {
                    sub.signature = {there_is_signature: false, data: ''};
                });
            }
        } catch (error) {
            console.error(error);
        } finally {
            isLoading = false;
        }
    }

    onMount(async () => {
        activeTab = params.tab == 'past' ? 'past' : 'active';
        await getSubscriptionList();
        initTooltips(document.body);

        const uploadMedicalCertificateUrl =
            __bakney.env.API.SUBSCRIPTION.MEDICAL_CERTIFICATE.UPLOAD.replace('<uid>', '${subscriptionId}');
        let ktDropzone = createDropzone(document.querySelector('#bkn_dropzone'), {
            accept: 'image/*,application/pdf',
            multiple: false,
        });

        ktDropzone.on('queuecomplete', async function (file) {
            await getSubscriptionList();
        });
    });

    async function setCertificateExpiration() {
        // close modal
        hideModal('uploadMedicalCertificateModal');
        let res = apiFetch(
            replaceUID(__bakney.env.API.SUBSCRIPTION.MEDICAL_CERTIFICATE.SET_CERTIFICATE_EXPIRATION, subscriptionId),
            {
                method: 'POST',
                body: JSON.stringify({
                    subscription_id: subscriptionId,
                    certificate_expiring_date: certificate_expring_date,
                }),
            }
        );
        getDropzone('#bkn_dropzone')?.removeAllFiles();
        await getSubscriptionList();

        if (res && !res.error) {
            toast.success('Certificato medico aggiornato con successo.');
        } else {
            toast.error('Qualcosa è andato storto.');
        }
    }
</script>

<!--begin::Entry-->
<div  class="d-flex flex-column-fluid">
    <!--begin::Container-->
    <div class="container" style="max-width: 70rem !important;">
        <div class="row">
            <div class="d-flex justify-content-between align-items-center mb-4 mb-md-6 mt-6 mt-md-0 w-full mx-auto">
                <InplaceTabs
                    showHR={false}
                    paddingClass={'pb-2'}
                    disabled={false}
                    on:tabChange={e => {
                        activeTab = e.detail.tabName;
                    }}
                    {activeTab}
                    navigationPages={[
                        {title: 'Iscrizioni attive', tabName: 'active', icon: SunDim},
                        {
                            title: 'Iscrizioni Archiviate',
                            tabName: 'past',
                            icon: MoonStars,
                        },
                    ]} />
            </div>
            {#if activeTab == 'active'}
                <div
                    in:scale|local={{delay: 0, duration: 50, start: 0.98, easing: easing.cubicInOut}}
                    style="width:100%;padding:0!important;">
                    {#each subscriptionList?.filter(sub => sub.is_current) || [] as subscription, id}
                        <SubscriptionElement
                            bind:subscriptionId
                            {subscription}
                            {signatureComponent}
                            {id}
                            {getSubscriptionList} />
                    {/each}
                </div>
            {:else if activeTab == 'past'}
                <div
                    in:scale|local={{delay: 0, duration: 50, start: 0.98, easing: easing.cubicInOut}}
                    style="width:100%;padding:0!important;">
                    {#each subscriptionList?.filter(sub => !sub.is_current) || [] as subscription, id}
                        <SubscriptionElement
                            bind:subscriptionId
                            {subscription}
                            {signatureComponent}
                            {id}
                            {getSubscriptionList} />
                    {/each}
                </div>
            {/if}

            {#if subscriptionList?.filter(sub => sub.is_current)?.length == 0 && activeTab == 'active' && !isLoading}
                <div class="col-12">
                    <div class="d-flex flex-column justify-content-center text-center align-items-center m-12">
                        <Smiley size={64} color="#000" weight="duotone" />
                        <h4 class="text-dark font-weight-boldest mt-4">Nessuna iscrizione attiva</h4>
                        <p class="text-dark-75 font-weight-bold">
                            Compila il modulo d'iscrizione della tua associazione.
                        </p>
                    </div>
                </div>
            {:else if subscriptionList?.filter(sub => !sub.is_current)?.length == 0 && activeTab == 'past' && !isLoading}
                <div class="col-12">
                    <div class="d-flex flex-column justify-content-center text-center align-items-center m-12">
                        <Smiley size={64} color="#000" weight="duotone" />
                        <h4 class="text-dark font-weight-boldest mt-4">Nessuna iscrizione passata</h4>
                        <p class="text-dark-75 font-weight-bold">Non sono presenti iscrizioni passate.</p>
                    </div>
                </div>
            {:else if isLoading}
                <div class="col-12">
                    <div
                        class="d-flex justify-content-center text-center p-3 mt-8 symbol-label font-weight-bolder font-weight-bold font-size-md">
                        <div class="spinner-border text-primary" role="status" />
                    </div>
                </div>
            {/if}
        </div>
    </div>
</div>

<!-- Modal-->
<div
    class="modal fade"
    id="uploadMedicalCertificateModal"
    data-backdrop="static"
    tabindex="-1"
    role="dialog"
    aria-labelledby="staticBackdrop"
    aria-hidden="true">
    <div class="modal-dialog" role="document">
        <div class="modal-content">
            <div class="modal-header">
                <h5 class="modal-title" id="exampleModalLabel">Certificato Medico</h5>
                <button type="button" class="close" data-dismiss="modal" aria-label="Close">
                    <X size={16} aria-hidden="true" />
                </button>
            </div>
            <div class="modal-body">
                <div class="dropzone dropzone-default" id="bkn_dropzone">
                    <div class="dropzone-msg dz-message needsclick">
                        <h3 class="dropzone-msg-title">Trascina o premi per caricare il Certificato Medico.</h3>
                        <span class="dropzone-msg-desc"
                            >Sono supportati file <b>pdf</b> e <b>immagini</b> di grandezza inferiore a
                            <b>5MB</b>.</span>
                    </div>
                </div>
                <div class="col-xl-12 ml-0 pl-0 pt-8">
                    <div class="form-group">
                        <!-- svelte-ignore a11y-label-has-associated-control -->
                        <label>Data di scadenza<b>*</b></label>
                        <!-- svelte-ignore a11y-click-events-have-key-events -->
                        <!-- svelte-ignore a11y-no-static-element-interactions -->
                        <div on:click={() => (aiSuggestion = false)}>
                            <DateInput id="expiring_certificate" name="bornDateAssociate"
                                format="L" placeholder="Seleziona Data"
                                bind:value={certificate_expring_date} />
                        </div>
                    </div>

                    {#if aiSuggestion}
                        <!-- alert -->
                        <div
                            class="d-flex align-items-center text-bold text-warning bg-light-warning p-4 mb-4"
                            style="border-radius: 0.35rem;">
                            <Warning size={18} weight="duotone" class="mr-2" />
                            La data di scadenza è stata suggerita automaticamente dal sistema.
                        </div>
                    {/if}
                    <div class="col-12 d-flex justify-content-start align-items-center">
                        <small class="text-muted font-size-sm lh-xs">
                            Verrà inviata una mail all'utente e all'associazione per notificare la scadenza del
                            certificato.
                        </small>
                    </div>
                </div>
            </div>
            <div class="modal-footer">
                <button type="button" class="btn btn-light-primary font-weight-bold" data-dismiss="modal"
                    >Chiudi</button>
                <button
                    on:click|preventDefault={setCertificateExpiration}
                    disabled={!isValid}
                    type="button"
                    class="btn btn-primary font-weight-bold">Salva</button>
            </div>
        </div>
    </div>
</div>
