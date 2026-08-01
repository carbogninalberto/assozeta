<script>
    import { createEventDispatcher, onMount } from 'svelte';
    import Portal from 'svelte-portal';
    import { Calendar } from 'phosphor-svelte';
    import moment from 'moment';
    import 'moment/locale/it';
    import DateRangeCalendar from './DateRangeCalendar.svelte';
    import DateRangePresetList from './DateRangePresetList.svelte';
    import BasicDrawer from '../drawer/basic-drawer.svelte';

    const dispatch = createEventDispatcher();

    // ---- Public props ----
    export let id = '';
    export let name = '';
    export let startValue = '';
    export let endValue = '';
    export let format = 'DD/MM/YYYY';
    export let required = false;
    export let disabled = false;
    export let startPlaceholder = 'gg/mm/aaaa';
    export let endPlaceholder = 'gg/mm/aaaa';
    export let placeholder = 'Seleziona un periodo';
    export let sizeClass = 'form-control-lg';
    export let wrapperClass = '';
    export let showPresets = true;
    export let immediate = false;
    export let minDate = null;   // in `format` format
    export let maxDate = null;   // in `format` format

    // ---- Internal state ----
    let open = false;
    let isMobile = false;
    let draftStart = null;       // DD/MM/YYYY
    let draftEnd = null;         // DD/MM/YYYY
    let leftMonth;
    let hoverDate = null;
    let activePreset = null;

    let triggerEl;
    let panelEl;

    // Panel positioning
    let panelStyle = '';

    // ---- Constants ----
    const DISPLAY_FMT = 'DD/MM/YYYY';
    const PRESET_KEYS = {
        currentYear:    'currentYear',
        previousYear:   'previousYear',
        today:          'today',
        yesterday:      'yesterday',
        last30Days:     'last30Days',
        thisMonth:      'thisMonth',
        lastMonth:      'lastMonth',
        custom:         'custom',
    };

    const PRESET_CONFIG = [
        { key: PRESET_KEYS.currentYear,  label: 'Anno Corrente' },
        { key: PRESET_KEYS.previousYear, label: 'Anno Precedente' },
        { key: PRESET_KEYS.today,        label: 'Oggi' },
        { key: PRESET_KEYS.yesterday,    label: 'Ieri' },
        { key: PRESET_KEYS.last30Days,   label: 'Ultimi 30 giorni' },
        { key: PRESET_KEYS.thisMonth,    label: 'Questo mese' },
        { key: PRESET_KEYS.lastMonth,    label: 'Mese scorso' },
        { key: PRESET_KEYS.custom,       label: 'Periodo personalizzato' },
    ];

    // ---- Helpers ----
    function normalizeFormat(fmt) {
        if (fmt === 'L') return 'DD/MM/YYYY';
        return fmt;
    }

    $: resolvedFormat = normalizeFormat(format);

    function toDisplay(val) {
        if (!val) return '';
        const m = moment(val, resolvedFormat, true);
        return m.isValid() ? m.format(DISPLAY_FMT) : '';
    }

    function fromDisplay(val) {
        if (!val) return '';
        const m = moment(val, DISPLAY_FMT, true);
        return m.isValid() ? m.format(resolvedFormat) : '';
    }

    $: startDisplay = toDisplay(startValue);
    $: endDisplay = toDisplay(endValue);
    $: displayRange = startDisplay && endDisplay ? `${startDisplay} al ${endDisplay}` : '';

    // Convert min/max to display format for calendar
    $: minDisplay = minDate ? moment(minDate, resolvedFormat, true).format(DISPLAY_FMT) : null;
    $: maxDisplay = maxDate ? moment(maxDate, resolvedFormat, true).format(DISPLAY_FMT) : null;

    // Format for range display in footer
    function fmtRange() {
        if (!draftStart) return '—';
        const s = moment(draftStart, DISPLAY_FMT).format('D MMM YYYY');
        if (!draftEnd) return `Dal ${s}`;
        const e = moment(draftEnd, DISPLAY_FMT).format('D MMM YYYY');
        return `${s} — ${e}`;
    }

    function computePresetValues(key) {
        const today = moment();
        switch (key) {
            case PRESET_KEYS.currentYear:
                return { start: moment().startOf('year').format(DISPLAY_FMT), end: today.format(DISPLAY_FMT) };
            case PRESET_KEYS.previousYear:
                return {
                    start: moment().subtract(1, 'year').startOf('year').format(DISPLAY_FMT),
                    end: moment().subtract(1, 'year').endOf('year').format(DISPLAY_FMT),
                };
            case PRESET_KEYS.today:
                return { start: today.format(DISPLAY_FMT), end: today.format(DISPLAY_FMT) };
            case PRESET_KEYS.yesterday:
                const y = moment().subtract(1, 'day');
                return { start: y.format(DISPLAY_FMT), end: y.format(DISPLAY_FMT) };
            case PRESET_KEYS.last30Days:
                return { start: moment().subtract(29, 'days').format(DISPLAY_FMT), end: today.format(DISPLAY_FMT) };
            case PRESET_KEYS.thisMonth:
                return { start: moment().startOf('month').format(DISPLAY_FMT), end: moment().endOf('month').format(DISPLAY_FMT) };
            case PRESET_KEYS.lastMonth:
                const lm = moment().subtract(1, 'month');
                return { start: lm.startOf('month').format(DISPLAY_FMT), end: lm.endOf('month').format(DISPLAY_FMT) };
            default:
                return { start: null, end: null };
        }
    }

    function detectPreset(s, e) {
        if (!s || !e) return PRESET_KEYS.custom;
        for (const { key } of PRESET_CONFIG) {
            if (key === PRESET_KEYS.custom) continue;
            const p = computePresetValues(key);
            if (p.start === s && p.end === e) return key;
        }
        return PRESET_KEYS.custom;
    }

    function commitValues(startStr, endStr) {
        const newStart = fromDisplay(startStr);
        const newEnd = fromDisplay(endStr);
        if (newStart !== startValue) startValue = newStart;
        if (newEnd !== endValue) endValue = newEnd;
        dispatch('change', { start: newStart, end: newEnd });
    }

    function closePanel() {
        open = false;
    }

    function cancelPanel() {
        draftStart = startDisplay;
        draftEnd = endDisplay;
        leftMonth = draftStart
            ? moment(draftStart, DISPLAY_FMT).startOf('month')
            : moment().startOf('month');
        activePreset = detectPreset(draftStart, draftEnd);
        hoverDate = null;
        closePanel();
        dispatch('cancel');
    }

    function applyPanel() {
        if (draftStart) {
            commitValues(draftStart, draftEnd || draftStart);
        }
        closePanel();
        dispatch('apply', { start: fromDisplay(draftStart), end: fromDisplay(draftEnd || draftStart) });
    }

    function openPanel() {
        if (disabled || open) return;
        draftStart = startDisplay;
        draftEnd = endDisplay;
        leftMonth = draftStart
            ? moment(draftStart, DISPLAY_FMT).startOf('month')
            : moment().startOf('month');
        activePreset = detectPreset(draftStart, draftEnd);
        hoverDate = null;
        open = true;
        nextFrame().then(updatePosition);
    }

    function nextFrame() {
        return new Promise(resolve => requestAnimationFrame(resolve));
    }

    // ---- Calendar handlers ----
    function handleSelectDate(e) {
        const ds = e.detail;
        if (!draftStart || (draftStart && draftEnd)) {
            // Start new selection
            draftStart = ds;
            draftEnd = null;
        } else {
            // Set end
            let s = draftStart;
            let e = ds;
            if (moment(e, DISPLAY_FMT).isBefore(moment(s, DISPLAY_FMT), 'day')) {
                [s, e] = [e, s];
            }
            draftStart = s;
            draftEnd = e;
        }
        activePreset = PRESET_KEYS.custom;

        if (immediate && draftStart && draftEnd) {
            commitValues(draftStart, draftEnd);
            closePanel();
        }
    }

    function handleHoverDate(e) {
        hoverDate = e.detail;
    }

    function handlePrevMonth() {
        leftMonth = moment(leftMonth).subtract(1, 'month');
    }

    function handleNextMonth() {
        leftMonth = moment(leftMonth).add(1, 'month');
    }

    // ---- Preset handler ----
    function handleSelectPreset(e) {
        const key = e.detail;
        if (key === PRESET_KEYS.custom) {
            activePreset = PRESET_KEYS.custom;
            return;
        }
        const p = computePresetValues(key);
        draftStart = p.start;
        draftEnd = p.end;
        activePreset = key;
        leftMonth = moment(draftStart, DISPLAY_FMT).startOf('month');
        hoverDate = null;

        // Auto-commit (all presets except custom)
        commitValues(draftStart, draftEnd);
        closePanel();
    }

    // ---- Panel positioning (desktop) ----
    function updatePosition() {
        if (!open || isMobile || !triggerEl) return;
        const triggerRect = triggerEl.getBoundingClientRect();
        if (!panelEl) {
            requestAnimationFrame(updatePosition);
            return;
        }
        const panelHeight = panelEl.offsetHeight;
        const panelWidth = panelEl.offsetWidth;
        const viewW = window.innerWidth;
        const viewH = window.innerHeight;
        const gap = 6;

        let top = triggerRect.bottom + gap;
        let left = triggerRect.left;

        if (left + panelWidth > viewW - 8) {
            left = Math.max(8, triggerRect.right - panelWidth);
        }
        if (left < 8) left = 8;

        if (top + panelHeight > viewH - 8) {
            top = triggerRect.top - panelHeight - gap;
            if (top < 8) top = 8;
        }

        panelStyle = `position:fixed;top:${top}px;left:${left}px;z-index:1060;`;
    }

    // ---- Escape / click-outside ----
    function handleGlobalKeydown(e) {
        if (e.key === 'Escape' && open) {
            cancelPanel();
        }
    }

    function handleClickOutside(e) {
        if (!open || isMobile) return;
        if (triggerEl && triggerEl.contains(e.target)) return;
        if (panelEl && panelEl.contains(e.target)) return;
        cancelPanel();
    }

    // ---- Lifecycle ----
    onMount(() => {
        moment.locale('it');
        updateViewport();
        window.addEventListener('resize', onResize);
        window.addEventListener('scroll', updatePosition, true);
        document.addEventListener('keydown', handleGlobalKeydown);
        document.addEventListener('mousedown', handleClickOutside);
        return () => {
            window.removeEventListener('resize', onResize);
            window.removeEventListener('scroll', updatePosition, true);
            document.removeEventListener('keydown', handleGlobalKeydown);
            document.removeEventListener('mousedown', handleClickOutside);
        };
    });

    function updateViewport() {
        isMobile = window.innerWidth <= 920;
    }

    function onResize() {
        updateViewport();
        if (open && !isMobile) nextFrame().then(updatePosition);
    }

    $: if (open && !isMobile && panelEl) {
        nextFrame().then(updatePosition);
    }

    // Sync draft when external values change while the panel is closed
    $: if (!open) {
        draftStart = startDisplay;
        draftEnd = endDisplay;
        activePreset = detectPreset(draftStart, draftEnd);
    }

    // Keep calendar focused on the start month while a new selection is being made
    $: if (open && draftStart && !draftEnd) {
        leftMonth = moment(draftStart, DISPLAY_FMT).startOf('month');
    }
