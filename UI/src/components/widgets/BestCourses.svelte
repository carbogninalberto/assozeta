<script>
    import {onMount, onDestroy} from 'svelte';
    import {ListLoader} from 'svelte-content-loader';
    import {apiFetch} from 'utils/ApiMiddleware';
    import {convert} from 'html-to-text';
    import {disposeChart, renderChart, resizeChart, tooltipHtml, widgetTooltip} from 'utils/ECharts';
    let data = {
        current_week_course_associates: [],
        best_courses: [],
        total_course_associates: 0,
    };
    let loading = true;
    let chart = null;

    const handleResize = () => resizeChart(chart);

    function initWidget() {
        var element = document.getElementById('bkn_dashboard_widget_best_courses');

        if (!element) {
            return;
        }

        var seriesData = data.current_week_course_associates || [];
        var yMax = seriesData.length ? Math.max(...seriesData) + 1 : 5;

        var options = {
            grid: {top: 0, right: 0, bottom: 0, left: 0},
            xAxis: {
                type: 'category',
                data: Array.from({length: 8}, (_, i) => 8 - i - 1 + ' giorni fa'),
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
                    return value !== undefined ? tooltipHtml('Iscritti', value + ' iscritti', '#351DC2') : '';
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
                        color: '#351DC2',
                        width: 3,
                    },
                    areaStyle: {
                        color: '#DEEDFF',
                        opacity: 1,
                    },
                },
            ],
        };

        chart = renderChart(element, chart, options);
    }

    onMount(async () => {
        const res = await apiFetch(`${__bakney.env.API.STATISTIC.DASHBOARD}?widget=bestcourses`, {
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
    class="card card-widget card-custom rounded-xl overflow-hidden card-stretch dashboard-card dashboard-widget mb-0"
    style="padding:0!important;">
    <!--begin::Header-->
    <div class="border-0 pt-6">
        <h3 class="card-title align-items-start flex-column mb-0 text-center">
            <span class="card-label font-weight-bolder font-size-h4 text-dark">I 3 corsi con più iscritti</span>
        </h3>
    </div>
    <!--end::Header-->
    <!--begin::Body-->
    {#if loading}
        <div class="card-body pb-0">
            <ListLoader />
        </div>
    {/if}
    <div class="card-body py-0 mt-3" style={loading ? 'visibility:hidden!important;position:absolute!important' : ''}>
        {#each data.best_courses as course}
            <!--begin::Item-->
            <div class="d-flex align-items-center justify-content-between">
                <!--begin::Text-->
                <div class="d-flex flex-column col-md-10 col-8 pl-0 font-weight-boldest">
                    <a
                        href="/#/course/overview/{course.course_id}"
                        class="text-primary text-hover-primary mb-1 font-size-sm">{course.title}</a>
                </div>
                <!--end::Text-->
                <div class="d-flex flex-column">
                    <span class="text-right">
                        <span class="label label-sm label-light text-dark label-inline font-weight-boldest"
                            ><b>{course.subscriptions}</b></span>
                    </span>
                </div>
            </div>
            <!--end::Item-->
        {/each}
    </div>
    <!--end::Body-->
    <!--begin::Footer-->
    <div class="card-footer border-0" style="padding:0!important;{loading ? 'visibility:hidden!important;' : ''}">
        <!--begin::Chart-->
        <div id="bkn_dashboard_widget_best_courses" class="card-rounded-bottom h-80px" style="height: 80px;" />
        <!--end::Chart-->
    </div>
    <!--end::Footer-->
</div>
