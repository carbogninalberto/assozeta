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

    export let payments;
    let chart = null;

    const formatCurrency = val => Number(val).toLocaleString('it-IT', {minimumFractionDigits: 2}) + ' €';
    const handleResize = () => resizeChart(chart);

    $: payments, initChart(payments);

    async function initChart(payments) {
        if (!payments) {
            disposeChart(chart);
            chart = null;
            return;
        }

        await tick();

        var element = document.getElementById('payments-chart');
        if (!element) {
            return;
        }

        var options = {
            grid: {
                top: 16,
                right: 16,
                bottom: 42,
                left: 72,
                containLabel: true,
            },
            xAxis: {
                type: 'category',
                data: payments.x,
                splitLine: { show: false },
                axisLine: reportAxisLine,
                axisTick: reportAxisTick,
                axisLabel: reportAxisLabel,
            },
            yAxis: {
                type: 'value',
                name: '€ pagati',
                nameLocation: 'middle',
                nameGap: 84,
                nameTextStyle: reportAxisNameTextStyle,
                splitLine: reportSplitLine,
                axisLine: reportAxisLine,
                axisTick: reportAxisTick,
                axisLabel: {
                    ...reportAxisLabel,
                    formatter: formatCurrency,
                },
            },
            tooltip: {
                ...reportAxisTooltip,
                valueFormatter: formatCurrency,
            },
            series: [
                {
                    name: 'Pagamenti',
                    type: 'bar',
                    data: payments.y,
                    barMaxWidth: 32,
                    itemStyle: {
                        color: '#255aee',
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
<label class="font-weight-bolder h3 mb-4">Flusso Incassi</label>
<div class="card report-chart-card">
    <div class="card-body report-chart-body" style="padding: 1.25rem;">
        {#if !payments}
            <div class="text-center d-flex align-items-center justify-content-center" style="height: 50px;width: 100%">
                <span class="text-muted font-weight-bolder">Dati non ancora disponibili</span>
            </div>
        {:else}
            <button
                type="button"
                class="report-chart-download not-printable"
                style="position: absolute; z-index: 20;"
                aria-label="Scarica grafico Flusso Incassi"
                on:click={() => downloadChartPng(chart, 'flusso-incassi')}>
                <DownloadSimple size={18} weight="regular" />
            </button>
            <div id="payments-chart" style="height: 280px;width: 100%" />
        {/if}
    </div>
</div>
