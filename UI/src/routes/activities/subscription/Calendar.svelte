<script>
    import {onDestroy, onMount} from 'svelte';
    import * as easing from 'svelte/easing';
    import {slide, scale} from 'svelte/transition';
    import ContentLoader from 'svelte-content-loader';
    import {apiFetch, replaceUID} from 'utils/ApiMiddleware';
    import {toast} from 'svelte-sonner';
    import {ArrowLeft} from 'phosphor-svelte';
    import BackButton from 'components/buttons/BackButton.svelte';
    import { UiApp } from 'shim/ui.js';
    import {normalizeCalendarEvents} from 'utils/eventCalendar.js';

    const EventCalendar = window.EventCalendar;

    export let params = {};
    let ec;
    let loading = true;
    let events = [];

    function initCalendar() {
        var todayDate = moment().startOf('day');
        var TODAY = todayDate.format('YYYY-MM-DD');

        var calendarEl = document.getElementById('course_attendance_calendar');

        ec = EventCalendar.create(calendarEl, {
            allDaySlot: true,
            locale: 'it',
            firstDay: 1,
            headerToolbar: {
                start: 'prev,next today',
                center: 'title',
                end: 'dayGridMonth,timeGridWeek,timeGridDay,listWeek',
            },
            buttonText: function (texts) {
                return {
                    ...texts,
                    today: 'Oggi',
                    dayGridMonth: 'mese',
                    timeGridWeek: 'settimana',
                    timeGridDay: 'giorno',
                    listWeek: 'lista',
                };
            },
            noEventsContent: 'Nessun evento',
            nowIndicator: true,
            height: '800px',
            views: {
                dayGridMonth: {
                    dayMaxEvents: true,
                },
            },

            view: 'dayGridMonth',
            date: TODAY,

            editable: false,
            dayMaxEvents: true,
            events: events,

            eventDidMount: function (info) {
                var el = info.el;

                if (info.event.extendedProps && info.event.extendedProps.description) {
                    if (el.closest('.ec-day-grid')) {
                        el.dataset.content = info.event.extendedProps.description;
                        el.dataset.placement = 'top';
                        UiApp.initPopover(el, { content: info.event.extendedProps.description, trigger: 'hover' });
                    } else {
                        var titleEl = el.querySelector('.ec-event-title');
                        if (titleEl) {
                            titleEl.insertAdjacentHTML('beforeend', '<div class="fc-description">' + info.event.extendedProps.description + '</div>');
                        }
                    }
                }
            },
        });

        setTimeout(() => {
            loading = false;
        }, 1000);
    }

    onDestroy(() => {
        if (ec) {
            EventCalendar.destroy(ec);
            ec = null;
        }
    });

    async function fetchData() {
        const res = await apiFetch(replaceUID(__bakney.env.API.SUBSCRIPTION.CALENDAR, params.subscriptionId), {
            method: 'GET',
        });

        if (!res.error) {
            events = normalizeCalendarEvents(res.response);
        } else {
            toast.error('Qualcosa è andato storto.');
        }
    }

    onMount(async () => {
        await fetchData();
        initCalendar();
    });
</script>

<!--begin::Entry-->
<div  class="d-flex flex-column-fluid">
    <!--begin::Container-->
    <div class="container container-overlay">
        <!--begin::Card-->
        <div class="card card-custom gutter-b">
            <div class="card-header pt-4 pb-0 header-mobile-btn-back" style="padding-bottom: 0 !important;">
                <div class="card-toolbar d-flex gap-4" style="gap: .5rem;">
                    <BackButton />
                </div>
                <div class="card-toolbar">
                    <h3 class="card-title font-size-h2">Calendario</h3>
                </div>
                <div class="card-toolbar" />
            </div>
            <div class="card-body pt-4">
                <div class="row" in:slide={{duration: 250}}>
                    <div class="col-sm-12 p-0 m-0">
                        {#if loading}
                            <ContentLoader width="100%" height="500">
                                <rect x="15" y="15" rx="4" ry="4" width="100%" height="50" />
                                <rect x="15" y="50" rx="2" ry="2" width="100%" height="450" />
                            </ContentLoader>
                        {/if}
                        <div in:slide id="course_attendance_calendar" />
                    </div>
                </div>
            </div>
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
    </style>
</svelte:head>
