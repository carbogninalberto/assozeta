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

    export let courses;
    let chart = null;

    const formatCurrency = val => Number(val).toLocaleString('it-IT', {minimumFractionDigits: 2}) + ' €';
    const handleResize = () => resizeChart(chart);

    $: courses, initChart(courses);

    async function initChart(courses) {
        if (!courses) {
            disposeChart(chart);
            chart = null;
            return;
        }

        await tick();

        var element = document.getElementById('courses-chart');
        if (!element) {
            return;
        }

        var revenueData = courses.revenue?.y || [];

        var options = {
            legend: {
                top: 0,
                left: 'center',
            },
            grid: {
                top: 58,
                right: 88,
                bottom: 46,
                left: 64,
                containLabel: true,
            },
            xAxis: {
                type: 'category',
                data: courses.x,
                splitLine: { show: false },
                axisLine: reportAxisLine,
                axisTick: reportAxisTick,
                axisLabel: reportAxisLabel,
            },
            yAxis: [
                {
                    type: 'value',
                    name: '# atleti',
                    nameLocation: 'middle',
                    nameGap: 48,
                    nameTextStyle: reportAxisNameTextStyle,
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
                {
                    type: 'value',
                    name: 'Entrate',
                    position: 'right',
                    nameLocation: 'middle',
                    nameGap: 82,
                    nameTextStyle: reportAxisNameTextStyle,
                    splitLine: { show: false },
                    axisLine: reportAxisLine,
                    axisTick: reportAxisTick,
                    axisLabel: {
                        ...reportAxisLabel,
                        formatter: formatCurrency,
                    },
                },
            ],
            tooltip: {
                ...reportAxisTooltip,
            },
            series: [
                {
                    name: 'Atleti',
                    type: 'bar',
                    data: courses.y,
                    barMaxWidth: 32,
                    itemStyle: {
                        color: '#255aee',
                    },
                },
                {
                    name: 'Entrate',
                    type: 'line',
                    yAxisIndex: 1,
                    data: revenueData,
                    symbol: 'circle',
                    lineStyle: {
                        width: 3,
                        type: 'dashed',
                        color: '#08D1AD',
                    },
                    itemStyle: {
                        color: '#08D1AD',
                    },
                    label: {
                        show: true,
                        position: 'top',
                        formatter: params => formatCurrency(params.value),
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
<label class="font-weight-bolder h3 mb-4 pagebreak">Panoramica dei Corsi</label>
<div class="card report-chart-card">
    <div class="card-body report-chart-body" style="padding: 1.25rem;">
        {#if !courses}
            <div class="text-center d-flex align-items-center justify-content-center" style="height: 50px;width: 100%">
                <span class="text-muted font-weight-bolder">Dati non ancora disponibili</span>
            </div>
        {:else}
            <button
                type="button"
                class="report-chart-download not-printable"
                style="position: absolute; z-index: 20;"
                aria-label="Scarica grafico Panoramica dei Corsi"
                on:click={() => downloadChartPng(chart, 'panoramica-corsi')}>
                <DownloadSimple size={18} weight="regular" />
            </button>
            <div id="courses-chart" style="height: 300px;width: 100%" />
        {/if}
    </div>
</div>

<svelte:head>
    <style>
        @media print {
            .pagebreak {
                page-break-before: always;
            } /* page-break-after works, as well */
        }
    </style>
</svelte:head>
