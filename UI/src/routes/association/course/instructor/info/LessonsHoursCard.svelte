<script>
    import moment from 'moment';
    import {tick, onDestroy} from 'svelte';
    import Portal from 'svelte-portal';
    import {lessonCalendarHref} from 'utils/calendarLessonNavigation.js';

    export let instructorId;
    export let data = null;
    export let loading = true;
    export let error = false;
    export let canOpenCalendar = false;

    let trigger;
    let panel;
    let open = false;
    let positioned = false;
    let closeTimer;
    $: panelId = `instructor-calendar-hours-${instructorId}`;
    $: if (loading) open = false;

    const hours = value => Number(value || 0).toLocaleString('it-IT', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });

    function positionPanel() {
        if (!open || !panel || !trigger) return;
        const bounds = trigger.getBoundingClientRect();
        const width = Math.min(420, window.innerWidth - 32);
        panel.style.width = `${width}px`;
        panel.style.left = `${Math.max(16, Math.min(bounds.left, window.innerWidth - width - 16))}px`;
        const height = panel.offsetHeight;
        const below = bounds.bottom + 10;
        panel.style.top = `${Math.max(16, below + height <= window.innerHeight - 16
            ? below : Math.min(bounds.top - height - 10, window.innerHeight - height - 16))}px`;
        positioned = true;
    }

    function cancelClose() {
        clearTimeout(closeTimer);
    }

    function scheduleClose() {
        cancelClose();
        // Allow the pointer to cross the gap into the body-mounted panel.
        closeTimer = setTimeout(() => {
            if (!panel?.contains(document.activeElement)) open = false;
        }, 300);
    }

    async function showPanel() {
        cancelClose();
        if (open) return;
        positioned = false;
        open = true;
        await tick();
        positionPanel();
    }

    function closePanel() {
        cancelClose();
        open = false;
        trigger?.focus();
    }

    function handleOutsideClick(event) {
        if (open && !trigger?.contains(event.target) && !panel?.contains(event.target)) {
            cancelClose();
            open = false;
        }
    }

    onDestroy(cancelClose);

    function handleKeydown(event) {
        if (open && event.key === 'Escape') {
            event.preventDefault();
            closePanel();
        }
    }
</script>

<svelte:window on:resize={positionPanel} on:scroll={positionPanel} on:click={handleOutsideClick} on:keydown={handleKeydown} />

<button
    bind:this={trigger}
    type="button"
    class="calendar-hours-card card-widget card p-4 m-0 w-100"
    on:click={showPanel}
    on:mouseenter={showPanel}
    on:mouseleave={scheduleClose}
    aria-expanded={open}
    aria-controls={panelId}
    aria-label="Ore a calendario: apri il dettaglio delle lezioni">
    <span class="font-weight-boldest text-center" style="font-size: 1rem;">ORE A CALENDARIO</span>
    <span class="text-center font-weight-bolder text-primary" style="font-size: 1.75rem;">
        {loading ? '…' : error ? 'Non disponibili' : hours(data?.total_hours)}
    </span>
</button>

