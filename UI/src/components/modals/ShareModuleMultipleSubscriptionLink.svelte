<script>
    import BasicModal from 'components/modals/BasicModal.svelte';
    import QrCode from 'svelte-qrcode';
    import Clipboard from 'svelte-clipboard';
    import {userData} from 'store/stores';
    import {Copy, PaperPlaneRight, Printer, WhatsappLogo} from 'phosphor-svelte';
    import {toast} from 'svelte-sonner';
    import SimpleButton from 'components/buttons/simple-button.svelte';
    import {onMount} from 'svelte';
    import {apiFetch} from 'utils/ApiMiddleware';
    import {SmartSelect} from 'components/formBuilder/preview-blocks';
    import Switch from 'components/inputs/Switch.svelte';

    userData.useLocalStorage();

    export let show;
    let loading = false;
    let copied = false;
    let associations = [];
    let token = null;
    let expires_at = null;
    let preregistration = false;

    async function getAssociations() {
        let res = await apiFetch(__bakney.env.API.SUBSCRIPTION.GET_ASSOCIATIONS_FOR_FEDERATION, {
            method: 'GET',
        });
        if (res.status === 200) {
            associations = Array.from([...res.response])?.map(association => ({
                value: association.id,
                label: association.denomination,
            }));
        }
    }

    async function generateLink(gym_name) {
        let res = await apiFetch(__bakney.env.API.SUBSCRIPTION.GENERATE_TOKEN_LINK, {
            method: 'POST',
            body: JSON.stringify({
                gym_name: gym_name,
            }),
        });
        if (res.status === 201) {
            token = res.response.token;
            expires_at = res.response.expires_at;
        }
    }

    onMount(async () => {
        getAssociations();
    });
</script>

