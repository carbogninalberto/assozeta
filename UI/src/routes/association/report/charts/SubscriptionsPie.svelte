<script>
    import {onDestroy, onMount, tick} from 'svelte';
    import {DownloadSimple} from 'phosphor-svelte';
    import {downloadChartPng, disposeChart, renderChart, resizeChart, reportItemTooltip, tooltipHtml} from 'utils/ECharts';

    export let data;
    let chart = null;

    const handleResize = () => resizeChart(chart);

    $: data, initChart(data);

    async function initChart(data) {
        if (!data) {
            disposeChart(chart);
            chart = null;
            return;
        }

        await tick();

        var element = document.getElementById('subscriptions-chart-pie');
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
                formatter: params => tooltipHtml(params.name, `${params.value} soci (${params.percent}%)`, params.color),
            },
            series: [
                {
                    name: 'Stato Iscrizioni',
                    type: 'pie',
                    radius: ['45%', '70%'],
                    center: ['38%', '50%'],
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
<label class="font-weight-bolder h3 mb-4">Stato Iscrizioni Libro Soci</label>
<div class="card report-chart-card">
    <div class="card-body report-chart-body" style="padding: 1.25rem;">
        <button
            type="button"
            class="report-chart-download not-printable"
            style="position: absolute; z-index: 20;"
            aria-label="Scarica grafico Stato Iscrizioni"
            on:click={() => downloadChartPng(chart, 'stato-iscrizioni')}>
            <DownloadSimple size={18} weight="regular" />
        </button>
        <div id="subscriptions-chart-pie" style="height: 220px;width: 100%" />
    </div>
</div>
