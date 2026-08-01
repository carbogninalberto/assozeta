<script>
    import {onDestroy, onMount} from 'svelte';
    import {canPerformAction, isFreePlan} from 'utils/Permissions.js';
    import * as easing from 'svelte/easing';
    import {slide, scale} from 'svelte/transition';
    import ContentLoader from 'svelte-content-loader';
    import {Export, Printer} from 'phosphor-svelte';
    import {apiFetch, replaceUID} from 'utils/ApiMiddleware';
    import {getCalendarClassName, normalizeCalendarEvents} from 'utils/eventCalendar.js';
    import Upgrade from 'routes/Upgrade.svelte';
    import AddCalendarEvent from './modals/AddCalendarEvent.svelte';
    import {v4 as uuidv4} from 'uuid';
    import {toast} from 'svelte-sonner';
    import { UiApp } from 'shim/ui.js';
    import { initTooltips } from 'shim/tooltip.js';
    import {hideModal} from 'shim/modal.js';

    const EventCalendar = window.EventCalendar;

    let calendar;
    let loading = true;
    let events = [];
    let instructors = [];
    let instructor = null;
    let course = null;
    let createEvent = null;

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
            durationEditable: true,
        };
    }

    function getClassNameFromEvent(event) {
        const oldCls = getCalendarClassName(event);
        return COLOR_MAP[oldCls] || (oldCls.startsWith('ec-event-') ? oldCls : 'ec-event-solid-primary');
    }

    async function saveCalendarDirectly(updateEvents, id) {
        UiApp.blockPage({
            overlayColor: '#000000',
            state: 'primary',
            message: 'Salvataggio in corso...',
        });

        let events = [];
        for (let i = 0; i < updateEvents.length; i++) {
            // Preserve local time and append Z suffix, matching the original FullCalendar convention
            events.push({
                event_id: updateEvents[i].id,
                start: moment(updateEvents[i].start).format('YYYY-MM-DDTHH:mm:ss') + '.000Z',
                end: updateEvents[i].end ? moment(updateEvents[i].end).format('YYYY-MM-DDTHH:mm:ss') + '.000Z' : null,
                className: getClassNameFromEvent(updateEvents[i]),
                allDay: updateEvents[i].allDay,
                title: updateEvents[i].title,
                extendedProps: updateEvents[i].extendedProps || {},
            });
        }
        let response;
        if (id) {
            response = await apiFetch(replaceUID(__bakney.env.API.COURSE.CALENDAR_UPDATE, id), {
                method: 'POST',
                body: JSON.stringify({
                    events: events,
                    status: 2, // 2 = updated & published (required)
                }),
            });
        } else {
            response = await apiFetch(__bakney.env.API.CALENDAR.UPDATE, {
                method: 'POST',
                body: JSON.stringify({
                    events: events,
                }),
            });
        }

        UiApp.unblockPage();

        if (!response.error) {
            calendar.refetchEvents();
            await fetchInstructors();
            toast.success('Successo.', 'Calendario aggiornato con successo!');
        } else {
            toast.error('Qualcosa è andato storto.');
        }
    }

    let CalendarListView = (function () {
        return {
            initExternalEvents: function (events) {},
            destory: function () {
                if (calendar) {
                    EventCalendar.destroy(calendar);
                    calendar = null;
                }
                var calendarEl = document.getElementById('general_calendar');
                if (calendarEl) calendarEl.innerHTML = '';
            },
            //main function to initiate the module
            init: function () {
                var todayDate = moment().startOf('day');
                var TODAY = todayDate.format('YYYY-MM-DD');

                var calendarEl = document.getElementById('general_calendar');
                if (calendarEl) calendarEl.innerHTML = '';

                createEvent = function (
                    startDate,
                    title,
                    endDate,
                    instructor = null,
                    allday = false,
                    course = null,
                    color = null,
                    description = null,
                    reminder_amount = null,
                    reminder_enabled = false,
                    reminder_unit = null
                ) {
                    instructor = instructor == '' ? null : instructor;
                    course = course == '' ? null : course;
                    color = color == '' ? null : (color?.value || color);

                    const event = mapEventForCalendar({
                        id: uuidv4(), // You must use a custom id generator
                        title: title,
                        start: startDate,
                        end: endDate,
                        allDay: allday || !endDate, // If there's no end date, the event will be all day of start date
                        className: color,
                        extendedProps: {
                            instructor: instructor,
                            course: course?.course_id || course,
                            description: description,
                            reminder_amount: reminder_amount || null,
                            reminder_enabled: reminder_enabled || false,
                            reminder_unit: reminder_unit || null,
                        },
                    });

                    calendar.addEvent(event);

                    let updatedEvents = calendar.getEvents();
                    if (course) {
                        updatedEvents = updatedEvents.filter(e => {
                            return e.extendedProps?.course == course.course_id;
                        });

                        updatedEvents = updatedEvents.map(e => {
                            if (e.id == event.id) {
                                return {
                                    id: e.id,
                                    title: e.title,
                                    start: e.start,
                                    end: e.end,
                                    allDay: e.allDay,
                                    className: event.className,
                                    extendedProps: {
                                        instructor: event.extendedProps.instructor,
                                        course: event.extendedProps.course,
                                        description: event.extendedProps.description,
                                        reminder_amount: event.extendedProps.reminder_amount || null,
                                        reminder_enabled: event.extendedProps.reminder_enabled || false,
                                        reminder_unit: event.extendedProps.reminder_unit || null,
                                    },
                                };
                            } else {
                                return {
                                    id: e.id,
                                    title: e.title,
                                    start: e.start,
                                    end: e.end,
                                    allDay: e.allDay,
                                    className: getClassNameFromEvent(e),
                                    extendedProps: {
                                        instructor: e.extendedProps.instructor,
                                        course: e.extendedProps.course,
                                        description: e.extendedProps.description,
                                        reminder_amount: e.extendedProps.reminder_amount || null,
                                        reminder_enabled: e.extendedProps.reminder_enabled || false,
                                        reminder_unit: e.extendedProps.reminder_unit || null,
                                    },
                                };
                            }
                        });
                        saveCalendarDirectly(updatedEvents, course.course_id);
                    } else {
                        updatedEvents = updatedEvents.filter(e => {
                            return e.extendedProps?.course == null;
                        });

                        updatedEvents = updatedEvents.map(e => {
                            if (e.id == event.id) {
                                return {
                                    id: e.id,
                                    title: e.title,
                                    start: e.start,
                                    end: e.end,
                                    allDay: e.allDay,
                                    className: event.className,
                                    extendedProps: {
                                        instructor: event.extendedProps.instructor,
                                        course: event.extendedProps.course,
                                        description: event.extendedProps.description,
                                        reminder_amount: event.extendedProps.reminder_amount || null,
                                        reminder_enabled: event.extendedProps.reminder_enabled || false,
                                        reminder_unit: event.extendedProps.reminder_unit || null,
                                    },
                                };
                            } else {
                                return {
                                    id: e.id,
                                    title: e.title,
                                    start: e.start,
                                    end: e.end,
                                    allDay: e.allDay,
                                    className: getClassNameFromEvent(e),
                                    extendedProps: {
                                        instructor: e.extendedProps.instructor,
                                        course: e.extendedProps.course,
                                        description: e.extendedProps.description,
                                        reminder_amount: e.extendedProps.reminder_amount || null,
                                        reminder_enabled: e.extendedProps.reminder_enabled || false,
                                        reminder_unit: e.extendedProps.reminder_unit || null,
                                    },
                                };
                            }
                        });

                        saveCalendarDirectly(updatedEvents, null);
                    }
                };

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
                        start: window.innerWidth > 600 ? 'prev,next today' : 'prev,next',
                        center: 'title',
                        end:
                            window.innerWidth > 600
                                ? 'dayGridMonth,timeGridWeek,timeGridDay,listDay,listWeek'
                                : 'dayGridMonth,timeGridWeek,timeGridDay,listDay,listWeek',
                    },
                    nowIndicator: true,
                    height: '700px',

                    views: {
                        dayGridMonth: {
                            dayMaxEvents: true,
                        },
                    },

                    view: 'listDay',
                    date: TODAY,

                    editable: true,
                    selectable: false,
                    eventDurationEditable: true,
                    eventStartEditable: true,
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

                    eventSources: [{
                        events: async function (fetchInfo) {
                            const startStr = moment(fetchInfo.start).format('YYYY-MM-DD');
                            const endStr = moment(fetchInfo.end).format('YYYY-MM-DD');

                            UiApp.block('#general_calendar', {
                                message: 'Caricamento in corso...',
                            });

                            const res = await apiFetch(
                                `${__bakney.env.API.CALENDAR.EVENTS}?start=${startStr}&end=${endStr}&get_lesson=false`
                            );

                            if (!res.error) {
                                let events = normalizeCalendarEvents(res.response);
                                UiApp.unblock('#general_calendar');
                                return events.map(mapEventForCalendar);
                            } else {
                                UiApp.unblock('#general_calendar');
                                toast.error('Qualcosa è andato storto.');
                                throw res.error;
                            }
                        }
                    }],

                    dateClick: async function (dateClickInfo) {
                        if (!canPerformAction('association.courses.update')) return;
                        document.querySelectorAll('#addElement').forEach(n => {
                            n.remove();
                        });

                        dateClickInfo.dateStr = dateClickInfo.dateStr.includes('T')
                            ? dateClickInfo.dateStr
                            : dateClickInfo.dateStr + 'T08:00';
                        let startDate = moment(dateClickInfo.dateStr).format('YYYY-MM-DDTHH:mm:ss');
                        let endDate = moment(dateClickInfo.dateStr).add(1, 'hours').format('YYYY-MM-DDTHH:mm:ss');
                        let addEventModal = new AddCalendarEvent({
                            target: document.querySelector(`body`),
                            intro: true,
                            props: {
                                instructors: instructors,
                                row: {
                                    title: '',
                                    start: startDate,
                                    end: endDate,
                                    extendedProps: {
                                        instructor: [],
                                        course: null,
                                        description: null,
                                    },
                                },
                            },
                        });
                        addEventModal.$on('save', data => {
                            addEventModal.$destroy();
                            createEvent(
                                moment(data.detail.event_start).format(),
                                data.detail.event_title,
                                data.detail.event_end ? moment(data.detail.event_end).format() : null,
                                JSON.parse(data.detail?.instructor || null),
                                data.detail.event_allday,
                                JSON.parse(data.detail.course || null),
                                data.detail?.color,
                                data.detail.description,
                                data.detail.reminder_amount,
                                data.detail.reminder_enabled,
                                data.detail.reminder_unit
                            );
                        });

                        addEventModal.$on('close', () => {
                            addEventModal.$destroy();
                        });

                        addEventModal.$on('refresh', async data => {});
                    },

                    eventDragStop: async function (info) {
                        console.info(info);
                    },

                    eventDrop: async function (info) {
                        const event = info.event;
                        const courseId = event.extendedProps?.course;

                        let updatedEvents = calendar.getEvents();
                        if (courseId) {
                            updatedEvents = updatedEvents.filter(e => {
                                return e.extendedProps?.course == courseId;
                            });
                            updatedEvents = updatedEvents.map(e => ({
                                id: e.id,
                                title: e.title,
                                start: e.start,
                                end: e.end,
                                allDay: e.allDay,
                                className: getClassNameFromEvent(e),
                                extendedProps: {
                                    instructor: e.extendedProps.instructor,
                                    course: e.extendedProps.course,
                                    description: e.extendedProps.description,
                                    reminder_amount: e.extendedProps.reminder_amount || null,
                                    reminder_enabled: e.extendedProps.reminder_enabled || false,
                                    reminder_unit: e.extendedProps.reminder_unit || null,
                                },
                            }));
                            saveCalendarDirectly(updatedEvents, courseId);
                        } else {
                            updatedEvents = updatedEvents.filter(e => {
                                return e.extendedProps?.course == null;
                            });
                            updatedEvents = updatedEvents.map(e => ({
                                id: e.id,
                                title: e.title,
                                start: e.start,
                                end: e.end,
                                allDay: e.allDay,
                                className: getClassNameFromEvent(e),
                                extendedProps: {
                                    instructor: e.extendedProps.instructor,
                                    course: e.extendedProps.course,
                                    description: e.extendedProps.description,
                                    reminder_amount: e.extendedProps.reminder_amount || null,
                                    reminder_enabled: e.extendedProps.reminder_enabled || false,
                                    reminder_unit: e.extendedProps.reminder_unit || null,
                                },
                            }));
                            saveCalendarDirectly(updatedEvents, null);
                        }
                    },

                    eventResize: async function (info) {
                        const event = info.event;
                        const courseId = event.extendedProps?.course;

                        let updatedEvents = calendar.getEvents();
                        if (courseId) {
                            updatedEvents = updatedEvents.filter(e => {
                                return e.extendedProps?.course == courseId;
                            });
                            updatedEvents = updatedEvents.map(e => ({
                                id: e.id,
                                title: e.title,
                                start: e.start,
                                end: e.end,
                                allDay: e.allDay,
                                className: getClassNameFromEvent(e),
                                extendedProps: {
                                    instructor: e.extendedProps.instructor,
                                    course: e.extendedProps.course,
                                    description: e.extendedProps.description,
                                    reminder_amount: e.extendedProps.reminder_amount || null,
                                    reminder_enabled: e.extendedProps.reminder_enabled || false,
                                    reminder_unit: e.extendedProps.reminder_unit || null,
                                },
                            }));
                            saveCalendarDirectly(updatedEvents, courseId);
                        } else {
                            updatedEvents = updatedEvents.filter(e => {
                                return e.extendedProps?.course == null;
                            });
                            updatedEvents = updatedEvents.map(e => ({
                                id: e.id,
                                title: e.title,
                                start: e.start,
                                end: e.end,
                                allDay: e.allDay,
                                className: getClassNameFromEvent(e),
                                extendedProps: {
                                    instructor: e.extendedProps.instructor,
                                    course: e.extendedProps.course,
                                    description: e.extendedProps.description,
                                    reminder_amount: e.extendedProps.reminder_amount || null,
                                    reminder_enabled: e.extendedProps.reminder_enabled || false,
                                    reminder_unit: e.extendedProps.reminder_unit || null,
                                },
                            }));
                            saveCalendarDirectly(updatedEvents, null);
                        }
                    },

                    eventResizeStop: async function (info) {
                        console.info(info);
                    },

                    eventClick: async function (info) {
                        document.querySelectorAll('#addElement').forEach(n => {
                            n.remove();
                        });

                        let jsonEvent = {
                            id: info.event.id,
                            title: info.event.title,
                            start: info.event.start,
                            end: info.event.end,
                            allDay: info.event.allDay,
                            className: info.event.classNames[0],
                            extendedProps: structuredClone(info.event.extendedProps),
                        };

                        let lessons = await apiFetch(
                            `${__bakney.env.API.CALENDAR.EVENTS}?event_id=${jsonEvent.id}&get_lesson=true`
                        );
                        if (lessons?.response?.data?.events?.length == 1) {
                            jsonEvent.extendedProps = structuredClone(lessons.response.data.events[0].extendedProps);
                        }

                        let editEventModal = new AddCalendarEvent({
                            target: document.querySelector(`body`),
                            intro: true,
                            props: {
                                row: jsonEvent,
                                instructors: instructors,
                                edit: true,
                            },
                        });
                        editEventModal.$on('save', data => {
                            hideModal('addElement');
                            editEventModal.$destroy();

                            let updatedEvents = calendar.getEvents();
                            if (data.detail?.course) {
                                updatedEvents = updatedEvents.filter(e => {
                                    return e.extendedProps?.course == data.detail?.course;
                                });
                                updatedEvents = updatedEvents.map(e => {
                                    if (e.id == data.detail.id) {
                                        return {
                                            id: e.id,
                                            title: data.detail?.event_title,
                                            start: e.start,
                                            end: e.end,
                                            allDay: e.allDay,
                                            className: data.detail?.color,
                                            extendedProps: {
                                                instructor: data.detail?.instructor,
                                                course: data.detail?.course,
                                                description: data.detail?.description,
                                                reminder_amount: data.detail?.reminder_amount || null,
                                                reminder_enabled: data.detail?.reminder_enabled || false,
                                                reminder_unit: data.detail?.reminder_unit || null,
                                            },
                                        };
                                    } else {
                                        return {
                                            id: e.id,
                                            title: e.title,
                                            start: e.start,
                                            end: e.end,
                                            allDay: e.allDay,
                                            className: getClassNameFromEvent(e),
                                            extendedProps: {
                                                instructor: e.extendedProps.instructor,
                                                course: e.extendedProps.course,
                                                description: e.extendedProps.description,
                                                reminder_amount: e.extendedProps.reminder_amount || null,
                                                reminder_enabled: e.extendedProps.reminder_enabled || false,
                                                reminder_unit: e.extendedProps.reminder_unit || null,
                                            },
                                        };
                                    }
                                });

                                saveCalendarDirectly(updatedEvents, data.detail?.course);
                            } else {
                                updatedEvents = updatedEvents.filter(e => {
                                    return e.extendedProps?.course == null;
                                });

                                updatedEvents = updatedEvents.map(e => {
                                    if (e.id == data.detail.id) {
                                        return {
                                            id: e.id,
                                            title: data.detail?.event_title,
                                            start: e.start,
                                            end: e.end,
                                            allDay: e.allDay,
                                            className: data.detail?.color,
                                            extendedProps: {
                                                instructor: data.detail?.instructor,
                                                course: data.detail?.course,
                                                description: data.detail?.description,
                                                reminder_amount: data.detail?.reminder_amount || null,
                                                reminder_enabled: data.detail?.reminder_enabled || false,
                                                reminder_unit: data.detail?.reminder_unit || null,
                                            },
                                        };
                                    } else {
                                        return {
                                            id: e.id,
                                            title: e.title,
                                            start: e.start,
                                            end: e.end,
                                            allDay: e.allDay,
                                            className: getClassNameFromEvent(e),
                                            extendedProps: {
                                                instructor: e.extendedProps.instructor,
                                                course: e.extendedProps.course,
                                                description: e.extendedProps.description,
                                                reminder_amount: e.extendedProps.reminder_amount || null,
                                                reminder_enabled: e.extendedProps.reminder_enabled || false,
                                                reminder_unit: e.extendedProps.reminder_unit || null,
                                            },
                                        };
                                    }
                                });

                                saveCalendarDirectly(updatedEvents, null);
                            }
                        });

                        editEventModal.$on('close', () => {
                            hideModal('addElement');
                            editEventModal.$destroy();
                        });

                        editEventModal.$on('delete', async data => {
                            UiApp.blockPage({
                                overlayColor: '#000000',
                                state: 'primary',
                                message: 'Eliminazione in corso...',
                            });

                            if (data.detail?.extendedProps.course) {
                                const response = await apiFetch(
                                    replaceUID(
                                        __bakney.env.API.COURSE.CALENDAR_UPDATE,
                                        data.detail?.extendedProps.course
                                    ),
                                    {
                                        method: 'DELETE',
                                        body: JSON.stringify({
                                            event_id: data.detail.id,
                                            before: data.detail.before || null,
                                            groupId: data.detail.groupId || null,
                                        }),
                                    }
                                );

                                UiApp.unblockPage();

                                if (!response.error) {
                                    calendar.removeEventById(data.detail.id);
                                    toast.success('Evento eliminato!');
                                } else {
                                    toast.error('Qualcosa è andato storto.');
                                }
                            } else {
                                calendar.removeEventById(data.detail.id);
                                let updatedEvents = calendar.getEvents();
                                updatedEvents = updatedEvents.filter(e => {
                                    return e.extendedProps?.course == null;
                                });

                                saveCalendarDirectly(updatedEvents, null);
                                toast.success('Evento eliminato!');
                            }
                            editEventModal.$destroy();
                        });

                        editEventModal.$on('refresh', async data => {});
                    },

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
                }, 20);
            },
        };
    })();

    async function fetchInstructors() {
        let res = await apiFetch(__bakney.env.API.INSTRUCTOR.LIST);
        let response = res.response;
        let tmpInstructors = response.data || [];
        instructors = [];
        tmpInstructors?.forEach(item => {
            instructors = [
                ...instructors,
                {
                    value: item.instructor_id,
                    instructor_id: item.instructor_id,
                    label: `${item.first_name} ${item.last_name}`,
                },
            ];
        });
    }

    onMount(async () => {
        CalendarListView.init(events);
        await fetchInstructors();
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
    {#if !isFreePlan()}
        <!--begin::Container-->
        <div class="container">
            <!--begin::Card-->
            <div class="card card-custom gutter-b">
                <div
                    class="card-header px-0 pt-0 pb-0 mb-6 header-mobile-btn-back border-0"
                    style="padding-bottom: 0 !important;min-height: auto !important;">
                    <div class="card-toolbar m-0">
                        <h3 class="card-title font-size-h2">Eventi e Promemoria</h3>
                    </div>
                    <div class="card-toolbar m-0 d-none d-md-flex">
                        <button
                            type="button"
                            on:click={() => {
                                const style = document.createElement('style');
                                style.innerHTML =
                                    '@media print { @page { size: A3 landscape; } .aside, .card-header {display: none !important; } .ec-toolbar {display: none !important;} .content, .wrapper { border: 0 !important;} }';
                                document.head.appendChild(style);
                                window.print();
                                document.head.removeChild(style);
                            }}
                            class="btn btn-light-primary font-weight-bold d-flex align-items-center mb-0 mr-2">
                            <Printer size={18} weight="duotone" />
                            <span class="d-none d-md-block ml-md-2">Stampa</span>
                        </button>
                        <button
                            type="button"
                            on:click={() => {
                                let sessionToken = JSON.parse(localStorage.getItem('sessionToken'));

                                fetch(__bakney.env.API.CALENDAR.EXPORT, {
                                    method: 'GET',
                                    headers: {
                                        Authorization: `Bearer ${sessionToken}`,
                                    },
                                })
                                    .then(res => res.blob())
                                    .then(blob => {
                                        // Create a URL for the blob
                                        const url = window.URL.createObjectURL(blob);
                                        const link = document.createElement('a');
                                        link.href = url;
                                        link.setAttribute(
                                            'download',
                                            `${new Date().toISOString().split('T')[0]}_calendario_completo.ics`
                                        ); // Set the file name for download
                                        document.body.appendChild(link);
                                        link.click(); // Programmatically click the link to trigger the download
                                        document.body.removeChild(link); // Clean up
                                    })
                                    .catch(error => {
                                        console.error('Download failed', error);
                                        toast.error('Qualcosa è andato storto.');
                                    });
                            }}
                            class="btn btn-primary font-weight-bold d-flex align-items-center mb-0"
                            style="margin-left: auto;">
                            <Export size={18} weight="duotone" />
                            <span class="d-none d-md-block ml-md-2"> Esporta </span>
                        </button>
                    </div>
                </div>
                <div class="card-body pt-0 px-4">
                    <div class="row" in:slide={{duration: 250}}>
                        <div class="col-sm-12 p-0 m-0">
                            {#if loading}
                                <ContentLoader width="100%" height="500">
                                    <rect x="15" y="15" rx="4" ry="4" width="100%" height="50" />
                                    <rect x="15" y="50" rx="2" ry="2" width="100%" height="450" />
                                </ContentLoader>
                            {/if}
                            <div style={loading ? 'display: none' : ''} in:slide id="general_calendar" />
                        </div>
                    </div>
                </div>
            </div>
        </div>
        <!--end::Container-->{:else}
        <Upgrade />
    {/if}
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
