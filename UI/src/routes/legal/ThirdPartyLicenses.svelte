<script>
    import {onMount} from 'svelte';
    import SvelteMarkdown from 'svelte-markdown';

    let loading = true;
    let error = '';
    let content = '';

    onMount(async () => {
        try {
            const response = await fetch('/THIRD-PARTY-LICENSES.md', {cache: 'no-cache'});
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            content = await response.text();
        } catch (e) {
            error = `Impossibile caricare il file delle licenze: ${e?.message || e}`;
        } finally {
            loading = false;
        }
    });
</script>

<svelte:head>
    <title>Licenze terze parti</title>
</svelte:head>

<div class="container">
    {#if loading}
        <div class="text-muted font-weight-bold">Caricamento licenze...</div>
    {:else if error}
        <div class="alert alert-custom alert-light-danger mb-0" role="alert">
            <div class="alert-text font-weight-bold">{error}</div>
        </div>
    {:else}
        <div class="license-content">
            <SvelteMarkdown source={content} />
        </div>
    {/if}
</div>

<style>
    .license-content {
        word-break: break-word;
        overflow-x: auto;
        background: var(--bg-surface, #f8f9fa);
        color: var(--text-primary, #181c32);
        font-size: 0.875rem;
        line-height: 1.5;
    }

    :global(.license-content h1),
    :global(.license-content h2),
    :global(.license-content h3) {
        margin-top: 1.5rem;
        margin-bottom: 1rem;
        font-weight: 700;
    }

    :global(.license-content h1:first-child) {
        margin-top: 0;
    }

    :global(.license-content table) {
        width: 100%;
        margin: 1rem 0;
        border-collapse: collapse;
        background: var(--bg-surface-secondary, #fff);
    }

    :global(.license-content th),
    :global(.license-content td) {
        padding: 0.65rem 0.75rem;
        border: 1px solid var(--border-color, #ebedf3);
        vertical-align: top;
    }

    :global(.license-content th) {
        font-weight: 700;
        background: var(--bg-surface, #f8f9fa);
    }

    :global(.license-content p),
    :global(.license-content ul) {
        margin-bottom: 1rem;
    }
</style>