{#if open}
<Portal target="body">
<section
    bind:this={panel}
    id={panelId}
    aria-labelledby={`${panelId}-title`}
    class="lessons-panel"
    on:mouseenter={cancelClose}
    on:mouseleave={scheduleClose}
    on:focusin={cancelClose}
    on:focusout={scheduleClose}
    style:visibility={positioned ? 'visible' : 'hidden'}>
    <div class="panel-heading">
        <div>
            <h3 id={`${panelId}-title`}>Ore a calendario</h3>
            <p>Durata delle lezioni pubblicate nel periodo, incluse quelle future. Non certifica la presenza.</p>
        </div>
        <button type="button" class="close-panel" on:click={closePanel} aria-label="Chiudi dettaglio lezioni">×</button>
    </div>
    {#if loading}
        <p class="panel-state">Caricamento…</p>
    {:else if error}
        <p class="panel-state">Ore a calendario non disponibili.</p>
    {:else if !data?.courses?.length}
        <p class="panel-state">Nessuna lezione nel periodo selezionato.</p>
    {:else}
        <div class="course-list">
            {#each data.courses as course}
                <div class="course-section">
                    <div class="course-heading">
                        <h4>
                            {#if canOpenCalendar}
                                <a class="course-link" href={`#/course/overview/${encodeURIComponent(course.course_id)}/calendar`}>{course.course_title}</a>
                            {:else}
                                {course.course_title}
                            {/if}
                        </h4>
                        <span>{hours(course.hours)} h</span>
                    </div>
                    <ul>
                        {#each course.lessons || [] as lesson}
                            <li>
                                {#if canOpenCalendar && lesson.event_id}
                                    <a class="lesson-row" href={lessonCalendarHref(course.course_id, lesson.event_id)}>
                                        <span><strong>{lesson.title}</strong><small>{moment(lesson.start).format('DD/MM/YYYY HH:mm')} – {moment(lesson.end).format('DD/MM/YYYY HH:mm')}</small></span>
                                        <span class="lesson-hours">{hours(lesson.hours)} h</span>
                                    </a>
                                {:else}
                                    <div class="lesson-row">
                                        <span><strong>{lesson.title}</strong><small>{moment(lesson.start).format('DD/MM/YYYY HH:mm')} – {moment(lesson.end).format('DD/MM/YYYY HH:mm')}</small></span>
                                        <span class="lesson-hours">{hours(lesson.hours)} h</span>
                                    </div>
                                {/if}
                            </li>
                        {/each}
                    </ul>
                </div>
            {/each}
        </div>
        <div class="panel-total"><span>Totale nel periodo</span><strong>{hours(data.total_hours)} h</strong></div>
    {/if}
</section>
</Portal>
{/if}

<style>
    .calendar-hours-card { align-items: center; border: 0; cursor: pointer; color: inherit; font: inherit; }
    .calendar-hours-card:hover { box-shadow: 0 0 0 2px #dce8ff; }
    .calendar-hours-card:focus-visible { outline: 2px solid #3699ff; outline-offset: 3px; }
    .lessons-panel { position: fixed; z-index: 1080; inset: auto; margin: 0; padding: 0; width: min(420px, calc(100vw - 32px)); max-height: calc(100vh - 32px); overflow: auto; border: 1px solid #e7eaf0; border-radius: 16px; background: white; color: #181c32; box-shadow: 0 16px 48px #18243a29; }
    .panel-heading { display: flex; gap: 16px; padding: 20px; border-bottom: 1px solid #eef0f5; }
    h3 { margin: 0 0 8px; font-size: 1.1rem; font-weight: 700; }
    .panel-heading p { margin: 0; color: #687385; font-size: .85rem; line-height: 1.5; }
    .close-panel { align-self: flex-start; background: #f3f6f9; border: 0; border-radius: 8px; padding: 0 9px; font-size: 1.4rem; cursor: pointer; }
    .course-list { padding: 4px 12px; }
    .course-section { padding: 12px 0; }
    .course-section + .course-section { border-top: 1px solid #eef0f5; }
    .course-heading { display: flex; justify-content: space-between; gap: 12px; padding: 0 8px 8px; font-weight: 700; }
    .course-link { color: #176ac6; }
    .course-link:hover, .course-link:focus-visible { text-decoration: underline; }
    h4 { margin: 0; font-size: .95rem; font-weight: 700; overflow-wrap: anywhere; }
    ul { list-style: none; margin: 0; padding: 0; }
    .lesson-row { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 10px 8px; border-radius: 8px; color: inherit; }
    a.lesson-row:hover, a.lesson-row:focus-visible { background: #eef5ff; color: #176ac6; }
    .lesson-row strong { display: block; font-size: .9rem; overflow-wrap: anywhere; }
    .lesson-row small { display: block; margin-top: 4px; color: #687385; font-size: .8rem; }
    .lesson-hours { flex-shrink: 0; font-size: .85rem; font-weight: 600; }
    .panel-state { padding: 20px; margin: 0; color: #687385; }
    .panel-total { display: flex; justify-content: space-between; gap: 12px; padding: 16px 20px; background: #f6f8fc; border-top: 1px solid #eef0f5; }
</style>
