<script>
    import {Gift, RocketLaunch} from 'phosphor-svelte';
    import {push} from 'svelte-spa-router';
</script>

{#if __bakney.OEM_CONFIG?.displaySettings?.navbar?.showAffiliateLink}
    <div class="row d-none d-md-block">
        <div class="col-12 pl-2 pl-md-4 pr-2 pr-md-4 mt-8 mt-md-auto">
            <!--begin::Stats Widget 4-->
            <div class="card card-widget card-custom card-stretch">
                <div class="card-body py-1 px-0 chart-tile-container d-flex align-items-center font-weight-bold">
                    <div
                        class="col-12 col-md-8 p-6 py-md-2 d-flex d-md-block align-items-center justify-content-center">
                        <Gift weight="bold" size="24" class="text-primary" />
                        <span class="text-primary font-weight-bold font-size-lg my-auto ml-2">
                            Ottieni ora i <span class="font-weight-boldest">premi</span> che ti aspettano col
                            <!-- svelte-ignore a11y-missing-attribute -->
                            <a class="font-weight-boldest text-primary" href="/#/affiliate" style="cursor:pointer"
                                >programma di affiliazione</a>
                            di {__bakney.OEM_CONFIG?.name}!
                        </span>
                    </div>
                    <div class="col-12 col-md-4 text-right d-none d-md-block mr-0">
                        <button
                            style="margin-right: 0 !important;"
                            class="btn btn-sm py-2 btn-ghost text-hover-primary font-size-lg font-weight-boldest m-auto d-flex align-items-center mr-0"
                            on:click={() => push('/affiliate')}>
                            <RocketLaunch weight="bold" class="mr-1" size="18" />
                            Scopri di più
                        </button>
                    </div>
                </div>
            </div>
        </div>
    </div>
{/if}
