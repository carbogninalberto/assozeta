<script>
    import {ShareNetwork, RocketLaunch, LinkSimple, Link} from 'phosphor-svelte';
    import {push} from 'svelte-spa-router';
</script>

<div class="row mt-4 d-none d-md-block">
    <div class="col-12 pl-2 pl-md-4 pr-2 pr-md-4 mt-8 mt-md-auto">
        <!--begin::Stats Widget 4-->
        <div class="card card-widget card-custom card-stretch">
            <div class="card-body py-1 px-0 chart-tile-container d-flex align-items-center font-weight-bold">
                <div class="col-12 col-md-8 p-6 py-md-2 d-flex d-md-block align-items-center justify-content-center">
                    <ShareNetwork weight="bold" size="24" class="text-primary" />
                    <!-- <QrCode value="{__bakney.env.DOMAIN}/#/subscribe/{String($userData.username).toLowerCase()}" /> -->
                    <span class="text-primary font-weight-bolder font-size-lg my-auto ml-2">
                        Condividi il <span class="font-weight-boldest">link iscrizioni</span> per far iscrivere i tuoi soci
                        online.
                    </span>
                </div>
                <div class="col-12 col-md-4 text-right d-none d-md-block">
                    <button
                        style="margin-right: 0 !important;"
                        class="btn btn-sm py-2 btn-ghost text-hover-primary font-size-lg font-weight-boldest m-auto d-flex align-items-center"
                        on:click={() => push('/members/subscription/share-module-link')}>
                        <Link weight="bold" class="mr-1" size="18" />
                        Link iscrizioni
                    </button>
                </div>
            </div>
        </div>
    </div>
</div>
