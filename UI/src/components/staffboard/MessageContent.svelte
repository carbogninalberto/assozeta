<script>
    import {documentHTML} from 'utils/staffBoardDocument.js';
    export let message;
</script>

<div class="message-content text-dark-75 font-weight-normal">
    {#if message.document}
        {@html documentHTML(message.document)}
    {:else}
        <p class="plain-text mb-0">{message.content}</p>
    {/if}
</div>

<style>
    .message-content {
        overflow-wrap: anywhere;
    }
    .plain-text {
        white-space: pre-wrap;
    }
    .message-content :global(img) {
        display: block;
        max-width: 100%;
        max-height: 32rem;
        height: auto;
        border-radius: 0.5rem;
        margin: 0.75rem 0;
        object-fit: contain;
    }
    .message-content :global(p:last-child) {
        margin-bottom: 0;
    }
    .message-content :global(pre) {
        white-space: pre-wrap;
        color: inherit;
        background: var(--bg-surface-secondary);
        padding: 1rem;
        border-radius: 0.5rem;
    }
    .message-content :global(table) {
        display: block;
        max-width: 100%;
        overflow-x: auto;
        border-collapse: collapse;
    }
    .message-content :global(td),
    .message-content :global(th) {
        border: 1px solid var(--border-color);
        padding: 0.5rem;
        min-width: 4rem;
    }
    .message-content :global(blockquote) {
        border-left: 3px solid var(--primary);
        padding-left: 1rem;
    }
    .message-content :global(input[type='checkbox']) {
        margin-right: 0.5rem;
    }
    .message-content :global(ul[data-type='taskList']) {
        list-style: none;
        padding-left: 0;
    }
</style>