</script>

<!-- ===== TRIGGER (single read-only input) ===== -->
<div
    class="drp-trigger input-group input-group-solid {wrapperClass}"
    bind:this={triggerEl}
    data-name={name || undefined}
>
    <input
        type="text"
        readonly
        class="form-control form-control-solid drp-display-input {sizeClass}"
        {id}
        {name}
        value={displayRange}
        {placeholder}
        {disabled}
        {required}
        on:click={openPanel}
        aria-label="Periodo"
    />
    <!-- svelte-ignore a11y-click-events-have-key-events -->
    <div class="input-group-append" on:click={openPanel}>
        <span class="input-group-text">
            <Calendar size={18} weight="duotone" />
        </span>
    </div>
</div>

<!-- ===== DESKTOP PANEL (Portal) ===== -->
{#if open && !isMobile}
    <Portal target="body">
        <!-- svelte-ignore a11y-no-static-element-interactions -->
        <div
            class="drp-panel"
            bind:this={panelEl}
            style={panelStyle}
            on:keydown={(e) => { if (e.key === 'Escape') cancelPanel(); }}
        >
            <div class="drp-panel-body">
                {#if showPresets}
                    <div class="drp-presets-col">
                        <DateRangePresetList
                            presets={PRESET_CONFIG}
                            activePreset={activePreset}
                            on:selectPreset={handleSelectPreset}
                        />
                    </div>
                {/if}
                <div class="drp-calendar-col">
                    <DateRangeCalendar
                        leftMonth={leftMonth}
                        draftStart={draftStart}
                        draftEnd={draftEnd}
                        hoverDate={hoverDate}
                        minDate={minDisplay}
                        maxDate={maxDisplay}
                        on:selectDate={handleSelectDate}
                        on:hoverDate={handleHoverDate}
                        on:prevMonth={handlePrevMonth}
                        on:nextMonth={handleNextMonth}
                    />
                </div>
            </div>
            {#if !immediate}
                <div class="drp-panel-footer">
                    <span class="drp-range-label">{fmtRange()}</span>
                    <div class="drp-footer-actions">
                        <button class="btn btn-light btn-sm" on:click={cancelPanel} type="button">Annulla</button>
                        <button class="btn btn-primary btn-sm" on:click={applyPanel} type="button">Applica</button>
                    </div>
                </div>
            {/if}
        </div>
    </Portal>
{/if}

<!-- ===== MOBILE DRAWER ===== -->
{#if isMobile}
    <BasicDrawer
        bind:isOpen={open}
        position="bottom"
        title="Seleziona periodo"
        width="100vw"
        height="auto"
        maxHeight="92vh"
        closeOnClickOutside={true}
        closeOnEsc={true}
        on:close={cancelPanel}
    >
        <div slot="content" class="drp-drawer-content">
            {#if showPresets}
                <div class="drp-drawer-presets">
                    <DateRangePresetList
                        presets={PRESET_CONFIG}
                        activePreset={activePreset}
                        on:selectPreset={handleSelectPreset}
                    />
                </div>
            {/if}
            <div class="drp-drawer-calendar">
                <DateRangeCalendar
                    leftMonth={leftMonth}
                    draftStart={draftStart}
                    draftEnd={draftEnd}
                    hoverDate={hoverDate}
                    minDate={minDisplay}
                    maxDate={maxDisplay}
                    on:selectDate={handleSelectDate}
                    on:hoverDate={handleHoverDate}
                    on:prevMonth={handlePrevMonth}
                    on:nextMonth={handleNextMonth}
                />
            </div>
            {#if !immediate}
                <div class="drp-drawer-footer">
                    <span class="drp-range-label">{fmtRange()}</span>
                    <div class="drp-footer-actions">
                        <button class="btn btn-light btn-sm" on:click={cancelPanel} type="button">Annulla</button>
                        <button class="btn btn-primary btn-sm" on:click={applyPanel} type="button">Applica</button>
                    </div>
                </div>
            {/if}
        </div>
    </BasicDrawer>
{/if}

<style>
    .drp-trigger {
        flex-wrap: nowrap;
    }

    .drp-display-input {
        cursor: pointer;
        text-align: center;
        padding-left: 0.5rem;
        padding-right: 0.5rem;
        min-width: 15.5rem;
        width: 100%;
    }

    .drp-display-input:disabled {
        cursor: not-allowed;
    }

    .drp-trigger .input-group-append {
        cursor: pointer;
    }

    .drp-trigger .input-group-text {
        padding-left: 0.5rem;
        padding-right: 0.5rem;
    }

    /* ---- Desktop Panel ---- */
    .drp-panel {
        background: var(--bg-surface);
        border: 1px solid var(--border-color);
        border-radius: 0.75rem;
        box-shadow: 0 10px 40px rgba(0,0,0,0.12);
        min-width: 38rem;
        max-width: calc(100vw - 1rem);
    }

    .drp-panel-body {
        display: flex;
        padding: 0.75rem;
        gap: 0.35rem;
    }

    .drp-presets-col {
        flex-shrink: 0;
        border-right: 1px solid var(--border-color);
        padding-right: 0.35rem;
    }

    .drp-calendar-col {
        flex: 1 1 auto;
        min-width: 0;
    }

    .drp-panel-footer {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 0.5rem 0.75rem;
        border-top: 1px solid var(--border-color);
    }

    .drp-range-label {
        font-size: 0.85rem;
        color: var(--text-secondary);
        font-weight: 500;
    }

    .drp-footer-actions {
        display: flex;
        gap: 0.5rem;
    }

    /* ---- Mobile Drawer ---- */
    .drp-drawer-content {
        padding: 0 0.75rem;
        display: flex;
        flex-direction: column;
        gap: 0.35rem;
    }

    .drp-drawer-presets {
        display: flex;
        flex-wrap: wrap;
        gap: 0.25rem;
    }

    .drp-drawer-presets :global(.preset-list) {
        flex-direction: row;
        flex-wrap: wrap;
        gap: 0.25rem;
        padding: 0.25rem 0;
    }

    .drp-drawer-presets :global(.preset-btn) {
        font-size: 0.82rem;
        padding: 0.3rem 0.55rem;
    }

    .drp-drawer-calendar {
        display: flex;
        flex-direction: column;
        align-items: stretch;
    }

    .drp-drawer-footer {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 0.5rem 0;
        border-top: 1px solid var(--border-color);
        margin-top: 0.25rem;
    }
</style>
