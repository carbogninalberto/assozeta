<script>
    export let label;
    export let name = 'switch';
    export let id;
    export let disabled = false;
    $: inputId = id || name;
    export let checked;
    export let helpText;
    export let required = false;
</script>

<div class="form-group">
    {#if label}
        <label for={inputId} class="font-weight-bolder mb-1">{label}</label>
    {/if}
    <div class="input-group">
        <span class="switch switch-icon switch-sm ml-0">
            <label>
                <input id={inputId} type="checkbox" role="switch" aria-label={label} aria-checked={!!checked} {name} bind:checked {required} {disabled} on:change aria-describedby={helpText ? `${inputId}-help` : undefined} />
                <span aria-hidden="true" />
            </label>
        </span>
    </div>
    {#if helpText}
        <div class="text-muted font-size-sm mt-1" id={`${inputId}-help`}>
            {helpText}
        </div>
    {/if}
</div>

<style>
    /* The theme hides the native input with zero size. Lay it, still
       transparent and under the drawn track, over that track instead, so the
       accessible switch has the size and position people see. */
    .switch {position: relative;}
    .switch input {inset: 0; width: 100%; height: 100%; margin: 0;}
    input:focus-visible + span {outline: 3px solid var(--primary); outline-offset: 3px;}
</style>
