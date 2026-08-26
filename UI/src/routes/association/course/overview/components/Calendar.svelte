<script>
	import { Info, X } from 'lucide-svelte';
    import moment from 'moment';
    import {userData} from 'store/stores.js';
    import Clipboard from 'svelte-clipboard';
    import {v4 as uuidv4} from 'uuid';
    import {afterUpdate, createEventDispatcher, onDestroy, onMount} from 'svelte';
    import Portal from 'svelte-portal';
    import {slide} from 'svelte/transition';
    import ContentLoader from 'svelte-content-loader';
    import {apiFetch, replaceUID} from 'utils/ApiMiddleware';
    import {Copy, PaperPlaneRight, Plus, WhatsappLogo, PlusCircle} from 'phosphor-svelte';
    import AddCalendarEvent from './modals/AddCalendarEvent.svelte';
    import EditCalendarEvent from './modals/EditCalendarEvent.svelte';
    import Select from 'svelte-select';
    import {canPerformAction} from 'utils/Permissions';
    import {toast} from 'svelte-sonner';
    import DateInput from 'components/inputs/DateInput.svelte';
    import { UiApp } from 'shim/ui.js';
    import {normalizeCalendarEvents} from 'utils/eventCalendar.js';
    import {recurringDates} from 'utils/dateValues.js';

    const EventCalendar = window.EventCalendar;

    const dispatch = createEventDispatcher();

    export let showToggle = false;
    export let calendar;
    export let calendarStatus = 1;
    export let id;
    export let fetchData = () => {};
    export let google_sync_enabled = false;
    export let syncGoogleCalendar = () => {};
    let calendarVisible = true;
    let loading = true;
    let openNewEventModal = false;
    let queue_events = [];
    let currentEventName = 'Lezione';
    let createEvent;
    let instructors = [];
    let instructor = null;
    let eventDate = '';
    let hex;
    let copied = false;
    let periodicAllDay = true;
    let periodicStart = moment().format('HH:mm');
    let periodicEnd = moment().add(1, 'hours').format('HH:mm');

    async function saveCalendarDirectly(updateEvents) {
        UiApp.blockPage({
            overlayColor: '#000000',
            state: 'primary',
            message: 'Salvataggio in corso...',
        });

        // alert(JSON.stringify(calendar.getEvents()));
        let events = [];
        for (let i = 0; i < updateEvents.length; i++) {
            events.push({
                event_id: updateEvents[i].id,
                start: updateEvents[i].start,
                end: updateEvents[i].end,
                allDay: updateEvents[i].allDay,
                title: updateEvents[i].title,
                extendedProps: updateEvents[i].extendedProps || {},
            });
        }
        const response = await apiFetch(replaceUID(__bakney.env.API.COURSE.CALENDAR_UPDATE, id), {
            method: 'POST',
            body: JSON.stringify({
                events: events,
                status: 2, // 2 = updated & published (required)
            }),
        });

        UiApp.unblockPage();

        if (!response.error) {
            await initPage();
            await fetchData();
            toast.success('Calendario aggiornato con successo!');
            calendarStatus = 2;
            if (google_sync_enabled)
                setTimeout(() => {
                    syncGoogleCalendar();
                }, 2000);
        } else {
            toast.error('Qualcosa è andato storto.');
        }
    }

    function initCalendar() {
        // External draggable elements setup (native HTML5 drag-and-drop with EventCalendar).
        // Note: EventCalendar standalone lacks a Draggable class; FullCalendarInteraction.Draggable is unavailable.
        // The #bkn_calendar_external_events container is not present in this component template,
        // so external element drag-and-drop is a no-op that will be handled at the parent level.
        var todayDate = moment().startOf('day');
        var YM = todayDate.format('YYYY-MM');
        var YESTERDAY = todayDate.clone().subtract(1, 'day').format('YYYY-MM-DD');
        var TODAY = todayDate.format('YYYY-MM-DD');
        var TOMORROW = todayDate.clone().add(1, 'day').format('YYYY-MM-DD');

        var calendarEl = document.getElementById('course_attendance_calendar');

        createEvent = async function (
            startDate,
            title,
            endDate,
            instructor = null,
            allday = false,
            description = null
        ) {
            instructor = instructor == '' ? null : instructor;
            const event = {
                id: uuidv4(), // You must use a custom id generator
                title: title,
                start: startDate,
                end: endDate,
                allDay: allday || !endDate, // If there's no end date, the event will be all day of start date
                extendedProps: {
                    instructor: instructor,
                    description: description || null,
                },
            };

            calendar.addEvent(event);
            let updatedEvents = calendar.getEvents();
            await saveCalendarDirectly(updatedEvents);
            dispatch('refresh');
        };

        calendar = EventCalendar.create(calendarEl, {
            dateClick: async function (dateClickInfo) {
                if (!canPerformAction('association.courses.update')) return;
                // if dateClickInfo.dateStr is missing the time, add it to it at 8:00
                var dateStr = dateClickInfo.dateStr.includes('T')
                    ? dateClickInfo.dateStr
                    : dateClickInfo.dateStr + 'T08:00';
                let startDate = moment(dateStr).format('YYYY-MM-DDTHH:mm:ss');
                let endDate = moment(dateStr).add(1, 'hours').format('YYYY-MM-DDTHH:mm:ss');
                let addEventModal = new AddCalendarEvent({
                    target: document.querySelector(`body`),
                    intro: true,
                    props: {
                        instructors: instructors,
                        row: {
                            start: startDate,
                            end: endDate,
                        },
                    },
                });
                addEventModal.$on('save', async data => {
                    // destroy addEventModal
                    addEventModal.$destroy();
                    await createEvent(
                        moment(data.detail.event_start).format(),
                        data.detail.event_title,
                        data.detail.event_end ? moment(data.detail.event_end).format() : null,
                        JSON.parse(data.detail?.instructor || null),
                        data.detail.event_allday,
                        data.detail?.description
                    );
                });

                addEventModal.$on('close', () => {
                    addEventModal.$destroy();
                });
            },
            eventClick: async function (info) {
                let editEventModal = new EditCalendarEvent({
                    target: document.querySelector(`body`),
                    intro: true,
                    props: {
                        row: info.event,
                        instructors: instructors,
                    },
                });
                editEventModal.$on('save', data => {
                    info.event.title = data.detail.event_title;
                    info.event.extendedProps.description = data.detail?.description;
                    try {
                        info.event.extendedProps.instructor = JSON.parse(data.detail?.instructor);
                    } catch (e) {
                        info.event.extendedProps.instructor = data.detail?.instructor;
                    }

                    let updatedEvents = calendar.getEvents();
                    saveCalendarDirectly(updatedEvents);
                    editEventModal.$destroy();

                    dispatch('refresh');
                });

                editEventModal.$on('close', () => {
                    editEventModal.$destroy();
                });

                editEventModal.$on('delete', async data => {
                    swal.fire({
                        text: `Sei sicuro di procedere all'eliminazione? Saranno eliminate anche le presenze.`,
                        icon: 'warning',
                        showCancelButton: true,
                        buttonsStyling: false,
                        confirmButtonText: 'Elimina',
                        cancelButtonText: 'Annulla',
                        reverseButtons: true,
                        customClass: {
                            confirmButton: 'btn font-weight-bold btn-light-danger',
                            cancelButton: 'btn font-weight-bold btn-light-primary',
                        },
                    }).then(async function (result) {
                        if (result.value) {
                            UiApp.blockPage({
                                overlayColor: '#000000',
                                state: 'primary',
                                message: 'Eliminazione in corso...',
                            });

                            const response = await apiFetch(
                                replaceUID(__bakney.env.API.COURSE.CALENDAR_UPDATE, id),
                                {
                                    method: 'DELETE',
                                    body: JSON.stringify({
                                        event_id: info.event.id,
                                        before: data.detail.before,
                                        groupId: data.detail.groupId,
                                    }),
                                }
                            );

                            UiApp.unblockPage();

                            if (!response.error) {
                                calendar.removeEventById(info.event.id);
                                initPage();
                                toast.success('Evento eliminato!');
                            } else {
                                toast.error('Qualcosa è andato storto.');
                            }
                            editEventModal.$destroy();
                            dispatch('refresh');
                        }
                    });
                });
            },
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
            height: '600px',
            views: {
                dayGridMonth: {
                    dayMaxEvents: true,
                },
            },

            view: 'dayGridMonth',
            date: TODAY,

            editable: true,
            dayMaxEvents: true,
            events: [],
            selectable: true,

            select: function (info) {
                if (!canPerformAction('association.courses.update')) return;
                if (moment(info.end).diff(moment(info.start), 'days') <= 1) return;
                swal.fire({
                    text: `Sei sicuro di voler eliminare tutti gli eventi dal ${info.start.toLocaleDateString()} al ${info.end.toLocaleDateString()}?`,
                    icon: 'warning',
                    showCancelButton: true,
                    buttonsStyling: false,
                    confirmButtonText: 'Elimina',
                    cancelButtonText: 'Annulla',
                    reverseButtons: true,
                    customClass: {
                        confirmButton: 'btn font-weight-bold btn-light-danger',
                        cancelButton: 'btn font-weight-bold btn-light-primary',
                    },
                }).then(async function (result) {
                    if (result.value) {
                        let events = calendar.getEvents();
                        for (let event of events) {
                            if (
                                moment(event.start).isSameOrAfter(info.start) &&
                                moment(event.start).isSameOrBefore(info.end)
                            ) {
                                await apiFetch(replaceUID(__bakney.env.API.COURSE.CALENDAR_UPDATE, id), {
                                    method: 'DELETE',
                                    body: JSON.stringify({
                                        event_id: event.id,
                                    }),
                                });
                                calendar.removeEventById(event.id);
                            }
                        }
                        swal.fire({
                            text: 'Eventi eliminati!',
                            icon: 'success',
                            buttonsStyling: false,
                            confirmButtonText: 'Ok, capito!',
                            customClass: {
                                confirmButton: 'btn font-weight-bold btn-light-primary',
                            },
                        });
                    } else if (result.dismiss === 'cancel') {
                        swal.fire({
                            text: 'Eventi non eliminati!',
                            icon: 'error',
                            buttonsStyling: false,
                            confirmButtonText: 'Ok, capito!',
                            customClass: {
                                confirmButton: 'btn font-weight-bold btn-light-primary',
                            },
                        });
                    }
                });
            },

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

            eventDrop: function (info) {
                let updatedEvents = calendar.getEvents();
                saveCalendarDirectly(updatedEvents);
            },
        });

        setTimeout(() => {
            loading = false;
        }, 1000);
    }

    onDestroy(() => {
        if (calendar) {
            EventCalendar.destroy(calendar);
            calendar = null;
        }
    });

    async function initPage() {
        // remove the calendar if it exists
        if (calendar) EventCalendar.destroy(calendar);
        const response = await apiFetch(replaceUID(__bakney.env.API.COURSE.CALENDAR, id));

        if (!response.error) {
            let waitMountCalendarPoller;
            waitMountCalendarPoller = setInterval(() => {
                if (calendar) {
                    clearInterval(waitMountCalendarPoller);
                    normalizeCalendarEvents(response.response).forEach(event => {
                        calendar.addEvent(event);
                    });
                    calendarStatus = response.response.data.status;
                    google_calendar_id = response.response.data.google_calendar_id;
                    google_sync_enabled = response.response.data.google_sync_enabled;
                }
            }, 100);
        }
        initCalendar();
        await fetchInstructors();
    }

    onMount(async () => {
        await initPage();
    });

    afterUpdate(() => {
        // External draggable element setup has been migrated to native HTML5 drag-and-drop.
        // Note: FullCalendarInteraction.Draggable is not available with EventCalendar.
        // The #bkn_calendar_external_events container is not present in this component;
        // external drag-and-drop should be wired at the parent level with HTML5 API.
        let queue_events_old = queue_events;
        queue_events = [];
    });

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

