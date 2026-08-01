<script>
    import {FileArrowDown, FileDashed} from 'phosphor-svelte';
    import {subPage} from 'store/stores';
    import {onMount} from 'svelte';
    import {apiFetch} from 'utils/ApiMiddleware';
    import { initTooltips } from 'shim/tooltip.js';
    import {oemConfig} from 'store/instanceStore.js';
    subPage.useLocalStorage();

    let invoices = [];

    onMount(() => {
        initTooltips(document.body);
        // fetch the invoice from __bakney.env.PROFILE.BILLING_INVOICE.LIST
        apiFetch(__bakney.env.API.PROFILE.BILLING_INVOICE.LIST, {
            method: 'GET',
        }).then(res => {
            if (res.status == 200) {
                invoices = res.response.data;
            }
        });
    });
</script>

{#if $subPage == 'billing'}
    <div class="flex-row-fluid">
        <!--begin::Card-->
        <div class="card card-custom card-stretch">
            <!--begin::Header-->
            <div class="card-header py-3">
                <div class="card-title align-items-start flex-column">
                    <h3 class="card-label font-weight-bolder text-dark font-size-h1">Fatturazione</h3>
                    <span class="text-muted font-weight-bold font-size-sm mt-1"
                        >In questa sezione trovi le fatture di {$oemConfig?.name || 'assozeta'}.</span>
                </div>
            </div>
            <div class="card-body">
                {#if Array.from(invoices).length == 0}
                    <div class="d-flex justify-content-center flex-column align-items-center py-12">
                        <div>
                            <FileDashed size={64} weight="duotone" class="mb-4 text-muted" />
                        </div>
                        <h2>Nessuna fattura presente</h2>
                        <div class="text-center">
                            Potrebbero volerci alcuni giorni per la fatturazione{#if $oemConfig?.supportEmail}, per problemi scrivi a <a
                                    class="font-weight-boldest"
                                    href="mailto:{$oemConfig.supportEmail}">{$oemConfig.supportEmail}</a>{/if}.
                        </div>
                    </div>
                {:else}
                    {#each Array.from(invoices) as invoice}
                        <div
                            class="d-flex align-items-center justify-content-between p-4 my-4 mx-0"
                            style="background-color: var(--bg-surface-secondary); border-color: var(--border-color);border-radius:.55rem;">
                            <div class="font-weight-bold font-size-lg">
                                <span class="font-weight-boldest">Fattura del</span>
                                <span class="font-weight-boldest text-primary">{invoice.invoice_date}</span>
                            </div>
                            <a
                                href="javascript:downloadPdf('{`${__bakney.env.API.DOCUMENT.RETRIEVE}/${invoice.document_id}`}');"
                                data-tooltip="Scarica Fattura"
                                data-placement="bottom"
                                class="btn btn-primary btn-sm font-weight-bold align-items-center d-flex p-2">
                                <FileArrowDown size="18" weight="duotone" />
                            </a>
                        </div>
                    {/each}
                {/if}
            </div>
        </div>
    </div>
{/if}
