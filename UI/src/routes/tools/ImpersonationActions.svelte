<script>
    import UserSwitch from 'components/buttons/UserSwitch.svelte';
    export let row;
    export let datatable;
    let busy = false;
    async function select() {
        if (busy) return;
        busy = true;
        try { await row.onSelect(row); } finally { busy = false; }
    }
</script>
<div class="action-column">
    <UserSwitch disabled={busy} popover_text={`Impersona ${row.username}`} on:open={select} />
</div>
