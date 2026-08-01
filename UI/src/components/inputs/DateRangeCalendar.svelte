<script>
    import { createEventDispatcher } from 'svelte';
    import { CaretLeft, CaretRight } from 'phosphor-svelte';
    import moment from 'moment';

    const dispatch = createEventDispatcher();

    export let leftMonth;         // moment instance
    export let draftStart = null; // DD/MM/YYYY or null
    export let draftEnd = null;   // DD/MM/YYYY or null
    export let hoverDate = null;  // DD/MM/YYYY or null
    export let minDate = null;    // DD/MM/YYYY or null
    export let maxDate = null;    // DD/MM/YYYY or null

    const FORMAT = 'DD/MM/YYYY';
    const ITALIAN_MONTHS = [
        'Gennaio', 'Febbraio', 'Marzo', 'Aprile', 'Maggio', 'Giugno',
        'Luglio', 'Agosto', 'Settembre', 'Ottobre', 'Novembre', 'Dicembre'
    ];
    const DAY_HEADERS = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom'];

    $: rightMonth = moment(leftMonth).add(1, 'month');

    function buildMonthGrid(monthMoment) {
        const startOfMonth = moment(monthMoment).startOf('month');
        const endOfMonth = moment(monthMoment).endOf('month');
        const isoStart = startOfMonth.isoWeekday();          // 1=Mon … 7=Sun
        const daysCount = endOfMonth.date();
        const cells = [];

        // Leading days (previous month)
        const prevMonthEnd = moment(startOfMonth).subtract(1, 'day');
        for (let i = isoStart - 2; i >= 0; i--) {
            const d = prevMonthEnd.date() - i;
            cells.push({ day: d, date: moment(startOfMonth).subtract(isoStart - 1 - i, 'days'), other: true });
        }

        // Current month
        for (let d = 1; d <= daysCount; d++) {
            cells.push({ day: d, date: moment(monthMoment).date(d), other: false });
        }

        // Trailing days (next month)
        const total = Math.ceil((cells.length) / 7) * 7;
        for (let d = 1; cells.length < total; d++) {
            cells.push({ day: d, date: moment(endOfMonth).add(d, 'days'), other: true });
        }

        return cells;
    }

    function dateStr(m) {
        return m.format(FORMAT);
    }

    function isToday(d) {
        return d.isSame(moment(), 'day');
    }

    function isOutOfBounds(ds) {
        const d = moment(ds, FORMAT);
        if (minDate && d.isBefore(moment(minDate, FORMAT), 'day')) return true;
        if (maxDate && d.isAfter(moment(maxDate, FORMAT), 'day')) return true;
        return false;
    }

    function isSelStart(ds) {
        return ds === draftStart;
    }

    function isSelEnd(ds) {
        return ds === draftEnd;
    }

    $: inRangeDates = (() => {
        const set = new Set();
        if (!draftStart) return set;
        const endStr = draftEnd || hoverDate;
        if (!endStr) return set;
        const a = moment(draftStart, FORMAT);
        const b = moment(endStr, FORMAT);
        const lo = a.isBefore(b) ? a : b;
        const hi = a.isBefore(b) ? b : a;
        let d = lo.clone();
        while (d.isSameOrBefore(hi, 'day')) {
            set.add(d.format(FORMAT));
            d.add(1, 'day');
        }
        return set;
    })();

    function isHover(ds) {
        return hoverDate && !draftEnd && ds === hoverDate;
    }

    function cellClass(cell) {
        const ds = dateStr(cell.date);
        const cls = [];
        if (cell.other) cls.push('other-month');
        if (isOutOfBounds(ds)) cls.push('disabled');
        if (isToday(cell.date)) cls.push('today');
        return cls.join(' ');
    }

    function onClick(cell) {
        const ds = dateStr(cell.date);
        if (isOutOfBounds(ds)) return;
        dispatch('selectDate', ds);
    }

    function onEnter(cell) {
        if (!draftStart || draftEnd) return;
        dispatch('hoverDate', dateStr(cell.date));
    }

    function onLeave() {
        dispatch('hoverDate', null);
    }
</script>

