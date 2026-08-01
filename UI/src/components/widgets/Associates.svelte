<script>
    import {onMount, onDestroy} from 'svelte';
    import {apiFetch} from 'utils/ApiMiddleware';
    import {disposeChart, renderChart, resizeChart, tooltipHtml, widgetTooltip} from 'utils/ECharts';
    let data = {
        current_month_associates: [],
        total_associates: 0,
    };
    let loading = true;
    let chart = null;

    const handleResize = () => resizeChart(chart);

    function initWidget() {
        var element = document.getElementById('bkn_dashboard_widget_associates');

        if (!element) {
            return;
        }

        var seriesData = data.current_month_associates || [];
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
                    return value !== undefined ? tooltipHtml('Nuovi soci', value + ' soci', '#351DC2') : '';
                },
            },
            series: [
                {
                    name: 'Nuovi Iscritti',
                    type: 'line',
                    data: seriesData,
                    smooth: true,
                    showSymbol: false,
                    lineStyle: {
                        color: '#ffffff',
                        width: 3,
                    },
                    areaStyle: {
                        color: '#351DC2',
                        opacity: 1,
                    },
                },
            ],
        };

        chart = renderChart(element, chart, options);
    }

    onMount(async () => {
        const res = await apiFetch(`${__bakney.env.API.STATISTIC.DASHBOARD}?widget=associates`, {
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

<div class="card card-custom rounded-xl overflow-hidden bg-primary card-stretch dashboard-widget">
    <!--begin::Header-->
    <div class="card-header border-0 pt-6">
        <h3 class="card-title align-items-start flex-column">
            <span class="card-label font-weight-bolder font-size-h6 text-white">Iscrizioni</span>
            <span class="font-weight-bolder font-size-h1 text-white mt-2">
                {#if loading}
                    ...
                {:else}
                    +{data.total_associates || '0'}
                {/if}
            </span>
            <span class="text-white mt-2 font-weight-bold font-size-sm"
                >Le iscrizioni medie del mese sono circa {Math.ceil(data.total_associates / 3.7 - 1).toFixed(0) || ''}
                alla settimana.</span>
        </h3>
    </div>
    <!--end::Header-->
    <!--begin::Body-->
    <div class="card-body p-0 h-125px chart-tile-container">
        <div id="bkn_dashboard_widget_associates" class="card-rounded-bottom position-absolute bottom-0 w-100" style="height: 120px;" />
    </div>
    <!--end::Body-->
</div>
