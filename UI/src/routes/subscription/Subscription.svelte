<script>
    import {scale} from 'svelte/transition';
    import * as easing from 'svelte/easing';
    import Engage from 'components/Engage.svelte';
    import {isExpired, userData, billingData} from 'store/stores.js';
    import {Wallet} from 'phosphor-svelte';
    import {stripeConfig} from 'store/instanceStore.js';

    isExpired.useLocalStorage();
    userData.useLocalStorage();
    billingData.useLocalStorage();

    $: clientPortalUrl = $stripeConfig?.clientPortal
        ? `${$stripeConfig.clientPortal}${$stripeConfig.clientPortal.includes('?') ? '&' : '?'}prefilled_email=${encodeURIComponent($userData?.email || '')}`
        : '';
</script>

<!--begin::Entry-->
<div  class="d-flex flex-column-fluid">
    <!--begin::Container-->
    <div class="container">
        {#if $isExpired}
            <Engage
                icon="danger"
                title="Il tuo abbonamento è scaduto."
                message="Per favore rinnova il tuo abbonamento per continuare ad utilizzare il servizio."
                textColor="danger"
                titleColor="danger" />
        {/if}

        <div class="row">
            <div in:scale|local={{delay: 20, duration: 50, start: 0.98, easing: easing.cubicInOut}} class="col-lg-12">
                <div class="card card-custom gutter-b mb-4">
                    <div class="card-body pt-4 pb-4 pl-6 pr-6">
                        <div in:scale={{duration: 150, start: 0.98, delay: 200}} class="form-group row">
                            <!-- svelte-ignore a11y-label-has-associated-control -->
                            <h2 class="col-12 font-weight-boldest text-center mb-4">Piano attivo corrente</h2>
                            <div class="col-12 col-md-7 m-auto">
                                <div
                                    class="alert alert-custom bg-light rounded-lg fade show mb-0 d-flex justify-content-between align-items-center flex-column flex-md-row"
                                    role="alert">
                                    <div in:scale={{duration: 150, start: 0.98}} class="alert-icon">
                                        <span class="menu-icon">
                                            <Wallet size="50" weight="duotone" />
                                        </span>
                                    </div>
                                    <div class="alert-text font-weight-bold text-center text-md-left">
                                        <span style="font-size: 2rem; font-weight:bolder"
                                            >{$billingData?.active_plan?.name}</span>
                                        <div>
                                            Abbonamento <span style="font-weight: 800;"
                                                >{$billingData?.active_plan?.billing_type == 2
                                                    ? 'annuale'
                                                    : 'mensile'}</span
                                            >, {$isExpired ? 'è scaduto' : 'scade'} il
                                            <span style="font-weight: 800;">
                                                {new Date($billingData?.ends_on).getDate()}/{new Date(
                                                    $billingData?.ends_on
                                                ).getMonth() + 1}/{new Date($billingData?.ends_on).getFullYear()}
                                            </span>
                                            <br />
                                            {#if clientPortalUrl}
                                                <span style="font-weight: 800;">Rinnovo automatico configurato.</span>
                                                <br />
                                            {/if}
                                        </div>
                                    </div>

                                    {#if clientPortalUrl}
                                        <div class="m-auto pt-8 pt-md-0">
                                            <a
                                                href={clientPortalUrl}
                                                class="btn btn-sm btn-dark font-weight-bolder text-uppercase mr-2"
                                                >GESTISCI ABBONAMENTO</a>
                                        </div>
                                    {/if}
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>
