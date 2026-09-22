<script>
    import {ChevronDown} from 'lucide-svelte';
</script>

<ChevronDown size={16} class="dropdown-caret-icon" aria-hidden="true" />

<style>
    :global(.dropdown-toggle.has-dropdown-caret::after),
    :global(.dropdown-toggle.has-dropdown-caret::before) {
        display: none !important;
        content: none !important;
    }

    :global(.dropdown-caret-icon) {
        display: inline-block;
        flex-shrink: 0;
        vertical-align: middle;
        margin-left: 0.35rem;
    }
</style>
