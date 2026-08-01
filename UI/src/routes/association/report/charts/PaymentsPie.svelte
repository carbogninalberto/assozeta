<script>
    import {onDestroy, onMount, tick} from 'svelte';
    import {DownloadSimple} from 'phosphor-svelte';
    import {downloadChartPng, disposeChart, renderChart, resizeChart, reportItemTooltip, tooltipHtml} from 'utils/ECharts';

    export let data;
    let chart = null;

    const formatCurrency = val => Number(val).toLocaleString('it-IT', {minimumFractionDigits: 2}) + ' €';
    const handleResize = () => resizeChart(chart);

    $: data, initChart(data);

    async function initChart(data) {
        if (!data) {
            disposeChart(chart);
            chart = null;
            return;
        }

        await tick();

        var element = document.getElementById('payments-chart-pie');
        if (!element) {
            return;
        }

        var options = {
            color: ['#255aee', '#4e7cf2', '#779df5', '#a0bef8', '#c9defb'],
            legend: {
                orient: 'vertical',
                right: 0,
                top: 'middle',
            },
            tooltip: {
                ...reportItemTooltip,
                formatter: params => tooltipHtml(params.name, `${formatCurrency(params.value)} (${params.percent}%)`, params.color),
            },
            series: [
                {
                    name: 'Stato Pagamenti',
                    type: 'pie',
                    radius: ['52%', '82%'],
                    center: ['26%', '50%'],
                    avoidLabelOverlap: true,
                    label: {
                        show: false,
                    },
                    data: (data.x || []).map((label, i) => ({name: label, value: data.y?.[i] || 0})),
                },
            ],
        };

        chart = renderChart(element, chart, options);
    }

    onMount(() => {
        window.addEventListener('resize', handleResize);
    });

    onDestroy(() => {
        window.removeEventListener('resize', handleResize);
        disposeChart(chart);
        chart = null;
    });
</script>

<!-- svelte-ignore a11y-label-has-associated-control -->
<label class="font-weight-bolder h3 mb-4">Stato Pagamenti</label>
<div class="card report-chart-card">
    <div class="card-body report-chart-body" style="padding: 1.25rem;">
        <button
            type="button"
            class="report-chart-download not-printable"
            style="position: absolute; z-index: 20;"
            aria-label="Scarica grafico Stato Pagamenti"
            on:click={() => downloadChartPng(chart, 'stato-pagamenti')}>
            <DownloadSimple size={18} weight="regular" />
        </button>
        <div id="payments-chart-pie" style="height: 240px;width: 100%" />
    </div>
</div>
