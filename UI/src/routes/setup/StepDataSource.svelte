<script>
	import { ArrowLeft, ChevronRight, Plus as LucidePlus, Upload as LucideUpload } from 'lucide-svelte';
    import {createEventDispatcher} from 'svelte';
    import {Database, Upload, Plus} from 'phosphor-svelte';

    export let dataSource = null;

    const dispatch = createEventDispatcher();

    function selectAndNext(source) {
        dataSource = source;
        dispatch('next');
    }
</script>

<div class="step-data-source">
    <div class="text-center mb-5">
        <div class="step-icon mx-auto mb-4">
            <Database size={32} weight="duotone" />
        </div>
        <h2 class="font-weight-bolder mb-2">Inizializzazione Dati</h2>
        <p class="text-muted font-size-sm">
            Scegli come vuoi inizializzare la tua istanza
        </p>
    </div>

    <div class="source-options">
        <button
            type="button"
            class="source-option"
            class:selected={dataSource === 'import'}
            on:click={() => selectAndNext('import')}
        >
            <div class="source-icon">
                <LucideUpload size={28} weight="duotone" />
            </div>
            <div class="source-content">
                <h5 class="font-weight-bolder mb-1">Importa da Backup</h5>
                <p class="text-muted mb-0 font-size-sm">
                    Ripristina i dati da un file di backup esistente (ZIP)
                </p>
            </div>
            <div class="source-arrow">
                <ChevronRight size={16} />
            </div>
        </button>

        <button
            type="button"
            class="source-option"
            class:selected={dataSource === 'fresh'}
            on:click={() => selectAndNext('fresh')}
        >
            <div class="source-icon fresh">
                <LucidePlus size={28} weight="duotone" />
            </div>
            <div class="source-content">
                <h5 class="font-weight-bolder mb-1">Inizia da Zero</h5>
                <p class="text-muted mb-0 font-size-sm">
                    Crea una nuova associazione e inizia con un database vuoto
                </p>
            </div>
            <div class="source-arrow">
                <ChevronRight size={16} />
            </div>
        </button>
    </div>

    <div class="d-flex justify-content-start mt-4">
        <button
            type="button"
            class="btn btn-light font-weight-bolder"
            on:click={() => dispatch('prev')}
        >
            <ArrowLeft size={16} class="mr-2" />
            Indietro
        </button>
    </div>
</div>

<style>
    .step-icon {
        width: 70px;
        height: 70px;
        border-radius: 50%;
        background: linear-gradient(135deg, rgba(53, 29, 194, 0.1) 0%, rgba(53, 29, 194, 0.05) 100%);
        display: flex;
        align-items: center;
        justify-content: center;
        color: var(--main-color, #351DC2);
    }

    .source-options {
        display: flex;
        flex-direction: column;
        gap: 1rem;
    }

    .source-option {
        display: flex;
        align-items: center;
        padding: 1.25rem;
        border: 1px solid #e4e6ef;
        border-radius: 0.75rem;
        background: white;
        cursor: pointer;
        transition: all 0.2s ease;
        text-align: left;
        width: 100%;
    }

    .source-option:hover {
        border-color: var(--main-color, #351DC2);
        background: rgba(53, 29, 194, 0.02);
    }

    .source-option.selected {
        border-color: var(--main-color, #351DC2);
        background: rgba(53, 29, 194, 0.05);
    }

    .source-icon {
        width: 50px;
        height: 50px;
        border-radius: 0.75rem;
        background: rgba(53, 29, 194, 0.1);
        display: flex;
        align-items: center;
        justify-content: center;
        color: var(--main-color, #351DC2);
        margin-right: 1rem;
        flex-shrink: 0;
    }

    .source-icon.fresh {
        background: rgba(80, 205, 137, 0.1);
        color: #50cd89;
    }

    .source-content {
        flex-grow: 1;
    }

    .source-arrow {
        color: var(--text-muted);
        margin-left: 1rem;
    }
</style>
