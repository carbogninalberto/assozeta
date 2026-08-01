<script>
	import { Calendar } from 'lucide-svelte';
    import {createEventDispatcher} from 'svelte';
    import moment from 'moment';

    const dispatch = createEventDispatcher();

    export let id = '';
    export let name = '';
    export let value = '';
    export let placeholder = 'Seleziona data';
    export let format = 'YYYY-MM-DD';
    export let required = false;
    export let disabled = false;
    export let min = undefined;
    export let max = undefined;
    export let includeTime = false;
    export let showCalendarIcon = true;
    export let sizeClass = 'form-control-lg';
    export let inputClass = '';
    export let wrapperClass = '';
    export let spellcheck = false;
    export let inputId = '';
    export let inputAttrs = {};
    export let bare = false;

    let inputEl;
    let nativeValue = '';

    function normalizeFormat(fmt) {
        if (fmt === 'L') return 'DD/MM/YYYY';
        return fmt;
    }

    const resolvedFormat = normalizeFormat(format);

    function toNative(val) {
        if (!val) return '';
        const m = moment(val, resolvedFormat, true);
        if (m.isValid()) {
            if (includeTime) return m.format('YYYY-MM-DDTHH:mm');
            return m.format('YYYY-MM-DD');
        }
        return '';
    }

    function fromNative(native) {
        if (!native) return '';
        if (includeTime) {
            return moment(native, 'YYYY-MM-DDTHH:mm').format(resolvedFormat);
        }
        return moment(native, 'YYYY-MM-DD').format(resolvedFormat);
    }

    $: {
        const converted = toNative(value);
        if (converted !== nativeValue) {
            nativeValue = converted;
        }
    }

    function handleChange(e) {
        const display = fromNative(e.target.value);
        if (display !== value) {
            value = display;
            dispatch('change', display);
        }
    }

    $: minAttr = min || undefined;
    $: maxAttr = max || undefined;

    function handleCalendarClick(e) {
        e.preventDefault();
        if (!inputEl) return;
        inputEl.focus();
        if (typeof inputEl.showPicker === 'function') {
            try { inputEl.showPicker(); } catch (err) {}
        } else {
            inputEl.click();
        }
    }
</script>

    {#if bare}
    <input
        bind:this={inputEl}
        id={inputId || id || undefined}
        type={includeTime ? 'datetime-local' : 'date'}
        value={nativeValue}
        {name}
        class="form-control form-control-solid {sizeClass} {inputClass}"
        {placeholder}
        {required}
        {disabled}
        min={minAttr}
        max={maxAttr}
        on:input={handleChange}
        on:change={handleChange}
    />
{:else}
    <div
        class="input-group input-group-solid date {wrapperClass}"
        id={id}
        data-target-input="nearest"
    >
        <input
            bind:this={inputEl}
            id={inputId || undefined}
            type={includeTime ? 'datetime-local' : 'date'}
            value={nativeValue}
            {name}
            class="form-control form-control-solid {sizeClass} datetimepicker-input {inputClass}"
            {placeholder}
            {required}
            {disabled}
            min={minAttr}
            max={maxAttr}
            on:input={handleChange}
            on:change={handleChange}
        />
        {#if showCalendarIcon}
            <!-- svelte-ignore a11y-click-events-have-key-events -->
            <div class="input-group-append" on:click={handleCalendarClick}>
                <span class="input-group-text">
                    <Calendar size={16} />
                </span>
            </div>
        {/if}
    </div>
{/if}

<style>
    input::-webkit-calendar-picker-indicator,
    input::-webkit-clear-button,
    input::-webkit-inner-spin-button {
        display: none;
        -webkit-appearance: none;
    }

    input[type='date'],
    input[type='datetime-local'] {
        appearance: textfield;
        -webkit-appearance: textfield;
    }

    .input-group {
        flex-wrap: nowrap;
    }

    .input-group-append {
        flex-shrink: 0;
    }
</style>
