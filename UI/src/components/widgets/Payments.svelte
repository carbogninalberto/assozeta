<script>
    import {onMount, onDestroy} from 'svelte';
    import {apiFetch} from 'utils/ApiMiddleware';
    import {disposeChart, renderChart, resizeChart, tooltipHtml, widgetTooltip} from 'utils/ECharts';
    let data = {
        current_month_payments: [],
        total_payments: 0,
    };
    let loading = true;
    let chart = null;

    const handleResize = () => resizeChart(chart);

    function initWidget() {
        var element = document.getElementById('bkn_dashboard_widget_payments');

        if (!element) {
            return;
        }

        var seriesData = data.current_month_payments || [];
        var yMax = seriesData.length ? Math.max(...seriesData) + 1 : 5;

        var options = {
            grid: {top: 0, right: 0, bottom: 0, left: 0},
            xAxis: {
                type: 'category',
                data: Array.from({length: 31}, (_, i) => 31 - i - 1 + ' giorni fa'),
                show: false,
                boundaryGap: false,
            },
            yAxis: {
                type: 'value',
                show: false,
                min: 0,
                max: yMax,
            },
            tooltip: {
                ...widgetTooltip,
                formatter: function (params) {
                    const value = params?.[0]?.value;
                    return value !== undefined ? tooltipHtml('Pagamenti', '€ ' + value, '#08D1AD') : '';
                },
            },
            series: [
                {
                    name: 'Pagamenti Incassati',
                    type: 'line',
                    data: seriesData,
                    smooth: true,
                    showSymbol: false,
                    lineStyle: {
                        color: '#ffffff',
                        width: 3,
                    },
                    areaStyle: {
                        color: '#08D1AD',
                        opacity: 1,
                    },
                },
            ],
        };

        chart = renderChart(element, chart, options);
    }

    onMount(async () => {
        const res = await apiFetch(`${__bakney.env.API.STATISTIC.DASHBOARD}?widget=payments`, {
            method: 'GET',
        });
        loading = false;
        // spinner stop
        if (!res.error) {
            data = res.response.data;
        }
        initWidget();
        window.addEventListener('resize', handleResize);
    });

    onDestroy(() => {
        window.removeEventListener('resize', handleResize);
        disposeChart(chart);
        chart = null;
    });
</script>

<div
    class="card card-custom rounded-xl overflow-hidden bg-success card-stretch dashboard-widget"
    style="background-color: var(--success);">

    <!--begin::Header-->
    <div class="card-header border-0 pt-6">
        <h3 class="card-title align-items-start flex-column">
            <span class="card-label font-weight-bolder font-size-h6 text-white">Pagamenti incassati</span>
            <span class="font-weight-bolder font-size-h1 text-white mt-2">
                {#if loading}
                    ...
                {:else}
                    {new Intl.NumberFormat('it-IT', {style: 'currency', currency: 'EUR'}).format(
                        data.total_payments || '0'
                    )}
                {/if}
            </span>
            <span class="text-white mt-2 font-weight-bold font-size-sm"
                >I ricavi medi del mese sono {new Intl.NumberFormat('it-IT', {
                    style: 'currency',
                    currency: 'EUR',
                }).format(data.total_payments / 30 || '')} al giorno.</span>
        </h3>
    </div>
    <!--end::Header-->
    <!--begin::Body-->
    <div class="card-body p-0 h-125px chart-tile-container">
        <div id="bkn_dashboard_widget_payments" class="card-rounded-bottom position-absolute bottom-0 w-100" style="height: 120px;" />
    </div>
    <!--end::Body-->
</div>