<div class="date-range-calendar">
    <button class="nav-btn nav-prev" on:click={() => dispatch('prevMonth')} type="button" aria-label="Mese precedente">
        <CaretLeft size={18} weight="bold" />
    </button>

    <div class="months-wrapper">
        <div class="month-grid">
            <div class="month-header">{ITALIAN_MONTHS[leftMonth.month()]} {leftMonth.year()}</div>
            <div class="day-headers">
                {#each DAY_HEADERS as h}<span class="day-header">{h}</span>{/each}
            </div>
            <div class="days">
                {#each buildMonthGrid(leftMonth) as cell (dateStr(cell.date))}
                    {@const ds = dateStr(cell.date)}
                    <button
                        class="day-cell {cellClass(cell)}"
                        class:sel-start={ds === draftStart}
                        class:sel-end={ds === draftEnd}
                        class:in-range={inRangeDates.has(ds)}
                        class:hover={isHover(ds)}
                        disabled={isOutOfBounds(ds)}
                        on:click={() => onClick(cell)}
                        on:mouseenter={() => onEnter(cell)}
                        on:mouseleave={onLeave}
                        type="button"
                    >{cell.day}</button>
                {/each}
            </div>
        </div>

        <div class="month-grid">
            <div class="month-header">{ITALIAN_MONTHS[rightMonth.month()]} {rightMonth.year()}</div>
            <div class="day-headers">
                {#each DAY_HEADERS as h}<span class="day-header">{h}</span>{/each}
            </div>
            <div class="days">
                {#each buildMonthGrid(rightMonth) as cell (dateStr(cell.date))}
                    {@const ds = dateStr(cell.date)}
                    <button
                        class="day-cell {cellClass(cell)}"
                        class:sel-start={ds === draftStart}
                        class:sel-end={ds === draftEnd}
                        class:in-range={inRangeDates.has(ds)}
                        class:hover={isHover(ds)}
                        disabled={isOutOfBounds(ds)}
                        on:click={() => onClick(cell)}
                        on:mouseenter={() => onEnter(cell)}
                        on:mouseleave={onLeave}
                        type="button"
                    >{cell.day}</button>
                {/each}
            </div>
        </div>
    </div>

    <button class="nav-btn nav-next" on:click={() => dispatch('nextMonth')} type="button" aria-label="Mese successivo">
        <CaretRight size={18} weight="bold" />
    </button>

    <div class="mobile-nav">
        <button class="nav-btn-mobile" on:click={() => dispatch('prevMonth')} type="button" aria-label="Mese precedente">
            <CaretLeft size={16} weight="bold" />
        </button>
        <button class="nav-btn-mobile" on:click={() => dispatch('nextMonth')} type="button" aria-label="Mese successivo">
            <CaretRight size={16} weight="bold" />
        </button>
    </div>
</div>

<style>
    .date-range-calendar {
        display: flex;
        align-items: flex-start;
        gap: 0.25rem;
        width: 100%;
        user-select: none;
    }

    .nav-btn {
        flex-shrink: 0;
        margin-top: 0.25rem;
        display: flex;
        align-items: center;
        justify-content: center;
        width: 2rem;
        height: 2.25rem;
        border: none;
        background: transparent;
        color: var(--text-secondary);
        cursor: pointer;
        border-radius: 0.42rem;
    }

    .nav-btn:hover {
        background: var(--bg-hover, rgba(0,0,0,0.05));
        color: var(--text-primary);
    }

    .mobile-nav {
        display: none;
    }

    .months-wrapper {
        display: flex;
        flex: 1 1 auto;
        gap: 0.5rem;
    }

    .month-grid {
        flex: 1 1 0;
        min-width: 0;
    }

    .month-header {
        text-align: center;
        font-weight: 600;
        font-size: 1rem;
        padding: 0.25rem 0 0.35rem;
        color: var(--text-primary);
    }

    .day-headers,
    .days {
        display: grid;
        grid-template-columns: repeat(7, 1fr);
        text-align: center;
    }

    .day-header {
        font-size: 0.78rem;
        font-weight: 600;
        color: var(--text-secondary);
        padding: 0.15rem 0;
    }

    .day-cell {
        appearance: none;
        border: none;
        background: transparent;
        font-size: 0.95rem;
        padding: 0.28rem 0;
        cursor: pointer;
        border-radius: 0.35rem;
        color: var(--text-primary);
        position: relative;
        transition: background 0.15s, color 0.15s;
    }

    .day-cell:hover:not(.disabled):not(.sel-start):not(.sel-end) {
        background: var(--bg-hover, rgba(0,0,0,0.06));
    }

    .day-cell.other-month {
        color: var(--text-muted, #92929C);
        opacity: 0.5;
    }

    .day-cell.disabled {
        opacity: 0.3;
        cursor: not-allowed;
    }

    .day-cell.today:not(.sel-start):not(.sel-end) {
        outline: 2px solid var(--primary);
        outline-offset: -2px;
        font-weight: 700;
    }

    .day-cell.sel-start,
    .day-cell.sel-end {
        background: var(--primary) !important;
        color: var(--white) !important;
        font-weight: 700;
        border-radius: 0.35rem;
    }

    .day-cell.in-range::before,
    .day-cell.hover::before {
        content: '';
        position: absolute;
        inset: 0;
        background: var(--primary);
        border-radius: inherit;
        z-index: -1;
    }

    .day-cell.in-range {
        border-radius: 0;
        color: var(--primary);
        font-weight: 600;
    }

    .day-cell.in-range::before {
        opacity: 0.18;
    }

    .day-cell.hover {
        border-radius: 0.35rem;
    }

    .day-cell.hover::before {
        opacity: 0.12;
    }

    .day-cell.sel-start.in-range {
        border-radius: 0.35rem 0 0 0.35rem;
    }

    .day-cell.sel-end.in-range {
        border-radius: 0 0.35rem 0.35rem 0;
    }

    @media (max-width: 920px) {
        .date-range-calendar {
            flex-direction: column;
            align-items: center;
            gap: 0.5rem;
        }

        .nav-btn {
            display: none;
        }

        .mobile-nav {
            display: flex;
            gap: 0.75rem;
            margin-top: 0.25rem;
        }

        .nav-btn-mobile {
            display: flex;
            align-items: center;
            justify-content: center;
            width: 2.5rem;
            height: 2.25rem;
            border: 1px solid var(--border-color);
            background: var(--bg-surface);
            color: var(--text-secondary);
            cursor: pointer;
            border-radius: 0.42rem;
        }

        .nav-btn-mobile:hover {
            background: var(--bg-hover, rgba(0,0,0,0.05));
            color: var(--text-primary);
        }

        .months-wrapper {
            flex-direction: column;
            gap: 0.75rem;
            width: 100%;
        }

        .month-header {
            font-size: 1.05rem;
            padding: 0.15rem 0 0.25rem;
        }

        .day-header {
            font-size: 0.8rem;
            padding: 0.1rem 0;
        }

        .day-cell {
            font-size: 1rem;
            padding: 0.35rem 0;
        }
    }
</style>