</script>

{#if calendarVisible}
    <div class="row" in:slide={{duration: 250}}>
        <div class="col-md-9 col-sm-12">
            {#if loading}
                <ContentLoader width="100%" height="600">
                    <rect x="15" y="15" rx="4" ry="4" width="100%" height="50" />
                    <rect x="15" y="50" rx="2" ry="2" width="100%" height="450" />
                </ContentLoader>
            {/if}
            <div in:slide id="course_attendance_calendar" />
        </div>
        <div class="col-md-3 col-sm-12">
            <h4 class="card-label font-size-h4 mb-4">
                <!-- style={calendarStatus == 2 ? 'opacity:0.5;pointer-events:none' : ''}> -->
                Crea lezioni
                <span class="d-block text-muted pt-2 font-size-sm">
                    Premi su <span style="font-weight: 800 !important;">Crea lezioni periodiche</span> per creare più lezioni
                    in un click.</span>
            </h4>
            <!-- <div class="p-0 text-right" style={calendarStatus == 2 ? 'opacity:0.5;pointer-events:none' : ''}> -->
            <div class="p-0 text-right">
                {#if openNewEventModal}
                    <div class="font-size-xs">
                        <div class="form-group text-left mb-3">
                            <!-- svelte-ignore a11y-label-has-associated-control -->
                            <label class="font-weight-bold" style="font-size: .9rem">Nome<b>*</b></label>
                            <input
                                bind:value={currentEventName}
                                type="text"
                                class="form-control form-control-solid form-control-sm margin-tb-1"
                                placeholder="Nome" />
                            <!-- <span class="form-text text-muted">Per favore inserisci il cognome.</span> -->
                        </div>
                    </div>
                    <div class="font-size-xs">
                        <div class="form-group text-left mb-3">
                            <!-- svelte-ignore a11y-label-has-associated-control -->
                            <label class="font-weight-bold" style="font-size: .9rem">Ripeti nei giorni<b>*</b></label>
                            <div id="days-checkboxes" class="checkbox-list font-sm" style="font-size: .9rem">
                                <label class="checkbox mb-1" style="font-size: 1rem">
                                    <input type="checkbox" name="monday" />
                                    <span />
                                    Lunedì
                                </label>
                                <label class="checkbox mb-1" style="font-size: 1rem">
                                    <input type="checkbox" name="tuesday" />
                                    <span />
                                    Martedì
                                </label>
                                <label class="checkbox mb-1" style="font-size: 1rem">
                                    <input type="checkbox" name="wednesday" />
                                    <span />
                                    Mercoledì
                                </label>
                                <label class="checkbox mb-1" style="font-size: 1rem">
                                    <input type="checkbox" name="thursday" />
                                    <span />
                                    Giovedì
                                </label>
                                <label class="checkbox mb-1" style="font-size: 1rem">
                                    <input type="checkbox" name="friday" />
                                    <span />
                                    Venerdì
                                </label>
                                <label class="checkbox mb-1" style="font-size: 1rem">
                                    <input type="checkbox" name="saturday" />
                                    <span />
                                    Sabato
                                </label>
                                <label class="checkbox mb-1" style="font-size: 1rem">
                                    <input type="checkbox" name="sunday" />
                                    <span />
                                    Domenica
                                </label>
                            </div>
                        </div>
                    </div>
                    <div class="font-size-xs">
                        <div class="form-group text-left mb-3">
                            <!-- svelte-ignore a11y-label-has-associated-control -->
                            <label class="font-weight-bold" style="font-size: .9rem"
                                >Ripeti fino al giorno (escluso)<b>*</b></label>
                            <DateInput id="event_date" name="event_date"
                                format="DD/MM/YYYY" placeholder="Seleziona Data"
                                bind:value={eventDate} sizeClass="form-control-sm" />
                        </div>
                    </div>
                    <div class="d-flex justify-content-between align-items-center">
                        <!-- svelte-ignore a11y-label-has-associated-control -->
                        <label class="mb-0">Tutto il giorno </label>
                        <!-- svelte-ignore a11y-label-has-associated-control -->
                        <div style="display: flex; align-items: center; height: 4rem;">
                            <span class="switch switch-sm switch-icon">
                                <label>
                                    <input type="checkbox" bind:checked={periodicAllDay} name="event_allday" />
                                    <span />
                                </label>
                            </span>
                        </div>
                    </div>
                    <div
                        class="font-size-xs d-flex justify-content-between"
                        style={periodicAllDay ? 'opacity: 0.5' : ''}>
                        <div class="form-group text-left mb-0">
                            <!-- svelte-ignore a11y-label-has-associated-control -->
                            <label class="font-weight-bold" style="font-size: .9rem">Inizio</label>
                            <input
                                disabled={periodicAllDay}
                                name="event_start"
                                bind:value={periodicStart}
                                type="time"
                                class="form-control form-control-solid form-control-sm m-0" />
                        </div>
                        <div class="form-group text-left mb-0">
                            <!-- svelte-ignore a11y-label-has-associated-control -->
                            <label class="font-weight-bold" style="font-size: .9rem">Fine</label>
                            <input
                                disabled={periodicAllDay}
                                bind:value={periodicEnd}
                                name="event_end"
                                type="time"
                                class="form-control form-control-solid form-control-sm m-0" />
                        </div>
                    </div>
                    <div class="form-group text-left mt-4">
                        <!-- svelte-ignore a11y-label-has-associated-control -->
                        <label class="font-weight-bold" style="font-size: .9rem">Istruttore lezione</label>
                        <Select
                            hideEmptyState={true}
                            multiple={true}
                            bind:value={instructor}
                            bind:items={instructors}
                            placeholder="Seleziona l'istruttore"
                            name="instructor" />
                    </div>
                {/if}
                <!-- svelte-ignore a11y-missing-attribute -->
                <!-- svelte-ignore a11y-click-events-have-key-events -->
                <div class="d-flex align-items-center justify-content-center">
                    {#if openNewEventModal}
                        <button
                            class="btn btn-sm btn-light-dark font-weight-boldest mr-2 d-flex align-items-center"
                            on:click={() => (openNewEventModal = false)}>
                            Chiudi
                        </button>
                    {/if}
                    {#if canPerformAction('association.courses.update')}
                        <!-- svelte-ignore a11y-click-events-have-key-events -->
                        <!-- svelte-ignore a11y-missing-attribute -->
                        <button
                            class="btn btn-sm btn-primary font-white font-weight-boldest d-flex align-items-center"
                            style={openNewEventModal && String(currentEventName).length < 3
                                ? 'opacity: 0.6;pointer-events:none'
                                : ''}
                            on:click={async () => {
                                if (openNewEventModal) {
                                    // generate events based on the selected days, the event name and the end date
                                    const selectedDays = document.querySelectorAll(
                                        '#days-checkboxes input[type="checkbox"]:checked'
                                    );

                                    // get the selected days
                                    const days = Array.from(selectedDays).map(d => d.name);
                                    const selectedDates = recurringDates(
                                        moment().format('YYYY-MM-DD'),
                                        eventDate,
                                        days
                                    );

                                    if (selectedDates.length === 0) {
                                        toast.error('Seleziona almeno un giorno e una data finale valida.');
                                        return;
                                    }

                                    let groupId = uuidv4();
                                    // create the events
                                    let newEvents = selectedDates.map(d => {
                                        let startDate = moment(d).format('YYYY-MM-DD');
                                        let endDate = moment(d).format('YYYY-MM-DD');

                                        if (!periodicAllDay) {
                                            // add the start and end time to the dates
                                            startDate = moment(d).format('YYYY-MM-DD') + 'T' + periodicStart + ':00';
                                            endDate = moment(d).format('YYYY-MM-DD') + 'T' + periodicEnd + ':00';
                                            // console.info(startDate, endDate);
                                        }
                                        let extendedProps = {
                                            groupId: groupId,
                                        };
                                        if (instructor !== undefined && instructor != null)
                                            extendedProps.instructor = instructor;

                                        return {
                                            id: uuidv4(),
                                            title: String(currentEventName),
                                            start: moment(startDate).format(),
                                            end: moment(endDate).format(),
                                            allDay: periodicAllDay,
                                            extendedProps: extendedProps,
                                            // className: 'bg-light-primary',
                                        };
                                    });

                                    // add the events to the calendar
                                    for (let i = 0; i < newEvents.length; i++) {
                                        try {
                                            calendar.addEvent(newEvents[i]);
                                        } catch (e) {
                                            console.error(e);
                                        }
                                    }
                                    openNewEventModal = false;
                                    currentEventName = 'Lezione';
                                    instructor = null;
                                    await saveCalendarDirectly(calendar.getEvents());
                                    dispatch('refresh');
                                } else {
                                    openNewEventModal = true;
                                }
                            }}>
                            <PlusCircle size={14} weight="bold" class="font-white mr-1" />
                            Crea lezioni periodiche
                        </button>
                    {/if}
                </div>
            </div>
        </div>
    </div>
{/if}

{#if showToggle}
    <button
        class="btn btn-primary"
        on:click={() => {
            calendarVisible = !calendarVisible;

            let calendarMounted = false;

            let calendarShowPoller = setInterval(() => {
                if (!calendarMounted && calendarVisible && document.getElementById('course_attendance_calendar'))
                    initCalendar();
                calendarMounted = true;
            }, 100);

            setTimeout(() => {
                clearInterval(calendarShowPoller);
                if (!calendarMounted) console.warn('calendar not mounted');
            }, 300);
        }}>Mostra Calendario</button>
{/if}

<div class="modal fade" id="share-link" tabindex="-1" role="dialog" aria-labelledby="staticBackdrop" aria-hidden="true">
    <div class="modal-dialog" role="document">
        <div class="modal-content">
            <div class="modal-header border-0 pb-0">
                <h5 class="modal-title" id="exampleModalLabel">Condividi calendario</h5>
                <button type="button" class="close" data-dismiss="modal" aria-label="Close">
                    <X size={16} aria-hidden="true" />
                </button>
            </div>
            <div class="modal-body">
                <div class="alert alert-custom alert-light-info fade show mb-5" role="alert">
                    <div class="alert-icon"><Info size={16} /></div>
                    <div class="alert-text">
                        Condividi il seguente link con i tuoi soci per permettere loro di visualizzare il calendario del
                        corso. Puoi anche copiare il codice HTML per incorporarlo nel tuo sito.
                    </div>
                    <div class="alert-close">
                        <button type="button" class="close" data-dismiss="alert" aria-label="Close">
                            <span aria-hidden="true">
                                <X size={16} />
                            </span>
                        </button>
                    </div>
                </div>
                <div class="input-group link-share-group">
                    <!-- svelte-ignore missing-declaration -->
                    <Clipboard
                        text="{__bakney.env.DOMAIN}/#/shared-calendar/{id}"
                        let:copy
                        on:copy={() => {
                            copied = true;
                            setTimeout(() => {
                                copied = false;
                            }, 2000);
                            toast.success('Link copiato negli appunti');
                        }}>
                        <input
                            type="text"
                            class="form-control {copied ? 'bg-light-success' : ''}"
                            style="pointer-events: none;"
                            value="{__bakney.env.DOMAIN}/#/shared-calendar/{id}" />
                        <div class="input-group-append">
                            <!-- svelte-ignore a11y-click-events-have-key-events -->
                            <!-- svelte-ignore a11y-missing-attribute -->
                            <a
                                on:click={copy}
                                class="btn btn-primary"
                                style="border-radius: 0 .55rem .55rem 0;"
                                data-clipboard="true"
                                data-clipboard-target="#bkn_clipboard_1">
                                <Copy size={16} weight="duotone" />
                            </a>
                        </div>
                    </Clipboard>
                </div>
                <div class="input-group mt-4">
                    <!-- svelte-ignore missing-declaration -->
                    <!-- svelte-ignore a11y-label-has-associated-control -->
                    <label class="font-weight-bold">Codice HTML da incorporare nel tuo sito</label>
                    <textarea
                        class="text-left"
                        style="width: 100%; border-radius: 0.35rem; border: 1px solid var(--border-color); background: var(--bg-input); padding: 1rem;resize: none;"
                        rows="4"
                        value={`<iframe src="${__bakney.env.DOMAIN}/#/shared-calendar/${id}" frameborder="0" style="overflow:hidden;height:100%;width:100%" height="100%" width="100%"></iframe>`} />
                </div>
            </div>
            <div class="modal-footer border-0 pt-0 d-flex justify-content-between">
                <div class="d-flex">
                    <button
                        type="button"
                        class="btn btn-teal-themed font-weight-bold d-flex align-items-center"
                        on:click={() => {
                            window.open(
                                `https://api.whatsapp.com/send/?text=` +
                                    encodeURIComponent(
                                        `Ciao 👋\nQuesto è il link del calendario per il corso:\n${`${__bakney.env.DOMAIN}/#/shared-calendar/${id}`} \n\nCordiali saluti,\n${
                                            $userData.sport_association.denomination
                                        }`
                                    )
                            );
                        }}><WhatsappLogo size="20" weight="fill" class="mr-2" />Whatsapp</button>
                    <button
                        type="button"
                        class="btn btn-primary font-weight-bold d-flex align-items-center ml-2"
                        on:click={() => {
                            window.open(
                                "mailto:user@example.com?subject=Compila il modulo d'iscrizione&body=" +
                                    encodeURIComponent(
                                        `Ciao,\nQuesto è il link per il corso:\n${`${__bakney.env.DOMAIN}/#/shared-calendar/${id}`}\n\nCordiali saluti,\n${
                                            $userData.sport_association.denomination
                                        }`
                                    )
                            );
                        }}><PaperPlaneRight size="20" weight="fill" class="mr-2" /> Invia Email</button>
                </div>
                <button type="button" class="btn mt-0 btn-secondary font-weight-bold" data-dismiss="modal"
                    >Chiudi</button>
            </div>
        </div>
    </div>
</div>
