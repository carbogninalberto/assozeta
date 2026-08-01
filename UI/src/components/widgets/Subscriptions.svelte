<script>
    import ContentLoader from 'svelte-content-loader';
    import {onMount, onDestroy} from 'svelte';
    import Chart from 'chart.js/auto';
    import {apiFetch} from 'utils/ApiMiddleware';
    let data = {
        pie_subscriptions: {},
        total_subscriptions: 0,
    };
    let loading = true;
    let chart = null;

    function initWidget() {
        var element = document.getElementById('bkn_dashboard_widget_subscriptions');

        if (!element) {
            return;
        }

        if (chart) {
            chart.destroy();
        }

        var config = {
            type: 'doughnut',
            data: {
                datasets: [
                    {
                        data: data.pie_subscriptions,
                        backgroundColor: [
                            '#351DC2',
                            '#F5CE01',
                            '#FF3D60',
                            '#08D1AD',
                        ],
                    },
                ],
                labels: ['Non firmate', 'In attesa', 'Rifiutate', 'Accettate'],
            },
            options: {
                cutout: '75%',
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: false,
                        position: 'top',
                    },
                    title: {
                        display: false,
                        text: 'Iscritti',
                    },
                    tooltip: {
                        enabled: true,
                        intersect: false,
                        mode: 'nearest',
                        bodySpacing: 5,
                        padding: 10,
                        displayColors: false,
                        backgroundColor: '#181C32',
                        titleColor: '#ffffff',
                        cornerRadius: 4,
                        footerSpacing: 0,
                        titleSpacing: 0,
                    },
                },
            },
        };

        var ctx = element.getContext('2d');
        chart = new Chart(ctx, config);
    }

    onMount(async () => {
        const res = await apiFetch(`${__bakney.env.API.STATISTIC.DASHBOARD}?widget=subscriptions`, {
            method: 'GET',
        });
        loading = false;
        // spinner stop
        if (!res.error) {
            data = res.response.data;
        }
        initWidget();
    });

    onDestroy(() => {
        if (chart) {
            chart.destroy();
            chart = null;
        }
    });
</script>

<div class="card card-widget card-custom rounded-xl overflow-hidden card-stretch dashboard-card dashboard-widget">
    <!--begin::Header-->
    <div class="border-0 pt-6">
        <h3 class="card-title align-items-start flex-column mb-0 text-center">
            <span class="card-label font-weight-bolder font-size-h4">Tesserati anno corrente</span>
        </h3>
    </div>

    <!--end::Header-->

    <!--begin::Body-->
    <div class="card-body d-flex align-items-center justify-content-center pt-0 flex-wrap">
        {#if loading}
            <ContentLoader viewBox="0 0 400 150" height={135} width={400}>
                <rect x="70" y="20" rx="0" ry="0" width="7" height="7" />
                <rect x="90" y="20" rx="0" ry="0" width="70" height="7" />
                <rect x="70" y="40" rx="0" ry="0" width="7" height="7" />
                <rect x="90" y="40" rx="0" ry="0" width="70" height="7" />
                <rect x="70" y="60" rx="0" ry="0" width="7" height="7" />
                <rect x="90" y="60" rx="0" ry="0" width="70" height="7" />
                <rect x="70" y="80" rx="0" ry="0" width="7" height="7" />
                <rect x="90" y="80" rx="0" ry="0" width="70" height="7" />
                <circle cx="250" cy="60" r="60" />
            </ContentLoader>
        {/if}
        <!--begin::Visuals-->
        <div
            style={loading ? 'visibility:hidden!important;position:absolute!important' : ''}
            class="d-flex align-items-center justify-content-center">
            <!--begin::legends-->
            <div class="d-flex flex-column mr-6">
                <!--begin::legend-->
                <div class="legend d-flex align-items-center py-1">
                    <span class="label label-primary label-dot label-lg mr-2" />
                    <span class="font-weight-bolder font-size-lg text-muted"
                        >Non firmate <small class="text-primary font-weight-boldest"
                            >({data.pie_subscriptions[0]})</small
                        ></span>
                </div>

                <!--end::legend-->

                <!--begin::legend-->
                <div class="legend d-flex align-items-center py-1">
                    <span class="label label-warning label-dot label-lg mr-2" />
                    <span class="font-weight-bolder font-size-lg text-muted"
                        >In attesa <small class="text-warning font-weight-boldest">({data.pie_subscriptions[1]})</small
                        ></span>
                </div>

                <!--end::legend-->

                <!--begin::legend-->
                <div class="legend d-flex align-items-center py-1">
                    <span class="label label-danger label-dot label-lg mr-2" />
                    <span class="font-weight-bolder font-size-lg text-muted"
                        >Rifiutate <small class="text-danger font-weight-boldest">({data.pie_subscriptions[2]})</small
                        ></span>
                </div>

                <!--end::legend-->

                <!--begin::legend-->
                <div class="legend d-flex align-items-center py-1">
                    <span class="label label-success label-dot label-lg mr-2" />
                    <span class="font-weight-bolder font-size-lg text-muted"
                        >Accettate <small class="text-success font-weight-boldest">({data.pie_subscriptions[3]})</small
                        ></span>
                </div>

                <!--end::legend-->
            </div>

            <!--end::legends-->

            <!--begin::Chart-->
            <div class="d-flex flex-center position-relative">
                <div
                    class="font-weight-bolder font-size-h5 text-primary position-absolute"
                    style="font-size:3rem!important;">
                    {data.total_subscriptions || ''}
                </div>
                <canvas
                    id="bkn_dashboard_widget_subscriptions"
                    style=" width: 10rem; height: 11.25rem;z-index:100;z-index: 0;" />
            </div>

            <!--end::Chart-->
        </div>

        <!--end::Visuals-->
    </div>

    <!--end::Body-->
</div>
