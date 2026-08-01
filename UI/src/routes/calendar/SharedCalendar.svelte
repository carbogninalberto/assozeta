<script>
    import {onMount, onDestroy} from 'svelte';
    import * as easing from 'svelte/easing';
    import {slide, scale} from 'svelte/transition';
    import ContentLoader from 'svelte-content-loader';
    import {apiFetch, replaceUID} from 'utils/ApiMiddleware';
    import {getCalendarClassName, normalizeCalendarEvents} from 'utils/eventCalendar.js';
    import {toast} from 'svelte-sonner';
    import { UiApp } from 'shim/ui.js';
    import { initTooltips } from 'shim/tooltip.js';
    import {oemConfig} from 'store/instanceStore.js';

    const EventCalendar = window.EventCalendar;

    export let params = {};
    let calendar;
    let loading = true;
    let events = [];
    let data;

    const COLOR_MAP = {
        'fc-event-solid-primary': 'ec-event-solid-primary',
        'fc-event-solid-success': 'ec-event-solid-success',
        'fc-event-solid-danger': 'ec-event-solid-danger',
        'fc-event-solid-warning': 'ec-event-solid-warning',
        'fc-event-solid-dark': 'ec-event-solid-dark',
        'fc-event-solid-light': 'ec-event-solid-light',
        'fc-event-white': 'ec-event-white',
        'fc-event-primary': 'ec-event-primary',
        'fc-event-success': 'ec-event-success',
        'fc-event-danger': 'ec-event-danger',
        'fc-event-warning': 'ec-event-warning',
        'fc-event-dark': 'ec-event-dark',
        'fc-event-light': 'ec-event-light',
    };

    function mapEventForCalendar(event) {
        const oldCls = getCalendarClassName(event);
        const newCls = COLOR_MAP[oldCls] || (oldCls.startsWith('ec-event-') ? oldCls : 'ec-event-solid-primary');
        return {
            ...event,
            className: newCls,
            classNames: [newCls],
            durationEditable: false,
            startEditable: false,
        };
    }

    let CalendarListView = (function () {
        return {
            initExternalEvents: function (events) {},
            //main function to initiate the module
            init: function () {
                var todayDate = moment().startOf('day');
                var TODAY = todayDate.format('YYYY-MM-DD');

                var calendarEl = document.getElementById('course_attendance_calendar');

                calendar = EventCalendar.create(calendarEl, {
                    allDaySlot: true,
                    locale: 'it',
                    firstDay: 1,
                    buttonText: function (texts) {
                        return {
                            ...texts,
                            today: 'Oggi',
                            dayGridMonth: 'mese',
                            timeGridWeek: 'settimana',
                            timeGridDay: 'giorno',
                            listDay: 'lista giorno',
                            listWeek: 'lista settimana',
                        };
                    },
                    noEventsContent: 'Nessun evento',
                    headerToolbar: {
                        start: 'prev,next today',
                        center: 'title',
                        end: 'dayGridMonth,timeGridWeek,timeGridDay,listWeek',
                    },
                    nowIndicator: true,
                    height: '700px',

                    views: {
                        dayGridMonth: {
                            dayMaxEvents: true,
                        },
                    },

                    view: 'dayGridMonth',
                    date: TODAY,

                    editable: false,
                    eventDurationEditable: false,
                    eventStartEditable: false,
                    selectable: false,
                    dayMaxEvents: true,

                    viewDidMount: function (info) {
                        // Check if calendar has no events rendered
                        setTimeout(() => {
                            var calendarEl = info.el;
                            var hasEvents = calendarEl.querySelector('.ec-event');
                            var existing = calendarEl.querySelector('.ec-no-events-message');
                            if (existing) existing.remove();
                            if (!hasEvents) {
                                var msg = document.createElement('div');
                                msg.className = 'ec-no-events-message';
                                msg.textContent = 'Nessun evento da visualizzare';
                                msg.style.cssText = 'text-align:center;padding:3rem 1rem;color:var(--text-muted, #92929C);font-size:0.95rem;position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);';
                                calendarEl.appendChild(msg);
                            }
                        }, 100);
                    },

                    events: events.map(mapEventForCalendar),

                    eventDidMount: function (info) {
                        var el = info.el;

                        // Set tooltip attributes on all events
                        var tooltipText = info.event.title;
                        if (info.event.start) {
                            tooltipText = moment(info.event.start).format('HH:mm') + ' — ' + tooltipText;
                        }
                        if (info.event.extendedProps && info.event.extendedProps.description) {
                            var desc = info.event.extendedProps.description;
                            if (desc.length > 80) desc = desc.substring(0, 80) + '…';
                            tooltipText += '\n' + desc;
                        }
                        el.setAttribute('data-toggle', 'tooltip');
                        el.setAttribute('data-original-title', tooltipText);
                        el.setAttribute('data-placement', 'top');

                        if (info.event.extendedProps && info.event.extendedProps.description) {
                            if (info.view.type === 'dayGridMonth') {
                                el.dataset.content = info.event.extendedProps.description;
                                el.dataset.placement = 'top';
                                UiApp.initPopover(el, { content: info.event.extendedProps.description, trigger: 'hover' });
                            } else if (info.view.type.startsWith('timeGrid')) {
                                var titleEl = el.querySelector('.ec-event-title');
                                if (titleEl) {
                                    titleEl.insertAdjacentHTML('beforeend', '<div class="ec-description">' + info.event.extendedProps.description + '</div>');
                                }
                            } else {
                                var listTitleEl = el.querySelector('.ec-event-title') || el.querySelector('.ec-list-item-title');
                                if (listTitleEl) {
                                    listTitleEl.insertAdjacentHTML('beforeend', '<div class="ec-description">' + info.event.extendedProps.description + '</div>');
                                }
                            }
                        }
                    },
                });

                initTooltips(calendarEl);

                setTimeout(() => {
                    loading = false;
                }, 1000);
            },
        };
    })();

    async function fetchData() {
        const res = await apiFetch(replaceUID(__bakney.env.API.COURSE.CALENDAR, params.id), {
            method: 'GET',
        });

        if (!res.error) {
            events = normalizeCalendarEvents(res.response);
            data = res.response.data;
        } else {
            toast.error('Qualcosa è andato storto.');
        }
    }

    onMount(async () => {
        await fetchData();
        CalendarListView.init(events);
    });

    onDestroy(() => {
        if (calendar) {
            EventCalendar.destroy(calendar);
            calendar = null;
        }
    });
