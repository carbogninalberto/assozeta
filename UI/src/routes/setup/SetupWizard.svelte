<script>
	import { AlertTriangle, Check as LucideCheck } from 'lucide-svelte';
    import {onMount, onDestroy} from 'svelte';
    import {fade, scale, fly} from 'svelte/transition';
    import {
        instanceStatus,
        saveInstanceConfig,
        uploadInstanceLogo,
        validateImportFile,
        startImport,
        checkImportStatus,
        getApiHost
    } from 'store/instanceStore.js';
    import {Check, Stack} from 'phosphor-svelte';

    import StepDomain from './StepDomain.svelte';
    import StepDataSource from './StepDataSource.svelte';
    import StepImport from './StepImport.svelte';
    import StepFresh from './StepFresh.svelte';
    import StepBranding from './StepBranding.svelte';
    import StepComplete from './StepComplete.svelte';

    // Wizard state
    let currentStep = 1;
    let loading = false;
    let error = null;

    // Step 1: Domain configuration
    let domainConfig = {
        domain: '',
    };

    // Step 2: Data source selection
    let dataSource = null; // 'import' or 'fresh'

    // Step 3a: Import configuration
    let importConfig = {
        file: null,
        ownerEmail: '',
        ownerPassword: '',
        preserveUuids: false,
        skipFiles: false,
        validationResult: null,
        importTaskId: null,
        importResult: null
    };

    // Step 3b: Fresh configuration
    let freshConfig = {
        associationName: '',
        ownerEmail: '',
        ownerPassword: '',
        ownerPasswordConfirm: ''
    };

    // Step 4: Branding configuration
    let brandingConfig = {
        name: '',
        abbreviation: '',
        primaryColor: '#351DC2',
        supportEmail: '',
        logoFile: null,
        logoPreview: null
    };

    // Step 5: OAuth configuration (optional)
    let oauthConfig = {
        googleClientId: '',
        appleClientId: ''
    };

    // Final result
    let setupResult = null;

    // Steps configuration
    const steps = [
        {id: 1, label: 'Dominio', icon: 'globe'},
        {id: 2, label: 'Dati', icon: 'database'},
        {id: 3, label: dataSource === 'import' ? 'Importa' : 'Crea', icon: dataSource === 'import' ? 'upload' : 'plus'},
        {id: 4, label: 'Branding', icon: 'palette'},
        {id: 5, label: 'Completato', icon: 'check'}
    ];

    onMount(() => {
        // Set default domain from current hostname
        domainConfig.domain = window.location.hostname;
    });

    function nextStep() {
        if (currentStep < 5) {
            currentStep++;
        }
    }

    function prevStep() {
        if (currentStep > 1) {
            currentStep--;
        }
    }

    function goToStep(step) {
        // Only allow going to previous steps or the current step
        if (step <= currentStep) {
            currentStep = step;
        }
    }

    async function handleFinalSubmit() {
        loading = true;
        error = null;

        try {
            // Upload logo if provided
            let logoUrl = null;
            if (brandingConfig.logoFile) {
                const logoResult = await uploadInstanceLogo(brandingConfig.logoFile);
                if (logoResult.success) {
                    logoUrl = logoResult.logo_url;
                }
            }

            // Build configuration object
            const config = {
                domain: domainConfig.domain,
                oem: {
                    name: brandingConfig.name,
                    abbreviation: brandingConfig.abbreviation,
                    primaryColor: brandingConfig.primaryColor,
                    supportEmail: brandingConfig.supportEmail,
                    logo: logoUrl
                },
                oauth: {
                    googleClientId: oauthConfig.googleClientId || null,
                    appleClientId: oauthConfig.appleClientId || null
                },
                initialization: dataSource === 'import'
                    ? {
                        type: 'import',
                        importTaskId: importConfig.importTaskId
                    }
                    : {
                        type: 'fresh',
                        associationName: freshConfig.associationName,
                        ownerEmail: freshConfig.ownerEmail,
                        ownerPassword: freshConfig.ownerPassword
                    }
            };

            const result = await saveInstanceConfig(config);

            if (result.success) {
                setupResult = result;
                currentStep = 5;
            } else {
                error = result.error || 'Si è verificato un errore durante la configurazione';
            }
        } catch (e) {
            error = e.message || 'Si è verificato un errore durante la configurazione';
        } finally {
            loading = false;
        }
    }

    function handleComplete() {
        // Redirect to login page
        window.location.href = '/#/login';
    }
</script>

<svelte:head>
    <title>Configurazione Iniziale</title>
</svelte:head>

