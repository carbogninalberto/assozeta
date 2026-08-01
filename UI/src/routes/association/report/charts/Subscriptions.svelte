<script>
    import {onDestroy, onMount, tick} from 'svelte';
    import {DownloadSimple} from 'phosphor-svelte';
    import {
        downloadChartPng,
        disposeChart,
        renderChart,
        reportAxisLabel,
        reportAxisLine,
        reportAxisNameTextStyle,
        reportAxisTick,
        reportAxisTooltip,
        reportSplitLine,
        resizeChart,
    } from 'utils/ECharts';

    export let subscriptions;
    let chart = null;

    const handleResize = () => resizeChart(chart);

    $: subscriptions, initChart(subscriptions);

    async function initChart(subscriptions) {
        if (!subscriptions) {
            disposeChart(chart);
            chart = null;
            return;
        }

        await tick();

        var element = document.getElementById('subscriptions-chart');
        if (!element) {
            return;
        }

        var yMax = subscriptions.y?.length ? Math.max(...subscriptions.y) + 1 : 5;

        var options = {
            grid: {
                top: 16,
                right: 16,
                bottom: 42,
                left: 64,
                containLabel: true,
            },
            xAxis: {
                type: 'category',
                data: subscriptions.x,
                boundaryGap: false,
                splitLine: { show: false },
                axisLine: reportAxisLine,
                axisTick: reportAxisTick,
                axisLabel: reportAxisLabel,
            },
            yAxis: {
                type: 'value',
                name: '# iscrizioni',
                nameLocation: 'middle',
                nameGap: 52,
                nameTextStyle: reportAxisNameTextStyle,
                min: 0,
                max: yMax,
                splitNumber: 4,
                splitLine: reportSplitLine,
                axisLine: reportAxisLine,
                axisTick: reportAxisTick,
                axisLabel: {
                    ...reportAxisLabel,
                    formatter: function (val) {
                        return Number(val).toFixed(0);
                    },
                },
            },
            tooltip: {
                ...reportAxisTooltip,
            },
            series: [
                {
                    name: 'Iscrizioni',
                    type: 'line',
                    data: subscriptions.y,
                    smooth: true,
                    showSymbol: false,
                    lineStyle: {
                        color: '#255aee',
                        width: 3,
                    },
                    areaStyle: {
                        color: 'rgba(37, 90, 238, 0.12)',
                    },
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
<label class="font-weight-bolder h3 mb-4">Andamento Iscrizioni</label>
<div class="card report-chart-card">
    <div class="card-body report-chart-body" style="padding: 1.25rem;">
        {#if !subscriptions}
            <div class="text-center d-flex align-items-center justify-content-center" style="height: 50px;width: 100%">
                <span class="text-muted font-weight-bolder">Dati non ancora disponibili</span>
            </div>
        {:else}
            <button
                type="button"
                class="report-chart-download not-printable"
                style="position: absolute; z-index: 20;"
                aria-label="Scarica grafico Andamento Iscrizioni"
                on:click={() => downloadChartPng(chart, 'andamento-iscrizioni')}>
                <DownloadSimple size={18} weight="regular" />
            </button>
            <div id="subscriptions-chart" style="height: 280px;width: 100%" />
        {/if}
    </div>
</div>