</script>

<!--begin::Entry-->
<div  class="d-flex flex-column-fluid">
    <!--begin::Container-->
    <div class="container container-overlay bg-white p-12 text-center">
        <h1 class="text-dark font-weight-bold mb-10">{data?.course?.title}</h1>
        <div class="shared-calendar-shell">
            {#if loading}
                <div class="shared-calendar-loader">
                    <ContentLoader width="100%" height="700">
                        <rect x="0" y="15" rx="4" ry="4" width="100%" height="50" />
                        <rect x="0" y="80" rx="2" ry="2" width="100%" height="620" />
                    </ContentLoader>
                </div>
            {/if}
            <div in:slide id="course_attendance_calendar" class:calendar-loading={loading} />
        </div>
        <div class="d-flex justify-content-between align-items-center pt-8">
            {#if $oemConfig?.isReseller}
                <div>
                    <img id="logo" class="h-30px mr-8" src={$oemConfig?.logo || ''} alt="logo" />
                    <img id="logo" class="h-30px" src={data?.sport_association?.logo} alt="logo" />
                </div>
                <span
                    >Calendario fornito da <b class="text-primary">{$oemConfig?.name || 'assozeta'}</b> in collaborazione con
                    <b class="text-primary">{data?.sport_association?.denomination}</b></span>
            {/if}
        </div>
    </div>
    <!--end::Container-->
</div>
<!--end::Entry-->

<svelte:head>
    <style>
        .nav-link {
            cursor: pointer;
        }
        .nav-link.active {
            border-bottom: 4px solid #351dc2 !important;
        }
        .nav-link:hover {
            border-bottom: 4px solid #351dc2 !important;
        }
        .card-toolbar::-webkit-scrollbar {
            display: none;
        }
        .shared-calendar-shell {
            min-height: 700px;
            position: relative;
        }
        .shared-calendar-loader {
            background: #fff;
            inset: 0;
            position: absolute;
            z-index: 2;
        }
        #course_attendance_calendar.calendar-loading {
            visibility: hidden;
        }
        #course_attendance_calendar .ec-preview,
        #course_attendance_calendar .ec-ghost {
            display: none !important;
        }
        #course_attendance_calendar .ec-day-grid.ec-month-view .ec-day:hover::after {
            display: none !important;
            content: none !important;
        }
    </style>
</svelte:head>
