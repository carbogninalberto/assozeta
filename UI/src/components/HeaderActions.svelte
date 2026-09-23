<script>
    import {PlusCircle} from 'phosphor-svelte';
    import BasicDropdown from './dropdowns/basic-dropdown.svelte';
    import AgentToggle from './agent/AgentToggle.svelte';

    export let quickAddItems = [];
    export let showQuickAdd = false;
    export let showAI = false;
</script>

<div class="header-actions d-flex align-items-center gap-2">
    {#if showQuickAdd && quickAddItems.length > 0}
        <BasicDropdown variant="clean" size="sm" buttonClass="px-2 py-2" items={quickAddItems} on:itemClick>
            <span slot="button-content" class="d-flex align-items-center" aria-label="Azioni rapide">
                <PlusCircle size={18} weight="bold" />
            </span>
        </BasicDropdown>
    {/if}
    {#if showAI}<AgentToggle />{/if}
</div>

<style>
    .header-actions :global(.bk-dropdown-toggle), .header-actions :global(.agent-toggle-btn) {
        height: 2.5rem;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        margin-top: 0;
        margin-bottom: 0;
    }
</style>