<div class="setup-wizard d-flex flex-column min-vh-100 position-relative" style="background-color: white;">
    <div class="position-absolute w-100 h-100" style="background-image: url('/static/forms/pattern.png'); background-size: cover; opacity: 0.03; pointer-events: none;"></div>
    <!-- Header -->
    <div class="setup-header bg-white shadow-sm py-4 px-5 position-relative" style="z-index: 1;">
        <div class="d-flex align-items-center justify-content-between">
            <div class="d-flex align-items-center">
                <div class="setup-icon mr-3">
                    <Stack size={32} weight="duotone" />
                </div>
                <div>
                    <h1 class="font-weight-bolder mb-0" style="font-size: 1.25rem;">Configurazione Iniziale</h1>
                    <p class="text-muted mb-0 font-size-sm">Configura la tua istanza in pochi passi</p>
                </div>
            </div>

            <!-- Step indicator -->
            <div class="step-indicator d-none d-md-flex align-items-center">
                {#each steps as step, i}
                    <button
                        type="button"
                        class="step-dot"
                        class:active={currentStep === step.id}
                        class:completed={currentStep > step.id}
                        on:click={() => goToStep(step.id)}
                        disabled={step.id > currentStep}
                    >
                        {#if currentStep > step.id}
                            <LucideCheck size={14} weight="bold" />
                        {:else}
                            {step.id}
                        {/if}
                    </button>
                    {#if i < steps.length - 1}
                        <div class="step-line" class:completed={currentStep > step.id}></div>
                    {/if}
                {/each}
            </div>
        </div>
    </div>

    <!-- Main content -->
    <div class="setup-content flex-grow-1 d-flex align-items-center justify-content-center py-5 px-4 position-relative" style="z-index: 1;">
        <div class="setup-card bg-white rounded-lg border" style="max-width: 600px; width: 100%;">
            {#if error}
                <div class="alert alert-danger m-4" transition:fade>
                    <AlertTriangle size={16} class="mr-2" />
                    {error}
                </div>
            {/if}

            <div class="p-5">
                {#if currentStep === 1}
                    <div in:fly={{x: 20, duration: 200}}>
                        <StepDomain
                            bind:config={domainConfig}
                            on:next={nextStep}
                        />
                    </div>
                {:else if currentStep === 2}
                    <div in:fly={{x: 20, duration: 200}}>
                        <StepDataSource
                            bind:dataSource
                            on:next={nextStep}
                            on:prev={prevStep}
                        />
                    </div>
                {:else if currentStep === 3}
                    <div in:fly={{x: 20, duration: 200}}>
                        {#if dataSource === 'import'}
                            <StepImport
                                bind:config={importConfig}
                                on:next={nextStep}
                                on:prev={prevStep}
                            />
                        {:else}
                            <StepFresh
                                bind:config={freshConfig}
                                on:next={nextStep}
                                on:prev={prevStep}
                            />
                        {/if}
                    </div>
                {:else if currentStep === 4}
                    <div in:fly={{x: 20, duration: 200}}>
                        <StepBranding
                            bind:config={brandingConfig}
                            bind:oauthConfig
                            {loading}
                            on:submit={handleFinalSubmit}
                            on:prev={prevStep}
                        />
                    </div>
                {:else if currentStep === 5}
                    <div in:fly={{x: 20, duration: 200}}>
                        <StepComplete
                            result={setupResult}
                            on:complete={handleComplete}
                        />
                    </div>
                {/if}
            </div>
        </div>
    </div>

    <!-- Footer -->
    <div class="setup-footer bg-white py-3 px-5 border-top position-relative" style="z-index: 1;">
        <div class="d-flex align-items-center justify-content-center">
            <div class="text-muted small">
                Software Assozeta ({__bakney.build.VERSION})
            </div>
        </div>
    </div>
</div>

<style>
    .setup-icon {
        color: var(--main-color, #351DC2);
    }

    .step-indicator {
        gap: 0;
    }

    .step-dot {
        width: 32px;
        height: 32px;
        border-radius: 50%;
        border: 2px solid var(--border-color);
        background: white;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 12px;
        font-weight: 600;
        color: var(--text-muted);
        cursor: pointer;
        transition: all 0.2s ease;
    }

    .step-dot:disabled {
        cursor: not-allowed;
    }

    .step-dot.active {
        border-color: var(--main-color, #351DC2);
        background: var(--main-color, #351DC2);
        color: white;
    }

    .step-dot.completed {
        border-color: var(--main-color, #351DC2);
        background: var(--main-color, #351DC2);
        color: white;
    }

    .step-line {
        width: 40px;
        height: 2px;
        background: var(--border-color);
        transition: background 0.2s ease;
    }

    .step-line.completed {
        background: var(--main-color, #351DC2);
    }

    .setup-card {
        border-color: #f0f0f0 !important;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.07), 0 2px 4px -1px rgba(0, 0, 0, 0.04);
    }

    :global(.setup-card .form-control) {
        border-radius: 0.5rem;
        padding: 1rem 1.25rem;
        border: 1px solid var(--border-color);
        background: var(--bg-surface-secondary);
    }

    :global(.setup-card .form-control:focus) {
        border-color: var(--main-color, #351DC2);
        background: white;
        box-shadow: 0 0 0 0.2rem rgba(53, 29, 194, 0.1);
    }

    :global(.setup-card .btn-primary) {
        background: var(--main-color, #351DC2);
        border-color: var(--main-color, #351DC2);
        padding: 0.75rem 1.5rem;
        border-radius: 0.5rem;
        font-weight: 600;
    }

    :global(.setup-card .btn-primary:hover) {
        background: var(--main-color, #351DC2);
        filter: brightness(1.1);
    }

    :global(.setup-card .btn-light) {
        background: var(--bg-surface-secondary);
        border-color: var(--border-color);
        padding: 0.75rem 1.5rem;
        border-radius: 0.5rem;
        font-weight: 600;
    }
</style>