<div>
    <BasicModal
        id={`share-link`}
        bind:show
        title="Condividi link iscrizioni multiple"
        showTitle={true}
        showActionButton={true}
        showCancelButton={true}
        showFooter={false}
        modalSize={'md'}
        scrollable={true}
        bodyClass={'py-2 px-0'}
        actionButton="Crea"
        dataHeight={300}>
        {#if !loading}
            <div class="px-8 py-4">
                {#if !token}
                    <div class="d-flex justify-content-center flex-column">
                        <div style="text-align: center" id="qr-code-subscriptions">
                            <QrCode
                                value="{__bakney.env.DOMAIN}/#/subscribe-multiple/{String(
                                    $userData.username
                                ).toLowerCase()}" />
                        </div>
                        <div class="pt-0 pb-4 text-center">
                            Scarica e stampa il QR code per raccogliere le iscrizioni multiple.
                        </div>
                    </div>
                    <div class="d-flex justify-content-center gap-2 mb-5">
                        <div class="input-group link-share-group">
                            <!-- svelte-ignore missing-declaration -->
                            <Clipboard
                                text="{__bakney.env.DOMAIN}/#/subscribe-multiple/{String(
                                    $userData.username
                                ).toLowerCase()}"
                                let:copy
                                on:copy={() => {
                                    copied = true;
                                    setTimeout(() => {
                                        copied = false;
                                    }, 2000);
                                    toast.success('Link copiato negli appunti');
                                }}>
                                <input
                                    type="text"
                                    class="form-control {copied ? 'bg-light-success' : ''}"
                                    style="pointer-events: none;"
                                    value="{__bakney.env.DOMAIN}/#/subscribe-multiple/{String(
                                        $userData.username
                                    ).toLowerCase()}" />
                                <div class="input-group-append">
                                    <!-- svelte-ignore a11y-click-events-have-key-events -->
                                    <!-- svelte-ignore a11y-missing-attribute -->
                                    <a
                                        on:click={copy}
                                        role="button"
                                        tabindex="0"
                                        class="btn btn-primary"
                                        style="border-radius: 0 .55rem .55rem 0;margin: auto; margin-top:0"
                                        data-clipboard="true"
                                        data-clipboard-target="#bkn_clipboard_1">
                                        <Copy size={16} weight="duotone" />
                                    </a>
                                </div>
                            </Clipboard>
                        </div>
                        <SimpleButton
                            label="Apri"
                            variant="success"
                            on:click={() => {
                                window.open(
                                    `${__bakney.env.DOMAIN}/#/subscribe-multiple/${String(
                                        $userData.username
                                    ).toLowerCase()}`,
                                    '_blank'
                                );
                            }} />
                    </div>
                    <hr />
                {/if}
                <div class="pt-2 d-flex flex-column align-items-center justify-content-center">
                    <h4 class="mb-2 font-weight-bolder">Link temporaneo</h4>
                    <p class="text-center mb-4">Genera link temporaneo della durata di 48h.</p>
                    <div class="form-group w-100 mb-2" style="z-index: 1000;">
                        {#if associations?.length > 0}
                            <SmartSelect
                                customClasses={'p-0 w-100 mb-0'}
                                editable={false}
                                active={false}
                                on:clear={() => {
                                    token = null;
                                    expires_at = null;
                                }}
                                on:change={e => {
                                    generateLink(e.detail.label);
                                }}
                                props={{
                                    id: 'association',
                                    name: 'association',
                                    label: 'Associazione',
                                    placeholder: 'Seleziona associazione',
                                    helperLabel: "Seleziona l'associazione per generare un link temporaneo",
                                    required: true,
                                    options: associations || [],
                                    value: null,
                                }} />
                        {/if}
                    </div>
                    <div class="form-group w-100 mb-2">
                        <Switch
                            label="Preiscrizione"
                            name="preregistration"
                            bind:checked={preregistration}
                            helpText="Abilita la modalità preiscrizione per il link temporaneo" />
                    </div>
                    {#if token}
                        <div
                            class="d-flex flex-column align-items-center my-6 justify-content-center w-100 border border-secondary rounded-xl py-4 px-6 bg-light">
                            <div class="d-flex justify-content-center gap-2 mb-2 w-100 mt-2">
                                <div class="input-group link-share-group">
                                    <!-- svelte-ignore missing-declaration -->
                                    <Clipboard
                                        text="{__bakney.env.DOMAIN}/#/subscribe-multiple/{String(
                                            $userData.username
                                        ).toLowerCase()}/{preregistration ? 'true' : 'false'}/{token}"
                                        let:copy
                                        on:copy={() => {
                                            copied = true;
                                            setTimeout(() => {
                                                copied = false;
                                            }, 2000);
                                            toast.success('Link copiato negli appunti');
                                        }}>
                                        <input
                                            type="text"
                                            class="form-control {copied ? 'bg-light-success' : ''}"
                                            style="pointer-events: none;"
                                            value="{__bakney.env.DOMAIN}/#/subscribe-multiple/{String(
                                                $userData.username
                                            ).toLowerCase()}/{preregistration ? 'true' : 'false'}/{token}" />
                                        <div class="input-group-append">
                                            <!-- svelte-ignore a11y-click-events-have-key-events -->
                                            <!-- svelte-ignore a11y-missing-attribute -->
                                            <a
                                                on:click={copy}
                                                role="button"
                                                tabindex="0"
                                                class="btn btn-primary"
                                                style="border-radius: 0 .55rem .55rem 0;margin: auto; margin-top:0"
                                                data-clipboard="true"
                                                data-clipboard-target="#bkn_clipboard_1">
                                                <Copy size={16} weight="duotone" />
                                            </a>
                                        </div>
                                    </Clipboard>
                                </div>
                                <SimpleButton
                                    label="Apri"
                                    variant="success"
                                    on:click={() => {
                                        window.open(
                                            `${__bakney.env.DOMAIN}/#/subscribe-multiple/${String(
                                                $userData.username
                                            ).toLowerCase()}/${preregistration ? 'true' : 'false'}/${token}`,
                                            '_blank'
                                        );
                                    }} />
                            </div>
                            <div class="d-flex justify-content-center gap-2 w-100 m-0">
                                <p class="text-center mb-0 text-info font-size-sm font-weight-boldest">
                                    Link valido fino al {new Date(expires_at).toLocaleString()}
                                </p>
                            </div>
                        </div>
                    {/if}
                </div>
            </div>
            <div class="modal-footer border-0 py-1 d-flex justify-content-between">
                <div class="d-flex">
                    <button
                        type="button"
                        class="btn btn-teal-themed font-weight-bold d-flex align-items-center"
                        on:click={() => {
                            window.open(
                                `https://api.whatsapp.com/send/?text=` +
                                    encodeURIComponent(
                                        `Ciao 👋\nQuesto è il link per iscriverti all'associazione *${
                                            $userData.sport_association.denomination
                                        }*:\n\n${__bakney.env.DOMAIN}/#/subscribe-multiple/${String(
                                            $userData.username
                                        ).toLowerCase()}${
                                            token && expires_at ? `/${preregistration ? 'true' : 'false'}/${token}` : ''
                                        }\n\nSe non hai ancora un account puoi crearne uno. Avrai tutto a portata di mano: iscrizioni, pagamenti online (corsi/iscrizioni/carnet) e ricevute. \n\nCordiali saluti,\n${
                                            $userData.sport_association.denomination
                                        }`
                                    )
                            );
                        }}><WhatsappLogo size="20" weight="fill" class="mr-2" />Whatsapp</button>
                    <button
                        type="button"
                        class="btn btn-primary font-weight-bold d-flex align-items-center ml-2"
                        on:click={() => {
                            window.open(
                                "mailto:user@example.com?subject=Compila il modulo d'iscrizione&body=" +
                                    encodeURIComponent(
                                        `Ciao,\nQuesto è il link per iscriverti all'associazione ${
                                            $userData.sport_association.denomination
                                        }:\n\n${__bakney.env.DOMAIN}/#/subscribe-multiple/${String(
                                            $userData.username
                                        ).toLowerCase()}${
                                            token && expires_at ? `/${preregistration ? 'true' : 'false'}/${token}` : ''
                                        }\n\nSe non hai ancora un account puoi crearne uno. Avrai tutto a portata di mano: iscrizioni, pagamenti online (corsi/iscrizioni/carnet) e ricevute. \n\nCordiali saluti,\n${
                                            $userData.sport_association.denomination
                                        }`
                                    )
                            );
                        }}><PaperPlaneRight size="20" weight="fill" class="mr-2" />Invia Email</button>

                    <button
                        type="button"
                        class="btn btn-primary font-weight-bold d-flex align-items-center ml-2"
                        on:click={() => {
                            let printDiv = document.getElementById('qr-code-subscriptions');
                            let printContents = printDiv.innerHTML;

                            // set print window
                            let printWindow = window.open(
                                '',
                                '',
                                'height=400,width=800,left=0,top=0,toolbar=0,scrollbars=0,status=0'
                            );
                            // set print content
                            printWindow.document.write(`
                                <html>
                                    <head>
                                        <title>QR code</title>
                                        <style>
                                            @media print {
                                                .no-print {
                                                    display: none;
                                                }
                                            }
                                            #content img {
                                                width: 100%;
                                            }
                                        </style>
                                    </head>
                                    <body>
                                        <div class="no-print">
                                            <button type="button" class="btn btn-primary font-weight-bold d-flex align-items-center" onclick="window.print();"><Printer size="20" weight="fill" class="mr-2" />Stampa</button>
                                        </div>
                                        <div id="content">
                                            ${printContents}
                                            <div class="text-center" style="font-family:sans-serif;text-align:center; font-size: 1.2rem;">
                                                <h1 class="text-center">Scannerizza il QR code per iscriverti a ${
                                                    $userData.sport_association.denomination
                                                }</h1>
                                                <div>
                                                    <br>oppure vai su:
                                                </div>
                                                <div>
                                                    ${__bakney.env.DOMAIN}/#/subscribe-multiple/${String(
                                $userData.username
                            ).toLowerCase()}${
                                token && expires_at ? `/${preregistration ? 'true' : 'false'}/${token}` : ''
                            }
                                                    </div>
                                            </div>
                                        </div>
                                    </body>
                                </html>
                            `);
                            printWindow.document.close();
                            printWindow.focus();
                            printWindow.print();
                        }}><Printer size="20" weight="fill" class="mr-2" />QR code</button>
                </div>
                <button
                    type="button"
                    class="btn mt-0 btn-secondary font-weight-bold"
                    data-dismiss="modal"
                    on:click={() => (show = false)}>Chiudi</button>
            </div>
        {:else}
            <div class="text-center py-20 d-flex justify-content-center">
                <div class="spinner spinner-primary spinner-lg" />
            </div>
        {/if}
    </BasicModal>
</div>
