import * as echarts from 'echarts/core';
import {BarChart, LineChart, PieChart} from 'echarts/charts';
import {GridComponent, LegendComponent, ToolboxComponent, TooltipComponent} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';

echarts.use([BarChart, LineChart, PieChart, GridComponent, LegendComponent, ToolboxComponent, TooltipComponent, CanvasRenderer]);

const fastTooltipBase = {
    showDelay: 0,
    hideDelay: 0,
    transitionDuration: 0,
    enterable: false,
    renderMode: 'html',
    appendToBody: true,
};

const modernTooltipStyle = {
    backgroundColor: 'rgba(24, 28, 50, 0.96)',
    borderColor: 'rgba(24, 28, 50, 0.96)',
    borderWidth: 0,
    padding: [8, 10],
    textStyle: {
        color: '#ffffff',
        fontSize: 12,
        fontFamily: 'Inter, sans-serif',
        fontWeight: 600,
    },
    extraCssText:
        'border-radius: 8px; box-shadow: 0 8px 24px rgba(15, 23, 42, 0.24); line-height: 1.35; pointer-events: none; will-change: transform;',
};

export const widgetTooltip = {
    ...fastTooltipBase,
    ...modernTooltipStyle,
    trigger: 'axis',
    confine: true,
    axisPointer: {type: 'none'},
};

export const reportAxisTooltip = {
    ...fastTooltipBase,
    ...modernTooltipStyle,
    trigger: 'axis',
    confine: true,
    axisPointer: {type: 'none'},
};

export const reportItemTooltip = {
    ...fastTooltipBase,
    ...modernTooltipStyle,
    trigger: 'item',
    confine: true,
};

export const reportSplitLine = {
    show: true,
    lineStyle: {type: 'solid', color: 'rgba(235, 237, 243, 0.55)', width: 1},
};

export const reportAxisLine = {
    show: true,
    lineStyle: {color: 'rgba(181, 181, 195, 0.45)', width: 1},
};

export const reportAxisTick = {show: false};

export const reportAxisLabel = {
    color: '#5E6278',
};

export const reportAxisNameTextStyle = {
    color: '#3F4254',
    fontWeight: 600,
};

export function tooltipHtml(label, value, color = '#255aee') {
    return `<div style="display:flex;align-items:center;gap:8px;white-space:nowrap;">
        <span style="width:8px;height:8px;border-radius:999px;background:${color};display:inline-block;"></span>
        <span style="opacity:.78;font-weight:500;">${label}</span>
        <strong style="font-weight:700;">${value}</strong>
    </div>`;
}

export function renderChart(element, previousChart, options) {
    if (!element) return previousChart || null;

    disposeChart(previousChart);

    const existingChart = echarts.getInstanceByDom(element);
    disposeChart(existingChart);

    const chart = echarts.init(element, null, {renderer: 'canvas'});
    chart.setOption(options);
    return chart;
}

export function disposeChart(chart) {
    if (chart && !chart.isDisposed()) {
        chart.dispose();
    }
}

export function resizeChart(chart) {
    if (chart && !chart.isDisposed()) {
        chart.resize();
    }
}

export function downloadChartPng(chart, filename = 'chart') {
    if (!chart || chart.isDisposed()) return;

    const url = chart.getDataURL({
        type: 'png',
        pixelRatio: 2,
        backgroundColor: '#ffffff',
    });
    const link = document.createElement('a');
    link.href = url;
    link.download = `${filename}.png`;
    link.click();
}
