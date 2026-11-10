<script>
    import {FloppyDisk} from 'phosphor-svelte';
    // Pianificazione: automatic backups (off by default) and how many to keep.
    import {toast} from 'svelte-sonner';
    import Switch from '../../../components/inputs/Switch.svelte';
    import {formatDate, formatRelative} from './instanceFormat.js';

    export let request;
    export let schedule;
    export let retention = {};
    export let disabled = false;
    export let onSaved = () => {};

    const weekdays = ['Lunedì', 'Martedì', 'Mercoledì', 'Giovedì', 'Venerdì', 'Sabato', 'Domenica'];
    const pick = (value, keep) => ({enabled: !!value.enabled, frequency: value.frequency || 'daily', time: value.time || '03:00', weekday: value.weekday ?? 0, keep: keep ?? 3});
    let draft = pick(schedule, retention?.automatic);
    let baseline = JSON.stringify(draft);
    let saving = false;
    let error = '';

    // Follow the server after a save or reload, unless the owner is editing.
    $: if (!changed) adopt(schedule, retention);
    $: changed = JSON.stringify(draft) !== baseline;
    $: last = schedule.last_run;
    // Times are the server's: say so only when the browser is elsewhere.
    $: browserZone = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone; } catch { return ''; } })();
    $: zoneHint = schedule.timezone && browserZone && schedule.timezone !== browserZone ? ` (${schedule.timezone})` : '';

    function adopt(value, keep) {
        const next = pick(value, keep?.automatic);
        if (JSON.stringify(next) !== baseline) { draft = next; baseline = JSON.stringify(next); }
    }

    async function save() {
        if (saving || !changed) return;
        saving = true;
        error = '';
        try {
            const result = await request('/admin/backups/schedule', {method: 'PUT', body: JSON.stringify(draft)});
            baseline = JSON.stringify(draft);
            toast.success(draft.enabled ? 'Pianificazione salvata.' : 'Backup automatici disattivati.');
            onSaved(result);
        } catch (e) { error = e.message; }
        finally { saving = false; }
    }
</script>

<form class="si-card" aria-labelledby="backup-schedule-title" on:submit|preventDefault={save}>
    <div class="si-card-head">
        <div>
            <h3 id="backup-schedule-title" class="si-subtitle">Backup automatici</h3>
            <p class="si-muted si-small">
                {#if schedule.enabled && schedule.next_run}Prossimo: {formatDate(schedule.next_run)}{:else}Disattivati{/if}
            </p>
        </div>
        {#if last}
            <span class="si-pill {last.status === 'failed' ? 'bad' : 'ok'}" title={formatDate(last.at)}>{last.status === 'failed' ? 'Ultimo non riuscito' : `Ultimo ${formatRelative(last.at)}`}</span>
        {/if}
    </div>
    {#if last?.status === 'failed' && last.error}<p class="text-danger si-small" role="alert">{last.error}</p>{/if}
    <fieldset disabled={disabled || saving}>
        <legend class="si-sr-only">Pianificazione dei backup</legend>
        <Switch id="backup-schedule-enabled" label="Attiva backup automatici" bind:checked={draft.enabled} />
        <div class="grid" class:off={!draft.enabled}>
            <div class="si-field">
                <label for="backup-schedule-frequency">Frequenza</label>
                <select id="backup-schedule-frequency" class="form-control" bind:value={draft.frequency} disabled={!draft.enabled}>
                    <option value="daily">Ogni giorno</option>
                    <option value="weekly">Ogni settimana</option>
                </select>
            </div>
            {#if draft.frequency === 'weekly'}
                <div class="si-field">
                    <label for="backup-schedule-weekday">Giorno</label>
                    <select id="backup-schedule-weekday" class="form-control" bind:value={draft.weekday} disabled={!draft.enabled}>
                        {#each weekdays as day, index}<option value={index}>{day}</option>{/each}
                    </select>
                </div>
            {/if}
            <label class="si-field">Ora{zoneHint}<input class="form-control" type="time" bind:value={draft.time} required disabled={!draft.enabled} /></label>
            <label class="si-field">Da conservare<input class="form-control" type="number" min="1" max="30" step="1" bind:value={draft.keep} required aria-describedby="backup-keep-help" /></label>
        </div>
        <p id="backup-keep-help" class="si-hint mt-2 mb-0">Numero di backup automatici da tenere. Se lo spazio non basta, i più vecchi vengono eliminati prima del nuovo.</p>
        {#if error}<p class="text-danger mt-3 mb-0" role="alert">{error}</p>{/if}
        <div class="si-savebar" class:dirty={changed}>
            <button type="submit" class="btn btn-primary si-btn" disabled={!changed}><FloppyDisk size={18} weight="duotone" />{saving ? 'Salvataggio…' : 'Salva pianificazione'}</button>
            {#if changed}<button type="button" class="btn btn-light" on:click={() => draft = JSON.parse(baseline)}>Annulla</button><span class="si-dirty" role="status">Modifiche non salvate</span>{/if}
        </div>
    </fieldset>
</form>

<style>
    fieldset { min-width: 0; }
    fieldset :global(.form-group) { margin-bottom: .75rem; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 9.5rem), 1fr)); gap: .75rem 1rem; }
    .grid.off .si-field:not(:last-child) { opacity: .6; }
    .si-field > label { margin: 0; }
</style>
